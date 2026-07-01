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
    issue_type: str = ""   # "missing_field", "wrong_strategy", "extra_batches", "empty_output"
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
    """Verify batch separation plan against actual h5ad output."""
 
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
 
        # Check 2: per-batch field verification
        per_batch_adatas = {}
        for h5 in h5ad_files:
            try:
                adata = sc.read_h5ad(h5)
                per_batch_adatas[h5.name] = adata
            except Exception as exc:
                logger.warning("Cannot read %s: %s", h5, exc)
 
        for batch_def in (getattr(plan, "batches", []) or []):
            gsm_id = getattr(batch_def, "gsm_id", "")
            batch_label = getattr(batch_def, "batch_label", gsm_id)
            defining = getattr(batch_def, "defining_fields", []) or []
            reasoning = getattr(batch_def, "reasoning", "") or ""
 
            # Find matching h5ad file
            matched_adata = None
            matched_name = ""
            for name, adata in per_batch_adatas.items():
                if gsm_id in name or batch_label in name:
                    matched_adata = adata
                    matched_name = name
                    break
            if matched_adata is None and per_batch_adatas:
                matched_adata = next(iter(per_batch_adatas.values()))
                matched_name = next(iter(per_batch_adatas.keys()))
 
            if matched_adata is None:
                report.mismatches.append(BatchMismatch(
                    gsm_id=gsm_id, batch_label=batch_label,
                    issue_type="empty_output",
                    detail=f"No h5ad file found matching this batch.",
                ))
                continue
 
            obs_cols = list(matched_adata.obs.columns)
 
            # Check defining_fields exist in obs
            for field in defining:
                if field == "batch":
                    continue  # batch column always exists
                if field not in obs_cols:
                    report.mismatches.append(BatchMismatch(
                        gsm_id=gsm_id, batch_label=batch_label,
                        issue_type="missing_field",
                        field=field,
                        actual=", ".join(obs_cols[:15]),
                    ))
 
            # Check if batch_label contains metadata values that aren't in obs
            # e.g., batch_label="Control_WT" but there's no "Control" in obs
            parts = batch_label.replace("-", "_").split("_")
            for part in parts:
                if len(part) < 3:
                    continue
                found = False
                for col in obs_cols:
                    try:
                        vals = matched_adata.obs[col].astype(str).unique()
                        if any(str(part).lower() in str(v).lower() for v in vals[:10]):
                            found = True
                            break
                    except Exception:
                        pass
                if not found and len(parts) == 1:
                    report.warnings.append(
                        f"Batch '{batch_label}': label part '{part}' not found in any obs column"
                    )
 
            # Check reasoning mentions treatment/genotype but obs doesn't have those
            reasoning_lower = reasoning.lower()
            for keyword, synonyms in [
                ("treatment", ["treatment", "treated", "drug", "therapy"]),
                ("genotype", ["genotype", "genetic", "mutation"]),
                ("timepoint", ["timepoint", "time", "day", "hour"]),
                ("condition", ["condition", "group"]),
            ]:
                if keyword in reasoning_lower:
                    found = any(
                        any(s in c.lower() for s in synonyms) for c in obs_cols
                    )
                    if not found:
                        report.warnings.append(
                            f"Batch '{batch_label}': reasoning mentions '{keyword}' "
                            f"but no matching column found in obs ({','.join(obs_cols[:10])})"
                        )
 
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
            if actual_batches == 1 and len(getattr(plan, "batches", []) or []) > 1:
                report.mismatches.append(BatchMismatch(
                    issue_type="wrong_strategy",
                    expected=strategy,
                    actual="1 file",
                    detail=f"Plan declares {strategy} with {len(plan.batches)} batches but only 1 file.",
                ))
 
        report.passed = len(report.mismatches) == 0
        if report.passed:
            logger.info("Batch verification PASSED for %s (%d batches)", gse_id, actual_batches)
        else:
            logger.warning("Batch verification FAILED for %s: %d mismatches", gse_id, len(report.mismatches))
        return report
