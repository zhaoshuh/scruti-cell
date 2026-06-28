"""Audit logging and confidence reporting for pipeline transparency.

Provides structured audit trail for every pipeline run, including:
- Step-by-step provenance tracking
- Confidence score breakdowns
- Human-review markers for borderline results
- Compliance-ready output for enterprise users
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class AuditLogger:
    """Structured audit logger for pipeline transparency."""

    def __init__(self, output_dir: Path, gse_id: str):
        self.output_dir = Path(output_dir)
        self.gse_id = gse_id
        self.entries: list[dict[str, Any]] = []
        self.start_time = datetime.now(timezone.utc)

    def log_step(
        self,
        step: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Record a pipeline step with status and details."""
        self.entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "step": step,
            "status": status,
            "details": details or {},
        })

    def log_confidence(self, scores: dict[str, float]) -> None:
        """Record confidence scores with breakdown."""
        self.log_step("validation", "completed", {"confidence_scores": scores})

    def log_manual_review(self, reason: str, context: dict[str, Any] | None = None) -> None:
        """Flag a result for human review."""
        self.log_step("manual_review", "flagged", {
            "reason": reason,
            "context": context or {},
        })

    def write(self) -> Path:
        """Write the audit log to a JSON file and return the path."""
        log_data = {
            "gse_id": self.gse_id,
            "pipeline_start": self.start_time.isoformat(),
            "pipeline_end": datetime.now(timezone.utc).isoformat(),
            "entries": self.entries,
        }
        output_path = self.output_dir / f"{self.gse_id}_audit.json"
        output_path.write_text(json.dumps(log_data, indent=2, ensure_ascii=False))
        return output_path


def generate_confidence_report(
    scores: dict[str, float],
    errors: list[str],
    warnings: list[str],
) -> str:
    """Generate a human-readable confidence report.

    Args:
        scores: Per-dimension confidence scores.
        errors: Validation error messages.
        warnings: Validation warning messages.

    Returns:
        Formatted multi-line string report.
    """
    lines = [
        "=" * 50,
        "  CONFIDENCE REPORT",
        "=" * 50,
        "",
    ]

    dim_labels = {
        "count_integrity": "Raw counts integrity",
        "metadata_completeness": "Metadata completeness",
        "alignment_accuracy": "Cell/gene alignment",
        "batch_validity": "Batch annotation validity",
    }

    for key, label in dim_labels.items():
        score = scores.get(key, 0.0)
        bar = "=" * int(score * 20)
        lines.append(f"  {label:<30} [{bar:<20}] {score:.0%}")

    lines.append(f"\n  OVERALL: {sum(scores.values()) / max(len(scores), 1):.0%}")

    if errors:
        lines.append(f"\n  ERRORS ({len(errors)}):")
        for e in errors:
            lines.append(f"    - {e}")

    if warnings:
        lines.append(f"\n  WARNINGS ({len(warnings)}):")
        for w in warnings:
            lines.append(f"    - {w}")

    lines.append("")
    lines.append("=" * 50)
    return "\n".join(lines)
