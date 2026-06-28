"""Parse node: extracts metadata from SOFT files."""

from __future__ import annotations

import logging
from typing import Any

from scruticell.parsing.field_resolver import FieldResolver
from scruticell.parsing.metadata import extract_gsm_metadata, identify_batches_from_gsm
from scruticell.parsing.soft import parse_soft_file
from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


def _detect_organism(gsm_entries: dict[str, dict[str, Any]], gse_metadata: dict[str, Any]) -> str:
    """Detect organism from GSM metadata or GSE-level data."""
    for gsm_data in gsm_entries.values():
        for field in ["taxid_ch1", "organism_ch1", "organism"]:
            if field in gsm_data:
                val = str(gsm_data[field]).strip()
                if val:
                    return val

    for key in gse_metadata:
        if "organism" in key.lower():
            val = gse_metadata[key]
            if isinstance(val, list):
                val = "; ".join(str(v) for v in val)
            val = str(val).strip()
            if val:
                return val

    return ""


def _detect_platform_ids(gpl_entries: list[dict[str, Any]]) -> list[str]:
    """Extract platform accessions from GPL entries."""
    return [gpl.get("accession", "") for gpl in gpl_entries if gpl.get("accession")]


def parse_soft_node(state: PipelineState) -> dict[str, Any]:
    """Parse the GEO SOFT file to extract metadata.

    Reads the SOFT family file, extracting:
    - Series-level metadata (title, summary, overall design, pubmed_id)
    - Platform-level metadata (GPL annotation)
    - Sample-level metadata (characteristics, source, description per GSM)
    - Field synonym normalization via knowledge graph lookup

    Args:
        state: Pipeline state with soft_file set.

    Returns:
        Partial state update with raw_metadata, gsm_entries,
        parsed_characteristics, organism, platform_ids, pubmed_id.
    """
    logger.info("Parsing SOFT file for %s", state.gse_id)

    if not state.soft_file:
        logger.error("No SOFT file available for %s", state.gse_id)
        return {
            "raw_metadata": {},
            "gsm_entries": {},
            "parsed_characteristics": {},
            "organism": "",
            "platform_ids": [],
            "pubmed_id": "",
            "errors": ["No SOFT file available"],
        }

    try:
        parsed = parse_soft_file(state.soft_file)
    except Exception as e:
        logger.exception("Failed to parse SOFT file %s", state.soft_file)
        return {
            "raw_metadata": {},
            "gsm_entries": {},
            "parsed_characteristics": {},
            "organism": "",
            "platform_ids": [],
            "pubmed_id": "",
            "errors": [f"Failed to parse SOFT file: {e}"],
        }

    gse_metadata = parsed.get("gse_metadata", {})
    gpl_entries = parsed.get("gpl_entries", [])
    raw_gsm = parsed.get("gsm_entries", {})

    pubmed_id = str(gse_metadata.get("pubmed_id", "")).strip()

    gsm_extracted = extract_gsm_metadata(raw_gsm)

    resolver = FieldResolver()

    parsed_characteristics: dict[str, dict[str, Any]] = {}
    for gsm_id, meta in gsm_extracted.items():
        characteristics: dict[str, Any] = {}
        skip_keys = {
            "_geo_title", "source_name", "description", "title",
            "contact_name", "contact_email", "contact_institute",
            "contact_address", "contact_city", "contact_state",
            "contact_zip/postal_code", "contact_country",
            "molecule_ch1", "extract_protocol_ch1",
            "label_protocol_ch1", "taxid_ch1",
            "instrument_model", "library_selection",
            "library_source", "library_strategy",
            "data_processing", "platform_id",
            "last_update_date", "submission_date", "gsm",
        }
        for key, value in meta.items():
            if key in skip_keys or key.startswith("_"):
                continue
            resolved = resolver.resolve(key)
            if isinstance(value, list):
                characteristics[resolved] = "; ".join(str(v) for v in value)
            else:
                characteristics[resolved] = value

        if "source_name" in meta:
            characteristics["source_name"] = meta["source_name"]
        if "description" in meta:
            characteristics["description"] = meta["description"]

        parsed_characteristics[gsm_id] = characteristics

    organism = _detect_organism(raw_gsm, gse_metadata)
    platform_ids = _detect_platform_ids(gpl_entries)

    logger.info("Parsed SOFT: %d GSMs, organism=%s, platforms=%s, pubmed_id=%s",
                len(gsm_extracted), organism, platform_ids, pubmed_id)

    return {
        "raw_metadata": gse_metadata,
        "gsm_entries": gsm_extracted,
        "parsed_characteristics": parsed_characteristics,
        "organism": organism,
        "platform_ids": platform_ids,
        "pubmed_id": pubmed_id,
    }
