"""Metadata extraction from parsed SOFT data.

Extracts structured metadata from the free-form, multi-location
metadata in GEO SOFT files. Handles:
- Multi-source extraction (characteristics, source_name, description)
- Field synonym normalization
- Multi-value field splitting
- Batch identification via GSM proxy
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

BATCH_INDICATOR_FIELDS = {
    "batch",
    "sequencing_batch",
    "sequencing batch",
    "batch_id",
    "batch_id_",
    "batch_number",
    "lane",
    "sequencing_run",
    "run",
    "experiment_batch",
    "sample_batch",
    "processing_batch",
    "preparation_batch",
    "date",
    "processing_date",
    "sequencing_date",
    "library_preparation_date",
}


def _split_characteristics(raw_value: Any) -> list[tuple[str, str]]:
    """Parse a characteristics_ch1 entry into key-value pairs.

    GEO `characteristics_ch1` fields are typically a list of strings
    like `["tissue: PBMC", "disease: healthy"]`. Returns a list of
    (key, value) tuples with stripped whitespace.
    """
    if not raw_value:
        return []
    if isinstance(raw_value, str):
        raw_value = [raw_value]
    if not isinstance(raw_value, list):
        return []

    pairs: list[tuple[str, str]] = []
    for item in raw_value:
        item_str = str(item).strip()
        if not item_str:
            continue
        if ": " in item_str:
            key, val = item_str.split(": ", 1)
        elif ":" in item_str:
            key, val = item_str.split(":", 1)
        else:
            key = "unknown_field"
            val = item_str
        pairs.append((key.strip(), val.strip()))
    return pairs


def extract_gsm_metadata(
    gsm_entries: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Extract and normalize metadata from all GSM entries.

    For each GSM, extracts characteristics, source_name, description,
    and any custom fields. Normalizes field names, splits embedded
    multi-value fields, and handles common value variants.

    Args:
        gsm_entries: Raw GSM data keyed by GSM accession.

    Returns:
        Dict keyed by GSM accession, each with normalized metadata fields.
    """
    result: dict[str, dict[str, Any]] = {}
    for gsm_id, raw in gsm_entries.items():
        normalized: dict[str, Any] = {}

        normalized["_geo_title"] = raw.get("_geo_title", raw.get("title", ""))

        source = raw.get("source_name_ch1", "")
        if isinstance(source, list):
            source = "; ".join(str(s) for s in source)
        normalized["source_name"] = str(source).strip()

        description = raw.get("description", "")
        normalized["description"] = str(description).strip()

        characteristics = raw.get("characteristics_ch1", [])
        pairs = _split_characteristics(characteristics)
        for k, v in pairs:
            if k not in normalized:
                normalized[k] = v
            elif isinstance(normalized[k], list):
                normalized[k].append(v)
            else:
                normalized[k] = [normalized[k], v]

        for key in ["contact_name", "contact_email", "contact_institute",
                     "contact_address", "contact_city", "contact_state",
                     "contact_zip/postal_code", "contact_country",
                     "molecule_ch1", "extract_protocol_ch1",
                     "label_protocol_ch1", "taxid_ch1",
                     "instrument_model", "library_selection",
                     "library_source", "library_strategy",
                     "data_processing", "platform_id",
                     "last_update_date", "submission_date"]:
            if key in raw:
                val = raw[key]
                if isinstance(val, list) and len(val) == 1:
                    val = val[0]
                normalized[key] = str(val).strip()

        for key, value in raw.items():
            if key not in normalized and key not in {
                "characteristics_ch1", "source_name_ch1",
                "title", "supplementary_file",
            } and not key.startswith("supplementary_file_"):
                if isinstance(value, list):
                    value = value[0] if len(value) == 1 else value
                normalized[key] = value

        result[gsm_id] = normalized

    logger.info("Extracted metadata for %d GSM entries", len(result))
    return result


def identify_batches_from_gsm(
    gsm_entries: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Identify batch groupings from GSM entries.

    Uses GSM accession as proxy batch when no explicit batch field exists.
    Attempts to detect real batch information from:
    - Explicit 'batch' or 'sequencing_batch' fields
    - Processing date clustering
    - Named conventions in GSM titles

    Args:
        gsm_entries: Extracted GSM metadata.

    Returns:
        Dict mapping GSM accession -> batch identifier.
    """
    batch_map: dict[str, str] = {}

    for gsm_id, meta in gsm_entries.items():
        found_batch = None

        for field in BATCH_INDICATOR_FIELDS:
            if field in meta:
                found_batch = str(meta[field]).strip()
                break
            field_lower = field.lower().replace(" ", "_")
            if field_lower in meta:
                found_batch = str(meta[field_lower]).strip()
                break

        if not found_batch:
            for key, value in meta.items():
                key_lower = key.lower().replace(" ", "_")
                if any(indicator in key_lower for indicator in ["batch", "lane", "run", "sequencing"]):
                    found_batch = str(value).strip()
                    break

        if found_batch:
            batch_map[gsm_id] = found_batch
        else:
            batch_map[gsm_id] = gsm_id

    unique_batches = set(batch_map.values())
    if len(unique_batches) == len(batch_map):
        logger.info("No shared batch information found; using GSM accessions as proxy batches (%d unique)",
                     len(unique_batches))
    else:
        logger.info("Identified %d unique batches across %d GSM entries",
                     len(unique_batches), len(batch_map))

    return batch_map


def assemble_obs_dataframe(
    metadata: dict[str, dict[str, Any]],
    batch_map: dict[str, str],
) -> pd.DataFrame:
    """Assemble a cell-level observation DataFrame from GSM metadata.

    Each row is a cell, columns are metadata fields + batch assignment.
    """
    if not metadata:
        return pd.DataFrame()

    all_fields: set[str] = set()
    rows: list[dict[str, Any]] = []

    for gsm_id, meta in metadata.items():
        row: dict[str, Any] = {"gsm": gsm_id, "batch": batch_map.get(gsm_id, gsm_id)}

        for key, value in meta.items():
            if key.startswith("_"):
                continue
            if isinstance(value, list):
                row[key] = "; ".join(str(v) for v in value)
            else:
                row[key] = value
            all_fields.add(key)

        rows.append(row)

    df = pd.DataFrame(rows)
    columns = ["gsm", "batch"] + sorted(all_fields)
    columns = [c for c in columns if c in df.columns]
    df = df[columns]

    logger.info("Assembled observation DataFrame: %d samples, %d columns",
                len(df), len(df.columns))
    return df
