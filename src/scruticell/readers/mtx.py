"""Read expression matrix from 10X Genomics MTX format.

Expects the trio of files: matrix.mtx(.gz), barcodes.tsv(.gz), features.tsv(.gz)
or the older genes.tsv(.gz) variant.
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse
from scipy.io import mmread

logger = logging.getLogger(__name__)


def _discover_mtx_files(mtx_dir: Path, matrix_hint: str | None = None) -> dict[str, Path]:
    """Search directory for the three required MTX files.

    When matrix_hint is provided (filename of a specific matrix file), 
    uses its prefix to narrow down matching barcodes/features files.
    This handles datasets where multiple samples' files are in one directory
    with sample-specific prefixes.

    Returns a dict with keys 'matrix', 'barcodes', 'features' pointing
    to the discovered file paths.

    Raises FileNotFoundError if any required file is missing.
    """
    files: dict[str, Path | None] = {
        "matrix": None,
        "barcodes": None,
        "features": None,
    }
    
    # Extract prefix from matrix_hint to match related files
    prefix = ""
    if matrix_hint:
        hint_lower = matrix_hint.lower()
        for suffix in (".mtx.gz", ".mtx"):
            if hint_lower.endswith(suffix):
                # Strip suffix, then strip trailing _matrix, _counts, _expression etc.
                stem = hint_lower[:-len(suffix)]
                # Remove trailing role words to get the sample prefix
                for role in ("_matrix", "_counts", "_expression", "_filtered", "_raw"):
                    if stem.endswith(role):
                        stem = stem[:-len(role)]
                        break
                # Ensure prefix ends with _ or - for clean matching
                if stem and not stem.endswith(("_", "-")):
                    # Find last delimiter to get the sample prefix
                    for delim in ("_", "-"):
                        idx = stem.rfind(delim)
                        if idx >= 0:
                            prefix = stem[:idx + 1]  # include the delimiter
                            break
                    if not prefix:
                        prefix = stem + "_"
                break

    for candidate in mtx_dir.iterdir():
        if not candidate.is_file():
            continue
        name_lower = candidate.name.lower()

        if "matrix" in name_lower and (
            name_lower.endswith(".mtx") or name_lower.endswith(".mtx.gz")
        ):
            if files["matrix"] is None or (prefix and name_lower.startswith(prefix)):
                files["matrix"] = candidate
        elif "barcodes" in name_lower and name_lower.endswith((".tsv", ".tsv.gz")):
            if files["barcodes"] is None or (prefix and name_lower.startswith(prefix)):
                files["barcodes"] = candidate
        elif ("features" in name_lower or "genes" in name_lower) and name_lower.endswith((".tsv", ".tsv.gz")):
            if files["features"] is None or (prefix and name_lower.startswith(prefix)):
                files["features"] = candidate

    missing = [k for k, v in files.items() if v is None]
    if missing:
        raise FileNotFoundError(
            f"Missing required 10X file(s) in {mtx_dir}: {', '.join(missing)}"
        )

    return {k: v for k, v in files.items() if v is not None}


def _open_possibly_gz(filepath: Path) -> Any:
    """Open a file, decompressing if .gz."""
    if filepath.suffix == ".gz":
        return gzip.open(filepath, "rt")
    return open(filepath, "r")


def _read_barcodes(filepath: Path) -> list[str]:
    """Read 10X barcodes TSV (no header, one column)."""
    with _open_possibly_gz(filepath) as fh:
        return [line.strip().split("\t")[0] for line in fh if line.strip()]


def _read_features(filepath: Path) -> list[str]:
    """Read 10X features/genes TSV.

    For 10X v3 (features.tsv): columns are [feature_id, feature_name, feature_type].
    We extract column 2 (feature_name) as the gene symbol.
    For 10X v2 (genes.tsv): columns are [gene_id, gene_name].
    We extract column 2 (gene_name) as the gene symbol.
    """
    with _open_possibly_gz(filepath) as fh:
        gene_names = []
        for line in fh:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) >= 3:
                gene_names.append(parts[1])
            elif len(parts) >= 2:
                gene_names.append(parts[1])
            else:
                gene_names.append(parts[0])
        return gene_names


def read_mtx(mtx_dir: str | Path, matrix_hint: str | None = None) -> dict[str, Any]:
    """Read a 10X-format sparse matrix from an MTX directory.

    Discovers the three required files (matrix.mtx, barcodes.tsv,
    features/genes.tsv) within the given directory. Handles .gz compression
    for all files. Reads the sparse matrix using scipy.io.mmread.

    Args:
        mtx_dir: Directory containing the 10X output files.
        matrix_hint: Optional filename to help match barcodes/features 
                     when multiple samples share a directory.

    Returns:
        Dict with keys: matrix (sparse csr_matrix), cell_barcodes,
        gene_names (with feature type annotation), is_sparse, shape.
    """
    mtx_dir = Path(mtx_dir)
    logger.info("Reading MTX directory: %s", mtx_dir)

    discovered = _discover_mtx_files(mtx_dir, matrix_hint)

    matrix = mmread(str(discovered["matrix"]))
    cell_barcodes = _read_barcodes(discovered["barcodes"])
    gene_names = _read_features(discovered["features"])

    if sparse.isspmatrix_csc(matrix):
        matrix = matrix.tocsr()

    is_sparse = True
    shape = matrix.shape

    # Standardize to gene×cell first (10X native), then transpose to cell×gene
    n_cells = len(cell_barcodes)
    n_genes = len(gene_names)

    # Determine current orientation by matching against gene count
    if shape[1] == n_genes:
        # cell×gene → transpose to gene×cell first
        matrix = matrix.T.tocsr()
        shape = matrix.shape
    elif shape[0] != n_genes:
        # Neither dimension matches gene count — try heuristic
        if abs(shape[0] - n_genes) < abs(shape[1] - n_genes):
            pass  # rows match gene count, likely gene×cell
        else:
            matrix = matrix.T.tocsr()
            shape = matrix.shape

    if shape[1] != n_cells:
        logger.warning(
            "Cell count mismatch: matrix has %d columns but %d barcodes found "
            "(dataset may have inconsistent supplementary files)",
            shape[1], n_cells,
        )

    # Now gene×cell → transpose to cell×gene (AnnData convention)
    matrix = matrix.T.tocsr()
    shape = matrix.shape

    logger.info(
        "Loaded MTX: shape=%s, sparse=%s, cells=%d, genes=%d",
        shape, is_sparse, len(cell_barcodes), len(gene_names),
    )

    return {
        "matrix": matrix,
        "cell_barcodes": cell_barcodes,
        "gene_names": gene_names,
        "is_sparse": is_sparse,
        "shape": shape,
    }


def _has_mtx_companions(mtx_dir: Path) -> bool:
    """Check if directory has 10X companion files alongside a matrix."""
    names = {f.name.lower() for f in mtx_dir.iterdir() if f.is_file()}
    has_barcodes = any("barcodes" in n for n in names)
    has_features = any("features" in n or "genes" in n for n in names)
    return has_barcodes and has_features


def read_bare_mtx(mtx_file: str | Path) -> dict[str, Any]:
    """Read a bare MTX file without companion barcodes/features files.

    Uses anndata.read_mtx() which auto-generates cell/gene indices.
    """
    import anndata
    from scipy import sparse as sp

    mtx_file = Path(mtx_file)
    logger.info("Reading bare MTX file: %s", mtx_file)

    adata = anndata.read_mtx(mtx_file)

    if sp.issparse(adata.X) and sp.isspmatrix_csc(adata.X):
        adata.X = adata.X.tocsr()

    return {
        "matrix": adata.X,
        "cell_barcodes": list(adata.obs_names),
        "gene_names": list(adata.var_names),
        "is_sparse": sp.issparse(adata.X),
        "shape": adata.X.shape,
    }
