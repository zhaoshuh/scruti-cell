"""Sandbox for executing LLM-generated extract_metadata() functions.

Operates on plain dict GSM entries (not GsmInfo objects).
Independent of the intelligence pipeline's executor sandbox.
"""

from __future__ import annotations

import logging
import re as _re
from typing import Any

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


def run_extraction_metadata(
    extraction_code: str,
    gsm_raw_entry: dict,
) -> dict[str, str]:
    """Execute LLM-generated def extract_metadata(gsm_raw_entry) in sandbox.

    Args:
        extraction_code: Python source string defining extract_metadata()
        gsm_raw_entry: dict with SOFT field names as string keys,
            values are all plain strings after serialization

    Returns:
        Dict of extracted field_name → value, with empty/null values filtered
    """
    sandbox: dict[str, Any] = {
        "re": _re,
        "get_char": lambda entry, key, default="": _get_char_from_dict(entry, key, default),
        "__builtins__": {
            "True": True, "False": False, "None": None,
            "int": int, "float": float, "str": str, "bool": bool,
            "list": list, "dict": dict, "set": set, "tuple": tuple,
            "len": len, "range": range, "enumerate": enumerate,
            "zip": zip, "map": map, "filter": filter,
            "print": print, "isinstance": isinstance,
            "Exception": Exception, "ValueError": ValueError,
            "TypeError": TypeError, "KeyError": KeyError,
            "__import__": __import__,
        },
    }

    code = extraction_code.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    local_ns: dict = {}
    try:
        exec(code, sandbox, local_ns)
    except SyntaxError as exc:
        logger.warning("extraction_code syntax error: %s", exc)
        return {}
    except Exception as exc:
        logger.warning("extraction_code exec error: %s", exc)
        return {}

    extract_fn = local_ns.get("extract_metadata")
    if extract_fn is None:
        logger.warning("No extract_metadata() function found in code")
        return {}

    try:
        result = extract_fn(gsm_raw_entry)
        if isinstance(result, dict):
            filtered: dict[str, str] = {}
            for k, v in result.items():
                sv = str(v).strip()
                if sv and sv.lower() not in ("", "na", "n/a", "none", "null"):
                    filtered[str(k)] = sv
            return filtered
    except Exception as exc:
        logger.warning("extract_metadata() runtime error: %s", exc)

    return {}
