"""LangSmith tracing integration for Scrut-Cell.

Provides:
- Auto-instrumentation of all ChatOpenAI calls (via LANGCHAIN_TRACING_V2 env)
- @traceable decorator for custom pipeline functions
- RunTree for hierarchical trace organization
- Metadata helper for attaching dataset context to traces

Usage:
    # Set environment variables (once):
    # LANGCHAIN_TRACING_V2=true
    # LANGCHAIN_API_KEY=lsv2_pt_...
    # LANGCHAIN_PROJECT=scruiti-cell

    # Auto-tracing: ChatOpenAI calls are automatically traced
    # Custom tracing: use @traceable or TraceContext

    from scruticell.utils.tracing import traceable, TraceContext
"""

from __future__ import annotations

import functools
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

LANGSMITH_AVAILABLE = False
try:
    import langsmith
    from langsmith import traceable as _ls_traceable
    from langsmith.run_trees import RunTree
    LANGSMITH_AVAILABLE = True
    logger.info("LangSmith tracing enabled (project: %s)", os.environ.get("LANGCHAIN_PROJECT", "unknown"))
except ImportError:
    logger.debug("langsmith not installed; tracing disabled")


def traceable(
    name: Optional[str] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
) -> Callable:
    """Decorator that wraps a function for LangSmith tracing.

    Falls back to a no-op if langsmith is not installed.

    Args:
        name: Run name in LangSmith (defaults to function name).
        metadata: Key-value metadata attached to the trace.
        tags: Tags for filtering in LangSmith UI.
    """
    if not LANGSMITH_AVAILABLE:
        return lambda fn: fn

    kwargs: dict = {}
    if name:
        kwargs["name"] = name
    if metadata:
        kwargs["metadata"] = metadata
    if tags:
        kwargs["tags"] = tags

    return _ls_traceable(**kwargs)


class TraceContext:
    """Context manager for creating hierarchical trace runs.

    Usage:
        with TraceContext("PlanGeneration", metadata={"gse_id": "GSE334035"}) as ctx:
            ctx.log("strategy_notes", "Three HDF5 matrices...")
            # ... nested work ...
            ctx.log("confidence", 0.95)
    """

    def __init__(
        self,
        name: str,
        metadata: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        inputs: Optional[dict] = None,
    ):
        self.name = name
        self.metadata = metadata or {}
        self.tags = tags or []
        self.inputs = inputs or {}
        self._run: Any = None
        self._start_time: float = 0

    def __enter__(self) -> TraceContext:
        if not LANGSMITH_AVAILABLE:
            return self
        try:
            self._start_time = time.time()
            self._run = RunTree(
                name=self.name,
                run_type="chain",
                inputs=self.inputs,
                metadata=self.metadata,
                tags=self.tags,
            )
        except Exception as exc:
            logger.debug("TraceContext enter failed: %s", exc)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._run is None:
            return
        try:
            duration = time.time() - self._start_time
            if exc_type is not None:
                self._run.end(error=str(exc_val), outputs={"error": str(exc_val)})
            else:
                self._run.end(outputs={"duration_s": duration, "status": "success"})
            self._run.post()
        except Exception as exc:
            logger.debug("TraceContext exit post failed: %s", exc)

    def log(self, key: str, value: Any) -> None:
        """Write a key-value metadata entry to the current trace."""
        if self._run is not None:
            self.metadata[key] = value
            try:
                self._run.metadata = self.metadata
            except Exception:
                pass


def create_run(
    name: str,
    run_type: str = "chain",
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[list[str]] = None,
) -> Optional[Any]:
    """Create a standalone LangSmith RunTree.

    Returns None if langsmith is unavailable.
    """
    if not LANGSMITH_AVAILABLE:
        return None
    try:
        return RunTree(
            name=name,
            run_type=run_type,
            inputs=inputs or {},
            metadata=metadata or {},
            tags=tags or [],
        )
    except Exception as exc:
        logger.debug("create_run failed: %s", exc)
        return None


def is_enabled() -> bool:
    """Check if LangSmith tracing is active."""
    return LANGSMITH_AVAILABLE and os.environ.get("LANGCHAIN_TRACING_V2", "").lower() == "true"


def trace_llm_call(model: str, prompt: str, response: str, duration_ms: float, error: str = "") -> None:
    """Manually trace an LLM call for environments without langchain callback support."""
    if not LANGSMITH_AVAILABLE:
        return
    try:
        run = RunTree(
            name=f"LLM:{model}",
            run_type="llm",
            inputs={"messages": prompt[:2000]},
            outputs={"content": response[:2000]} if not error else None,
            error=error if error else None,
            extra={"duration_ms": duration_ms, "model": model},
        )
        run.end()
        run.post()
    except Exception as exc:
        logger.debug("trace_llm_call failed: %s", exc)
