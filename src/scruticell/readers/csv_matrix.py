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

            try:
                data_rows.append([float(v) for v in values])
            except ValueError:
                # First column might be gene names without an empty header cell.
                # Re-read with index_col=0.
                fh.seek(0) if hasattr(fh, "seek") else None
                return _load_dense_matrix_fallback(filepath, delimiter, orientation)

    matrix = np.array(data_rows, dtype=np.float32)

    if orientation == "gene_x_cell":
        gene_names = row_names if has_row_names else [f"gene_{i}" for i in range(matrix.shape[0])]
        cell_barcodes = col_headers
    else:
        cell_barcodes = (row_names if has_row_names
                         else [f"cell_{i}" for i in range(matrix.shape[0])])
        gene_names = col_headers

    return matrix, gene_names, cell_barcodes


def _load_dense_matrix_fallback(
    filepath: Path, delimiter: str, orientation: str,
) -> tuple[np.ndarray, list[str], list[str]]:
    """Fallback: read with first column as row names."""
    opener = gzip.open if filepath.suffix == ".gz" else open
    with opener(filepath, "rt") as fh:
        reader = csv.reader(fh, delimiter=delimiter)
        header = next(reader)
        col_headers = header[1:] if len(header) > 1 else header
        row_names = []
        data_rows = []
        for row in reader:
            if not row:
                continue
            if len(row) > 1:
                row_names.append(row[0])
                try:
                    data_rows.append([float(v) for v in row[1:]])
                except ValueError:
                    continue
            else:
                try:
                    data_rows.append([float(v) for v in row])
                except ValueError:
                    continue

    matrix = np.array(data_rows, dtype=np.float32)

    if orientation == "gene_x_cell":
        gene_names = row_names if row_names else [f"gene_{i}" for i in range(matrix.shape[0])]
        cell_barcodes = col_headers
    else:
        cell_barcodes = row_names if row_names else [f"cell_{i}" for i in range(matrix.shape[0])]
        gene_names = col_headers

    logger.info("Fallback mode: loaded with index_col=0 interpretation")
    return matrix, gene_names, cell_barcodes


def _is_numeric(series) -> bool:
    """Column is (mostly) numeric — coerces object columns that are actually
    numeric (GEO expression matrices often carry a few stray non-numeric
    values that force pandas to infer ``object`` dtype)."""
    import pandas as pd

    try:
        if pd.api.types.is_numeric_dtype(series):
            return float(series.notna().mean()) > 0.5
        coerced = pd.to_numeric(series, errors="coerce")
        return float(coerced.notna().mean()) > 0.5
    except Exception:  # noqa: BLE001
        return False


def _pick_gene_col(non_numeric: list, df) -> str | None:
    """Best gene-id annotation column among the non-numeric columns."""
    for c in non_numeric:
        if any(k in str(c).lower() for k in ("geneid", "gene_id", "gene symbol",
                                             "gene_name", "symbol", "ensembl")):
            return c
    for c in non_numeric:
        vals = df[c].astype(str).head(200)
        if sum(1 for v in vals if _looks_like_gene_name(v)) / max(1, len(vals)) >= 0.3:
            return c
    return None


def _num_frame(df, cols: list[str]):
    """Float DataFrame for the given columns, coercing stray non-numeric values."""
    import pandas as pd

    return pd.DataFrame({c: pd.to_numeric(df[c], errors="coerce").fillna(0.0)
                         for c in cols})


def _empty_result() -> dict[str, Any]:
    return {"matrix": None, "cell_barcodes": [], "gene_names": [],
            "is_sparse": False, "shape": (0, 0)}


def read_csv_matrix(filepath: str | Path) -> dict[str, Any]:
    """Read an expression matrix from a CSV/TSV file.

    Robustly handles the formats GEO/expression studies actually use:

    - plain ``cell × gene`` matrices (header = gene ids, rows = cells);
    - ``gene × sample`` (bulk) matrices with one or more leading annotation
      columns (e.g. ``UniqueID,GeneID,<samples...>`` or Cufflinks FPKM);
    - tab/comma/semicolon delimiters, optional ``.gz``.

    Always returns the matrix in the AnnData orientation (obs × var): for bulk
    input the ``gene × sample`` matrix is transposed so observations = samples.
    """
    import pandas as pd

    filepath = Path(filepath)
    logger.info("Reading CSV matrix: %s", filepath)
    delimiter = _detect_delimiter(filepath)
    logger.debug("Detected delimiter: %r", delimiter)

    try:
        df = pd.read_csv(filepath, sep=delimiter, header=0, index_col=False,
                         low_memory=False, on_bad_lines="skip")
    except Exception as exc:  # noqa: BLE001
        logger.warning("pandas read failed %s: %s", filepath, exc)
        return _empty_result()
    if df is None or df.shape[1] == 0:
        return _empty_result()
    df = df.dropna(axis=1, how="all")

    numeric_cols = [str(c) for c in df.columns if _is_numeric(df[c])]
    if not numeric_cols:
        return _empty_result()
    non_numeric = [str(c) for c in df.columns if c not in numeric_cols]
    gene_col = _pick_gene_col(non_numeric, df)

    n_rows = int(df.shape[0])
    header_gene_like = sum(
        1 for c in numeric_cols[:8] if _looks_like_gene_name(str(c))) / \
        max(1, len(numeric_cols[:8]))

    if gene_col is not None and len(numeric_cols) >= 1:
        # gene × sample (bulk) — observations = sample columns
        matrix = _num_frame(df, numeric_cols).to_numpy(dtype=np.float32).T  # samples × genes
        gene_names = df[gene_col].astype(str).tolist()
        cell_barcodes = list(numeric_cols)
    elif header_gene_like >= 0.5:
        # cell × gene — observations = rows
        matrix = _num_frame(df, numeric_cols).to_numpy(dtype=np.float32)
        gene_names = list(numeric_cols)
        cell_barcodes = (df[non_numeric[0]].astype(str).tolist()
                         if non_numeric else df.index.astype(str).tolist())
    else:
        # ambiguous → pick by shape: more numeric columns → bulk (genes×samples)
        if len(numeric_cols) > n_rows:
            matrix = _num_frame(df, numeric_cols).to_numpy(dtype=np.float32).T
            gene_names = (df[non_numeric[0]].astype(str).tolist()
                          if non_numeric else df.index.astype(str).tolist())
            cell_barcodes = list(numeric_cols)
        else:
            matrix = _num_frame(df, numeric_cols).to_numpy(dtype=np.float32)
            gene_names = list(numeric_cols)
            cell_barcodes = (df[non_numeric[0]].astype(str).tolist()
                             if non_numeric else df.index.astype(str).tolist())

    if matrix is None or matrix.ndim != 2:
        return _empty_result()
    shape = matrix.shape
    is_sparse = False
    logger.info("Loaded CSV matrix: shape=%s, cells=%d, genes=%d",
                shape, len(cell_barcodes), len(gene_names))
    return {"matrix": matrix, "cell_barcodes": cell_barcodes,
            "gene_names": gene_names, "is_sparse": is_sparse, "shape": shape}
