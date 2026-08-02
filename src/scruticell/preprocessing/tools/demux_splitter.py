"""Demux splitting — LLM-generated code execution + fallback heuristics.

Splits merged multi-sample files into per-sample sub-files.
Primary path: LLM generates split_demux_file() code → sandbox → verify → repair → fallback.
"""

from __future__ import annotations

import logging
import os
import re
import traceback
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT = 60
_MAX_RETRIES = 2


def _sanitize_code(code: str) -> tuple[bool, str]:
    _FORBIDDEN = [
        "os.system", "subprocess", "socket", "requests", "httpx",
        "urllib", "__import__", "exec(", "eval(", "compile(",
        "importlib", "pickle", "shutil", "pathlib",
    ]
    for p in _FORBIDDEN:
        if p in code:
            return False, f"Forbidden pattern: {p}"
    return True, ""


def _run_code_in_sandbox(
    code: str, source_path: str, output_dir: str, demux_rule: dict,
) -> tuple[bool, list[dict], str]:
    """Execute LLM-generated split_demux_file() in restricted sandbox."""
    ok, msg = _sanitize_code(code)
    if not ok:
        return False, [], msg

    namespace: dict[str, Any] = {}
    try:
        exec(code, namespace)
    except Exception as e:
        return False, [], f"Compilation failed: {e}"

    func = namespace.get("split_demux_file")
    if func is None or not callable(func):
        return False, [], "split_demux_file() not found"

    result: list[dict] = []
    error: str = ""

    def _run():
        nonlocal result, error
        try:
            os.makedirs(output_dir, exist_ok=True)
            output = func(source_path, output_dir, demux_rule)
            if isinstance(output, list):
                result = output
            else:
                error = f"Expected list, got {type(output).__name__}"
        except Exception as e:
            error = f"{e}\n{traceback.format_exc()[-500:]}"

    thread = threading.Thread(target=_run)
    thread.daemon = True
    thread.start()
    thread.join(timeout=_SANDBOX_TIMEOUT)

    if thread.is_alive():
        return False, [], f"Timed out ({_SANDBOX_TIMEOUT}s)"
    if error:
        return False, [], error
    return True, result, ""


def _heuristic_fallback(
    source_path: str, output_dir: str, demux_rule: dict,
) -> list[dict]:
    """Fallback CSV/TSV splitting when LLM code generation fails."""
    import pandas as pd

    os.makedirs(output_dir, exist_ok=True)
    ext = Path(source_path).suffix
    if ext in (".gz",):
        stem = Path(source_path).stem
        ext = Path(stem).suffix + ".gz"

    df = pd.read_csv(source_path, sep=None, engine="python", nrows=None, dtype=str)
    method = demux_rule.get("method", "")
    pattern = demux_rule.get("identifier_pattern", "")
    results: list[dict] = []

    if method == "column_split" and pattern:
        sample_cols: dict[str, list[str]] = {}
        other_cols: list[str] = []
        for col in df.columns:
            m = re.search(pattern, str(col)) if pattern else None
            if m:
                sample_cols.setdefault(m.group(0), []).append(col)
            else:
                other_cols.append(col)
        for sample_id, cols in sample_cols.items():
            sub = df[other_cols + cols]
            out = str(Path(output_dir) / f"{sample_id}{ext}")
            sub.to_csv(out, index=False)
            results.append({"sample_id": sample_id, "output_file": out})

    elif method == "row_split":
        value_col = demux_rule.get("value_column", "")
        if value_col and value_col in df.columns:
            for val, group in df.groupby(value_col):
                m = re.search(pattern, str(val)) if pattern else None
                sid = m.group(0) if m else str(val)
                out = str(Path(output_dir) / f"{sid}{ext}")
                group.to_csv(out, index=False)
                results.append({"sample_id": sid, "output_file": out})

    elif method == "barcode_split" and pattern:
        barcode_col = demux_rule.get("barcode_column", "")
        if barcode_col and barcode_col in df.columns:
            df["_demux_sample_id"] = df[barcode_col].apply(
                lambda x: re.search(pattern, str(x)).group(0) if re.search(pattern, str(x)) else None
            )
            for sid, group in df.dropna(subset=["_demux_sample_id"]).groupby("_demux_sample_id"):
                out = str(Path(output_dir) / f"{sid}{ext}")
                group.drop(columns=["_demux_sample_id"]).to_csv(out, index=False)
                results.append({"sample_id": sid, "output_file": out})

    return results


def handle_demux_split(
    source_path: str,
    output_dir: str,
    demux_rule: dict,
    modality: str,
    content_preview: dict | None = None,
) -> list[dict]:
    """Split a merged multi-sample file using LLM-generated code or fallback.

    Primary path: LLM code gen → sandbox → verify → repair → fallback.
    Fallback path: heuristic CSV/TSV splitting.
    """
    from scruticell.preprocessing.verification import verify_demux_results

    # ── Try LLM code generation + sandbox + repair ──
    if content_preview:
        for attempt in range(_MAX_RETRIES + 1):
            code = _generate_demux_code(demux_rule, modality, content_preview,
                                        error_context="" if attempt == 0 else "Retry")
            if not code:
                continue
            ok, result, err = _run_code_in_sandbox(code, source_path, output_dir, demux_rule)
            if ok:
                verif = verify_demux_results(result, demux_rule)
                if verif.passed:
                    logger.info("Demux LLM code: %d sub-files (attempt %d)", len(result), attempt)
                    return result
                logger.warning("Demux verify failed (attempt %d): %s", attempt, verif.failure_summary())
            else:
                logger.warning("Demux LLM code failed (attempt %d): %s", attempt, err)

    # ── Fallback ──
    result: list[dict] = []
    try:
        result = _heuristic_fallback(source_path, output_dir, demux_rule)
        if result:
            logger.info("Demux fallback: %d sub-files", len(result))
    except Exception as e:
        logger.warning("Demux fallback failed: %s", e)

    return result


def _generate_demux_code(
    demux_rule: dict,
    modality: str,
    content_preview: dict,
    error_context: str = "",
) -> str:
    """Call LLM to generate split_demux_file() code."""
    import json as _json
    from langchain_openai import ChatOpenAI
    from scruticell.config import LLM_MODEL, LLM_API_KEY, LLM_BASE_URL
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
