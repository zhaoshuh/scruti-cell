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

_DEFAULT_CACHE_PATH = Path.cwd() / "output" / "modality_cache.db"


def _build_fingerprint(gse_id: str, field_value_maps: dict, signal_groups: list) -> str:
    """Build a content-based fingerprint from compact report fields."""
    parts: list[str] = [gse_id]

    # Field names and their value counts
    for fname, fv in sorted((field_value_maps or {}).items()):
        if isinstance(fv, dict):
            parts.append(f"{fname}:{fv.get('distinct_count', fv.get('distinct_count', 0))}")

    # Signal group patterns
    for sg in (signal_groups or []):
        if isinstance(sg, dict):
            parts.append(sg.get("pattern", ""))

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
        conn.commit()
        conn.close()
        return count
    except Exception as e:
        logger.warning("Failed to clear cache: %s", e)
        return 0
