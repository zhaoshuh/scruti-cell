"""MCTS search for optimal pipeline strategy.

When heuristic and LLM-based reflexion strategies are exhausted (all
retries used, confidence still low), MCTS searches a combinatorial
space of parse/alignment strategies to find the best configuration.

The search space consists of discrete strategy actions organized as
templates. MCTS uses UCB1-based selection, template-driven expansion,
lightweight validation rollouts, and score backpropagation.
"""

from __future__ import annotations

import logging
import math
import random
from typing import Any, Callable

from scruticell.mcts.evaluate import quick_validate
from scruticell.mcts.node import MCTSNode

logger = logging.getLogger(__name__)

STRATEGY_TEMPLATES: list[dict[str, Any]] = [
    {"name": "default", "notes": "Default parsing (no changes)",
     "force_transpose": False, "category": "matrix"},
    {"name": "transpose", "notes": "Force matrix transpose",
     "force_transpose": True, "category": "matrix"},
    {"name": "barcode_strip", "notes": "Strip barcode suffixes",
     "realign_barcodes": {"strip_prefix": True}, "category": "alignment"},
    {"name": "barcode_10x_v2", "notes": "Force 10X v2 barcode format",
     "realign_barcodes": {"force_format": "10x_v2"}, "category": "alignment"},
    {"name": "barcode_10x_v3", "notes": "Force 10X v3 barcode format",
     "realign_barcodes": {"force_format": "10x_v3"}, "category": "alignment"},
    {"name": "gene_symbol", "notes": "Force gene symbol ID system",
     "gene_id_override": "symbol", "category": "genes"},
    {"name": "gene_ensembl", "notes": "Force Ensembl gene ID mapping",
     "gene_id_override": "ensembl_gene", "category": "genes"},
    {"name": "batch_title", "notes": "Use GSM title for batch",
     "adjust_batch": {"use_gsm_title": True}, "category": "batch"},
    {"name": "batch_file", "notes": "Use file name for batch",
     "adjust_batch": {"use_filename": True}, "category": "batch"},
    {"name": "reparse_fields", "notes": "Aggressive field re-parsing",
     "reparse_fields": {"characteristics_ch1": "deep_parse"}, "category": "metadata"},
    {"name": "drop_duplicates", "notes": "Drop duplicate genes (first occurrence)",
     "duplicate_strategy": "first", "category": "genes"},
    {"name": "mean_duplicates", "notes": "Average duplicate gene expression",
     "duplicate_strategy": "mean", "category": "genes"},
]

MAX_DEPTH = 4
EXPLORATION_WEIGHT = 1.414
MAX_ITERATIONS = 30


def search(
    initial_state: Any,
    state_snapshot: dict[str, Any],
    max_iterations: int = MAX_ITERATIONS,
    max_depth: int = MAX_DEPTH,
) -> dict[str, Any]:
    """Run MCTS search to find the best strategy for the pipeline state.

    Args:
        initial_state: The PipelineState object before applying any strategy.
        state_snapshot: Snapshot of key state attributes for evaluation.
        max_iterations: Maximum MCTS iterations (default 30).
        max_depth: Maximum search tree depth (default 4).

    Returns:
        Best strategy dict found, or empty dict if search fails.
    """
    if not initial_state or not state_snapshot:
        return {}

    root = MCTSNode(strategy={"name": "root", "notes": "Starting state"})
    available = _filter_relevant_templates(state_snapshot)

    for _ in range(max_iterations):
        node = _select(root, available)
        if not node.is_terminal:
            node = _expand(node, available, max_depth)
        score = _simulate(node, state_snapshot)
        _backpropagate(node, score)

    best_child = _best_child(root, 0.0)
    if best_child and best_child.strategy and best_child.avg_score > 0:
        combined = _merge_strategies(best_child.get_path_strategies())
        combined["mcts_score"] = best_child.avg_score
        combined["mcts_visits"] = best_child.visits
        logger.info("MCTS best strategy: score=%.3f, path=%s",
                     best_child.avg_score,
                     [s.get("name", "?") for s in best_child.get_path_strategies()])
        return combined

    return {}


def _filter_relevant_templates(
    state_snapshot: dict[str, Any]
) -> list[dict[str, Any]]:
    """Filter strategy templates relevant to the current state."""
    cell_count = len(state_snapshot.get("cell_barcodes_raw", []))
    gene_count = len(state_snapshot.get("gene_names_raw", []))

    relevant = []
    for tpl in STRATEGY_TEMPLATES:
        category = tpl.get("category", "")
        if category == "alignment" and cell_count < 2:
            continue
        if category == "genes" and gene_count < 2:
            continue
        relevant.append(tpl)
    return relevant


