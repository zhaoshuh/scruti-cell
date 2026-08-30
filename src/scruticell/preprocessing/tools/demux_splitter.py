"""Demux splitting — LLM-generated code execution.

Splits merged multi-sample files into per-sample sub-files.
Path: LLM generates split_demux_file() code → subprocess sandbox → verify → retry.
If the LLM path fails (or no content preview is available), no rule-based
fallback is used; the caller routes the source file to unclassified.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT = 60
_MAX_RETRIES = 2


def _sanitize_code(code: str) -> tuple[bool, str]:
    forbidden = [
        "os.system", "subprocess", "socket", "requests", "httpx",
        "urllib", "__import__", "exec(", "eval(", "compile(",
        "importlib", "pickle", "shutil", "pathlib",
    ]
    for p in forbidden:
        if p in code:
            return False, f"Forbidden pattern: {p}"
    return True, ""


def _run_code_in_sandbox(
    code: str, source_path: str, output_dir: str, demux_rule: dict,
) -> tuple[bool, list[dict], str]:
    """Execute LLM-generated split_demux_file() in an isolated subprocess."""
    from scruticell.preprocessing.sandbox import run_code_in_subprocess

    ok, msg = _sanitize_code(code)
    if not ok:
        return False, [], msg

    out = run_code_in_subprocess(
        code,
        "split_demux_file",
        args=(source_path, output_dir, demux_rule),
        timeout=_SANDBOX_TIMEOUT,
    )
    if not out.get("ok"):
        return False, [], str(out.get("error", "sandbox failed"))
    result = out.get("result")
    if not isinstance(result, list):
        return False, [], f"Expected list, got {type(result).__name__}"
    return True, result, ""


def handle_demux_split(
    source_path: str,
    output_dir: str,
    demux_rule: dict,
    modality: str,
    content_preview: dict | None = None,
) -> list[dict]:
    """Split a merged multi-sample file using LLM-generated code.

    Primary path: LLM code gen → sandbox → verify → repair (retry).
    No rule-based fallback: on failure returns [] so the caller can
    route the source file to unclassified.
    """
    from scruticell.preprocessing.verification import verify_demux_results

    if not content_preview:
        logger.warning("Demux skipped: no content preview available for %s", source_path)
        return []

    os.makedirs(output_dir, exist_ok=True)

    # ── LLM code generation + sandbox + verify + retry ──
    for attempt in range(_MAX_RETRIES + 1):
        code = _generate_demux_code(demux_rule, modality, content_preview,
                                    error_context="" if attempt == 0 else "Retry")
        if not code:
            continue
        ok, result, err = _run_code_in_sandbox(code, source_path, output_dir, demux_rule)
        if not ok:
            logger.warning("Demux LLM code failed (attempt %d): %s", attempt, err[:300])
            continue
        verif = verify_demux_results(result, demux_rule)
        if verif.passed:
            logger.info("Demux LLM code: %d sub-files (attempt %d)", len(result), attempt)
            return result
        logger.warning("Demux verify failed (attempt %d): %s", attempt, verif.failure_summary())

    logger.warning("Demux split failed for %s; routing to unclassified", source_path)
    return []


def _generate_demux_code(
    demux_rule: dict,
    modality: str,
    content_preview: dict,
    error_context: str = "",
) -> str:
    """Call LLM to generate split_demux_file() code."""
    import json as _json

    from langchain_openai import ChatOpenAI

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    from scruticell.preprocessing.prompts import DEMUX_CODE_GENERATION_PROMPT

    cp_lines = content_preview.get("raw_lines", [])
    cp_display = "\n".join(cp_lines[:10])

    ctx = {
        "modality": modality,
        "file_type": content_preview.get("file_type", "unknown"),
        "columns": content_preview.get("columns", [])[:20],
        "feature_type": content_preview.get("feature_type", ""),
        "n_rows": content_preview.get("n_rows", 0),
        "n_cols": content_preview.get("n_cols", 0),
        "raw_lines": cp_display,
        "demux_rule": demux_rule,
    }

    prompt = DEMUX_CODE_GENERATION_PROMPT
    if error_context:
        prompt += f"\n\nPrevious attempt failed: {error_context}"

    try:
        llm = ChatOpenAI(
            model=LLM_MODEL, temperature=0.0,
            openai_api_key=LLM_API_KEY, openai_api_base=LLM_BASE_URL,
        )
        response = llm.invoke(prompt + "\n\n" + _json.dumps(ctx, ensure_ascii=False))
        code = str(response.content) if hasattr(response, "content") else str(response)
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        return code
    except Exception as e:
        logger.warning("Failed to generate demux code: %s", e)
        return ""
