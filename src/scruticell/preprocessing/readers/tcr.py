"""TCR/BCR immune repertoire reader: loads 10x VDJ filtered_contig_annotations.csv.

Follows AIRR standard conventions as used by scirpy.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Key AIRR columns from 10x VDJ filtered_contig_annotations.csv
_VALID_COLS = {
    "barcode", "is_cell", "contig_id", "high_confidence", "length",
    "chain", "v_gene", "d_gene", "j_gene", "c_gene",
    "full_length", "productive", "cdr3", "cdr3_nt",
    "reads", "umis", "raw_clonotype_id", "raw_consensus_id",
}


def read_contig_annotations(filepath: str) -> pd.DataFrame | None:
    """Read filtered_contig_annotations.csv from 10x VDJ output.

    Args:
        filepath: Path to filtered_contig_annotations.csv

    Returns:
        DataFrame with AIRR columns. Returns None if file doesn't exist.
    """
    p = Path(filepath)
    if not p.exists():
        return None

    df = pd.read_csv(p)

    # Handle common 10x barcode convention: strip -1 suffix
    if "barcode" in df.columns:
        df["barcode"] = df["barcode"].astype(str).str.replace(r"-\d+$", "", regex=True)

    return df


def read_clonotypes(filepath: str) -> pd.DataFrame | None:
    """Read clonotypes.csv from 10x VDJ output.

    Args:
        filepath: Path to clonotypes.csv

    Returns:
        DataFrame with clonotype_id, frequency, proportion, cdr3s_aa, etc.
        Returns None if file doesn't exist.
    """
    p = Path(filepath)
    if not p.exists():
        return None

    return pd.read_csv(p)


def detect_chain_type(contigs: pd.DataFrame) -> str:
    """Detect whether this is TCR or BCR from chain types.

    Args:
        contigs: DataFrame from read_contig_annotations

    Returns:
        "TCR" if TRA/TRB/TRD/TRG found, "BCR" if IGH/IGK/IGL found,
        "unknown" otherwise.
    """
    if "chain" not in contigs.columns:
        return "unknown"

    chains = set(contigs["chain"].dropna().unique())
    tcr_chains = {"TRA", "TRB", "TRD", "TRG"}
    bcr_chains = {"IGH", "IGK", "IGL"}

    if chains & tcr_chains:
        return "TCR"
    if chains & bcr_chains:
        return "BCR"
    return "unknown"


def get_productive_cells(contigs: pd.DataFrame) -> pd.DataFrame:
    """Filter to productive contigs from cells.

    Returns only rows where is_cell=True and productive=True.
    """
    df = contigs.copy()
    if "is_cell" in df.columns:
        df = df[df["is_cell"] == True]  # noqa: E712
    if "productive" in df.columns:
        df = df[df["productive"] == True]  # noqa: E712
    return df