def _select(node: MCTSNode, available: list[dict[str, Any]]) -> MCTSNode:
    """Select a node to expand using UCB1.

    Traverses from root, always choosing the child with highest UCB1
    score, until reaching an unexpanded or terminal node.
    """
    while not node.is_terminal and not node.is_leaf():
        if not node.is_fully_expanded(len(available)):
            return node
        total_visits = sum(c.visits for c in node.children) + 1
        node = max(node.children, key=lambda c: c.ucb1(total_visits, EXPLORATION_WEIGHT))
    return node


def _expand(
    node: MCTSNode, available: list[dict[str, Any]], max_depth: int
) -> MCTSNode:
    """Expand the node by adding a new child with an untried strategy template."""
    if node.depth() >= max_depth:
        node.is_terminal = True
        return node

    tried_names = {c.strategy.get("name", "") for c in node.children}
    untried = [t for t in available if t.get("name", "") not in tried_names]

    if not untried:
        node.is_terminal = True
        return node

    chosen = random.choice(untried)
    child = MCTSNode(strategy=chosen, parent=node)
    node.add_child(child)
    return child


def _simulate(
    node: MCTSNode, state_snapshot: dict[str, Any]
) -> float:
    """Simulate (evaluate) a node by applying its strategy and scoring.

    Uses lightweight quick_validate to get an approximate confidence score
    without rebuilding the full AnnData object.
    """
    applied = _apply_template(state_snapshot, node.strategy)

    score = quick_validate(
        expression_matrix=applied.get("expression_matrix"),
        cell_barcodes=applied.get("cell_barcodes_raw", []),
        gene_names=applied.get("gene_names_raw", []),
        parsed_characteristics=applied.get("parsed_characteristics", {}),
        obs=applied.get("obs"),
    )
    return score


def _apply_template(
    snapshot: dict[str, Any], strategy: dict[str, Any]
) -> dict[str, Any]:
    """Apply a strategy template to a state snapshot, returning a new snapshot."""
    result = dict(snapshot)

    matrix = snapshot.get("expression_matrix")
    if strategy.get("force_transpose") and matrix is not None:
        from scipy import sparse as sp
        if sp.issparse(matrix):
            result["expression_matrix"] = matrix.T.tocsr()
        else:
            import numpy as np
            result["expression_matrix"] = np.asarray(matrix).T
        result["cell_barcodes_raw"] = snapshot.get("gene_names_raw", [])
        result["gene_names_raw"] = snapshot.get("cell_barcodes_raw", [])

    if strategy.get("realign_barcodes"):
        barcode_opts = strategy["realign_barcodes"]
        barcodes = list(snapshot.get("cell_barcodes_raw", []))
        if barcode_opts.get("strip_prefix"):
            barcodes = [b.split("_")[-1] if "_" in b else b for b in barcodes]
        barcodes = [b.upper() for b in barcodes]
        result["cell_barcodes_raw"] = barcodes

        if strategy.get("adjust_batch") and "obs" in snapshot:
            obs = snapshot["obs"]
            if obs is not None:
                if hasattr(obs, "copy"):
                    obs = obs.copy()
                if hasattr(obs, "columns") and "batch" in obs.columns and strategy["adjust_batch"].get("use_gsm_title"):
                    obs["batch"] = "corrected_batch"
                result["obs"] = obs

    if strategy.get("reparse_fields"):
        chars = dict(snapshot.get("parsed_characteristics", {}))
        for gsm_id in chars:
            chars[gsm_id] = dict(chars[gsm_id])
            chars[gsm_id]["reparsed"] = "true"
        result["parsed_characteristics"] = chars

    return result


def _backpropagate(node: MCTSNode, score: float) -> None:
    """Backpropagate the rollout score up the tree."""
    current = node
    while current is not None:
        current.update(score)
        current = current.parent


def _best_child(node: MCTSNode, exploration_weight: float = 0.0) -> MCTSNode | None:
    """Get the child with the highest average score (or UCB1 if weight > 0)."""
    if not node.children:
        return None
    if exploration_weight > 0:
        total = sum(c.visits for c in node.children) + 1
        return max(node.children, key=lambda c: c.ucb1(total, exploration_weight))
    return max(node.children, key=lambda c: c.avg_score)


def _merge_strategies(strategies: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge a path of strategies into a single combined strategy dict."""
    merged: dict[str, Any] = {"action": "retry", "notes": "MCTS combined strategy"}
    for s in strategies:
        for key in s:
            if key not in ("name", "notes", "category", "action", "source"):
                merged[key] = s[key]
        if s.get("notes"):
            merged["notes"] = merged.get("notes", "") + f" [{s['notes']}]"
    return merged
