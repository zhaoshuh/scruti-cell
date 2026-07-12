"""DNA methylation reader: converts .cov.gz files to AnnData.

Supported formats:
  - Bismark .cov.gz: chrom, start, end, pct_meth, n_meth, n_unmeth (no header)
  - Biscuit/snmC-seq .cov.gz: same + optional context column

Best practices:
  - Stores beta values (0-1) in X
  - Raw counts in layers['meth_counts'] and layers['total_counts']
  - var stores CpG annotations: chrom, start, end, context
  - For millions of CpG sites: stores as sparse CSR, one cell at a time
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

logger = logging.getLogger(__name__)

_BISMARK_COLS = ["chrom", "start", "end", "pct_meth", "n_meth", "n_unmeth"]
_BISCUIT_COLS = _BISMARK_COLS + ["context"]

# Maximum CpG sites to load in memory before switching to sparse
_MAX_DENSE_CPG = 100_000


def _detect_format(filepath: str) -> tuple[list[str], bool]:
    """Detect column format from first line of .cov.gz."""
    with gzip.open(filepath, "rt") as f:
        line = f.readline().strip()
    parts = line.split("\t")
    n_cols = len(parts)

    if n_cols == 6:
        return _BISMARK_COLS, False  # Bismark format, no context
    elif n_cols == 7:
        return _BISCUIT_COLS, True   # Biscuit format, has context
    elif n_cols >= 6:
        return _BISMARK_COLS, False  # Extra columns, use Bismark base
    else:
        # Fewer columns — try minimal
        return ["chrom", "start", "end", "pct_meth", "n_meth", "n_unmeth"][:n_cols], False


def read_methylation_cov(
    filepath: str,
    cell_barcode: str = "",
    sample_id: str = "",
) -> Any | None:
    """Read a single .cov.gz file and return an AnnData for one cell/sample.

    Args:
        filepath: Path to .cov.gz file
        cell_barcode: Cell barcode (optional)
        sample_id: Sample ID (optional)

    Returns:
        AnnData with:
          - X: (1, n_cpgs) beta values 0-1
          - layers['meth_counts']: methylated read counts
          - layers['total_counts']: total read counts (meth + unmeth)
          - var: CpG annotations (chrom, start, end, context)
    """
    try:
        import anndata as ad
    except ImportError:
        raise ImportError("anndata is required to read methylation data. pip install anndata")

    cols, has_context = _detect_format(filepath)

    df = pd.read_csv(
        filepath, sep="\t", names=cols,
        compression="gzip",
        dtype={"chrom": str, "start": int, "end": int},
    )

    n_cpgs = len(df)
    if n_cpgs == 0:
        return None

    # Build var (CpG annotations)
    var = pd.DataFrame(index=range(n_cpgs))
    var["chrom"] = df["chrom"].values
    var["start"] = df["start"].values
    var["end"] = df["end"].values
    var.index = df["chrom"] + ":" + df["start"].astype(str) + "-" + df["end"].astype(str)

    if has_context and "context" in df.columns:
        var["context"] = df["context"].values

    # Build X: beta values (methylation fraction)
    pct = df["pct_meth"].values.astype(np.float32) / 100.0
    X = pct.reshape(1, -1)

    # Build layers
    meth_counts = df["n_meth"].values.astype(np.float32)
    total_counts = (df["n_meth"].values + df["n_unmeth"].values).astype(np.float32)

    obs = pd.DataFrame(
        {"cell_barcode": [cell_barcode], "sample_id": [sample_id]},
        index=[cell_barcode or "sample_0"],
    )

    adata = ad.AnnData(
        X=X if n_cpgs <= _MAX_DENSE_CPG else sparse.csr_matrix(X),
        obs=obs,
        var=var,
        layers={
            "meth_counts": meth_counts.reshape(1, -1) if n_cpgs <= _MAX_DENSE_CPG
            else sparse.csr_matrix(meth_counts.reshape(1, -1)),
            "total_counts": total_counts.reshape(1, -1) if n_cpgs <= _MAX_DENSE_CPG
            else sparse.csr_matrix(total_counts.reshape(1, -1)),
        },
    )

    return adata


def merge_methylation_samples(adatas: list[Any]) -> Any | None:
    """Merge AnnData from multiple cells/samples into one.

    Uses anndata.concat with outer join on var (CpG sites).
    """
    if not adatas:
        return None
    if len(adatas) == 1:
        return adatas[0]

    try:
        import anndata as ad
    except ImportError:
        raise ImportError("anndata is required. pip install anndata")

    return ad.concat(adatas, axis=0, join="outer", merge="first")
