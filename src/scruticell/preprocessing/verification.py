"""Verification checks for LLM-generated outputs.

Validates extraction results, demux splits, and classification output
for structural correctness and completeness.
"""

from __future__ import annotations

from typing import Any

from scruticell.preprocessing.models import VerificationCheck, VerificationReport


def verify_extraction_results(
    per_gsm_results: dict[str, dict],
    total_gsm_count: int,
) -> VerificationReport:
    """Verify extract_metadata() execution results across all GSM.

    Args:
        per_gsm_results: {GSM_ID: {field: value, ...}} from sandbox execution
        total_gsm_count: total number of GSM in the dataset

    Returns:
        VerificationReport with checks and pass/fail status
    """
    checks: list[VerificationCheck] = []

    covered = len(per_gsm_results)
    if total_gsm_count > 0 and covered < total_gsm_count:
        checks.append(VerificationCheck(
            type="coverage", severity="warning",
            detail=f"Only {covered}/{total_gsm_count} GSM succeeded",
        ))

    non_empty = sum(1 for r in per_gsm_results.values() if r)
    if non_empty == 0:
        checks.append(VerificationCheck(
            type="empty_result", severity="error",
            detail="extract_metadata() produced empty results for all GSM",
        ))

    total_fields: set[str] = set()
    for r in per_gsm_results.values():
        total_fields.update(r.keys())
    if len(total_fields) < 2 and covered > 0:
        checks.append(VerificationCheck(
            type="too_few_fields", severity="warning",
            detail=f"Only {len(total_fields)} distinct fields extracted across {covered} GSM",
        ))

    passed = not any(c.severity == "error" for c in checks)
    return VerificationReport(passed=passed, checks=checks)


def verify_demux_results(
    split_files: list[dict],
    demux_rule: dict,
) -> VerificationReport:
    """Verify demux splitting results.

    Args:
        split_files: [{sample_id, output_file}, ...] from split_demux_file()
        demux_rule: the demux rule used for splitting

    Returns:
        VerificationReport
    """
    checks: list[VerificationCheck] = []

    if not split_files:
        checks.append(VerificationCheck(
            type="no_output", severity="error",
            detail="split_demux_file() returned empty list",
        ))
        return VerificationReport(passed=False, checks=checks)

    import os
    for sf in split_files:
        fp = sf.get("output_file", "")
        if not fp:
            checks.append(VerificationCheck(
                type="missing_path", severity="error",
                detail="Output entry has no output_file path",
            ))
        elif not os.path.exists(fp):
            checks.append(VerificationCheck(
                type="missing_file", severity="error",
                detail=f"Output file {fp} does not exist",
            ))
        elif os.path.getsize(fp) == 0:
            checks.append(VerificationCheck(
                type="empty_file", severity="error",
                detail=f"Output file {fp} is empty",
            ))

    sample_ids = set(sf.get("sample_id", "") for sf in split_files)
    if len(sample_ids) < 2:
        checks.append(VerificationCheck(
            type="too_few_samples", severity="warning",
            detail=f"Only {len(sample_ids)} distinct sample_ids extracted (expected 2+)",
        ))

    passed = not any(c.severity == "error" for c in checks)
    return VerificationReport(passed=passed, checks=checks)


def verify_classification(
    classification: dict[str, Any],
    total_gsm_count: int = 0,
) -> VerificationReport:
    """Verify classifier output for structural correctness.

    Args:
        classification: the classification JSON from classifier
        total_gsm_count: expected total GSM count

    Returns:
        VerificationReport
    """
    checks: list[VerificationCheck] = []

    sample_groups = classification.get("sample_groups", [])
    if not sample_groups:
        checks.append(VerificationCheck(
            type="no_groups", severity="error",
            detail="No sample groups in classification output",
        ))

    assigned_gsms: set[str] = set()
    for group in sample_groups:
        if isinstance(group, str):
            continue
        assigned_gsms.update(group.get("gsm_ids", []))

    if total_gsm_count > 0 and len(assigned_gsms) < total_gsm_count:
        missing = total_gsm_count - len(assigned_gsms)
        checks.append(VerificationCheck(
            type="missing_gsm", severity="warning",
            detail=f"{missing} GSM not assigned to any group",
        ))

    all_gsm_list: list[str] = []
    for g in sample_groups:
        if isinstance(g, str):
            continue
        gsm_ids = g.get("gsm_ids", [])
        if isinstance(gsm_ids, list):
            all_gsm_list.extend(gsm_ids)
    duplicates = [g for g in set(all_gsm_list) if all_gsm_list.count(g) > 1]
    if duplicates:
        checks.append(VerificationCheck(
            type="overlap", severity="error",
            detail=f"GSM appearing in multiple groups: {duplicates[:5]}",
        ))

    for group in sample_groups:
        if isinstance(group, str):
            continue
        if not group.get("modalities"):
            checks.append(VerificationCheck(
                type="no_modality", severity="error",
                detail=f"Group with GSM {group.get('gsm_ids', [])[:2]} has no modalities",
            ))
        mods = group.get("modalities", {})
        if isinstance(mods, list):
            continue  # skip list format — structural error caught by json loads
        for mod_name, mod_data in mods.items():
            if isinstance(mod_data, dict) and mod_data.get("has_demux"):
                if not mod_data.get("demux_rule"):
                    checks.append(VerificationCheck(
                        type="missing_demux_rule", severity="error",
                        detail=f"Modality {mod_name}: has_demux=true but no demux_rule",
                    ))

    passed = not any(c.severity == "error" for c in checks)
    return VerificationReport(passed=passed, checks=checks, extra={"assigned_gsms": list(assigned_gsms)})
