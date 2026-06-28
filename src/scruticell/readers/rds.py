"""Read expression data from R RDS/RData files.

Uses pyreadr to read R serialized objects (Seurat, SingleCellExperiment, etc.).
Attempts to extract expression matrix, cell metadata, and gene names from
common single-cell R object conventions.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

logger = logging.getLogger(__name__)


def read_rds(filepath: str | Path) -> dict[str, Any]:
    """Read expression data from an R RDS or RData file.

    Attempts to extract from common single-cell R object conventions:
    - Seurat: @assays$RNA@counts or @assays$RNA@data
    - SingleCellExperiment: @assays$counts
    - A plain data.frame/matrix with genes as rows, cells as columns

    Args:
        filepath: Path to .rds or .RData file.

    Returns:
        Dict with keys: matrix, cell_barcodes, gene_names, is_sparse, shape.
    """
    import pyreadr

    filepath = Path(filepath)
    logger.info("Reading RDS file: %s", filepath)

    try:
        parsed = pyreadr.read_r(str(filepath))
    except Exception as exc:
        logger.error("Failed to read RDS file: %s", exc)
        return _empty_result()

    if not parsed:
        return _empty_result()

    matrix = None
    gene_names: list[str] = []
    cell_barcodes: list[str] = []

    for key, df in parsed.items():
        if df is None or isinstance(df, str):
            continue

        key_lower = key.lower()

        if not isinstance(df, pd.DataFrame):
            continue

        if df.shape[0] < 2 or df.shape[1] < 2:
            continue

        if "count" in key_lower or "expr" in key_lower or "data" in key_lower:
            if _is_expression_matrix(df.values):
                matrix = df.values.astype(np.float32)
                gene_names = list(df.index.astype(str))
                cell_barcodes = list(df.columns.astype(str))
                logger.info("Found expression matrix in RDS key '%s'", key)
                break

    if matrix is None:
        for key, df in parsed.items():
            if isinstance(df, pd.DataFrame) and df.shape[0] >= 50 and df.shape[1] >= 2:
                if _is_expression_matrix(df.values):
                    matrix = df.values.astype(np.float32)
                    gene_names = list(df.index.astype(str))
                    cell_barcodes = list(df.columns.astype(str))
                    logger.info("Found expression matrix in RDS key '%s' (fallback)", key)
                    break

    if matrix is None:
        for _, df in parsed.items():
            if isinstance(df, pd.DataFrame) and df.shape[0] >= 5 and df.shape[1] >= 2:
                matrix = df.values.astype(np.float32)
                gene_names = list(df.index.astype(str))
                cell_barcodes = list(df.columns.astype(str))
                logger.info("Using first available DataFrame as matrix")
                break

    if matrix is None:
        return _empty_result()

    is_sparse = False
    shape = matrix.shape

    # Standardize to cell×gene (AnnData convention) — R matrices are gene×cell
    if shape[0] == len(gene_names) and shape[1] == len(cell_barcodes):
        matrix = matrix.T
        shape = matrix.shape

    logger.info(
        "Loaded RDS: shape=%s, cells=%d, genes=%d",
        shape, len(cell_barcodes), len(gene_names),
    )

    return {
        "matrix": matrix,
        "cell_barcodes": cell_barcodes,
        "gene_names": gene_names,
        "is_sparse": is_sparse,
        "shape": shape,
    }


def _is_expression_matrix(arr: np.ndarray) -> bool:
    """Heuristic: check if a numpy array looks like an expression matrix."""
    if arr.size == 0:
        return False
    arr = np.asarray(arr, dtype=np.float64)
    if np.all(arr == 0):
        return True
    if np.any(np.isnan(arr)):
        return False
    n_negative = np.sum(arr < 0)
    if n_negative > arr.size * 0.3:
        return False
    return True


def _empty_result() -> dict[str, Any]:
    return {
        "matrix": np.array([], dtype=np.float32),
        "cell_barcodes": [],
        "gene_names": [],
        "is_sparse": False,
        "shape": (0, 0),
    }
