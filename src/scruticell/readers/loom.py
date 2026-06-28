"""Read expression data from Loom format files.

Uses loompy to read .loom files. Extracts expression matrix, gene names
from row attributes, and cell barcodes from column attributes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

logger = logging.getLogger(__name__)


def read_loom(filepath: str | Path) -> dict[str, Any]:
    """Read expression data from a Loom file.

    Extracts:
    - Main expression matrix (dense: layers[''], sparse: layers['matrix'] or 'counts')
    - Gene names from row_attrs (Gene, gene_name, gene_names, Accession)
    - Cell barcodes from col_attrs (barcode, CellID, cell_names, obs_names)

    Args:
        filepath: Path to .loom file.

    Returns:
        Dict with keys: matrix, cell_barcodes, gene_names, is_sparse, shape.
    """
    import loompy

    filepath = Path(filepath)
    logger.info("Reading loom file: %s", filepath)

    try:
        with loompy.connect(str(filepath)) as ds:
            matrix = _extract_matrix(ds)
            gene_names = _extract_gene_names(ds)
            cell_barcodes = _extract_cell_barcodes(ds)
            shape = ds.shape
            is_sparse = sparse.issparse(matrix) if hasattr(sparse, "issparse") else False
    except Exception as exc:
        logger.error("Failed to read loom file: %s", exc)
        return _empty_result()

    if matrix is None:
        return _empty_result()

    # Standardize to cell×gene (AnnData convention) — loom native is gene×cell
    if shape[0] == len(gene_names) and shape[1] == len(cell_barcodes):
        if sparse.issparse(matrix):
            matrix = matrix.T.tocsr()
        else:
            import numpy as np
            matrix = np.asarray(matrix).T
        shape = matrix.shape

    logger.info(
        "Loaded loom: shape=%s, sparse=%s, cells=%d, genes=%d",
        shape, is_sparse, len(cell_barcodes), len(gene_names),
    )

    return {
        "matrix": matrix,
        "cell_barcodes": cell_barcodes,
        "gene_names": gene_names,
        "is_sparse": is_sparse,
        "shape": shape,
    }


def _extract_matrix(ds) -> Any:
    """Extract expression matrix from loom connection."""
    try:
        if "matrix" in ds.layers:
            arr = ds.layers["matrix"]
        elif "counts" in ds.layers:
            arr = ds.layers["counts"]
        elif "" in ds.layers:
            arr = ds.layers[""]
        else:
            arr = ds[:, :]
        return arr
    except Exception as exc:
        logger.warning("Failed to extract loom matrix: %s", exc)
        try:
            return ds[:, :]
        except Exception:
            return None


def _extract_gene_names(ds) -> list[str]:
    """Extract gene names from loom row attributes."""
    row_attrs = ds.ra if hasattr(ds, "ra") else ()

    candidates = ["Gene", "gene_name", "gene_names", "GeneName",
                   "symbol", "Symbol", "feature_name", "Accession"]

    for col_name in candidates:
        if col_name in row_attrs:
            values = row_attrs[col_name]
            if hasattr(values, "tolist"):
                values = values.tolist()
            gene_names = [str(v) for v in values]
            if gene_names:
                return gene_names

    n_rows = ds.shape[0]
    return [f"gene_{i}" for i in range(n_rows)]


def _extract_cell_barcodes(ds) -> list[str]:
    """Extract cell barcodes from loom column attributes."""
    col_attrs = ds.ca if hasattr(ds, "ca") else ()

    candidates = ["barcode", "Barcode", "CellID", "cell_id",
                   "cell_names", "obs_names", "cell_barcode"]

    for col_name in candidates:
        if col_name in col_attrs:
            values = col_attrs[col_name]
            if hasattr(values, "tolist"):
                values = values.tolist()
            cell_barcodes = [str(v) for v in values]
            if cell_barcodes:
                return cell_barcodes

    n_cols = ds.shape[1]
    return [f"cell_{i}" for i in range(n_cols)]


def _empty_result() -> dict[str, Any]:
    return {
        "matrix": np.array([], dtype=np.float32),
        "cell_barcodes": [],
        "gene_names": [],
        "is_sparse": False,
        "shape": (0, 0),
    }
