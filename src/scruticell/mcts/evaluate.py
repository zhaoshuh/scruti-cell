"""Lightweight evaluation for MCTS rollouts without full AnnData rebuild.

Uses sampled validation checks that are fast enough for MCTS iterations
(typically < 1s per rollout), while still being correlated with the final
pipeline confidence score.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def quick_validate(
    expression_matrix: Any,
    cell_barcodes: list[str],
    gene_names: list[str],
    parsed_characteristics: dict[str, Any],
    obs: Any,
    n_samples: int = 1000,
) -> float:
    """Lightweight validation for MCTS rollouts.

    Uses sampled checks instead of full validation. Returns a confidence
    score 0-1 that approximates the full pipeline validation.

    Checks:
    - Matrix contains data (not empty, not all zeros)
    - Barcode/gene counts are plausible
    - Metadata has some content
    - Matrix values look like counts (non-negative, mostly integral)
    """
    score = 0.0
    n_checks = 0

    if expression_matrix is not None:
        from scipy import sparse as sp
        if sp.issparse(expression_matrix):
            data = expression_matrix.data
            n_elements = expression_matrix.nnz
        else:
            data = np.asarray(expression_matrix).ravel()
            n_elements = data.size

        if n_elements > 0:
            n_checks += 1
            sample_size = min(n_samples, n_elements)
            indices = np.random.choice(n_elements, sample_size, replace=False)
            sample = data[indices]

            if np.any(sample != 0):
                score += 0.2
            if np.all(sample >= 0):
                score += 0.3
            is_int = np.allclose(sample, np.round(sample))
            if is_int:
                score += 0.3
            if np.max(sample) < 1e7:
                score += 0.2

    if cell_barcodes and len(cell_barcodes) > 0:
        n_checks += 1
        if 10 <= len(cell_barcodes) <= 5_000_000:
            score += 1.0

    if gene_names and len(gene_names) > 0:
        n_checks += 1
        if 50 <= len(gene_names) <= 100_000:
            score += 1.0

    if parsed_characteristics:
        n_checks += 1
        n_gsms = len(parsed_characteristics)
        if n_gsms >= 1:
            score += 0.5
        fields_per_gsm = sum(len(v) for v in parsed_characteristics.values()) / max(n_gsms, 1)
        if fields_per_gsm >= 1:
            score += 0.5

    if obs is not None and hasattr(obs, "columns"):
        n_checks += 1
        if len(obs.columns) >= 2:
            score += 1.0

    return score / max(n_checks, 1)
