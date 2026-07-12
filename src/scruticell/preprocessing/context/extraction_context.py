"""Build compact extraction context for LLM metadata extraction.

Constructs a three-layer compact JSON from serialized SOFT GSM data
for token-efficient LLM processing:
  1. gse_global — GSE-level fields (raw text, no dedup)
  2. gse_protocols — protocol text deduped by distinct value
  3. per_gsm_text_fields — per-GSM text fields deduped by distinct value
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_TEXT_FIELDS = [
    "title",
    "description",
    "source_name_ch1",
    "characteristics_ch1",
]

_PROTOCOL_FIELDS = [
    "treatment_protocol_ch1",
    "extract_protocol_ch1",
]


def _serialize_soft_value(val: Any) -> str:
    """Normalize any SOFT value to a plain string."""
    if val is None:
        return ""
    if isinstance(val, (list, tuple)):
        parts = [_serialize_soft_value(v) for v in val]
        return "; ".join(p for p in parts if p)
    if isinstance(val, (int, float)):
        return str(val)
    return str(val).strip()


def _serialize_gsm_entries(raw_gsm: dict[str, dict]) -> dict[str, dict]:
    """Deep-serialize all GSM entry values to plain strings."""
    serialized: dict[str, dict] = {}
    for gsm_id, entry in raw_gsm.items():
        serialized[gsm_id] = {}
        for key, value in entry.items():
            serialized[gsm_id][key] = _serialize_soft_value(value)
    return serialized


def _build_field_mappings(
    gsm_entries: dict[str, dict],
    field_names: list[str],
) -> dict[str, list[dict]]:
    """Build compact value→GSM mappings for given field names.

    Returns {field_name: [{"value": str, "gsm_ids": [str]}]}.
    Only distinct values are included — same values merge GSM IDs.
    """
    result: dict[str, list[dict]] = {}
    for field in field_names:
        value_to_gsms: dict[str, list[str]] = {}
        for gsm_id, entry in gsm_entries.items():
            val = entry.get(field, "")
            if val:
                value_to_gsms.setdefault(val, []).append(gsm_id)

        mappings = [
            {"value": v, "gsm_ids": sorted(gsm_ids)}
            for v, gsm_ids in value_to_gsms.items()
        ]
        if mappings:
            result[field] = mappings

    return result


def build_extraction_context(
    raw_gsm: dict[str, dict],
    gse_info: dict[str, Any],
) -> dict:
    """Build compact three-layer extraction context for LLM.

    Args:
        raw_gsm: {GSM_ID: {field_name: raw_value, ...}} from SOFT parsing
        gse_info: GSE-level metadata dict with title, organism, type, summary, etc.

    Returns:
        {
            "gse_global": {title, organism, experiment_type, summary, overall_design},
            "gse_protocols": {field: [{"value": str, "gsm_ids": [str]}]},
            "per_gsm_text_fields": {field: [{"value": str, "gsm_ids": [str]}]},
        }
    """
    serialized = _serialize_gsm_entries(raw_gsm)

    gse_global = {
        "title": str(gse_info.get("title", "")),
        "organism": str(gse_info.get("organism", "")),
        "experiment_type": str(gse_info.get("type", gse_info.get("experiment_type", ""))),
        "summary": str(gse_info.get("summary", "")),
        "overall_design": str(gse_info.get("overall_design", "")),
    }

    gse_protocols = _build_field_mappings(serialized, _PROTOCOL_FIELDS)
    per_gsm_text_fields = _build_field_mappings(serialized, _TEXT_FIELDS)

    return {
        "gse_global": gse_global,
        "gse_protocols": gse_protocols,
        "per_gsm_text_fields": per_gsm_text_fields,
    }
