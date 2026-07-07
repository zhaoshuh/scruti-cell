"""Reflexion node: analyzes failures and generates recovery strategies.

Implements the Reflexion pattern: when validation fails, this node:
1. Queries the knowledge graph for similar past failures and their fixes
2. If found, applies the stored fix strategy
3. If not found, uses LLM to analyze the error and propose a new strategy
4. Stores the new strategy/failure case in the knowledge graph for future learning
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from scruticell.pipeline.state import PipelineState
from scruticell.utils.tokens import invoke_and_track
from scruticell.utils.tracing import traceable

logger = logging.getLogger(__name__)

_KB_INSTANCE: Any = None


def set_knowledge_base(kb: Any) -> None:
    """Set the global knowledge base instance for reflexion lookups."""
    global _KB_INSTANCE
    _KB_INSTANCE = kb


def get_knowledge_base() -> Any:
    """Get the current knowledge base instance, creating one if needed."""
    global _KB_INSTANCE
    if _KB_INSTANCE is None:
        from scruticell.knowledge.graph import LocalKnowledgeBase
        _KB_INSTANCE = LocalKnowledgeBase()
    return _KB_INSTANCE


def reflexion_node(state: PipelineState) -> dict[str, Any]:
    """Analyze validation failures and propose recovery strategies.

    Process:
    1. Generate error signature from validation errors
    2. Query knowledge graph for similar past failures
    3a. If found: apply stored fix strategy, increment retry count
    3b. If not found: use LLM to diagnose and propose new strategy
    4. Store result in knowledge graph for future reference
    """
    logger.info("Reflexion analyzing failures for %s (retry %d)",
                state.gse_id, state.retry_count + 1)

    errors = state.validation_errors
    notes: list[str] = []
    new_strategy: dict[str, Any] = {}

    if not errors:
        notes.append("No validation errors to analyze — reflexion skipped")
        return {
            "current_strategy": {},
            "retry_count": state.retry_count,
            "reflexion_notes": state.reflexion_notes + notes,
            "strategy_history": state.strategy_history,
        }

    error_sig = _generate_error_signature(errors)
    notes.append(f"Error signature: {error_sig}")

    kb = get_knowledge_base()

    cached = _lookup_similar_failure(error_sig, kb)
    if cached:
        notes.append("Found past failure with stored fix")
        new_strategy = cached
        new_strategy["source"] = "knowledge_graph"
    else:
        notes.append("No prior fix — generating strategy")
        new_strategy = _generate_strategy(state, errors)
        new_strategy["source"] = "llm" if new_strategy.get("llm_generated") else "heuristic"

        _store_failure_case(state, error_sig, new_strategy, kb)

    state.strategy_history.append(new_strategy)

    return {
        "current_strategy": new_strategy,
        "retry_count": state.retry_count + 1,
        "reflexion_notes": state.reflexion_notes + notes,
        "strategy_history": state.strategy_history,
        "validation_errors": [],
    }


def _generate_error_signature(errors: list[str]) -> str:
    """Create a stable hash-based signature from validation errors.

    Normalizes specific values (GSE IDs, dimensions, file paths) so
    that similar error patterns from different datasets share a signature.
    """
    import re
    normalized_errors = []
    for e in errors:
        e = re.sub(r"GSE\d+", "GSE_NNN", e)
        e = re.sub(r"\(\d+\)", "(N)", e)
        e = re.sub(r"\d+\.\d+", "N.N", e)
        e = re.sub(r"'[^']+'", "'<VALUE>'", e)
        normalized_errors.append(e)
    normalized = json.dumps(sorted(normalized_errors), sort_keys=True)
    return hashlib.md5(normalized.encode()).hexdigest()[:12]


def _lookup_similar_failure(
    error_signature: str, kb: Any
) -> dict[str, Any] | None:
    """Query knowledge graph for similar past failures.

    Returns the stored fix strategy if found, or None.
    """
    try:
        similar = kb.find_similar_failures(error_signature, "validation")
        if similar:
            record = similar[0]
            fix_strategy_str = record.get("fix_strategy", "")
            if fix_strategy_str and record.get("fixed"):
                try:
                    return json.loads(fix_strategy_str)
                except (json.JSONDecodeError, TypeError):
                    return {
                        "action": "retry",
                        "notes": fix_strategy_str,
                        "kg_record": True,
                    }
    except Exception as exc:
        logger.debug("KG lookup failed: %s", exc)

    return None


def _generate_strategy(
    state: PipelineState, errors: list[str]
) -> dict[str, Any]:
    """Generate a recovery strategy from validation errors.

    Uses LLM if available, falls back to heuristic rules, then MCTS
    if prior strategies have been exhausted.
    """
    llm_strategy = _try_llm_strategy(state, errors)
    if llm_strategy:
        return llm_strategy

    if state.retry_count >= 2:
        mcts_strategy = _try_mcts_strategy(state, errors)
        if mcts_strategy:
            return mcts_strategy

    return _heuristic_strategy(state, errors)


@traceable(name="Reflexion.LLMStrategy", tags=["reflexion", "llm"])
def _try_llm_strategy(
    state: PipelineState, errors: list[str]
) -> dict[str, Any] | None:
    """Try to generate a strategy using LLM."""
    try:
        from langchain_openai import ChatOpenAI
        from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE

        prompt = _build_reflexion_prompt(state, errors)
        llm_kwargs: dict[str, Any] = {"model": LLM_MODEL, "temperature": LLM_TEMPERATURE}
        if LLM_BASE_URL:
            llm_kwargs["openai_api_base"] = LLM_BASE_URL
        if LLM_API_KEY:
            llm_kwargs["openai_api_key"] = LLM_API_KEY
        llm = ChatOpenAI(**llm_kwargs)
        response = invoke_and_track(llm, prompt, "reflexion_llm")
        content = str(response.content).strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1])
        return json.loads(content)

    except ImportError:
        logger.debug("langchain_openai not available for reflexion LLM")
    except Exception as exc:
        logger.warning("LLM reflexion failed: %s", exc)

    return None


def _build_reflexion_prompt(state: PipelineState, errors: list[str]) -> str:
    """Build the LLM prompt for reflexion analysis."""
    confidence_summary = json.dumps(state.confidence_scores, indent=2)

    return f"""You are analyzing a failed GEO data pipeline run and need to propose a recovery strategy.

