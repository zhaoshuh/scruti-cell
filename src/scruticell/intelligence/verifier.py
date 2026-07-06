"""Post-execution batch verification.
 
After plan→execute produces .h5ad files, verify that the LLM's
batch separation strategy matches the actual output data.
On mismatch, carry structured context back to the plan phase
for re-planning.
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BatchMismatch:
    """A detected inconsistency between plan and actual data."""
    gsm_id: str = ""
    batch_label: str = ""
    issue_type: str = ""   # "missing_field", "wrong_strategy", "extra_batches", "empty_output", "mixed_values", "redundant_batches"
    field: str = ""
    expected: str = ""
    actual: str = ""
    detail: str = ""

    def format(self) -> str:
        if self.issue_type == "missing_field":
            return (
                f"Batch '{self.batch_label}' (GSM {self.gsm_id}): "
                f"defining_field '{self.field}' not found in obs columns. "
                f"Available columns: {self.actual}"
            )
        if self.issue_type == "wrong_strategy":
            return (
                f"Plan declares batch_separation_strategy='{self.expected}' "
                f"but {self.actual}. Detail: {self.detail}"
            )
        if self.issue_type == "extra_batches":
            return (
                f"Plan expects {self.expected} batches but {self.actual} .h5ad "
                f"files exist. Detail: {self.detail}"
            )
        if self.issue_type == "empty_output":
            return (
                f"Batch '{self.batch_label}' (GSM {self.gsm_id}): "
                f"output h5ad has 0 cells. {self.detail}"
            )
        if self.issue_type == "mixed_values":
            return self.detail
        if self.issue_type == "redundant_batches":
            return self.detail
        return self.detail


@dataclasses.dataclass
class VerificationReport:
    """Complete post-execution verification result."""
    gse_id: str
    passed: bool = False
    mismatches: list[BatchMismatch] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable context suitable for injecting into plan prompt."""
        if self.passed:
            return ""
        lines = ["BATCH VERIFICATION FAILED — issues to fix:"]
        for m in self.mismatches:
            lines.append(f"  - {m.format()}")
        if self.warnings:
            lines.append("Additional warnings:")
            for w in self.warnings:
                lines.append(f"  - {w}")
        lines.append(
            "Please revise the plan. Check: "
            "1) Are defining_fields correctly named? "
            "2) Does batch_separation_strategy match the data? "
            "3) Do batch_labels reflect actual metadata values?"
        )
        return "\n".join(lines)


