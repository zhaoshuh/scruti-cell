"""Failure case management for Reflexion learning."""

from __future__ import annotations

from typing import Any


def build_failure_case(
    gse_id: str,
    error_signature: str,
    pipeline_stage: str,
    error_message: str,
    fix_strategy: str = "",
) -> dict[str, Any]:
    """Build a failure case record for Neo4j storage."""
    return {
        "case_id": f"{gse_id}_{pipeline_stage}_{hash(error_signature)}",
        "error_signature": error_signature,
        "gse_id": gse_id,
        "pipeline_stage": pipeline_stage,
        "error_message": error_message,
        "fix_strategy": fix_strategy,
        "fixed": bool(fix_strategy),
    }