GSE ID: {state.gse_id}
Organism: {state.organism}
Retry: {state.retry_count + 1}/{state.max_retries}

Validation errors:
{chr(10).join(f'  - {e}' for e in errors)}

Confidence scores:
{confidence_summary}

Current strategy history:
{json.dumps(state.strategy_history[-3:] if state.strategy_history else [], indent=2)}

Propose a recovery strategy as a JSON object with these possible keys:
- "action": always "retry" unless unrecoverable
- "reparse_fields": dict mapping raw_field -> force_canonical_name (e.g., {{"condition_1": "disease"}})
- "remap_genes": boolean, true to use more aggressive gene ID mapping
- "realign_barcodes": dict with barcode alignment overrides (e.g., {{"strip_prefix": true, "force_format": "10x_v3"}})
- "adjust_batch": dict with batch assignment hints (e.g., {{"use_gsm_title": true}})
- "force_transpose": boolean, true to force matrix transpose
- "abort": boolean, true if the error cannot be recovered
- "notes": string explaining the strategy
- "confidence_impact": float (0.0-1.0) estimating confidence improvement

Return ONLY the JSON object, no other text."""
    return prompt


def _heuristic_strategy(
    state: PipelineState, errors: list[str]
) -> dict[str, Any]:
    strategy: dict[str, Any] = {
        "action": "retry",
        "notes": "",
    }

    error_text = " ".join(errors).lower()

    if "dimension" in error_text or "!= obs" in error_text or "transpos" in error_text:
        strategy["force_transpose"] = True
        strategy["notes"] += "Force matrix transpose. "
        strategy["confidence_impact"] = 0.3

    if "negative" in error_text:
        strategy["notes"] += "Matrix contains negative values (may not be raw counts). "
        strategy["confidence_impact"] = 0.1

    if "metadata" in error_text or "column" in error_text or "sparse" in error_text:
        strategy["reparse_fields"] = {"characteristics_ch1": "manual_split"}
        strategy["notes"] += "Reparse metadata characteristics. "
        strategy["confidence_impact"] = strategy.get("confidence_impact", 0.0) + 0.2

    if "batch" in error_text:
        strategy["adjust_batch"] = {"use_gsm_title": True}
        strategy["notes"] += "Try GSM title for batch assignment. "
        strategy["confidence_impact"] = strategy.get("confidence_impact", 0.0) + 0.15

    if "barcode" in error_text or "cell" in error_text:
        strategy["realign_barcodes"] = {"strip_prefix": True}
        strategy["notes"] += "Strip barcode prefixes. "
        strategy["confidence_impact"] = strategy.get("confidence_impact", 0.0) + 0.15

    if not strategy.get("notes"):
        strategy["notes"] = f"General retry for {len(errors)} validation errors"
        strategy["confidence_impact"] = 0.1

    return strategy


def _try_mcts_strategy(
    state: PipelineState, errors: list[str]
) -> dict[str, Any] | None:
    """Use MCTS to search for an optimal strategy combination.

    Activated when heuristic and LLM strategies have been exhausted
    (retry_count >= 2). Builds a state snapshot and runs MCTS over
    strategy templates to find the best combination.

    Returns:
        MCTS-generated strategy dict, or None if search fails.
    """
    try:
        from scruticell.mcts import search

        snapshot = {
            "expression_matrix": state.expression_matrix,
            "cell_barcodes_raw": list(state.cell_barcodes_raw),
            "gene_names_raw": list(state.gene_names_raw),
            "parsed_characteristics": dict(state.parsed_characteristics),
            "obs": state.obs,
            "gse_id": state.gse_id,
            "organism": state.organism,
        }

        result = search(state, snapshot, max_iterations=20)
        if result and result.get("action"):
            result["source"] = "mcts"
            result["llm_generated"] = True
            logger.info("MCTS found strategy with score=%.3f",
                         result.get("mcts_score", 0))
            return result
    except ImportError:
        logger.debug("MCTS module not available")
    except Exception as exc:
        logger.warning("MCTS search failed: %s", exc)

    return None


def _store_failure_case(
    state: PipelineState,
    error_signature: str,
    strategy: dict[str, Any],
    kb: Any,
) -> None:
    """Store the failure case and strategy in the knowledge graph."""
    error_message = "; ".join(state.validation_errors[:3])
    fix_strategy_json = json.dumps(strategy)

    try:
        case_id = f"{state.gse_id}_{error_signature[:8]}"
        kb.store_failure_case(
            case_id=case_id,
            error_signature=error_signature,
            gse_id=state.gse_id,
            pipeline_stage="validation",
            error_message=error_message,
            fix_strategy=fix_strategy_json,
            fixed=bool(strategy.get("action") == "retry"),
        )
        logger.info("Stored failure case %s in knowledge graph", case_id)
    except Exception as exc:
        logger.warning("Failed to store failure case: %s", exc)


def apply_strategy_to_state(
    state: PipelineState, strategy: dict[str, Any]
) -> PipelineState:
    """Apply a reflexion strategy to the pipeline state for retry.

    Modifies state in place to reflect strategy changes.
    """
    if not strategy:
        return state

    if strategy.get("force_transpose") and state.expression_matrix is not None:
        from scipy import sparse as sp
        matrix = state.expression_matrix
        if sp.issparse(matrix):
            matrix = matrix.T.tocsr()
        else:
            import numpy as np
            matrix = np.asarray(matrix).T
        state.expression_matrix = matrix
        state.cell_barcodes_raw, state.gene_names_raw = (
            state.gene_names_raw, state.cell_barcodes_raw
        )
        state.matrix_shape = matrix.shape
        logger.info("Applied force_transpose strategy")

    if strategy.get("realign_barcodes"):
        barcode_opts = strategy["realign_barcodes"]
        if barcode_opts.get("strip_prefix"):
            from scruticell.alignment.barcodes import normalize_barcodes
            state.cell_barcodes_raw = normalize_barcodes(
                state.cell_barcodes_raw, fmt="10x"
            )
            logger.info("Applied barcode strip_prefix strategy")

    if strategy.get("reparse_fields"):
        field_overrides = strategy["reparse_fields"]
        for gsm_id, chars in state.parsed_characteristics.items():
            for raw, canonical in field_overrides.items():
                if raw in chars and canonical not in chars:
                    chars[canonical] = chars.pop(raw)
        logger.info("Applied field reparse strategy: %s", list(field_overrides.keys()))

    return state
