"""Signal collection for file→GSM attribution (L0+L1 deterministic only).

Collects signals from ancestry paths and SOFT references.
All judgment/decision is deferred to the classifier (LLM via prompt).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from scruticell.preprocessing.models import AttributionResult, FileMeta

logger = logging.getLogger(__name__)


def _extract_gsm_from_ancestry(ancestry: str) -> str | None:
    """L0: Extract first GSM ID from ancestry path."""
    match = re.search(r"GSM\d+", ancestry)
    return match.group(0) if match else None


def _extract_attribution_signals(file_meta: FileMeta) -> dict[str, Any]:
    """Extract compact token signals from FileMeta for LLM inference.

    Returns a dict with:
      - filename_gsm_ids
      - filename_sample_ids
      - filename_donor_ids
      - filename_treatments
      - filename_conditions
      - filename_tissues
      - column_sample_ids: {col_name: [sample_ids]}
      - column_donor_ids: {col_name: [donor_ids]}
      - prefix_sample_ids: [sample_ids from barcode prefixes]
      - prefix_donor_ids: [donor_ids from barcode prefixes]
    """
    signals: dict[str, Any] = {
        "filename_gsm_ids": file_meta.filename.gsm_ids,
        "filename_sample_ids": file_meta.filename.sample_ids,
        "filename_donor_ids": file_meta.filename.donor_ids,
        "filename_treatments": file_meta.filename.treatments,
        "filename_conditions": file_meta.filename.conditions,
        "filename_tissues": file_meta.filename.tissues,
    }

    # Column header tokens
    col_sample_ids: dict[str, list[str]] = {}
    col_donor_ids: dict[str, list[str]] = {}
    for col in file_meta.columns:
        if col.header_meta.sample_ids:
            col_sample_ids[col.header_meta.original] = col.header_meta.sample_ids
        if col.header_meta.donor_ids:
            col_donor_ids[col.header_meta.original] = col.header_meta.donor_ids
    if col_sample_ids:
        signals["column_sample_ids"] = col_sample_ids
    if col_donor_ids:
        signals["column_donor_ids"] = col_donor_ids

    # Barcode prefix tokens
    prefix_sample_ids: list[str] = []
    prefix_donor_ids: list[str] = []
    for pm in file_meta.row_index.prefix_meta:
        prefix_sample_ids.extend(pm.sample_ids)
        prefix_donor_ids.extend(pm.donor_ids)
    if prefix_sample_ids:
        signals["prefix_sample_ids"] = prefix_sample_ids
    if prefix_donor_ids:
        signals["prefix_donor_ids"] = prefix_donor_ids

    return signals


def _lookup_in_field_value_maps(
    token: str,
    field_value_maps: dict[str, Any],
) -> list[str]:
    """Try to match a token against all field_value_maps values.

    Returns list of GSM IDs if a match is found, empty list otherwise.
    Used as a utility by caller, not automatically applied.
    """
    for fv in field_value_maps.values():
        values = fv.get("values", []) if isinstance(fv, dict) else []
        if isinstance(values, list):
            for vg in values:
                value = vg.get("value", "") if isinstance(vg, dict) else str(vg)
                if str(token).lower() == str(value).lower():
                    return vg.get("gsm_ids", []) if isinstance(vg, dict) else []
    return []


def compute_attribution(
    file_meta: FileMeta,
    field_value_maps: dict[str, Any],
    soft_suppl_refs: dict[str, str] | None = None,
) -> AttributionResult:
    """Collect signals for file→GSM attribution. No decisions made here.

    L0: Extract GSM ID from ancestry path (high confidence)
    L1: Match filename against SOFT supplementary_file references
    Signals: Extract token signals from FileMeta for LLM inference

    Returns an AttributionResult with candidates + signals.
    The classifier (LLM) makes all final decisions.
    """
    result = AttributionResult(ancestry=file_meta.ancestry)

    # ── L0: Ancestry path GSM extraction ──
    gsm_from_ancestry = _extract_gsm_from_ancestry(file_meta.ancestry)
    if gsm_from_ancestry:
        result.candidates.append(gsm_from_ancestry)
        result.source = "ancestry"
        result.confidence = 1.0

    # ── L1: SOFT supplementary reference ──
    if soft_suppl_refs:
        filename = file_meta.ancestry.split("/")[-1].split("\\")[-1]
        stem = re.sub(r"\.(gz|tar|zip)$", "", filename)
        stem = re.sub(r"\.[^.]+$", "", stem)
        for ref_filename, gsm_id in soft_suppl_refs.items():
            if filename == ref_filename or stem == ref_filename or ref_filename in filename:
                if gsm_id not in result.candidates:
                    result.candidates.append(gsm_id)
                if not result.source:
                    result.source = "soft_ref"
                    result.confidence = 1.0
                break

    # ── Signals: Extract token signals for LLM ──
    result.signals = _extract_attribution_signals(file_meta)

    if not result.candidates:
        result.source = "none"
        result.confidence = 0.0

    return result


def build_soft_suppl_refs(gsm_entries_raw: dict[str, Any]) -> dict[str, str]:
    """Build {filename: gsm_id} map from SOFT supplementary_file_N references."""
    refs: dict[str, str] = {}
    for gsm_id, raw in gsm_entries_raw.items():
        for key in sorted(raw.keys()):
            if not re.match(r"supplementary_file_\d+", key, re.IGNORECASE):
                continue
            url_or_name = str(raw.get(key, ""))
            if not url_or_name:
                continue
            filename = url_or_name.split("/")[-1] if "/" in url_or_name else url_or_name
            filename = filename.split("?")[0]
            if filename and filename not in refs:
                refs[filename] = gsm_id
    return refs
