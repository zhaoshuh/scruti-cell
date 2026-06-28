"""Tests for MCTS search node, strategy templates, and quick evaluation."""

import numpy as np
import pytest

from scruticell.mcts.evaluate import quick_validate
from scruticell.mcts.node import MCTSNode
from scruticell.mcts.search import (
    STRATEGY_TEMPLATES,
    _apply_template,
    _best_child,
    _filter_relevant_templates,
    _merge_strategies,
    search,
)


class TestMCTSNode:
    def test_node_init(self):
        node = MCTSNode(strategy={"name": "test"})
        assert node.visits == 0
        assert node.total_score == 0.0
        assert node.avg_score == 0.0
        assert node.depth() == 0
        assert node.is_leaf()

    def test_update_and_avg(self):
        node = MCTSNode()
        node.update(0.5)
        node.update(0.9)
        assert node.visits == 2
        assert node.avg_score == 0.7

    def test_ucb1_unvisited_is_inf(self):
        node = MCTSNode()
        assert node.ucb1(10) == float("inf")

    def test_ucb1_visited(self):
        node = MCTSNode()
        node.update(0.8)
        score = node.ucb1(total_visits=10)
        assert score < float("inf")
        assert score > 0

    def test_depth_calculation(self):
        root = MCTSNode()
        child = MCTSNode(parent=root)
        grandchild = MCTSNode(parent=child)
        assert root.depth() == 0
        assert child.depth() == 1
        assert grandchild.depth() == 2

    def test_get_path_strategies(self):
        root = MCTSNode(strategy={"name": "root"})
        child = MCTSNode(strategy={"name": "child"}, parent=root)
        gc = MCTSNode(strategy={"name": "grandchild"}, parent=child)
        path = gc.get_path_strategies()
        assert len(path) == 3
        assert path[0]["name"] == "root"
        assert path[2]["name"] == "grandchild"

    def test_add_child(self):
        parent = MCTSNode()
        child = MCTSNode(parent=parent)
        parent.add_child(child)
        assert not parent.is_leaf()

    def test_is_fully_expanded(self):
        node = MCTSNode()
        assert not node.is_fully_expanded(3)
        for _ in range(3):
            node.add_child(MCTSNode(parent=node))
        assert node.is_fully_expanded(3)

    def test_terminal_node(self):
        node = MCTSNode()
        node.is_terminal = True
        assert node.is_fully_expanded(10)


class TestQuickValidate:
    def test_valid_matrix_scores_high(self):
        matrix = np.random.randint(0, 100, (100, 50)).astype(np.float32)
        score = quick_validate(
            matrix, ["cell_1"], ["gene_1"],
            {"GSM001": {"tissue": "blood"}},
            None,
        )
        assert score >= 0.5

    def test_all_zeros_scores_lower(self):
        matrix = np.zeros((10, 5), dtype=np.float32)
        score = quick_validate(
            matrix, ["cell_1"], ["gene_1"],
            {"GSM001": {"tissue": "blood"}},
            None,
        )
        assert score < 0.8

    def test_negative_values_reduce_score(self):
        matrix = np.array([[-1.0, 0], [0, 5.0]], dtype=np.float32)
        score = quick_validate(
            matrix, ["cell_1"], ["gene_1"],
            {"GSM001": {"tissue": "blood"}},
            None,
        )
        assert score < 0.7

    def test_many_genes_increases_score(self):
        matrix = np.ones((500, 10), dtype=np.float32)
        score = quick_validate(
            matrix, ["cell_1"], [f"gene_{i}" for i in range(500)],
            {"GSM001": {"tissue": "blood"}},
            None,
        )
        assert score > 0.6

    def test_few_genes_reduces_score(self):
        matrix = np.ones((3, 10), dtype=np.float32)
        score = quick_validate(
            matrix, ["cell_1"], ["g1", "g2", "g3"],
            {}, None,
        )
        assert score < 0.5

    def test_no_metadata_reduces_score(self):
        matrix = np.random.randint(0, 100, (100, 10)).astype(np.float32)
        score = quick_validate(matrix, ["c1"], ["g1"], {}, None)
        assert score < 1.0

    def test_empty_matrix_scores_zero(self):
        matrix = np.zeros((0, 0), dtype=np.float32)
        score = quick_validate(matrix, [], [], {}, None)
        assert score == 0.0


