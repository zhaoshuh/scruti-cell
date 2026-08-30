"""Sandbox for executing LLM-generated extract_metadata() functions.

Operates on plain dict GSM entries (not GsmInfo objects).
Independent of the intelligence pipeline's executor sandbox.
Execution runs in a subprocess with a restricted import whitelist.
"""

from __future__ import annotations

import logging

from scruticell.preprocessing.sandbox import run_code_in_subprocess

logger = logging.getLogger(__name__)


def _get_char_from_dict(entry: dict, key: str, default: str = "") -> str:
    """Extract a characteristic value from a raw GSM dict.

    The characteristics_ch1 field is a semicolon-separated string
    of "key: value" pairs after serialization.
    """
    chars = entry.get("characteristics_ch1", "")
    if not chars:
        return default
    items = chars.split("; ") if "; " in chars else [chars]
    key_lower = key.lower().strip()
    for item in items:
        item_str = str(item).strip()
        if not item_str:
            continue
        if ": " in item_str:
            k, v = item_str.split(": ", 1)
        elif ":" in item_str:
            k, v = item_str.split(":", 1)
        else:
            continue
        if k.strip().lower() == key_lower:
            return v.strip()
    return default


def _wrap_extraction_code(extraction_code: str) -> str:
    """Wrap user code so the sandbox also sees `re` and a `get_char` helper."""
    return (
        "import re\n"
        "\n"
        "def get_char(entry, key, default=''):\n"
        "    chars = entry.get('characteristics_ch1', '')\n"
        "    if not chars:\n"
        "        return default\n"
        "    items = chars.split('; ') if '; ' in chars else [chars]\n"
        "    key_lower = key.lower().strip()\n"
        "    for item in items:\n"
        "        item = str(item).strip()\n"
        "        if not item:\n"
        "            continue\n"
        "        if ': ' in item:\n"
        "            k, v = item.split(': ', 1)\n"
        "        elif ':' in item:\n"
        "            k, v = item.split(':', 1)\n"
        "        else:\n"
        "            continue\n"
        "        if k.strip().lower() == key_lower:\n"
        "            return v.strip()\n"
        "    return default\n"
        "\n"
        + extraction_code
    )


def run_extraction_metadata(
    extraction_code: str,
    gsm_raw_entry: dict,
) -> dict[str, str]:
    """Execute LLM-generated def extract_metadata(gsm_raw_entry) in a subprocess.

    Args:
        extraction_code: Python source string defining extract_metadata()
        gsm_raw_entry: dict with SOFT field names as string keys,
            values are all plain strings after serialization

    Returns:
        Dict of extracted field_name → value, with empty/null values filtered
    """
    code = extraction_code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    code = _wrap_extraction_code(code)
    out = run_code_in_subprocess(
        code,
        "extract_metadata",
        args=(gsm_raw_entry,),
        timeout=30,
    )
    if not out.get("ok"):
        logger.warning("extraction_code sandbox error: %s", out.get("error", "unknown")[:300])
        return {}

    result = out.get("result")
    if not isinstance(result, dict):
        logger.warning("extract_metadata() returned %s, expected dict",
                       type(result).__name__)
        return {}

    filtered: dict[str, str] = {}
    for k, v in result.items():
        sv = str(v).strip()
        if sv and sv.lower() not in ("", "na", "n/a", "none", "null"):
            filtered[str(k)] = sv
    return filtered
