"""LangGraph pipeline graph definition.

Builds the DAG for the GSE -> AnnData pipeline. The graph defines:
- Node execution order
- Conditional branching (validation -> retry or output)
- Parallel execution where possible (literature search alongside parsing)
- Reflexion loop for low-confidence results
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)

try:
    from langgraph.graph import END, StateGraph
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    END = "__end__"  # type: ignore
    logger.warning("langgraph not installed; pipeline execution unavailable")


def should_retry(state: PipelineState) -> Literal["reflexion", "output"]:
    """Conditional edge: decide whether to retry with reflexion or output."""
    if state.fatal_error:
        logger.error("Fatal error encountered, stopping pipeline")
        return "output"
    if state.validation_passed:
        logger.info("Validation passed, proceeding to output")
        return "output"
    if state.needs_reflexion() and state.can_retry():
        logger.warning(
            "Confidence %.2f below threshold, attempting reflexion (retry %d/%d)",
            state.overall_confidence(), state.retry_count + 1, state.max_retries,
        )
        return "reflexion"
    logger.warning(
        "Cannot retry (retries exhausted or reflexion not needed), outputting with warnings"
    )
    return "output"


def after_reflexion(state: PipelineState) -> Literal["parse_soft", "output"]:
    """After reflexion provides new strategy, decide where to restart."""
    if state.can_retry() and state.current_strategy:
        logger.info("Reflexion provided new strategy, restarting from parse")
        return "parse_soft"
    return "output"


def build_graph() -> Any:
    """Construct the full GSE -> AnnData pipeline graph.

    Returns a compiled StateGraph ready for invocation.

    Graph flow:
        download -> parse_soft -> discover_matrix -> read_matrix
                                     |
        literature (parallel) -------/
                                     |
        align_cells -> unify_genes -> validate
                                     |
                           [pass] -> output
                           [fail] -> reflexion -> parse_soft (retry)
                                                -> output (give up)
    """
    if not LANGGRAPH_AVAILABLE:
        raise ImportError(
            "langgraph is required for pipeline execution. "
            "Install with: pip install langgraph"
        )

    from scruticell.pipeline.nodes import (
        alignment,
        download,
        literature,
        matrix,
        parse,
        reflexion,
        validation,
    )

    workflow = StateGraph(PipelineState)

    # === Register nodes ===
    workflow.add_node("download", download.download_node)
    workflow.add_node("parse_soft", parse.parse_soft_node)
    workflow.add_node("discover_matrix", matrix.discover_matrix_node)
    workflow.add_node("read_matrix", matrix.read_matrix_node)
    workflow.add_node("literature", literature.literature_node)
    workflow.add_node("align_cells", alignment.align_cells_node)
    workflow.add_node("unify_genes", alignment.unify_genes_node)
    workflow.add_node("validate", validation.validate_node)
    workflow.add_node("reflexion", reflexion.reflexion_node)
    workflow.add_node("output", alignment.output_node)

    # === Define edges ===

    # Entry point
    workflow.set_entry_point("download")

    # Main linear flow
    workflow.add_edge("download", "parse_soft")
    workflow.add_edge("parse_soft", "discover_matrix")
    workflow.add_edge("discover_matrix", "read_matrix")

    # Literature runs in parallel after parse (metadata available)
    workflow.add_edge("parse_soft", "literature")

    # After matrix and literature both complete, proceed to alignment
    workflow.add_edge("read_matrix", "align_cells")
    workflow.add_edge("literature", "align_cells")
    workflow.add_edge("align_cells", "unify_genes")
    workflow.add_edge("unify_genes", "validate")

    # Conditional: pass -> output, fail -> reflexion
    workflow.add_conditional_edges(
        "validate",
        should_retry,
        {
            "output": "output",
            "reflexion": "reflexion",
        },
    )

    # After reflexion: retry from parse, or give up
    workflow.add_conditional_edges(
        "reflexion",
        after_reflexion,
        {
            "parse_soft": "parse_soft",
            "output": "output",
        },
    )

    # Output terminates
    workflow.add_edge("output", END)

    return workflow


def create_pipeline(checkpointer: Any = None) -> Any:
    """Create a compiled pipeline graph, optionally with persistence.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence

    Returns:
        Compiled StateGraph ready for .invoke() or .stream().
    """
    workflow = build_graph()
    if checkpointer:
        return workflow.compile(checkpointer=checkpointer)
    return workflow.compile()