class TestSearch:
    def test_search_returns_strategy(self):
        matrix = np.random.randint(0, 100, (100, 50)).astype(np.float32)
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_TEST")
        snapshot = {
            "expression_matrix": matrix,
            "cell_barcodes_raw": [f"cell_{i}" for i in range(50)],
            "gene_names_raw": [f"gene_{i}" for i in range(100)],
            "parsed_characteristics": {"GSM001": {"tissue": "blood"}},
            "obs": None,
        }
        result = search(state, snapshot, max_iterations=15)
        assert result is not None

    def test_search_returns_action_retry(self):
        matrix = np.random.randint(0, 100, (100, 50)).astype(np.float32)
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_TEST")
        snapshot = {
            "expression_matrix": matrix,
            "cell_barcodes_raw": [f"cell_{i}" for i in range(50)],
            "gene_names_raw": [f"gene_{i}" for i in range(100)],
            "parsed_characteristics": {"GSM001": {"tissue": "blood"}},
            "obs": None,
        }
        result = search(state, snapshot, max_iterations=15)
        if result:
            assert result.get("action") == "retry"

    def test_search_with_empty_snapshot(self):
        from scruticell.pipeline.state import PipelineState
        state = PipelineState(gse_id="GSE_EMPTY")
        result = search(state, {}, max_iterations=5)
        assert result == {}

    def test_best_child_returns_highest_avg(self):
        root = MCTSNode()
        c1 = MCTSNode(strategy={"name": "a"}, parent=root)
        c1.update(0.3)
        c2 = MCTSNode(strategy={"name": "b"}, parent=root)
        c2.update(0.8)
        c3 = MCTSNode(strategy={"name": "c"}, parent=root)
        c3.update(0.5)
        root.children = [c1, c2, c3]
        best = _best_child(root)
        assert best.strategy["name"] == "b"

    def test_merge_strategies_combines(self):
        strategies = [
            {"name": "s1", "force_transpose": True, "notes": "Try transpose"},
            {"name": "s2", "realign_barcodes": {"strip_prefix": True}, "notes": "Strip"},
        ]
        merged = _merge_strategies(strategies)
        assert merged["force_transpose"] is True
        assert merged["realign_barcodes"] == {"strip_prefix": True}
        assert "Strip" in merged["notes"]

    def test_filter_templates_relevant(self):
        snapshot_good = {
            "cell_barcodes_raw": ["c1", "c2"],
            "gene_names_raw": ["g1", "g2", "g3"],
        }
        relevant = _filter_relevant_templates(snapshot_good)
        assert len(relevant) > 0

    def test_filter_excludes_irrelevant(self):
        snapshot = {
            "cell_barcodes_raw": [],
            "gene_names_raw": [],
        }
        relevant = _filter_relevant_templates(snapshot)
        assert all(t.get("category") not in ("alignment", "genes")
                   for t in relevant if t.get("category") in ("alignment", "genes"))


class TestApplyTemplate:
    def test_transpose(self):
        matrix = np.array([[1, 2], [3, 4], [5, 6]], dtype=np.float32)
        snapshot = {
            "expression_matrix": matrix,
            "cell_barcodes_raw": ["c1", "c2"],
            "gene_names_raw": ["g1", "g2", "g3"],
        }
        strategy = {"force_transpose": True}
        result = _apply_template(snapshot, strategy)
        assert result["expression_matrix"].shape == (2, 3)
        assert result["cell_barcodes_raw"] == ["g1", "g2", "g3"]
        assert result["gene_names_raw"] == ["c1", "c2"]

    def test_barcode_strip_prefix(self):
        snapshot = {
            "cell_barcodes_raw": ["pre_cell1", "pre_cell2"],
        }
        strategy = {"realign_barcodes": {"strip_prefix": True}}
        result = _apply_template(snapshot, strategy)
        assert "CELL1" == result["cell_barcodes_raw"][0]

    def test_reparse_fields(self):
        snapshot = {
            "parsed_characteristics": {"GSM001": {"tissue": "blood"}},
        }
        strategy = {"reparse_fields": {"characteristics_ch1": "deep_parse"}}
        result = _apply_template(snapshot, strategy)
        assert result["parsed_characteristics"]["GSM001"]["reparsed"] == "true"


class TestStrategyTemplates:
    def test_all_templates_have_required_fields(self):
        for tpl in STRATEGY_TEMPLATES:
            assert "name" in tpl
            assert "notes" in tpl
            assert "category" in tpl

    def test_template_count(self):
        assert len(STRATEGY_TEMPLATES) == 12
