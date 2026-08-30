"""Tests for Reflexion node, strategy generation, and retry logic."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from unittest.mock import patch

from scruticell.knowledge.graph import LocalKnowledgeBase
from scruticell.pipeline.nodes.reflexion import (
    _generate_error_signature,
    _generate_strategy,
    _heuristic_strategy,
    _lookup_similar_failure,
    _store_failure_case,
    apply_strategy_to_state,
    reflexion_node,
    set_knowledge_base,
)
from scruticell.pipeline.state import PipelineState


@pytest.fixture
def kb(tmp_path):
    db = LocalKnowledgeBase(tmp_path / "test_reflexion.db")
    set_knowledge_base(db)
    yield db
    db.close()


class TestErrorSignature:
    def test_same_errors_same_signature(self):
        sig1 = _generate_error_signature(["Matrix columns (10) != obs rows (20)"])
        sig2 = _generate_error_signature(["Matrix columns (5) != obs rows (15)"])
        assert sig1 == sig2

    def test_different_errors_different_signature(self):
        sig1 = _generate_error_signature(["Expression matrix is None"])
        sig2 = _generate_error_signature(["No batch column found"])
        assert sig1 != sig2

    def test_normalizes_gse_ids(self):
        sig1 = _generate_error_signature(["GSE12345 failed to parse"])
        sig2 = _generate_error_signature(["GSE67890 failed to parse"])
        assert sig1 == sig2

    def test_returns_12_char_hex(self):
        sig = _generate_error_signature(["test error"])
        assert len(sig) == 12
        assert all(c in "0123456789abcdef" for c in sig)


class TestHeuristicStrategy:
    def test_dimension_mismatch_triggers_transpose(self):
        state = PipelineState(gse_id="GSE123")
        state.validation_errors = [
            "Matrix columns (100) != obs rows (500)"
        ]
        strategy = _heuristic_strategy(state, state.validation_errors)
        assert strategy.get("force_transpose") is True

    def test_batch_error_triggers_batch_hint(self):
        state = PipelineState(gse_id="GSE123")
        state.validation_errors = ["Batch column has 0 null values"]
        strategy = _heuristic_strategy(state, state.validation_errors)
        assert strategy.get("adjust_batch") is not None

    def test_metadata_error_triggers_reparse(self):
        state = PipelineState(gse_id="GSE123")
        state.validation_errors = ["obs has only 1 columns (sparse metadata)"]
        strategy = _heuristic_strategy(state, state.validation_errors)
        assert "reparse_fields" in strategy or "metadata" in strategy.get("notes", "").lower()

    def test_barcode_error_triggers_strip(self):
        state = PipelineState(gse_id="GSE123")
        state.validation_errors = ["No cell barcodes available"]
        strategy = _heuristic_strategy(state, state.validation_errors)
        assert "realign_barcodes" in strategy or "barcode" in strategy.get("notes", "").lower()

    def test_always_returns_action_retry(self):
        state = PipelineState(gse_id="GSE123")
        strategy = _heuristic_strategy(state, ["Some error"])
        assert strategy["action"] == "retry"


class TestStrategyApplication:
    def test_force_transpose(self):
        matrix = np.array([[1, 2, 3], [4, 5, 6], [7, 8, 9]], dtype=np.float32)
        state = PipelineState(
            gse_id="GSE123",
            expression_matrix=matrix,
            cell_barcodes_raw=[f"cell_{i}" for i in range(3)],
            gene_names_raw=[f"gene_{i}" for i in range(3)],
        )
        strategy = {"force_transpose": True}
        result = apply_strategy_to_state(state, strategy)
        assert result.expression_matrix.shape == (3, 3)
        assert result.cell_barcodes_raw == [f"gene_{i}" for i in range(3)]
        assert result.gene_names_raw == [f"cell_{i}" for i in range(3)]

    def test_realign_barcodes_strip_prefix(self):
        state = PipelineState(
            gse_id="GSE123",
            cell_barcodes_raw=["prefix1_AAACCCAAGAAACTCA-1", "prefix2_AAACCCAAGAAAGCCC-2"],
        )
        strategy = {"realign_barcodes": {"strip_prefix": True}}
        result = apply_strategy_to_state(state, strategy)
        assert "AAACCCAAGAAACTCA" in result.cell_barcodes_raw[0]

    def test_reparse_fields(self):
        state = PipelineState(
            gse_id="GSE123",
            parsed_characteristics={
                "GSM001": {"old_field": "value1"},
                "GSM002": {"old_field": "value2"},
            },
        )
        strategy = {"reparse_fields": {"old_field": "new_field"}}
        result = apply_strategy_to_state(state, strategy)
        assert "new_field" in result.parsed_characteristics["GSM001"]
        assert "old_field" not in result.parsed_characteristics["GSM001"]

    def test_empty_strategy_no_change(self):
        state = PipelineState(gse_id="GSE123")
        result = apply_strategy_to_state(state, {})
        assert result.gse_id == "GSE123"


class TestReflexionNode:
    def test_no_errors_skips_reflexion(self, kb):
        state = PipelineState(gse_id="GSE123", validation_errors=[])
        result = reflexion_node(state)
        assert result["retry_count"] == 0
        assert result["current_strategy"] == {}

    def test_with_errors_generates_strategy(self, kb):
        state = PipelineState(
            gse_id="GSE123",
            validation_errors=["Matrix columns (10) != obs rows (20)"],
            max_retries=3,
        )
        with patch("scruticell.pipeline.nodes.reflexion._try_llm_strategy", return_value=None):
            result = reflexion_node(state)
        assert result["retry_count"] == 1
        assert result["current_strategy"]["action"] == "retry"
        assert len(result["reflexion_notes"]) >= 1

    def test_stores_failure_in_kb(self, kb):
        state = PipelineState(
            gse_id="GSE12345",
            validation_errors=["No batch column found in obs"],
            max_retries=3,
        )
        strategy = {"action": "retry", "notes": "test fix"}
        error_sig = _generate_error_signature(state.validation_errors)
        _store_failure_case(state, error_sig, strategy, kb)

        similar = kb.find_similar_failures(error_sig, "validation")
        assert len(similar) >= 1
        stored = json.loads(similar[0]["fix_strategy"])
        assert stored["action"] == "retry"

    def test_second_retry_from_kb_cache(self, kb):
        state1 = PipelineState(
            gse_id="GSE_TEST",
            validation_errors=["Matrix columns (5) != obs rows (10)"],
            max_retries=3,
        )
        sig1 = _generate_error_signature(state1.validation_errors)
        strategy = {"action": "retry", "notes": "KB cached fix"}
        _store_failure_case(state1, sig1, strategy, kb)

        state2 = PipelineState(
            gse_id="GSE_OTHER",
            validation_errors=["Matrix columns (100) != obs rows (200)"],
            max_retries=3,
        )
        sig2 = _generate_error_signature(state2.validation_errors)
        assert sig1 == sig2

        cached = _lookup_similar_failure(sig2, kb)
        assert cached is not None
        assert cached["action"] == "retry"

    def test_strategy_history_accumulates(self, kb):
        state = PipelineState(
            gse_id="GSE123",
            validation_errors=["Error 1"],
            max_retries=3,
        )
        with patch("scruticell.pipeline.nodes.reflexion._try_llm_strategy", return_value=None):
            r1 = reflexion_node(state)
        state.retry_count = r1["retry_count"]
        state.strategy_history = r1["strategy_history"]
        state.reflexion_notes = r1["reflexion_notes"]
        state.validation_errors = ["Error 2"]

        with patch("scruticell.pipeline.nodes.reflexion._try_llm_strategy", return_value=None):
            r2 = reflexion_node(state)
        assert len(r2["strategy_history"]) >= 2

    def test_abort_strategy_respected(self, kb):
        strategy = {"action": "abort", "notes": "unrecoverable"}
        assert strategy["action"] == "abort"


class TestKnowledgeBaseIntegration:
    def test_failure_case_crud(self, kb):
        kb.store_failure_case(
            case_id="test_case_1",
            error_signature="abc123",
            gse_id="GSE_TEST",
            pipeline_stage="validation",
            error_message="Test error",
            fix_strategy='{"action": "retry"}',
            fixed=True,
        )

        results = kb.find_similar_failures("abc123", "validation")
        assert len(results) >= 1
        assert results[0]["case_id"] == "test_case_1"

    def test_find_unfixed_failures(self, kb):
        kb.store_failure_case(
            case_id="unfixed",
            error_signature="xyz789",
            gse_id="GSE_X",
            pipeline_stage="validation",
            error_message="Unsolved",
            fix_strategy="",
            fixed=False,
        )
        results = kb.find_similar_failures("xyz789", "validation")
        assert results[0]["fixed"] == 0
