"""Matrix node: discovers and reads expression matrices."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

from scruticell.pipeline.state import PipelineState
from scruticell.readers import read_h5ad, read_mtx, read_csv_matrix, read_rds, read_loom

logger = logging.getLogger(__name__)

_FORMAT_ORDER = ["h5ad", "mtx", "csv", "rds", "loom"]

_H5AD_EXTS = {".h5ad", ".h5"}
_MTX_EXTS = {".mtx", ".mtx.gz"}
_CSV_EXTS = {".csv", ".csv.gz", ".tsv", ".tsv.gz", ".txt", ".txt.gz", ".tab", ".tab.gz"}
_RDS_EXTS = {".rds", ".RDS", ".rdata", ".RData", ".Rda", ".rda"}
_LOOM_EXTS = {".loom"}

_TENX_DIR_MARKERS = {"matrix.mtx", "matrix.mtx.gz", "barcodes.tsv", "barcodes.tsv.gz"}


def _guess_format(filepath: Path) -> str:
    """Guess the matrix format from a file path."""
    name_lower = filepath.name.lower()
    for ext in _H5AD_EXTS:
        if name_lower.endswith(ext):
            return "h5ad"
    for ext in _MTX_EXTS:
        if name_lower.endswith(ext):
            return "mtx"
    for ext in _CSV_EXTS:
        if name_lower.endswith(ext):
            return "csv"
    for ext in _RDS_EXTS:
        if name_lower.endswith(ext):
            return "rds"
    for ext in _LOOM_EXTS:
        if name_lower.endswith(ext):
            return "loom"
    return ""


def _is_mtx_directory(path: Path) -> bool:
    """Check if a directory contains 10X MTX files."""
    if not path.is_dir():
        return False
    dir_contents = {f.name.lower() for f in path.iterdir() if f.is_file()}
    return any(marker in dir_contents for marker in _TENX_DIR_MARKERS)


def discover_matrix_node(state: PipelineState) -> dict[str, Any]:
    """Discover all expression matrices among supplementary files.

    Searches the downloaded supplementary files for expression matrices.
    File format priority: .h5ad > .mtx directory > .csv/.tsv > .rds > .loom.

    For MTX format, checks whether the file is a directory containing the
    10X trio (matrix.mtx, barcodes.tsv, features/genes.tsv) or an individual
    .mtx file whose parent directory should be used.

    When multiple matrix files of the same format are found (multi-sample GEO),
    all are returned in matrix_files for combined reading.

    Args:
        state: Pipeline state with supplementary_files and raw_metadata set.

    Returns:
        Partial state update with matrix_file, matrix_files, matrix_format.
    """
    logger.info("Discovering expression matrix for %s", state.gse_id)

    candidates: dict[str, Path] = {}
    all_matrix_files: dict[str, list[Path]] = {}

    for filepath in state.supplementary_files:
        if not isinstance(filepath, Path):
            continue
        if not filepath.exists():
            continue

        if filepath.is_dir() and _is_mtx_directory(filepath):
            fmt = "mtx"
        else:
            fmt = _guess_format(filepath)
            if not fmt:
                continue

        if fmt not in all_matrix_files:
            all_matrix_files[fmt] = []
        all_matrix_files[fmt].append(filepath)

        if fmt not in candidates:
            candidates[fmt] = filepath

    for fmt in _FORMAT_ORDER:
        if fmt in candidates:
            matrix_files = all_matrix_files.get(fmt, [candidates[fmt]])
            logger.info(
                "Selected matrix format: %s (%d file(s))",
                fmt, len(matrix_files),
            )
            return {
                "matrix_file": candidates[fmt],
                "matrix_files": matrix_files,
                "matrix_format": fmt,
            }

    logger.warning("No expression matrix found among supplementary files")
    return {
        "matrix_file": None,
        "matrix_files": [],
        "matrix_format": "",
    }


def _read_matrix(filepath: Path, fmt: str) -> dict[str, Any]:
    """Dispatch to the appropriate reader based on format."""
    readers = {
        "h5ad": read_h5ad,
        "mtx": read_mtx,
        "csv": read_csv_matrix,
        "rds": read_rds,
        "loom": read_loom,
    }

    reader = readers.get(fmt)
    if reader is None:
        raise ValueError(f"Unknown matrix format: {fmt}")

    return reader(filepath)


def _is_gene_x_cell(gene_names: list[str], matrix_shape: tuple[int, int]) -> bool:
    """Heuristically detect if the matrix orientation is gene x cell.

    Compares the number of gene names to matrix dimensions. If gene names
    count matches the first dimension (rows), it is gene x cell.
    """
    n_rows, n_cols = matrix_shape
    n_genes = len(gene_names)

    if n_genes == 0 or n_rows == 0 or n_cols == 0:
        return False

    if n_genes == n_rows and n_genes != n_cols:
        return True
    if n_genes == n_cols and n_genes != n_rows:
        return False
    if n_rows < n_cols:
        return True
    return False


def read_matrix_node(state: PipelineState) -> dict[str, Any]:
    """Read the discovered expression matrix file(s).

    Dispatches to the appropriate format-specific reader (h5ad, mtx, csv,
    rds, loom). After loading, detects matrix orientation (gene x cell vs
    cell x gene) and transposes if the data is cell x gene so that the
    result is always gene x cell.

    When multiple matrix files exist (multi-sample GEO), reads all of them,
    aligns genes across files, and concatenates the matrices.

    Args:
        state: Pipeline state with matrix_file, matrix_files and matrix_format set.

    Returns:
        Partial state update with expression_matrix, cell_barcodes_raw,
        gene_names_raw, matrix_shape, is_sparse, and cell_batch_labels.
    """
    if not state.matrix_files or not state.matrix_format:
        logger.error("No matrix file to read (matrix_files=%s, format=%s)",
                      state.matrix_files, state.matrix_format)
        return {
            "expression_matrix": None,
            "cell_barcodes_raw": [],
            "gene_names_raw": [],
            "matrix_shape": (0, 0),
            "is_sparse": False,
            "cell_batch_labels": [],
        }

    fmt = state.matrix_format
    matrix_files = state.matrix_files

    logger.info("Reading %d expression matrix file(s) (format: %s)",
                len(matrix_files), fmt)

    try:
        if len(matrix_files) == 1:
            return _read_single_matrix(matrix_files[0], fmt, state)
        else:
            return _read_and_merge_matrices(matrix_files, fmt, state)
    except Exception as exc:
        logger.exception("Failed to read matrix: %s", exc)
        state.add_error(f"Matrix read error: {exc}")
        return {
            "expression_matrix": None,
            "cell_barcodes_raw": [],
            "gene_names_raw": [],
            "matrix_shape": (0, 0),
            "is_sparse": False,
            "cell_batch_labels": [],
        }


def _read_single_matrix(filepath: Path, fmt: str, state: PipelineState) -> dict[str, Any]:
    """Read a single expression matrix file (original behavior)."""
    logger.info("Reading expression matrix from %s (format: %s)", filepath, fmt)

    result = _read_matrix(filepath, fmt)
    matrix = result["matrix"]
    cell_barcodes = result["cell_barcodes"]
    gene_names = result["gene_names"]
    is_sparse = result["is_sparse"]
    shape = result["shape"]

    if _is_gene_x_cell(gene_names, shape):
        logger.info("Matrix orientation: gene x cell (correct)")
    else:
        logger.info("Matrix orientation: cell x gene — transposing")
        from scipy import sparse as sp
        if sp.issparse(matrix):
            matrix = matrix.T.tocsr()
        else:
            matrix = matrix.T
        shape = matrix.shape

    expression_matrix = matrix
    if not is_sparse and isinstance(matrix, np.ndarray):
        expression_matrix = np.asarray(matrix, dtype=np.float32)

    batch_labels = [str(filepath.stem)] * len(cell_barcodes)

    logger.info(
        "Matrix loaded: shape=%s, sparse=%s, cells=%d, genes=%d",
        shape, is_sparse, len(cell_barcodes), len(gene_names),
    )

    return {
        "expression_matrix": expression_matrix,
        "cell_barcodes_raw": cell_barcodes,
        "gene_names_raw": gene_names,
        "matrix_shape": shape,
        "is_sparse": is_sparse,
        "cell_batch_labels": batch_labels,
    }


def _read_and_merge_matrices(
    filepaths: list[Path], fmt: str, state: PipelineState
) -> dict[str, Any]:
    """Read multiple expression matrix files and merge them.

    All files are read, genes are aligned to the first file's gene list,
    and matrices are concatenated. Returns a combined gene×cell matrix.
    """
    from scipy import sparse as sp

    all_matrices: list[Any] = []
    all_cell_barcodes: list[str] = []
    all_batch_labels: list[str] = []
    reference_genes: list[str] = []
    is_sparse = False

    for i, filepath in enumerate(filepaths):
        logger.info("Reading matrix file %d/%d: %s", i + 1, len(filepaths), filepath)
        result = _read_matrix(filepath, fmt)

        matrix = result["matrix"]
        cell_barcodes = result["cell_barcodes"]
        gene_names = result["gene_names"]
        is_sparse = is_sparse or result["is_sparse"]
        shape = result["shape"]

        # First file establishes the reference gene list
        if i == 0:
            reference_genes = list(gene_names)
        else:
            # Align genes to reference
            matrix = _align_genes_to_reference(matrix, gene_names, reference_genes)

        if _is_gene_x_cell(gene_names if i == 0 else reference_genes, matrix.shape):
            logger.info("  File %d: gene x cell (correct)", i + 1)
        else:
            logger.info("  File %d: cell x gene — transposing", i + 1)
            if sp.issparse(matrix):
                matrix = matrix.T.tocsr()
            else:
                matrix = matrix.T

        batch_name = _extract_gsm_from_filename(str(filepath), state.gsm_entries) or filepath.stem
        all_matrices.append(matrix)
        all_cell_barcodes.extend(cell_barcodes)
        all_batch_labels.extend([batch_name] * len(cell_barcodes))

    # Concatenate matrices horizontally (same genes, different cells)
    # After transpose, matrices are gene×cell (rows=genes, cols=cells)
    if sp.issparse(all_matrices[0]):
        merged = sp.hstack(all_matrices, format="csr")
    else:
        merged = np.hstack(all_matrices)

    shape = merged.shape
    if not is_sparse and isinstance(merged, np.ndarray):
        merged = np.asarray(merged, dtype=np.float32)

    logger.info(
        "Merged %d matrices: shape=%s, sparse=%s, cells=%d, genes=%d",
        len(filepaths), shape, is_sparse, len(all_cell_barcodes), len(reference_genes),
    )

    return {
        "expression_matrix": merged,
        "cell_barcodes_raw": all_cell_barcodes,
        "gene_names_raw": reference_genes,
        "matrix_shape": shape,
        "is_sparse": is_sparse,
        "cell_batch_labels": all_batch_labels,
    }


def _align_genes_to_reference(
    matrix: Any, gene_names: list[str], reference_genes: list[str]
) -> Any:
    """Align a matrix's gene dimension to a reference gene list.

    Reorders rows (genes) to match reference order. Missing genes get zero rows.
    Extra genes (not in reference) are dropped.
    """
    if gene_names == reference_genes:
        return matrix

    from scipy import sparse as sp

    gene_to_ref_idx = {g: i for i, g in enumerate(reference_genes)}
    is_sp = sp.issparse(matrix)

    # Build new matrix: reference_genes rows, original cols
    n_ref = len(reference_genes)
    n_cols = matrix.shape[1]

    if is_sp:
        # Build aligned sparse matrix row by row from original data
        # For each reference gene, find its row in the original matrix
        gene_to_row = {g: i for i, g in enumerate(gene_names)}

        rows: list[int] = []
        cols: list[int] = []
        data: list[float] = []
        for ref_i, ref_g in enumerate(reference_genes):
            if ref_g in gene_to_row:
                orig_row = gene_to_row[ref_g]
                row_data = matrix[orig_row, :]
                if sp.issparse(row_data):
                    row_coo = row_data.tocoo()
                    rows.extend([ref_i] * len(row_coo.data))
                    cols.extend(row_coo.col.tolist())
                    data.extend(row_coo.data.tolist())
                else:
                    for j in range(n_cols):
                        val = row_data[0, j] if len(row_data.shape) == 2 else row_data[j]
                        if val != 0:
                            rows.append(ref_i)
                            cols.append(j)
                            data.append(float(val))
        aligned = sp.csr_matrix((data, (rows, cols)), shape=(n_ref, n_cols))
    else:
        aligned = np.zeros((n_ref, n_cols), dtype=matrix.dtype)
        gene_to_row = {g: i for i, g in enumerate(gene_names)}
        for ref_i, ref_g in enumerate(reference_genes):
            if ref_g in gene_to_row:
                aligned[ref_i, :] = matrix[gene_to_row[ref_g], :]

    return aligned


def _extract_gsm_from_filename(
    filename: str, gsm_entries: dict
) -> str | None:
    """Extract GSM ID from a supplementary file name.

    E.g., 'GSM9779490_E3_vehicle_filtered_feature_bc_matrix.h5' -> 'GSM9779490'
    """
    import re
    for gsm_id in gsm_entries:
        if gsm_id in filename:
            return gsm_id
    match = re.search(r'(GSM\d+)', filename, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    return None
