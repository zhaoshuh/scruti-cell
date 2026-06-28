"""Strategy matcher: queries knowledge graph for similar known cases.

Extracts a feature fingerprint from a GseReport and queries
Neo4j or LocalKnowledgeBase (SQLite) for similar past cases.
Returns matched strategies or None if no match found.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from scruticell.intelligence.reporter import GseReport

logger = logging.getLogger(__name__)


@dataclass
class GseFingerprint:
    """Stable feature fingerprint for a GSE dataset.

    Used to find similar past cases in the knowledge graph.
    """
    gse_id: str
    platform_ids: list[str] = field(default_factory=list)
    organism: str = ""
    matrix_formats: list[str] = field(default_factory=list)
    gsm_count: int = 0
    matrix_file_count: int = 0
    field_names: list[str] = field(default_factory=list)
    total_cells: int = 0
    total_genes: int = 0

    def to_signature(self) -> str:
        """Generate a stable hash-based signature for similarity search."""
        key_parts = [
            f"org:{self.organism}",
            f"fmt:{';'.join(sorted(self.matrix_formats))}",
            f"gsm:{self.gsm_count}",
        ]
        key = "|".join(key_parts)
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "platform_ids": self.platform_ids,
            "organism": self.organism,
            "matrix_formats": self.matrix_formats,
            "gsm_count": self.gsm_count,
            "matrix_file_count": self.matrix_file_count,
            "field_names": self.field_names,
            "total_cells": self.total_cells,
            "total_genes": self.total_genes,
        }


@dataclass
class MatchedStrategy:
    """A strategy matched from a similar past GSE case."""
    source_gse_id: str
    similarity_score: float
    plan_json: str
    success_count: int = 0
    fail_count: int = 0
    notes: str = ""


class StrategyMatcher:
    """Match current GSE to known strategies in the knowledge graph.

    Supports dual backends:
    - LocalKnowledgeBase (SQLite, always available)
    - KnowledgeGraph (Neo4j, enterprise)

    Usage:
        matcher = StrategyMatcher(kb)
        matches = matcher.find_similar(report)
        if matches:
            plan = ProcessingPlan.from_llm_response(gse_id, matches[0].plan_json)
    """

    def __init__(self, kb: Any = None):
        self._kb = kb

    def fingerprint(self, report: GseReport) -> GseFingerprint:
        """Extract a feature fingerprint from a GseReport."""
        formats: set[str] = set()
        total_cells = 0
        total_genes = 0
        field_names: set[str] = set()

        for k, s in report.file_summaries.items():
            if s.get("file_type") in ("h5", "h5ad", "mtx", "rds", "loom", "csv"):
                formats.add(s.get("file_type", ""))
            if s.get("h5_shape"):
                total_cells += s["h5_shape"][0]
                total_genes += s["h5_shape"][1]
            if s.get("n_rows"):
                total_cells += s["n_rows"]
            if s.get("n_cols"):
                total_genes += s["n_cols"]

        for gsm in report.gsm_entries:
            for k in gsm.characteristics:
                field_names.add(k)

        return GseFingerprint(
            gse_id=report.gse_id,
            platform_ids=report.gse_info.platform_ids,
            organism=report.gse_info.organism,
            matrix_formats=sorted(formats),
            gsm_count=len(report.gsm_entries),
            matrix_file_count=len(report.data_files),
            field_names=sorted(field_names),
            total_cells=total_cells,
            total_genes=total_genes,
        )

    def find_similar(
        self, report: GseReport, min_score: float = 0.5, max_results: int = 3
    ) -> list[MatchedStrategy]:
        """Find similar past cases in the knowledge graph.

        Returns strategies sorted by similarity score (highest first).
        """
        fp = self.fingerprint(report)

        if self._kb is None:
            return []

        try:
            results = self._query_kb(fp)
        except Exception as exc:
            logger.warning("KB similarity query failed: %s", exc)
            return []

        strategies: list[MatchedStrategy] = []
        for r in results:
            score = self._compute_similarity(fp, r)
            if score >= min_score:
                strategies.append(MatchedStrategy(
                    source_gse_id=r.get("gse_id", ""),
                    similarity_score=score,
                    plan_json=r.get("fix_strategy", r.get("strategy_json", "{}")),
                    success_count=r.get("success_count", 0),
                    fail_count=r.get("fail_count", 0),
                    notes=r.get("notes", ""),
                ))

        strategies.sort(key=lambda s: s.similarity_score, reverse=True)
        return strategies[:max_results]

    def store_strategy(
        self, report: GseReport, plan_json: str, success: bool = True
    ) -> None:
        """Store a generated plan as a reusable strategy in the knowledge base."""
        if self._kb is None:
            return

        fp = self.fingerprint(report)
        signature = fp.to_signature()

        try:
            if hasattr(self._kb, "store_strategy"):
                self._kb.store_strategy(
                    strategy_id=signature,
                    plan_json=plan_json,
                    success=success,
                    confidence=0.85,
                )
                logger.info("Stored strategy %s in KB", signature)
        except Exception as exc:
            logger.warning("Failed to store strategy: %s", exc)

    def update_strategy_stats(
        self, report: GseReport, success: bool,
    ) -> None:
        """Increment success or failure counter for a known strategy."""
        if self._kb is None:
            return

        fp = self.fingerprint(report)
        signature = fp.to_signature()

        try:
            if hasattr(self._kb, "update_strategy_stats"):
                self._kb.update_strategy_stats(
                    strategy_id=signature,
                    success=success,
                )
                logger.info(
                    "Updated strategy stats for %s: success=%s",
                    signature, success,
                )
        except Exception as exc:
            logger.warning("Failed to update strategy stats: %s", exc)

    def _query_kb(self, fp: GseFingerprint) -> list[dict[str, Any]]:
        """Query the knowledge graph for similar strategies."""
        # Primary: strategies table with accumulated stats
        if hasattr(self._kb, "find_similar_strategies"):
            results = self._kb.find_similar_strategies(fp.to_signature(), limit=5)
            if results:
                return [
                    {
                        "gse_id": "",
                        "fix_strategy": r.get("plan_json", "{}"),
                        "success_count": r.get("success_count", 0),
                        "fail_count": r.get("fail_count", 0),
                    }
                    for r in results
                ]
        # Fallback: failure_cases table (for reflexion lookup)
        if hasattr(self._kb, "find_all_by_stage"):
            return self._kb.find_all_by_stage("planning")
        return []

    def _compute_similarity(self, fp: GseFingerprint, record: dict[str, Any]) -> float:
        """Compute similarity score between fingerprint and a KB record."""
        score = 0.0
        record_org = record.get("error_message", "")
        try:
            record_data = json.loads(record_org) if isinstance(record_org, str) else record_org
        except (json.JSONDecodeError, TypeError):
            return 0.0

        if not isinstance(record_data, dict):
            return 0.0

        # Same organism → +0.3
        if record_data.get("organism") == fp.organism:
            score += 0.3

        # Same GSM count → +0.2
        if record_data.get("gsm_count") == fp.gsm_count:
            score += 0.2

        # Matrix format overlap → +0.2
        record_formats = set(record_data.get("matrix_formats", []))
        fp_formats = set(fp.matrix_formats)
        if record_formats and fp_formats:
            overlap = len(record_formats & fp_formats) / len(fp_formats)
            score += 0.2 * overlap

        # Field name overlap → +0.3
        record_fields = set(record_data.get("field_names", []))
        fp_fields = set(fp.field_names)
        if record_fields and fp_fields:
            overlap = len(record_fields & fp_fields) / max(len(fp_fields), 1)
            score += 0.3 * overlap

        return min(score, 1.0)
