"""Read AnnData .h5ad and 10X .h5 files.

Extracts expression matrix, cell barcodes, and gene names from AnnData objects
and 10X Genomics HDF5 files. Handles both .X and .raw.X conventions for raw counts.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
from scipy import sparse

logger = logging.getLogger(__name__)


def _read_10x_h5(filepath: Path) -> Any:
    """Read a 10X Genomics HDF5 file using scanpy."""
    import scanpy as sc
    return sc.read_10x_h5(filepath)


def read_h5ad(filepath: str | Path) -> dict[str, Any]:
    """Read an HDF5-based expression matrix file (.h5ad or .h5).

    For .h5ad files, uses anndata.read_h5ad.
    For .h5 files (10X Genomics), uses scanpy.read_10x_h5.

    Attempts to locate raw counts by checking .raw.X first, falling back to .X.
    Detects sparse vs dense storage and returns the matrix in its native format.

    Args:
        filepath: Path to the .h5ad or .h5 file.

    Returns:
        Dict with keys: matrix (ndarray or sparse.csr_matrix), cell_barcodes,
        gene_names, is_sparse, shape.
    """
    import anndata

    filepath = Path(filepath)
    logger.info("Reading HDF5 file: %s", filepath)

    if filepath.suffix.lower() == ".h5":
        logger.info("Detected 10X .h5 format — using scanpy.read_10x_h5")
        adata = _read_10x_h5(filepath)
    else:
        adata = anndata.read_h5ad(filepath)

    if adata.raw is not None and adata.raw.X is not None:
        logger.info("Using .raw.X (raw counts)")
        X = adata.raw.X
        var_names = adata.raw.var_names
    elif adata.raw is not None:
        logger.info(".raw present but no .raw.X — falling back to .X")
        X = adata.X
        var_names = adata.var_names
    else:
        logger.info("No .raw layer — using .X")
        X = adata.X
        var_names = adata.var_names

    cell_barcodes = list(adata.obs_names)
    gene_names = list(var_names)

    is_sparse = sparse.issparse(X)

    if is_sparse and sparse.isspmatrix_csc(X):
        matrix = X.tocsr()
    else:
        matrix = X

    if not is_sparse:
        matrix = np.asarray(matrix, dtype=np.float32)

    shape = matrix.shape

    logger.info(
        "Loaded h5ad: shape=%s, sparse=%s, cells=%d, genes=%d",
        shape, is_sparse, len(cell_barcodes), len(gene_names),
    )

    return {
        "matrix": matrix,
        "cell_barcodes": cell_barcodes,
        "gene_names": gene_names,
        "is_sparse": is_sparse,
        "shape": shape,
    }
