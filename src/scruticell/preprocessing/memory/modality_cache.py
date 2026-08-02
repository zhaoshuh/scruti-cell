"""L6: SQLite-based cache for modality classification decisions.

Stores (fingerprint → CompactClassificationResult) pairs.
Only stores classification decisions — NOT full file lists.
File mapping is reconstructed from FileContextReport on cache hit.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from scruticell.config import DATA_DIR

_DEFAULT_CACHE_PATH = DATA_DIR / "modality_cache.db"


def _build_fingerprint(gse_id: str, field_value_maps: dict, signal_groups: list) -> str:
    """Build a content-based fingerprint from compact report fields."""
    parts: list[str] = [gse_id]

    # Field names and their value counts
    for fname, fv in sorted((field_value_maps or {}).items()):
        dc = 0
        if isinstance(fv, dict):
            dc = fv.get("distinct_count", fv.get("distinct_count", 0))
        elif hasattr(fv, "distinct_count"):
            dc = fv.distinct_count
        parts.append(f"{fname}:{dc}")

    # Signal group patterns
    for sg in (signal_groups or []):
        pattern = ""
        if isinstance(sg, dict):
            pattern = sg.get("pattern", "")
        elif hasattr(sg, "pattern"):
            pattern = sg.pattern
        elif hasattr(sg, "filename") and hasattr(sg.filename, "raw_name"):
            pattern = sg.filename.raw_name
        parts.append(pattern)

    return hashlib.md5("|".join(parts).encode()).hexdigest()[:16]


def _get_cache_db(db_path: str = "") -> Any:
    """Get or create the modality cache SQLite database."""
    path = Path(db_path) if db_path else _DEFAULT_CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS modality_cache (
            fingerprint TEXT PRIMARY KEY,
            gse_id TEXT NOT NULL,
            decision_json TEXT NOT NULL,
            created_at REAL NOT NULL,
            access_count INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS code_cache (
            fingerprint TEXT NOT NULL,
            code_type TEXT NOT NULL,
            gse_id TEXT NOT NULL,
            code_text TEXT NOT NULL,
            created_at REAL NOT NULL,
            access_count INTEGER DEFAULT 1,
            PRIMARY KEY (fingerprint, code_type)
        )
    """)
    conn.commit()
    return conn


def store_classification(
    gse_id: str,
    field_value_maps: dict[str, Any],
    signal_groups: list[dict[str, Any]],
    compact_result: dict[str, Any],
    db_path: str = "",
) -> None:
    """L6: Store ONLY classification decisions (not file lists)."""
    try:
        conn = _get_cache_db(db_path)
        fingerprint = _build_fingerprint(gse_id, field_value_maps, signal_groups)

        # Strip file-level details (files, source_path) from cache
        # so cache keys remain compact and cross-version compatible
        compact = {
            "sample_groups": compact_result.get("sample_groups", []),
            "shared_file_groups": compact_result.get("shared_file_groups", []),
            "reasoning": compact_result.get("reasoning", ""),
        }

        conn.execute(
            "INSERT OR REPLACE INTO modality_cache (fingerprint, gse_id, decision_json, created_at) VALUES (?, ?, ?, ?)",
            (fingerprint, gse_id, json.dumps(compact, ensure_ascii=False), time.time()),
        )
        conn.commit()
        conn.close()
        logger.debug("Stored compact classification for %s (fp: %s)", gse_id, fingerprint)
    except Exception as e:
        logger.warning("Failed to store classification: %s", e)


def find_similar_classification(
    gse_id: str,
    field_value_maps: dict[str, Any],
    signal_groups: list[dict[str, Any]],
    db_path: str = "",
) -> dict[str, Any] | None:
    """Find cached compact classification by fingerprint.

    Backward-compatible: old cache entries missing shared_file_groups
    are auto-patched with an empty list.
    """
    try:
        conn = _get_cache_db(db_path)
        fingerprint = _build_fingerprint(gse_id, field_value_maps, signal_groups)
        row = conn.execute(
            "SELECT decision_json, gse_id FROM modality_cache WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE modality_cache SET access_count = access_count + 1 WHERE fingerprint = ?",
                (fingerprint,),
            )
            conn.commit()
            conn.close()
            logger.info("Cache hit for %s (match from %s)", gse_id, row[1])
            result = json.loads(row[0])
            # Backward compat: old cache may not have shared_file_groups
            if "shared_file_groups" not in result:
                result["shared_file_groups"] = []
            return result
        conn.close()
        return None
    except Exception as e:
        logger.warning("Failed to query cache: %s", e)
        return None


def clear_cache(db_path: str = "") -> int:
    """Clear all cached decisions. Returns count of deleted rows."""
    try:
        conn = _get_cache_db(db_path)
        cursor = conn.execute("DELETE FROM modality_cache")
        count = cursor.rowcount
        cursor = conn.execute("DELETE FROM code_cache")
        count += cursor.rowcount
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.warning("Failed to clear cache: %s", e)
        return 0


# ─── Code cache — extraction_code / flowchart / demux_code ───

def store_code_cache(
    gse_id: str,
    field_value_maps: dict[str, Any],
    signal_groups: list[dict[str, Any]],
    code_type: str,
    code_text: str,
    db_path: str = "",
) -> None:
    """Store generated code (extraction_code, flowchart, demux_code) by fingerprint.

    Args:
        gse_id: GSE accession ID
        field_value_maps: deduplicated metadata maps
        signal_groups: file signal groups
        code_type: "extraction_code" | "experiment_flowchart" | "demux_code"
        code_text: the generated code string
    """
    if not code_text:
        return
    try:
        conn = _get_cache_db(db_path)
        fingerprint = _build_fingerprint(gse_id, field_value_maps, signal_groups)
        conn.execute(
            "INSERT OR REPLACE INTO code_cache (fingerprint, gse_id, code_type, code_text, created_at) VALUES (?, ?, ?, ?, ?)",
            (fingerprint, gse_id, code_type, code_text, time.time()),
        )
        conn.commit()
        conn.close()
        logger.debug("Stored %s for %s (fp: %s)", code_type, gse_id, fingerprint)
    except Exception as e:
        logger.warning("Failed to store %s: %s", code_type, e)


def find_code_cache(
    gse_id: str,
    field_value_maps: dict[str, Any],
    signal_groups: list[dict[str, Any]],
    code_type: str,
    db_path: str = "",
) -> str | None:
    """Find cached code by fingerprint and type.

    Returns the code string if found, None otherwise.
    """
    try:
        conn = _get_cache_db(db_path)
        fingerprint = _build_fingerprint(gse_id, field_value_maps, signal_groups)
        row = conn.execute(
            "SELECT code_text FROM code_cache WHERE fingerprint = ? AND code_type = ?",
            (fingerprint, code_type),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE code_cache SET access_count = access_count + 1 WHERE fingerprint = ? AND code_type = ?",
                (fingerprint, code_type),
            )
            conn.commit()
            conn.close()
            logger.info("Code cache hit for %s (%s)", gse_id, code_type)
            return row[0]
        conn.close()
        return None
    except Exception as e:
        logger.warning("Failed to query code cache: %s", e)
        return None
