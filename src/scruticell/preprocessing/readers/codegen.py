"""LLM-driven file reader code generator.

Generates Python reading functions for arbitrary bioinformatics file formats.
Uses content_preview as the ground truth, SKILL.md as baseline knowledge,
and sandbox execution with retry for validation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time

from typing import Any

logger = logging.getLogger(__name__)

_SANDBOX_TIMEOUT = 30
_MAX_RETRIES = 2

# Allowed imports in sandbox
_ALLOWED_IMPORTS = {
    "pandas", "numpy", "scipy", "anndata", "scanpy",
    "gzip", "csv", "re", "io", "json",
    "pyreadr", "loompy",
    "os.path",
}

# Forbidden patterns
_FORBIDDEN_PATTERNS = [
    "os.system", "subprocess", "socket", "requests", "httpx",
    "urllib", "__import__", "exec(", "eval(", "compile(",
    "open(", "write(", "remove(", "unlink(", "rmdir(",
    "importlib", "pickle",
]


def _build_fingerprint(content_preview: dict, modality: str, file_role: str) -> str:
    """Build stable cache key from file characteristics."""
    parts = [
        content_preview.get("file_type", ""),
        content_preview.get("feature_type", ""),
        str(content_preview.get("n_cols", 0)),
        str(content_preview.get("n_rows", 0)),
        modality,
        file_role,
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def _sanitize_code(code: str) -> tuple[bool, str]:
    """Check generated code for forbidden patterns."""
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in code:
            return False, f"Forbidden pattern detected: {pattern}"
    return True, ""


def _execute_sandbox(code: str, filepath: str, role: str) -> tuple[bool, dict, str]:
    """Execute generated reader code in an isolated subprocess sandbox.

    Returns (success, result_dict, error_message).
    """
    from scruticell.preprocessing.sandbox import run_code_in_subprocess

    func_name = f"read_{role}"
    out = run_code_in_subprocess(
        code,
        func_name,
        args=(filepath,),
        timeout=_SANDBOX_TIMEOUT,
    )
    if not out.get("ok"):
        return False, {}, str(out.get("error", "sandbox failed"))
    result = out.get("result")
    if not isinstance(result, dict):
        return False, {}, f"Function returned {type(result).__name__}, expected dict"
    return True, result, ""


def _build_llm_prompt(
    content_preview: dict,
    modality: str,
    file_role: str,
    skill_md: str,
    error_context: str = "",
) -> str:
    """Build the LLM prompt for code generation."""
    cp_lines = content_preview.get("raw_lines", [])
    cp_display = "\n".join(cp_lines[:10])

    prompt = f"""Generate a Python function to read this bioinformatics file.

## File Context
- Modality: {modality}
- File role: {file_role}
- File type: {content_preview.get("file_type", "unknown")}
- Shape: {content_preview.get("n_rows")} rows x {content_preview.get("n_cols")} cols
- Feature type: {content_preview.get("feature_type", "unknown")}
- Feature samples: {content_preview.get("feature_samples", [])[:5]}
- Columns: {content_preview.get("columns", [])[:10]}

## Actual File Content (first 10 lines)
```
{cp_display}
```

## Reference Knowledge
{skill_md[:3000]}

{error_context}
## Requirements
- Function name: read_{file_role}
- Signature: def read_{file_role}(filepath: str) -> dict
- Return a dict. Keys depend on file role:
  - matrix → {{"data": ndarray|sparse, "row_names": list, "col_names": list, "shape": tuple, "is_sparse": bool}}
  - features → {{"feature_ids": list, "feature_names": list}}
  - barcodes → {{"barcodes": list}}
  - contigs/clonotypes → {{"data": DataFrame}}
  - BED/peaks/fragments → {{"data": DataFrame}}
  - spatial_positions → {{"data": DataFrame}}
  - other → {{"data": DataFrame|dict}} as appropriate
- Only use allowed imports: {", ".join(sorted(_ALLOWED_IMPORTS))}
- Do NOT use: os.system, subprocess, network, file write, __import__, eval, exec
- Analyze the actual file content to determine header, delimiter, orientation, columns
- Return ONLY the Python function code, no explanation, no markdown fences
"""
    return prompt


def generate_reader_code(
    content_preview: dict,
    modality: str,
    file_role: str,
    filepath: str,
    skill_path: str = "",
    kb: Any = None,
    model: str | None = None,
) -> tuple[str, dict | None, str]:
    """Generate a reader function for a specific file.

    Args:
        content_preview: ContentPreview dict from inspect phase
        modality: One of the 12 modality labels
        file_role: matrix/barcodes/features/peaks/fragments/contigs/...
        filepath: Actual path to the file (for sandbox execution)
        skill_path: Path to SKILL.md (if empty, uses default)
        kb: Knowledge base for caching (optional)
        model: LLM model

    Returns:
        (reader_code, execution_result, error_message)
        reader_code is the Python function string.
        execution_result is the dict from running the function on the actual file (or None).
        error_message is "" on success.
    """
    # ── Cache check ──
    fingerprint = _build_fingerprint(content_preview, modality, file_role)

    if kb:
        try:
            cached = kb.find_reader_code(fingerprint) if hasattr(kb, "find_reader_code") else None
            if cached:
                ok, result, err = _execute_sandbox(cached, filepath, file_role)
                if ok:
                    logger.info("Reader code cache hit: %s/%s (fp=%s)", modality, file_role, fingerprint)
                    return cached, result, ""
                logger.warning("Cached reader code failed: %s", err[:100])
        except Exception:
            pass

    # ── Load SKILL.md ──
    skill_md = ""
    if skill_path:
        try:
            with open(skill_path, encoding="utf-8") as f:
                skill_md = f.read()
        except Exception:
            pass
    if not skill_md:
        try:
            from pathlib import Path
            default_skill = Path(__file__).parent.parent / "skills" / "file_reader" / "SKILL.md"
            if default_skill.exists():
                with open(default_skill, encoding="utf-8") as f:
                    skill_md = f.read()
        except Exception:
            pass

    # ── LLM code generation with retry ──
    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=model or LLM_MODEL, temperature=0.0,
        openai_api_base=LLM_BASE_URL, openai_api_key=LLM_API_KEY,
    )

    error_context = ""
    best_code = ""
    best_result = None

    for attempt in range(1 + _MAX_RETRIES):
        prompt = _build_llm_prompt(content_preview, modality, file_role, skill_md, error_context)

        try:
            response = llm.invoke(prompt)
            code = str(response.content).strip()
        except Exception as e:
            return "", None, f"LLM call failed: {e}"

        # Strip markdown fences if present
        code = re.sub(r"^```python\s*\n?", "", code)
        code = re.sub(r"\n?```\s*$", "", code)

        ok, msg = _sanitize_code(code)
        if not ok:
            error_context = f"\n## Previous attempt failed\nSafety check failed: {msg}\nPlease fix."
            continue

        ok, result, err = _execute_sandbox(code, filepath, file_role)
        if ok:
            # ── Cache ──
            if kb:
                try:
                    if hasattr(kb, "store_reader_code"):
                        kb.store_reader_code(fingerprint, modality, file_role, code)
                except Exception:
                    pass

            logger.info("Reader code generated: %s/%s (fp=%s, attempt=%d)",
                         modality, file_role, fingerprint, attempt + 1)
            return code, result, ""

        error_context = f"\n## Previous attempt failed (attempt {attempt + 1})\nError: {err}\nPlease fix the code."
        best_code = code
        best_result = None

    # All retries exhausted
    return best_code, None, f"All {1 + _MAX_RETRIES} attempts failed: {error_context[-300:]}"
