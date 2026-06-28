"""Read expression matrix from CSV/TSV format.

Handles both dense and compressed (.gz) formats.
Auto-detects delimiter and orientation (gene x cell vs cell x gene).
"""

from __future__ import annotations

import csv
import gzip
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DELIMITERS = {",": ",", "\t": "\t", ";": ";"}

_GENE_PATTERN_SUBSTRINGS = ("ENSG", "ENSMUSG", ".", "|", "ENSDARG", "WBGENE")
_GENE_SYMBOL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-\.]{1,14}$")


def _detect_delimiter(filepath: Path) -> str:
    """Detect the CSV delimiter by sampling the first few lines."""
    opener = gzip.open if filepath.suffix == ".gz" else open
    with opener(filepath, "rt") as fh:
        sample = "".join(fh.readline() for _ in range(5))

    counts: dict[str, int] = {}
    for name, delim in _DELIMITERS.items():
        counts[name] = sample.count(delim)

    best = max(counts, key=counts.get)
    return _DELIMITERS.get(best, ",")


def _looks_like_gene_name(value: str) -> bool:
    """Heuristic: does this string look like a gene identifier?"""
    upper = value.upper()
    for pattern in _GENE_PATTERN_SUBSTRINGS:
        if pattern in upper:
            return True
    if _GENE_SYMBOL_RE.match(value.strip()):
        return True
    return False


def _detect_orientation(filepath: Path, delimiter: str, has_row_names: bool) -> str:
    """Detect matrix orientation: 'gene_x_cell' or 'cell_x_gene'."""
    opener = gzip.open if filepath.suffix == ".gz" else open
    with opener(filepath, "rt") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        first_row = next(reader)

        row_name_samples: list[str] = []
        if has_row_names:
            for _ in range(5):
                try:
                    r = next(reader)
                except StopIteration:
                    break
                if r:
                    row_name_samples.append(r[0])

    if not first_row:
        return "cell_x_gene"

    header_start = 1 if has_row_names else 0
    gene_like_count = sum(
        1 for val in first_row[header_start:header_start + 5]
        if _looks_like_gene_name(val)
    )
    if gene_like_count >= 2:
        if has_row_names:
            return "gene_x_cell"
        return "cell_x_gene"

    row_gene_like = sum(
        1 for val in row_name_samples if _looks_like_gene_name(val)
    )
    if row_gene_like >= 2:
        return "gene_x_cell"

    return "cell_x_gene"


def _has_row_names(filepath: Path, delimiter: str) -> bool:
    """Check if the first column header is empty (indicating row names)."""
    opener = gzip.open if filepath.suffix == ".gz" else open
    with opener(filepath, "rt") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        first_row = next(reader)
        return first_row[0].strip() == ""


def _load_dense_matrix(
    filepath: Path, delimiter: str, has_row_names: bool, orientation: str
) -> tuple[np.ndarray, list[str], list[str]]:
    """Load a dense matrix from CSV, returning (matrix, gene_names, cell_barcodes)."""
    opener = gzip.open if filepath.suffix == ".gz" else open
    with opener(filepath, "rt") as fh:
        reader = csv.reader(fh, delimiter=delimiter)

        header = next(reader)
        if has_row_names:
            col_headers = header[1:]
        else:
            col_headers = header

        row_names = []
        data_rows = []
        for row in reader:
            if not row:
                continue
            if has_row_names:
                row_names.append(row[0])
                values = row[1:]
            else:
                values = row

            data_rows.append([float(v) for v in values])

    matrix = np.array(data_rows, dtype=np.float32)

    if orientation == "gene_x_cell":
        gene_names = row_names if has_row_names else [f"gene_{i}" for i in range(matrix.shape[0])]
        cell_barcodes = col_headers
    else:
        cell_barcodes = row_names if has_row_names else [f"cell_{i}" for i in range(matrix.shape[0])]
        gene_names = col_headers

    return matrix, gene_names, cell_barcodes


def read_csv_matrix(filepath: str | Path) -> dict[str, Any]:
    """Read an expression matrix from a CSV/TSV file.

    Auto-detects:
    - Delimiter (comma, tab, semicolon)
    - Whether the first column contains row names (gene/cell identifiers)
    - Matrix orientation (gene x cell vs cell x gene)

    Args:
        filepath: Path to .csv, .tsv, .csv.gz, or .tsv.gz file.

    Returns:
        Dict with keys: matrix (np.ndarray), cell_barcodes (list[str]),
        gene_names (list[str]), is_sparse (bool), shape (tuple).
    """
    filepath = Path(filepath)
    logger.info("Reading CSV matrix: %s", filepath)

    delimiter = _detect_delimiter(filepath)
    logger.debug("Detected delimiter: %r", delimiter)

    has_row_names = _has_row_names(filepath, delimiter)
    logger.debug("Has row names column: %s", has_row_names)

    orientation = _detect_orientation(filepath, delimiter, has_row_names)
    logger.debug("Detected orientation: %s", orientation)

    matrix, gene_names, cell_barcodes = _load_dense_matrix(
        filepath, delimiter, has_row_names, orientation
    )

    # Standardize to cell×gene (AnnData convention)
    if orientation == "gene_x_cell":
        matrix = matrix.T

    shape = matrix.shape
    is_sparse = False

    logger.info(
        "Loaded CSV matrix: shape=%s, cells=%d, genes=%d",
        shape, len(cell_barcodes), len(gene_names),
    )

    return {
        "matrix": matrix,
        "cell_barcodes": cell_barcodes,
        "gene_names": gene_names,
        "is_sparse": is_sparse,
        "shape": shape,
    }
