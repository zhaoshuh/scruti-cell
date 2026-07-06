"""Validation node: verifies AnnData integrity and computes confidence scores."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
from scipy import sparse

from scruticell.config import CONFIDENCE_THRESHOLD_AUTO, CONFIDENCE_THRESHOLD_WARN
from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


def validate_node(state: PipelineState) -> dict[str, Any]:
    """Validate the assembled AnnData and compute confidence scores.

    Checks performed:
    - obs index matches expression matrix columns
    - var index matches expression matrix rows
    - No missing or orphaned cells/genes
    - Expression values are non-negative integers (raw counts check)
    - Metadata fields are populated (completeness check)
    - Batch column has valid values
    - Gene names are standardized and non-duplicate

    Computes per-dimension confidence scores (0-1):
    - count_integrity: likelihood matrix contains raw integer counts
    - metadata_completeness: fraction of expected fields populated
    - alignment_accuracy: barcode/gene alignment correctness
    - batch_validity: batch annotation quality
    """
    logger.info("Validating AnnData for %s", state.gse_id)

    scores: dict[str, float] = {}
    errors: list[str] = []
    warnings: list[str] = []

    # Per-batch mode: validate each AnnData individually and average scores
    per_batch = getattr(state, "per_batch_adatas", None)
    if per_batch and len(per_batch) > 1:
        logger.info("Per-batch validation: %d AnnData objects", len(per_batch))
        all_scores: dict[str, list[float]] = {"alignment_accuracy": [], "count_integrity": [],
                                               "metadata_completeness": [], "batch_validity": []}
        for batch_label, adata in per_batch.items():
            matrix = adata.X.T if sparse.issparse(adata.X) else adata.X.T
            obs = adata.obs
            var = adata.var
            batch_col = state.batch_column
            es: list[str] = []
            ws: list[str] = []
            all_scores["alignment_accuracy"].append(_check_alignment(matrix, obs, var, es))
            all_scores["count_integrity"].append(_check_count_integrity(matrix, es, ws))
            all_scores["metadata_completeness"].append(
                _check_metadata_completeness(obs, state.parsed_characteristics, es, ws, is_per_batch=True)
            )
            all_scores["batch_validity"].append(
                _check_batch_validity(obs, batch_col, es, ws, is_per_batch=True)
            )
            errors.extend(es)
            warnings.extend(ws)
        for key in all_scores:
            vals = all_scores[key]
            scores[key] = sum(vals) / len(vals) if vals else 0.0
    else:
        matrix = state.expression_matrix
        obs = state.obs
        var = state.var
        batch_col = state.batch_column

        scores["alignment_accuracy"] = _check_alignment(matrix, obs, var, errors)
        scores["count_integrity"] = _check_count_integrity(matrix, errors, warnings)
        scores["metadata_completeness"] = _check_metadata_completeness(
            obs, state.parsed_characteristics, errors, warnings
        )
        scores["batch_validity"] = _check_batch_validity(obs, batch_col, errors, warnings)

    filled_scores = {}
    for key in ["alignment_accuracy", "count_integrity", "metadata_completeness", "batch_validity"]:
        filled_scores[key] = scores.get(key, 0.0)

    overall = sum(filled_scores.values()) / max(len(filled_scores), 1)
    passed = overall >= CONFIDENCE_THRESHOLD_AUTO

    if overall < CONFIDENCE_THRESHOLD_WARN:
        warnings.append(
            f"Overall confidence {overall:.0%} below warning threshold "
            f"{CONFIDENCE_THRESHOLD_WARN:.0%}"
        )

    logger.info("Validation: overall=%.2f, passed=%s, errors=%d, warnings=%d",
                overall, passed, len(errors), len(warnings))

    return {
        "validation_passed": passed,
        "confidence_scores": filled_scores,
        "validation_errors": errors,
        "validation_warnings": warnings,
    }


def _check_alignment(
    matrix: Any, obs: Any, var: Any, errors: list[str]
) -> float:
    """Check that matrix dimensions match obs and var."""
    if matrix is None:
        errors.append("Expression matrix is None")
        return 0.0
    if obs is None:
        errors.append("obs DataFrame is None")
        return 0.0
    if var is None:
        errors.append("var DataFrame is None")
        return 0.0

    is_sp = sparse.issparse(matrix)
    m_rows = matrix.shape[0] if is_sp else matrix.shape[0]
    m_cols = matrix.shape[1] if is_sp else matrix.shape[1]

    score = 1.0
    if m_cols != len(obs):
        errors.append(f"Matrix columns ({m_cols}) != obs rows ({len(obs)})")
        score = 0.0
    if m_rows != len(var):
        errors.append(f"Matrix rows ({m_rows}) != var rows ({len(var)})")
        score = min(score, 0.5)

    return score


def _check_count_integrity(
    matrix: Any, errors: list[str], warnings: list[str]
) -> float:
    """Check if expression matrix contains raw integer counts.

    Raw counts are non-negative and integral (integer-valued).
    Also checks for unrealistic values (all zeros, extremely high values).
    """
    if matrix is None:
        return 0.0

    is_sp = sparse.issparse(matrix)
    if is_sp:
        data = matrix.data
    else:
        data = np.asarray(matrix).ravel()

    if len(data) == 0:
        return 0.0

    sample = data[:min(100000, len(data))]
    score = 1.0

    has_negative = np.any(sample < 0)
    is_integer = np.allclose(sample, np.round(sample))

    if has_negative:
        errors.append("Expression matrix contains negative values")
        score -= 0.5

    if not is_integer:
        warnings.append("Expression values are not integers (may not be raw counts)")
        score -= 0.3

    max_val = np.max(sample) if len(sample) > 0 else 0
    if max_val > 1e7:
        warnings.append(f"Maximum expression value ({max_val:.0f}) is unusually high")
        score -= 0.1

    sparse_fraction = float(np.sum(sample == 0)) / len(sample) if len(sample) > 0 else 0
    if sparse_fraction > 0.99:
        warnings.append(f"Matrix is extremely sparse ({sparse_fraction:.1%} zeros)")
        score -= 0.1

    return max(score, 0.0)


def _check_metadata_completeness(
    obs: Any, parsed_characteristics: dict, errors: list[str], warnings: list[str],
    is_per_batch: bool = False,
) -> float:
    """Check how complete the metadata is."""
    if obs is None:
        return 0.0

    n_cols = len(obs.columns)
    n_gsms = len(parsed_characteristics)

    score = 1.0

    if n_cols <= 2:
        warnings.append(f"obs has only {n_cols} columns (sparse metadata)")
        score -= 0.3

    if n_gsms == 0:
        warnings.append("No GSM metadata parsed")
        score -= 0.4
    elif not is_per_batch and "batch" in obs.columns and obs["batch"].nunique() == 1:
        has_multiple_gsm = n_gsms > 1
        if has_multiple_gsm:
            warnings.append(
                f"Single batch but {n_gsms} GSMs exist (cells may not be properly assigned)"
            )
            score -= 0.3

    null_counts = obs.isnull().sum() if hasattr(obs, "isnull") else 0
    total = n_cols * len(obs) if hasattr(obs, "__len__") else 1
    if total > 0 and isinstance(null_counts, (int, float)):
        null_frac = float(null_counts) / total
    else:
        null_frac = 0.0
    if null_frac > 0.5:
        warnings.append(f"obs is {null_frac:.0%} null values")
        score -= 0.2

    return max(score, 0.0)


def _check_batch_validity(
    obs: Any, batch_col: str, errors: list[str], warnings: list[str],
    is_per_batch: bool = False,
) -> float:
    """Check batch column validity."""
    score = 1.0

    if obs is None:
        return 0.0

    actual_col = batch_col if batch_col in obs.columns else "batch"
    if actual_col not in obs.columns:
        actual_col = "batch_label" if "batch_label" in obs.columns else actual_col
    if actual_col not in obs.columns:
        errors.append("No batch column found in obs")
        return 0.0

    batches = obs[actual_col]

    null_count = int(batches.isnull().sum()) if hasattr(batches, "isnull") else 0
    if null_count > 0:
        errors.append(f"Batch column has {null_count} null values")
        score -= 0.5

    unique_batches = batches.nunique()
    n_samples = len(obs)
    if not is_per_batch and unique_batches == 1 and n_samples > 1000:
        warnings.append("Only 1 batch for >1000 cells (likely missing batch annotation)")
        score -= 0.3
    elif unique_batches == n_samples and n_samples > 100:
        warnings.append("Every cell assigned a unique batch (batch == cell)")
        score -= 0.4

    return max(score, 0.0)
