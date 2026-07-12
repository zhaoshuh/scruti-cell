"""Unified token→semantic matching engine.

Shared by FileNameMeta, ColumnMeta, and barcode prefix parsing.
All three extraction paths use the same matching rules.
"""

from __future__ import annotations

import re
from typing import Any

# ─── Token patterns (ordered by specificity) ───

_PATTERNS: list[tuple[str, str, str]] = [
    (r"^GSM\d+$", "gsm_id", "gsm_ids"),
    (r"^[Ss]ample[_\s]?\d+$", "sample_id", "sample_ids"),
    (r"^[Pp]atient[_\s]?\w+$", "donor_id", "donor_ids"),
    (r"^[Dd]onor[_\s]?\w+$", "donor_id", "donor_ids"),
    (r"^[Pp]\d+$", "sample_id", "sample_ids"),
    (r"^[Tt]reated$", "treatment", "treatments"),
    (r"^[Uu]ntreated$", "treatment", "treatments"),
    (r"^[Cc]ontrol$", "condition", "conditions"),
    (r"^[Ww][Tt]$", "condition", "conditions"),
    (r"^[Kk][Oo]$", "condition", "conditions"),
    (r"^[Tt]g$", "condition", "conditions"),
    (r"^[Kk]D$", "condition", "conditions"),
    (r"^[Dd]ay\d+$", "timepoint", "timepoints"),
    (r"^[Ww]eek\d+$", "timepoint", "timepoints"),
    (r"^[Hh]our\d+$", "timepoint", "timepoints"),
    (r"^[Dd]\d+$", "timepoint", "timepoints"),
    (r"^[Rr]ep\d+$", "replicate", "replicates"),
    (r"^[Rr]\d+$", "replicate", "replicates"),
    (r"^[Ll]iver$", "tissue", "tissues"),
    (r"^[Bb]rain$", "tissue", "tissues"),
    (r"^[Ll]ung$", "tissue", "tissues"),
    (r"^[Kk]idney$", "tissue", "tissues"),
    (r"^[Hh]eart$", "tissue", "tissues"),
    (r"^[Ss]pleen$", "tissue", "tissues"),
    (r"^[Pp]bmc$", "tissue", "tissues"),
    (r"^[Bb]one$", "tissue", "tissues"),
    (r"^[Ss]kin$", "tissue", "tissues"),
    (r"^[Pp]ancreas$", "tissue", "tissues"),
    (r"^[Cc]olon$", "tissue", "tissues"),
    (r"^[Mm]ale$", "tissue", "tissues"),
    (r"^[Ff]emale$", "tissue", "tissues"),
    (r"^[Ww]hole$", "tissue", "tissues"),
    (r"^[Cc]ortex$", "tissue", "tissues"),
    (r"^[Nn]euron$", "cell_type", "tissues"),
    (r"^[Aa]strocyte$", "cell_type", "tissues"),
    (r"^[Mm]icroglia$", "cell_type", "tissues"),
    (r"^[Oo]ligodendrocyte$", "cell_type", "tissues"),
    (r"^[Tt][-_]?[Cc]ell$", "cell_type", "tissues"),
    (r"^[Bb][-_]?[Cc]ell$", "cell_type", "tissues"),
    (r"^[Mm]acrophage$", "cell_type", "tissues"),
    (r"^[Ee]ndothelial$", "cell_type", "tissues"),
    (r"^[Ff]ibroblast$", "cell_type", "tissues"),
    (r"^[Ee]pithelial$", "cell_type", "tissues"),
]

# ─── Non-bio stopwords ───

_NON_BIO_TOKENS = {
    "counts", "matrix", "data", "filtered", "raw", "processed",
    "normalized", "output", "results", "barcodes", "features",
    "genes", "gene", "expression", "sample", "samples", "cells",
    "file", "files", "combined", "merged", "all",
    "10x", "v2", "v3", "v1", "chromium", "single", "cell",
    "rna", "seq", "scrna", "snrna", "atac", "multiome",
    "csv", "tsv", "mtx", "h5ad", "h5", "rds", "loom",
    "gz", "tar", "zip", "batch", "run", "lane",
    "library", "index", "read", "reads",
}


