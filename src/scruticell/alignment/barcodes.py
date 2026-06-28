"""Cell barcode alignment between expression matrix and metadata."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 10X barcode pattern: 16bp sequence optionally followed by -1, -2, etc.
BC_10X_PATTERN = re.compile(r"^[ACGTacgt]{16}(-\d)?$")


def detect_barcode_format(barcodes: list[str]) -> str:
    """Detect the barcode format from a sample of barcodes.

    Returns one of:
        "10x_v2", "10x_v3", "10x_generic", "smartseq", "unknown"
    """
    if not barcodes:
        return "unknown"

    sample = barcodes[:min(100, len(barcodes))]
    tenx_match = sum(1 for b in sample if BC_10X_PATTERN.match(str(b)))

    if tenx_match > len(sample) * 0.7:
        # Check for -1 suffix pattern
        suffix_count = sum(1 for b in sample if str(b).endswith("-1"))
        if suffix_count > len(sample) * 0.5:
            return "10x_v3"  # v3 chemistry uses -1 suffix
        return "10x_v2"

    # Check for common non-10X patterns
    if all(str(b).startswith("SMART") for b in sample[:5]):
        return "smartseq"

    return "unknown"


def strip_barcode_suffix(barcode: str) -> str:
    """Remove common barcode suffixes (e.g., '-1' in 10X data)."""
    barcode = str(barcode).strip()
    if "-" in barcode and BC_10X_PATTERN.match(barcode):
        return barcode.rsplit("-", 1)[0]
    return barcode


def normalize_barcodes(
    barcodes: list[str], fmt: str = ""
) -> list[str]:
    """Normalize barcodes to a consistent format.

    Options:
    - Strip -1/-2 suffixes when detected
    - Convert to uppercase
    - Trim whitespace/quotes
    """
    normalized = []
    for b in barcodes:
        b = str(b).strip().strip("'\"")
        b = b.upper()
        if fmt.startswith("10x"):
            b = strip_barcode_suffix(b)
        normalized.append(b)
    return normalized


def align_barcodes_to_metadata(
    matrix_barcodes: list[str],
    metadata_index: list[str],
) -> tuple[list[str], list[str], float]:
    """Align matrix column barcodes with metadata row index.

    Handles prefix/suffix mismatches, ordering differences, and
    subset relationships (metadata may have extra cells or vice versa).

    Returns:
        Tuple of (aligned_matrix_barcodes, aligned_metadata_index, match_rate).
    """
    if not matrix_barcodes or not metadata_index:
        return list(matrix_barcodes), list(metadata_index), 1.0

    matrix_set = set(matrix_barcodes)
    meta_set = set(metadata_index)

    intersection = matrix_set & meta_set
    if len(intersection) == len(matrix_set) == len(meta_set):
        return list(matrix_barcodes), list(metadata_index), 1.0

    matrix_upper = {b.upper() for b in matrix_barcodes}
    meta_upper = {m.upper() for m in metadata_index}
    upper_intersection = matrix_upper & meta_upper

    match_rate = len(upper_intersection) / max(len(matrix_upper), len(meta_upper), 1)

    if match_rate > 0.8:
        upper_to_orig = {b.upper(): b for b in matrix_barcodes}
        aligned = [upper_to_orig.get(m.upper(), m) for m in metadata_index]
        return aligned, list(metadata_index), match_rate

    if len(matrix_barcodes) == len(metadata_index):
        return list(metadata_index), list(metadata_index), 1.0

    return list(matrix_barcodes), list(metadata_index), match_rate
