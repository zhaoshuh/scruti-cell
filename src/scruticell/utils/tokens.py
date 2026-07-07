"""Token usage tracking for LLM calls across the pipeline.

Lightweight singleton tracker that records input/output token counts
per step and produces a formatted report at pipeline completion.
Works with DeepSeek, OpenAI, and any OpenAI-compatible API.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TokenRecord:
    step: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class TokenTracker:
    """Singleton accumulator for token usage across the pipeline."""

    _instance: TokenTracker | None = None

    def __new__(cls) -> TokenTracker:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._records: list[TokenRecord] = []
            cls._instance._start_time: float = time.monotonic()
        return cls._instance

    def record(
        self,
        step: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float = 0.0,
    ) -> None:
        self._records.append(
            TokenRecord(
                step=step,
                model=model or "",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                duration_ms=duration_ms,
            )
        )

    def reset(self) -> None:
        self._records.clear()
        self._start_time = time.monotonic()

    def to_csv(self, path: str) -> None:
        """Save all token records as CSV.

        Columns: step, model, input_tokens, output_tokens, total_tokens, duration_ms
        """
        if not self._records:
            return
        import csv
        from pathlib import Path
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["step", "model", "input_tokens", "output_tokens",
                         "total_tokens", "duration_ms"])
            for r in self._records:
                w.writerow([
                    r.step, r.model, r.input_tokens, r.output_tokens,
                    r.total_tokens, f"{r.duration_ms:.0f}",
                ])
        logger.info("Token usage CSV saved: %s", p)

    def report(self) -> str:
        if not self._records:
            return "[TokenTracker] No LLM calls recorded."

        lines: list[str] = [
            "",
            "=" * 72,
            "  Token Usage Report",
            "=" * 72,
            f"  {'Step':<28s} {'Calls':>5s} "
            f"{'Input':>10s} {'Output':>9s} "
            f"{'Total':>10s} {'Time':>6s}",
            "  " + "-" * 68,
        ]

        # Aggregate by step
        aggregated: dict[str, dict[str, Any]] = {}
        for r in self._records:
            key = r.step
            if key not in aggregated:
                aggregated[key] = {"calls": 0, "input": 0, "output": 0, "total": 0, "dur": 0.0}
            agg = aggregated[key]
            agg["calls"] += 1
            agg["input"] += r.input_tokens
            agg["output"] += r.output_tokens
            agg["total"] += r.total_tokens
            agg["dur"] += r.duration_ms

        # Phase totals
        phases = {
            "INSPECT (5-step)": ["step1_flowchart", "step2_gsm", "step3_file",
                                 "step4_metadata", "step5a_batch", "step5b_matrix"],
            "RE-PLAN": ["step5a_replan", "step5b_replan"],
            "RETRY": ["step_retry"],
            "PLAN": ["plan_generate", "plan_validate", "plan_literature"],
            "EXECUTE": ["execute_reflect"],
            "REFLEXION": ["reflexion_llm"],
            "LITERATURE": ["literature_rag", "literature_extract"],
        }

        grand_total = {"calls": 0, "input": 0, "output": 0, "total": 0, "dur": 0.0}
        seen_steps: set[str] = set()

        for phase, step_names in phases.items():
            phase_input = 0
            phase_output = 0
            phase_total = 0
            phase_calls = 0
            phase_dur = 0.0

            for sn in step_names:
                agg = aggregated.get(sn)
                if agg and agg["calls"] > 0:
                    phase_calls += agg["calls"]
                    phase_input += agg["input"]
                    phase_output += agg["output"]
                    phase_total += agg["total"]
                    phase_dur += agg["dur"]
                    seen_steps.add(sn)
                    lines.append(
                        f"  {sn:<28s} {agg['calls']:>5d} "
                        f"{_fmt_tokens(agg['input']):>10s} "
                        f"{_fmt_tokens(agg['output']):>9s} "
                        f"{_fmt_tokens(agg['total']):>10s} "
                        f"{agg['dur'] / 1000:>5.1f}s"
                    )

            if phase_calls > 0:
                lines.append("  " + "-" * 68)
                lines.append(
                    f"  {phase:<28s} {phase_calls:>5d} "
                    f"{_fmt_tokens(phase_input):>10s} "
                    f"{_fmt_tokens(phase_output):>9s} "
                    f"{_fmt_tokens(phase_total):>10s} "
                    f"{phase_dur / 1000:>5.1f}s"
                )
                lines.append("")
                grand_total["calls"] += phase_calls
                grand_total["input"] += phase_input
                grand_total["output"] += phase_output
                grand_total["total"] += phase_total
                grand_total["dur"] += phase_dur

        # Un-categorized steps
        for sn, agg in sorted(aggregated.items()):
            if sn not in seen_steps and agg["calls"] > 0:
                grand_total["calls"] += agg["calls"]
                grand_total["input"] += agg["input"]
                grand_total["output"] += agg["output"]
                grand_total["total"] += agg["total"]
                grand_total["dur"] += agg["dur"]

        lines.append("  " + "=" * 68)
        lines.append(
            f"  {'TOTAL':28s} {grand_total['calls']:>5d} "
            f"{_fmt_tokens(grand_total['input']):>10s} "
            f"{_fmt_tokens(grand_total['output']):>9s} "
            f"{_fmt_tokens(grand_total['total']):>10s} "
            f"{grand_total['dur'] / 1000:>5.1f}s"
        )
        lines.append("=" * 72)

        return "\n".join(lines)


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def invoke_and_track(
    llm: Any,
    messages: Any,
    step: str,
    model: str = "",
) -> Any:
    """Invoke an LLM and automatically record token usage.

    Wraps llm.invoke() to extract token_usage from response.response_metadata.
    Compatible with LangChain ChatOpenAI. Works with DeepSeek, OpenAI, and
    any OpenAI-compatible provider.

    Args:
        llm: ChatOpenAI instance or compatible.
        messages: Messages to pass to llm.invoke().
        step: Human-readable step name for reporting (e.g. "step1_flowchart").
        model: Model name override (optional, inferred from llm if empty).

    Returns:
        The raw LLM response (AIMessage), unmodified.
    """
    start = time.monotonic()
    try:
        response = llm.invoke(messages)
    except Exception:
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000
        # record zero-token entry — the caller's finally block may overwrite
        # with actual counts if the call succeeded
        pass

    duration_ms = (time.monotonic() - start) * 1000

    meta = getattr(response, "response_metadata", None) or {}
    usage = meta.get("token_usage") or meta.get("usage") or {}

    input_tokens = int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or 0
    )
    output_tokens = int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or 0
    )

    resolved_model = model or getattr(llm, "model_name", "") or ""
    if not resolved_model:
        resolved_model = getattr(llm, "model", "") or ""

    TokenTracker().record(
        step=step,
        model=str(resolved_model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        duration_ms=duration_ms,
    )

    return response