# ─── Tokenizer ───

def tokenize(name: str) -> list[str]:
    """Split a name string into semantic tokens."""
    # Replace common delimiters with spaces, then split
    cleaned = re.sub(r"[_\-.]+", " ", name)
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", cleaned)
    return [t.strip() for t in cleaned.split() if t.strip()]


# ─── Main matching function ───

def match_token(token: str) -> tuple[str, str] | None:
    """Match a single token to a semantic category.

    Returns (category, annotated_value) or None.
    """
    for pattern, category, annotate_key in _PATTERNS:
        if re.match(pattern, token):
            return (category, token)
    return None


def extract_column_meta(raw_name: str) -> ColumnMeta:
    """Extract ColumnMeta from a raw name string (column header, barcode prefix, or unique value).

    Used by:
      - FileNameMeta extraction
      - Column header analysis
      - Barcode prefix analysis
      - Unique value analysis
    """
    from scruticell.preprocessing.models import ColumnMeta

    tokens = tokenize(raw_name)

    meta = ColumnMeta(original=raw_name, tokens=tokens)

    for token in tokens:
        if token.lower() in _NON_BIO_TOKENS:
            continue
        match = match_token(token)
        if match:
            category, value = match
            target = getattr(meta, category.replace("gsm_id", "gsm_ids").rstrip("s") + "s", None)
            if target is not None:
                target.append(value)
        else:
            meta.unrecognized.append(token)

    return meta


def extract_filename_meta(raw_filename: str, ancestry: str = "") -> FileNameMeta:
    """Extract FileNameMeta from a raw filename string and optional full ancestry path."""
    from scruticell.preprocessing.models import FileNameMeta

    tokens = tokenize(raw_filename)
    meta = FileNameMeta(tokens=tokens, raw=raw_filename)

    for token in tokens:
        if token.lower() in _NON_BIO_TOKENS:
            continue
        match = match_token(token)
        if match:
            category, value = match
            if category == "gsm_id":
                meta.gsm_ids.append(value)
            elif category == "sample_id":
                meta.sample_ids.append(value)
            elif category == "donor_id":
                meta.donor_ids.append(value)
            elif category == "treatment":
                meta.treatments.append(value)
            elif category == "timepoint":
                meta.timepoints.append(value)
            elif category == "condition":
                meta.conditions.append(value)
            elif category == "tissue":
                meta.tissues.append(value)
            elif category == "replicate":
                meta.replicates.append(value)
        else:
            meta.unrecognized.append(token)

    # Direct GSM regex on raw filename AND full ancestry path
    search_text = raw_filename
    if ancestry:
        search_text = ancestry + " " + raw_filename
    for m in re.finditer(r"GSM\d+", search_text):
        if m.group() not in meta.gsm_ids:
            meta.gsm_ids.append(m.group())

    # Also extract from parent directory tokens in ancestry
    if ancestry:
        ancestry_tokens = tokenize(ancestry.replace("/", " ").replace("\\", " "))
        for token in ancestry_tokens:
            if token.lower() in _NON_BIO_TOKENS or token in meta.tokens:
                continue
            match = match_token(token)
            if match:
                category, value = match
                if category == "gsm_id" and value not in meta.gsm_ids:
                    meta.gsm_ids.append(value)
                elif category == "sample_id" and value not in meta.sample_ids:
                    meta.sample_ids.append(value)
                elif category == "donor_id" and value not in meta.donor_ids:
                    meta.donor_ids.append(value)
            elif token not in meta.unrecognized:
                meta.unrecognized.append(token)

    return meta


def infer_dtype_from_values(values: list) -> str:
    """Infer the dtype of a column from sampled values."""
    if not values:
        return "unknown"
    try:
        import numpy as np
        arr = np.array(values, dtype=float)
        if np.all(arr == arr.astype(int)):
            return "int64"
        return "float64"
    except (ValueError, TypeError):
        return "str"
