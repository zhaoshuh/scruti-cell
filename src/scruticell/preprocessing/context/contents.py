"""Extracts structured signals from file content inspection.

Detects feature ID types, barcode formats, and column patterns from
inspector file summaries. These are OBSERVATIONS, not classifications.
"""

from __future__ import annotations

import re
from typing import Any

from scruticell.preprocessing.models import ContentSignal

# Patterns for feature ID type detection
_GENE_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9\-]{1,14}$")
_ENSG_PATTERN = re.compile(r"^ENS[A-Z]*G\d{11}(\.\d+)?$", re.IGNORECASE)
_ENSMUSG_PATTERN = re.compile(r"^ENSMUSG\d{11}(\.\d+)?$", re.IGNORECASE)
_GENOMIC_INTERVAL_PATTERN = re.compile(r"^(chr)?\d{1,2}[XYM]?:\d+-\d+$", re.IGNORECASE)
_CPG_PATTERN = re.compile(r"^cg\d{8,10}$", re.IGNORECASE)
_PROTEIN_PATTERN = re.compile(r"^[A-Z]{2,8}(\d{1,3})?$")  # short uppercase, maybe with number
_SGRNA_PATTERN = re.compile(r"^sg", re.IGNORECASE)


def _detect_feature_id_type(samples: list[str], column_names: list[str]) -> str:
    """Detect the type of feature identifiers from sample values.

    Returns one of: "ensembl", "gene_symbol", "genomic_interval", "cpg",
    "protein", "sgrna", "numeric", "unknown"
    """
    if not samples:
        return "unknown"

    # Combine samples and column names for analysis
    all_text = samples + column_names
    total = len(samples)
    if total == 0:
        return "unknown"

    ensembl_count = sum(1 for s in samples if _ENSG_PATTERN.match(s) or _ENSMUSG_PATTERN.match(s))
    genomic_count = sum(1 for s in samples if _GENOMIC_INTERVAL_PATTERN.match(s))
    cpg_count = sum(1 for s in samples if _CPG_PATTERN.match(s))
    gene_symbol_count = sum(1 for s in samples if _GENE_SYMBOL_PATTERN.match(s))
    protein_count = sum(1 for s in samples if _PROTEIN_PATTERN.match(s))
    sgrna_count = sum(1 for s in samples if _SGRNA_PATTERN.match(s))

    # Determine dominant type (needs at least 30% to be considered dominant)
    threshold = max(1, total * 0.3)

    if ensembl_count >= threshold:
        return "ensembl"
    if genomic_count >= threshold:
        return "genomic_interval"
    if cpg_count >= threshold:
        return "cpg"
    if gene_symbol_count >= threshold:
        return "gene_symbol"
    if protein_count >= threshold:
        return "protein"
    if sgrna_count >= threshold:
        return "sgrna"

    return "unknown"


def _detect_column_patterns(columns: list[str]) -> list[str]:
    """Detect notable column patterns (e.g. 'barcode', 'umi', 'gene', 'peak')."""
    patterns: list[str] = []
    for col in columns:
        col_lower = col.lower()
        if "barcode" in col_lower:
            patterns.append(f"barcode_column:{col}")
        elif "umi" in col_lower:
            patterns.append(f"umi_column:{col}")
        elif "gene" in col_lower:
            patterns.append(f"gene_column:{col}")
        elif "peak" in col_lower:
            patterns.append(f"peak_column:{col}")
        elif "chr" in col_lower and ("start" in col_lower or "end" in col_lower):
            patterns.append(f"genomic_interval_column:{col}")
        elif "cpg" in col_lower:
            patterns.append(f"cpg_column:{col}")
        elif col_lower in ("cell_id", "cell", "cell_barcode"):
            patterns.append(f"cell_id_column:{col}")
    return patterns


def extract_content_signals(file_summary: dict[str, Any]) -> ContentSignal:
    """Extract content signals from a file inspector summary.

    Args:
        file_summary: Dict from Inspector.inspect_file / FileSummary.asdict()

    Returns:
        ContentSignal with feature type, barcode info, and column patterns
    """
    file_type = file_summary.get("file_type", "unknown")

    # Collect feature samples
    feature_samples: list[str] = []
    columns: list[str] = file_summary.get("columns", [])
    n_rows = file_summary.get("n_rows", 0)
    n_cols = file_summary.get("n_cols", 0)

    # For H5/H5AD files
    h5_shape = file_summary.get("h5_shape")
    if h5_shape:
        n_rows, n_cols = h5_shape[0], h5_shape[1]
        var_columns = file_summary.get("var_columns", [])
        if var_columns:
            columns = var_columns

    # For MTX
    mtx_shape = file_summary.get("mtx_shape")
    if mtx_shape:
        n_rows, n_cols = mtx_shape[0], mtx_shape[1]

    # For Loom
    loom_shape = file_summary.get("loom_shape")
    if loom_shape:
        n_rows, n_cols = loom_shape[0], loom_shape[1]
        loom_col_attrs = file_summary.get("loom_col_attrs", [])
        if loom_col_attrs:
            columns = loom_col_attrs

    # Extract preview_rows for feature type detection
    preview_rows = file_summary.get("preview_rows", [])
    for row in preview_rows[:30]:
        if isinstance(row, dict):
            first_val = next(iter(row.values()), None)
            if first_val:
                feature_samples.append(str(first_val))
        elif isinstance(row, list) and row:
            feature_samples.append(str(row[0]))

    # Also check for feature names in columns (from headerless CSVs the first col might be features)
    if not feature_samples and columns:
        # The first column might be the feature identifier column
        feature_samples = columns[:100]

    # Detect feature ID type
    feature_id_type = _detect_feature_id_type(feature_samples, columns)

    # Barcode signals
    barcode_format = file_summary.get("barcode_format", "")
    barcode_prefixes = file_summary.get("barcode_prefixes", [])

    # Column patterns
    column_patterns = _detect_column_patterns(columns)

    # Check sparsity
    is_sparse = file_summary.get("h5_sparse") or file_summary.get("mtx_is_sparse") or False

    return ContentSignal(
        feature_id_type=feature_id_type,
        feature_samples=feature_samples[:30],
        barcode_format=barcode_format,
        barcode_prefixes=barcode_prefixes[:30] if isinstance(barcode_prefixes, list) else [],
        column_patterns=column_patterns,
        n_rows=n_rows or 0,
        n_cols=n_cols or 0,
        is_sparse=is_sparse,
    )
