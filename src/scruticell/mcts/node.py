"""MCTS node representation for strategy search space.

Each node represents a pipeline state achieved by applying a sequence
of strategy actions. The tree explores combinations of discrete pipeline
configuration choices to maximize validation confidence.
"""

from __future__ import annotations

import math
from typing import Any


class MCTSNode:
    """A node in the MCTS search tree representing a strategy state.

    Attributes:
        strategy: The strategy dict applied to reach this node.
        state_snapshot: Optional snapshot of pipeline state for evaluation.
        parent: Parent node in the tree.
        children: Child nodes (not yet expanded or expanded).
        visits: Number of times this node was visited.
        total_score: Cumulative confidence score from rollouts.
        is_terminal: Whether this is a leaf node (all actions applied).
    """

    def __init__(
        self,
        strategy: dict[str, Any] | None = None,
        parent: MCTSNode | None = None,
    ):
        self.strategy = strategy or {}
        self.parent = parent
        self.children: list[MCTSNode] = []
        self.visits: int = 0
        self.total_score: float = 0.0
        self.is_terminal: bool = False

    @property
    def avg_score(self) -> float:
        """Average confidence score from this node's rollouts."""
        if self.visits == 0:
            return 0.0
        return self.total_score / self.visits

    def ucb1(self, total_visits: int, exploration_weight: float = 1.414) -> float:
        """Upper Confidence Bound (UCB1) for node selection.

        Balances exploitation (avg_score) with exploration (visit count).
        """
        if self.visits == 0:
            return float("inf")
        exploitation = self.avg_score
        exploration = exploration_weight * math.sqrt(
            math.log(total_visits) / self.visits
        )
        return exploitation + exploration

    def update(self, score: float) -> None:
        """Record a rollout result."""
        self.visits += 1
        self.total_score += score

    def add_child(self, child: MCTSNode) -> None:
        """Add a child node."""
        self.children.append(child)

    def is_leaf(self) -> bool:
        """A leaf node has no children."""
        return len(self.children) == 0

    def is_fully_expanded(self, available_actions: int) -> bool:
        """Check if all possible actions have been tried from this node."""
        return len(self.children) >= available_actions or self.is_terminal

    def depth(self) -> int:
        """Depth of this node in the tree (root = 0)."""
        if self.parent is None:
            return 0
        return self.parent.depth() + 1

    def get_path_strategies(self) -> list[dict[str, Any]]:
        """Get all strategies from root to this node."""
        path = []
        node = self
        while node is not None and node.strategy:
            path.append(node.strategy)
            node = node.parent
        return list(reversed(path))

    def __repr__(self) -> str:
        return (
            f"MCTSNode(visits={self.visits}, avg={self.avg_score:.3f}, "
            f"depth={self.depth()}, terminal={self.is_terminal})"
        )