class BatchVerifier:
    """Verify batch separation plan against actual h5ad output.

    Core test: defining_fields values must have a 1:1 relationship
    with batches.  If "treatment"="Drug_A" appears in batch 1,
    it must NOT appear in any other batch, and batch 1 must NOT
    also contain "Drug_B".
    """

    def verify(
        self,
        plan: Any,
        output_dir: Path,
        gse_id: str,
    ) -> VerificationReport:
        """Run all verification checks. Returns a VerificationReport."""
        try:
            import scanpy as sc
        except Exception:
            logger.warning("scanpy not available, skipping batch verification")
            return VerificationReport(gse_id=gse_id, passed=True)

        report = VerificationReport(gse_id=gse_id)

        # Find all h5ad files
        h5ad_files = sorted(output_dir.glob("batch_*/matrix.h5ad"))
        if not h5ad_files:
            h5ad_files = sorted(output_dir.glob("*.h5ad"))

        # Check 1: batch count vs expected
        expected_batches = len(getattr(plan, "batches", []) or [])
        actual_batches = len(h5ad_files)
        if actual_batches == 0:
            report.mismatches.append(BatchMismatch(
                issue_type="empty_output",
                detail="No .h5ad files found after execution.",
            ))
            report.passed = False
            return report
        if expected_batches > 0 and actual_batches != expected_batches:
            report.mismatches.append(BatchMismatch(
                issue_type="extra_batches",
                expected=str(expected_batches),
                actual=str(actual_batches),
                detail=f"Plan declares {expected_batches} but found {actual_batches} files.",
            ))

        # Load all h5ad files (lightweight: only need obs)
        batch_data: list[dict] = []
        for h5 in h5ad_files:
            try:
                adata = sc.read_h5ad(h5)
                obs_vals: dict[str, set[str]] = {}
                for col in adata.obs.columns:
                    try:
                        uv = set(adata.obs[col].astype(str).unique())
                        if len(uv) <= 50:
                            obs_vals[col] = uv
                    except Exception:
                        pass
                batch_data.append({
                    "file": h5.name,
                    "n_cells": adata.n_obs,
                    "n_genes": adata.n_vars,
                    "obs_unique": obs_vals,
                    "obs_cols": list(adata.obs.columns),
                })
            except Exception as exc:
                logger.warning("Cannot read %s: %s", h5, exc)

        # Check 2: 1:1 relationship between defining_fields values and batches
        # Collect (field, value) → batch_index mapping
        field_value_to_batches: dict[str, dict[str, list[int]]] = {}
        plan_batches = getattr(plan, "batches", []) or []

        for i, (batch_def, bd) in enumerate(
            zip(plan_batches, batch_data[:len(plan_batches)])
        ):
            gsm_id = getattr(batch_def, "gsm_id", "")
            batch_label = getattr(batch_def, "batch_label", gsm_id) or gsm_id
            defining = getattr(batch_def, "defining_fields", []) or []

            if not defining:
                report.mismatches.append(BatchMismatch(
                    gsm_id=gsm_id,
                    batch_label=batch_label,
                    issue_type="missing_field",
                    field="defining_fields",
                    expected="non-empty list",
                    actual="empty",
                    detail=(
                        f"Batch '{batch_label}' (GSM {gsm_id}) has no defining_fields. "
                        f"Every batch MUST declare defining_fields that uniquely "
                        f"identify this batch across the dataset."
                    ),
                ))
                continue

            obs = bd.get("obs_unique", {})
            obs_cols = bd.get("obs_cols", [])

            for field in defining:
                # Auto-map "batch" → "batch_label" (common LLM output)
                if field == "batch" and field not in obs_cols and "batch_label" in obs_cols:
                    field = "batch_label"
                # Field not in obs → warning, not mismatch
                if field not in obs_cols:
                    similar = [c for c in obs_cols if field in c or c in field]
                    report.warnings.append(
                        f"Batch '{batch_label}': defining_field '{field}' not found "
                        f"in obs columns. Similar fields: {similar or 'none'}."
                    )
                    continue

                values = obs.get(field, set())
                n_vals = len(values)

                if n_vals == 0:
                    report.warnings.append(
                        f"Batch '{batch_label}': field '{field}' exists but is empty."
                    )
                    continue

                # Boundary check: one batch = one value per defining field
                if n_vals > 1:
                    report.mismatches.append(BatchMismatch(
                        gsm_id=gsm_id,
                        batch_label=batch_label,
                        issue_type="mixed_values",
                        field=field,
                        actual=", ".join(sorted(values)[:5]),
                        detail=(
                            f"Batch '{batch_label}' has {n_vals} different values "
                            f"for defining_field '{field}': {', '.join(sorted(values)[:5])}. "
                            f"Batch boundary is wrong — this batch contains data from "
                            f"multiple groups."
                        ),
                    ))

                # Track for cross-batch redundancy check
                if field not in field_value_to_batches:
                    field_value_to_batches[field] = {}
                for v in values:
                    v_str = str(v)
                    field_value_to_batches[field].setdefault(v_str, []).append(i)

        # Guard: no batches have defining_fields → warn
        if plan_batches and not field_value_to_batches:
            report.warnings.append(
                "No batches have defining_fields set — cannot verify 1:1 "
                "relationship. The LLM may not have identified batch boundaries."
            )

        # Cross-batch redundancy: check unique COMBINATION of defining_fields per batch
        # Individual field values naturally overlap in multi-condition experiments
        # (e.g. genotype=APOE4 appears in Vehicle AND Ceria-TPP batches).
        # Only a truly duplicated COMBINATION (same values for ALL defining_fields)
        # indicates batches that should be merged.
        if len(plan_batches) >= 2:
            combo_to_batch: dict[tuple, int] = {}
            for i, batch_def in enumerate(plan_batches):
                defining = getattr(batch_def, "defining_fields", []) or []
                if not defining:
                    # No defining_fields → skip cross-batch check for this batch
                    continue
                if i >= len(batch_data):
                    continue
                bd = batch_data[i]
                obs = bd.get("obs_unique", {})
                combo_parts: list[tuple[str, str]] = []
                for field in sorted(defining):
                    # Auto-map "batch" → "batch_label" (same as per-batch loop)
                    if field == "batch" and field not in obs and "batch_label" in obs:
                        field = "batch_label"
                    vals = obs.get(field, set())
                    if not vals:
                        continue
                    v_str = sorted(vals)[0]
                    combo_parts.append((field, v_str))
                if not combo_parts:
                    continue
                combo = tuple(combo_parts)
                if combo in combo_to_batch:
                    prev_i = combo_to_batch[combo]
                    prev_label = (
                        getattr(plan_batches[prev_i], "batch_label", "")
                        or getattr(plan_batches[prev_i], "gsm_id", f"batch_{prev_i}")
                    )
                    curr_label = (
                        getattr(batch_def, "batch_label", "")
                        or getattr(batch_def, "gsm_id", f"batch_{i}")
                    )
                    report.mismatches.append(BatchMismatch(
                        issue_type="redundant_batches",
                        field="+".join(f for f, _ in combo_parts),
                        expected="unique combination per batch",
                        actual="same in multiple",
                        detail=(
                            f"Field combination {dict(combo)} appears in "
                            f"both batch '{prev_label}' and '{curr_label}'. "
                            f"These batches have identical defining characteristics "
                            f"and should be merged."
                        ),
                    ))
                else:
                    combo_to_batch[combo] = i

        # Check 3: batch_separation_strategy consistency
        strategy = getattr(plan, "batch_separation_strategy", "") or ""
        if strategy == "single_batch" and actual_batches > 1:
            report.mismatches.append(BatchMismatch(
                issue_type="wrong_strategy",
                expected="single_batch",
                actual=f"{actual_batches} files found",
                detail="Plan expects single batch but multiple files produced.",
            ))
        if strategy in ("per_sample", "per_condition", "per_timepoint", "per_patient"):
            if actual_batches == 1 and len(plan_batches) > 1:
                report.mismatches.append(BatchMismatch(
                    issue_type="wrong_strategy",
                    expected=strategy,
                    actual="1 file",
                    detail=f"Plan declares {strategy} with {len(plan_batches)} batches but only 1 file.",
                ))

        report.passed = len(report.mismatches) == 0
        if report.passed:
            logger.info("Batch verification PASSED for %s (%d batches)", gse_id, actual_batches)
        else:
            logger.warning("Batch verification FAILED for %s: %d mismatches", gse_id, len(report.mismatches))
        return report
