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
        h5ad_files = sorted(output_dir.glob(f"{gse_id}_*_scruticell.h5ad"))
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
            zip(plan_batches, batch_data)
            if len(plan_batches) == len(batch_data)
            else zip(plan_batches, batch_data * len(plan_batches))
        ):
            gsm_id = getattr(batch_def, "gsm_id", "")
            batch_label = getattr(batch_def, "batch_label", gsm_id) or gsm_id
            defining = getattr(batch_def, "defining_fields", []) or []

            if not defining:
                continue

            obs = bd.get("obs_unique", {})
            obs_cols = bd.get("obs_cols", [])

            for field in defining:
                if field == "batch":
                    continue

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

        # Cross-batch redundancy: same defining value in multiple batches
        for field, vmap in field_value_to_batches.items():
            for value, batch_indices in vmap.items():
                if len(batch_indices) > 1:
                    labels = []
                    for bi in batch_indices:
                        bd = plan_batches[bi] if bi < len(plan_batches) else None
                        labels.append(
                            getattr(bd, "batch_label", "")
                            or getattr(bd, "gsm_id", f"batch_{bi}")
                        )
                    report.mismatches.append(BatchMismatch(
                        issue_type="redundant_batches",
                        field=field,
                        expected="unique per batch",
                        actual="same in multiple",
                        detail=(
                            f"Field '{field}' value '{value}' appears in "
                            f"{len(batch_indices)} batches: {', '.join(labels)}. "
                            f"These batches should be merged — they share the same "
                            f"defining characteristic."
                        ),
                    ))

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
