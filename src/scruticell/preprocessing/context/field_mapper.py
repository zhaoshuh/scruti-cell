"""L1+L7: Builds field→value→GSM deduplication maps from GSM metadata.

Converts per-GSM metadata redundancy into a compact representation.
L7: Auto-skips technical/protocol fields with long identical text.
"""

from __future__ import annotations

import re
from typing import Any

from scruticell.preprocessing.models import FieldValueMap, ValueGroup

# L7: Fields to skip (protocol/technical — long identical text across all GSM)
_SKIP_FIELD_PATTERNS = [
    re.compile(r".*protocol.*", re.IGNORECASE),
    re.compile(r".*construction.*", re.IGNORECASE),
    re.compile(r".*library_preparation.*", re.IGNORECASE),
    re.compile(r".*data_processing.*", re.IGNORECASE),
    re.compile(r".*extract_protocol.*", re.IGNORECASE),
    re.compile(r".*contact.*", re.IGNORECASE),
    re.compile(r".*email.*", re.IGNORECASE),
    re.compile(r".*lab.*", re.IGNORECASE),
    re.compile(r".*institute.*", re.IGNORECASE),
    re.compile(r".*address.*", re.IGNORECASE),
    re.compile(r".*supplementary_file.*", re.IGNORECASE),
    re.compile(r".*taxid.*", re.IGNORECASE),
    # Invariant fields (same value for ALL GSM — not useful for classification)
    re.compile(r"^type$", re.IGNORECASE),
    re.compile(r"^status$", re.IGNORECASE),
    re.compile(r"^channel_count$", re.IGNORECASE),
    re.compile(r"^data_row_count$", re.IGNORECASE),
    re.compile(r"^last_update_date$", re.IGNORECASE),
    re.compile(r"^submission_date$", re.IGNORECASE),
    # Per-GSM unique identifiers (not useful for grouping)
    re.compile(r"^_geo_title$", re.IGNORECASE),
    re.compile(r"^relation$", re.IGNORECASE),
    re.compile(r"^series_id$", re.IGNORECASE),
    re.compile(r"^library_id$", re.IGNORECASE),
    re.compile(r"^library_igf_id$", re.IGNORECASE),
    re.compile(r"^sample_id$", re.IGNORECASE),
    re.compile(r"^sample\s+id$", re.IGNORECASE),
    re.compile(r"^supplementary_file$", re.IGNORECASE),
    re.compile(r"^supplementary_file_\d+$", re.IGNORECASE),
    re.compile(r"^instrument_model$", re.IGNORECASE),
    re.compile(r"^platform_id$", re.IGNORECASE),
]

# L7: Global representative kept for protocol fields (one copy for all GSM)
_GLOBAL_REPRESENTATIVES: dict[str, str] = {}

# Fields with all-unique values that should be skipped from dedup
_UNIQUE_ONLY_FIELDS = {
    "title", "description", "geo_accession", "gsm_id", "sample_id",
    "patient_id", "donor_id", "barcode", "cell_barcode",
    "name", "relation", "series_id", "library_id", "library_igf_id",
    "supplementary_file", "supplementary_file_1", "supplementary_file_2",
    "supplementary_file_3", "supplementary_file_4", "supplementary_file_5",
}

CATEGORY_MAP = {
    "disease": "clinical",
    "disease_state": "clinical",
    "condition": "clinical",
    "phenotype": "clinical",
    "diagnosis": "clinical",
    "tissue": "sample",
    "organ": "sample",
    "cell_type": "sample",
    "sex": "clinical",
    "gender": "clinical",
    "age": "clinical",
    "development_stage": "clinical",
    "organism": "sample",
    "strain": "sample",
    "treatment": "experimental",
    "drug": "experimental",
    "genotype": "experimental",
    "time_point": "experimental",
    "batch": "technical",
    "sequencing_batch": "technical",
    "platform": "technical",
    "molecule": "technical",
    "library_strategy": "technical",
    "suspension_type": "technical",
}


def _should_skip_field(field_name: str) -> bool:
    """L7: Check if a field should be skipped (protocol/technical noise)."""
    normalized = field_name.replace(" ", "_").replace("-", "_")
    for pattern in _SKIP_FIELD_PATTERNS:
        if pattern.match(field_name) or pattern.match(normalized):
            return True
    return False


def _normalize_value(value: Any) -> str:
    """Normalize a field value to a canonical string."""
    if isinstance(value, list):
        return "; ".join(str(v) for v in value)
    return str(value).strip()


def _field_category(field_name: str) -> str:
    """Map a raw field name to a category."""
    name_lower = field_name.lower().replace("_", "").replace(" ", "")
    for key, cat in CATEGORY_MAP.items():
        if key.replace("_", "").replace(" ", "") in name_lower:
            return cat
    return "other"


def build_field_value_maps(
    gsm_metadata_map: dict[str, dict[str, Any]],
    gsm_count_threshold: int = 1,
) -> dict[str, FieldValueMap]:
    """Build deduplicated field→value→GSM mapping tables.

    Args:
        gsm_metadata_map: {GSM_ID: {field_name: value, ...}, ...}
        gsm_count_threshold: Only include fields with values appearing in
                            at least this many GSM (filters noise).

    Returns:
        Dict of field_name → FieldValueMap, sorted by field significance.
    """
    field_values: dict[str, dict[str, list[str]]] = {}

    for gsm_id, metadata in gsm_metadata_map.items():
        for raw_field, raw_value in metadata.items():
            name = raw_field.lower().strip()
            if _should_skip_field(name):
                _GLOBAL_REPRESENTATIVES[name] = _normalize_value(raw_value)
                continue
            if name in _UNIQUE_ONLY_FIELDS:
                continue

            normalized = _normalize_value(raw_value)
            if not normalized or normalized in ("na", "n/a", "unknown", ""):
                continue

            field_values.setdefault(name, {}).setdefault(normalized, []).append(gsm_id)

    result: dict[str, FieldValueMap] = {}
    for field_name, value_map in field_values.items():
        values = []
        for val, gsm_ids in sorted(value_map.items(), key=lambda x: -len(x[1])):
            if len(gsm_ids) >= gsm_count_threshold:
                values.append(ValueGroup(
                    value=val,
                    gsm_ids=gsm_ids[:20],  # Cap GSM list display at 20
                    gsm_count=len(gsm_ids),
                ))

        if values:
            result[field_name] = FieldValueMap(
                field_name=field_name,
                category=_field_category(field_name),
                values=values,
                distinct_count=len(values),
            )

    return dict(sorted(result.items(), key=lambda x: -x[1].distinct_count))
