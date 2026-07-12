"""ATAC-seq reader: loads peaks.bed and fragments.tsv.gz files.

Follows the community conventions for peak naming and fragment format.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_PEAK_BED_COLS = ["chrom", "start", "end"]
_PEAK_BED_COLS_EXTRA = _PEAK_BED_COLS + ["name", "score", "strand"]

_FRAGMENT_COLS = ["chrom", "start", "end", "cell_barcode", "count"]


def read_peaks(filepath: str) -> pd.DataFrame | None:
    """Read a peaks.bed file.

    Handles BED3 (chrom, start, end), BED6, and narrowPeak formats.
    Returns a DataFrame with at minimum chrom, start, end columns.

    Args:
        filepath: Path to .bed file

    Returns:
        DataFrame with chrom, start, end (+ extra columns if present)
    """
    p = Path(filepath)
    if not p.exists():
        return None

    # Detect header
    with open(p) as f:
        first = f.readline().strip()
    has_header = first.startswith("track") or first.startswith("#") or first.startswith("browser")

    # Count columns from first data line
    skip = 0
    with open(p) as f:
        for line in f:
            if line.startswith("track") or line.startswith("#") or line.startswith("browser"):
                skip += 1
            else:
                n_cols = len(line.strip().split("\t"))
                break

    # Choose column names based on column count
    if n_cols >= 10:
        cols = _PEAK_BED_COLS + ["name", "score", "strand", "signal", "pval", "qval", "peak"]
    elif n_cols >= 6:
        cols = _PEAK_BED_COLS + ["name", "score", "strand"][:n_cols - 3] + [f"extra_{i}" for i in range(max(0, n_cols - 6))]
    else:
        cols = _PEAK_BED_COLS[:n_cols]

    return pd.read_csv(p, sep="\t", names=cols, skiprows=skip)


def read_fragments(filepath: str, nrows: int | None = None) -> pd.DataFrame | None:
    """Read a fragments.tsv.gz file (10x ATAC format).

    Args:
        filepath: Path to .tsv.gz file
        nrows: Number of rows to read (None = all)

    Returns:
        DataFrame with columns: chrom, start, end, cell_barcode, count
        Or None if file doesn't exist.
    """
    p = Path(filepath)
    if not p.exists():
        return None

    return pd.read_csv(
        p, sep="\t", names=_FRAGMENT_COLS, header=None,
        compression="gzip" if filepath.endswith(".gz") else None,
        nrows=nrows,
    )


def build_peak_name(row: pd.Series) -> str:
    """Build standard peak name from chrom, start, end.

    Follows Signac/ArchR/SnapATAC convention: chr1:10000-10500
    """
    return f"{row['chrom']}:{int(row['start'])}-{int(row['end'])}"
