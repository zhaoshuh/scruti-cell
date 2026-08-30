"""Tests for KB strategy matching (find_similar) and the reuse toggle."""

import os

from scruticell.intelligence.matcher import GseFingerprint, StrategyMatcher
from scruticell.intelligence.reporter import GseInfo, GseReport
from scruticell.knowledge.graph import LocalKnowledgeBase


def _make_report(gse_id="GSE1", organism="Homo sapiens"):
    return GseReport(
        gse_id=gse_id,
        gse_info=GseInfo(gse_id=gse_id, organism=organism),
        file_summaries={"f1.csv": {"file_type": "csv"}},
        data_files=["f1.csv"],
    )


def test_find_similar_returns_stored_strategy(tmp_path):
    kb = LocalKnowledgeBase(tmp_path / "kb.db")
    report = _make_report()
    fp = GseFingerprint(
        gse_id=report.gse_id,
        organism="Homo sapiens",
        matrix_formats=["csv"],
        gsm_count=0,
    )
    sig = fp.to_signature()
    kb.store_strategy(sig, plan_json='{"plan": true}', success=True)

    matcher = StrategyMatcher(kb)
    matches = matcher.find_similar(report, min_score=0.5)
    assert len(matches) >= 1
    assert matches[0].similarity_score == 1.0
    kb.close()


def test_find_similar_empty_without_strategies(tmp_path):
    kb = LocalKnowledgeBase(tmp_path / "kb.db")
    matcher = StrategyMatcher(kb)
    assert matcher.find_similar(_make_report()) == []
    kb.close()


def test_disabled_strategy_reuse_returns_empty(tmp_path):
    kb = LocalKnowledgeBase(tmp_path / "kb.db")
    report = _make_report()
    sig = GseFingerprint(
        gse_id="GSE1", organism="Homo sapiens", matrix_formats=["csv"], gsm_count=0,
    ).to_signature()
    kb.store_strategy(sig, plan_json='{"plan": true}', success=True)

    matcher = StrategyMatcher(kb, enabled=False)
    assert matcher.find_similar(report) == []
    kb.close()


def test_similarity_signature_scoring():
    matcher = StrategyMatcher(None)
    fp = GseFingerprint(gse_id="GSE1", organism="Homo sapiens",
                        matrix_formats=["csv"], gsm_count=0)
    sig = fp.to_signature()
    assert matcher._compute_similarity(fp, {"strategy_id": sig}) == 1.0
    assert matcher._compute_similarity(fp, {"strategy_id": sig[:8]}) == 0.7
    assert matcher._compute_similarity(fp, {"strategy_id": "deadbeef1234"}) == 0.5
    assert matcher._compute_similarity(fp, {"error_message": "some text"}) == 0.0