"""Deep file metadata extraction — builds FileMeta from filename + columns + values + barcode.

Extracts all embedded metadata signals from a file for attribution matching.
"""

from __future__ import annotations

import logging
from typing import Any

from scruticell.utils.paths import ancestry_filename
from scruticell.preprocessing.models import (
    ColumnSummary,
    FileMeta,
    RowIndexMeta,
)
from scruticell.preprocessing.context.semantic import (
    extract_column_meta,
    extract_filename_meta,
    infer_dtype_from_values,
    tokenize,
)

logger = logging.getLogger(__name__)

# Maximum columns to analyze per file
_MAX_COLUMNS = 50
# Unique value threshold for categorical detection
_CATEGORICAL_THRESHOLD = 30


def build_file_meta(
    ancestry: str,
    file_type: str,
    file_summary: dict[str, Any],
    companion_files: list[str],
) -> FileMeta:
    """Build a complete FileMeta portrait for a single file.

    Extracts metadata from three sources:
    1. File name — deep token parsing + semantic matching
    2. Column headers + values — per-column analysis with categorical detection
    3. Row index / barcodes — prefix detection + semantic matching

    Args:
        ancestry: Full decompression ancestry path
        file_type: File type from Inspector (csv/h5/mtx/...)
        file_summary: Dict from Inspector FileSummary
        companion_files: List of companion file names in same directory

    Returns:
        FileMeta with all extracted signals
    """
    raw_filename = ancestry_filename(ancestry) if "/" in ancestry or "\\" in ancestry else ancestry

    # ── Source 1: File name ──
    filename_meta = extract_filename_meta(raw_filename, ancestry)

    # ── Source 2: Columns ──
    columns_meta: list[ColumnSummary] = []
    columns_list = file_summary.get("columns", [])[:_MAX_COLUMNS]
    preview_rows = file_summary.get("preview_rows", [])

    for col_name in columns_list:
        col_name_str = str(col_name)
        header_meta = extract_column_meta(col_name_str)

        # Sample column values
        col_values: list[Any] = []
        for row in preview_rows[:100]:
            if isinstance(row, dict):
                if col_name_str in row:
                    col_values.append(row[col_name_str])
            elif isinstance(row, list):
                idx = columns_list.index(col_name) if col_name in columns_list else -1
                if idx >= 0 and idx < len(row):
                    col_values.append(row[idx])

        dtype = infer_dtype_from_values(col_values) if col_values else "str"

        # Categorical detection
        unique_vals = list(dict.fromkeys(str(v) for v in col_values))  # order-preserving dedup
        n_unique = len(unique_vals)
        is_cat = 1 < n_unique <= _CATEGORICAL_THRESHOLD

        # Value frequency
        freqs: dict[str, int] = {}
        for v in col_values:
            vs = str(v)
            freqs[vs] = freqs.get(vs, 0) + 1

        # Value semantic hints (for categorical columns)
        value_hints: list[dict] = []
        if is_cat:
            for val in unique_vals[:20]:
                val_meta = extract_column_meta(str(val))
                if val_meta.all_semantic_tokens():
                    value_hints.append({
                        "value": str(val),
                        "hints": val_meta.all_semantic_tokens(),
                        "sample_ids": val_meta.sample_ids,
                        "donor_ids": val_meta.donor_ids,
                    })

        columns_meta.append(ColumnSummary(
            header_meta=header_meta,
            unique_count=n_unique if is_cat else 1,
            unique_values=unique_vals[:20] if is_cat else [],
            value_frequencies=freqs,
            is_categorical=is_cat,
            value_hints=value_hints,
            dtype=dtype,
        ))

    # ── Source 3: Row index / barcodes ──
    row_index = RowIndexMeta(
        barcode_format=file_summary.get("barcode_format", ""),
        n_rows=(
            file_summary.get("n_rows", 0)
            or (file_summary.get("h5_shape", [0, 0])[0] if file_summary.get("h5_shape") else 0)
            or (file_summary.get("mtx_shape", [0, 0])[0] if file_summary.get("mtx_shape") else 0)
            or 0
        ),
    )

    # Barcode prefix analysis
    prefixes = file_summary.get("barcode_prefixes", [])
    prefix_counts = file_summary.get("barcode_prefix_counts", {})
    if prefixes:
        row_index.prefixes = prefixes[:10]
        row_index.prefix_counts = {p: prefix_counts.get(p, 0) for p in prefixes[:10]}
        row_index.prefix_meta = [extract_column_meta(p) for p in prefixes[:10]]

    # Feature type
    from scruticell.preprocessing.context.contents import extract_content_signals
    content_sig = extract_content_signals(file_summary)
    row_index.feature_id_type = content_sig.feature_id_type
    row_index.feature_samples = content_sig.feature_samples[:20]

    return FileMeta(
        ancestry=ancestry,
        file_type=file_type,
        filename=filename_meta,
        columns=columns_meta,
        row_index=row_index,
        companion_files=companion_files[:10],
    )
