"""Parse strategy storage and retrieval."""

from __future__ import annotations

from typing import Any


def build_strategy_from_state(
    gse_id: str, organism: str, assay_type: str, strategy_params: dict[str, Any]
) -> dict[str, Any]:
    """Build a parse strategy record from current pipeline state."""
    return {
        "strategy_id": f"{gse_id}_strategy_{hash(str(strategy_params))}",
        "gse_pattern": f"{organism}:{assay_type}",
        "strategy_json": strategy_params,
        "gse_id": gse_id,
        "success_count": 1,
        "fail_count": 0,
        "avg_confidence": 1.0,
    }
