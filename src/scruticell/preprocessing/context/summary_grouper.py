"""Builds FileContentPreview from Inspector file summaries.

Replaces the old L3 (format-based summary grouping) — instead, content
previews are attached directly to each FileSignalGroup in L2.
The file type info and shape range come from the actual files in the group.
"""

from __future__ import annotations

from typing import Any

from scruticell.preprocessing.models import FileContentPreview


def _get_shape(summary: dict[str, Any]) -> tuple[int, int] | None:
    hs = summary.get("h5_shape")
    if hs:
        return (hs[0], hs[1])
    ms = summary.get("mtx_shape")
    if ms:
        return (ms[0], ms[1])
    ls = summary.get("loom_shape")
    if ls:
        return (ls[0], ls[1])
    nr = summary.get("n_rows")
    nc = summary.get("n_cols")
    if nr and nc:
        return (nr, nc)
    return None


def _detect_feature_types_column(columns: list[str]) -> list[str]:
    """10x v3 features.tsv.gz has a features_type column."""
    for col in columns:
        if col.lower() in ("feature_type", "feature_types", "type"):
            return []  # sampled per-file, not here
    return []


def build_content_preview(
    summary: dict[str, Any],
    signals: Any,
    download_dir: str = "",
) -> FileContentPreview:
    """Build a comprehensive content preview for LLM classification.

    Args:
        summary: Dict from Inspector FileSummary
        signals: FileSignals object for this file
        download_dir: Root download dir (for reading raw text)

    Returns:
        FileContentPreview with all available data
    """
    ft = summary.get("file_type", "unknown")

    # Shape info
    shape = _get_shape(summary)
    nr = summary.get("n_rows") or (shape[0] if shape else None)
    nc = summary.get("n_cols") or (shape[1] if shape else None)

    preview = FileContentPreview(
        ancestry=summary.get("ancestry", ""),
        file_type=ft,
        n_rows=nr,
        n_cols=nc,
        is_sparse=bool(summary.get("h5_sparse") or summary.get("mtx_is_sparse")),
        columns=summary.get("columns", [])[:30],
        feature_type=signals.content.feature_id_type,
        feature_samples=signals.content.feature_samples[:20],
        barcode_format=summary.get("barcode_format", ""),
        barcode_samples=summary.get("barcode_samples", [])[:10],
        obs_columns=summary.get("obs_columns", [])[:30],
        var_columns=summary.get("var_columns", [])[:30],
        uns_keys=summary.get("uns_keys", [])[:30],
        rds_keys=summary.get("rds_keys", [])[:30],
        companion_files=signals.filename.companion_files[:10] if signals.filename.companion_files else [],
    )

    # 10x v3 feature_type detection
    col_list = summary.get("columns", [])
    preview_rows = summary.get("preview_rows_raw", [])
    if len(col_list) >= 3:
        third_col = col_list[2].lower() if len(col_list) > 2 else ""
        if any(kw in third_col for kw in ("feature_type", "features_type", "type")):
            # Sample unique values from preview rows at column index 2
            types: set[str] = set()
            for row in preview_rows[:20]:
                if isinstance(row, list) and len(row) > 2:
                    types.add(str(row[2]))
                elif isinstance(row, dict):
                    vals = list(row.values())
                    if len(vals) > 2:
                        types.add(str(vals[2]))
            preview.feature_types_detected = sorted(types)[:10]

    # Raw text (tabular files)
    if ft in ("csv", "mtx", "soft", "bed"):
        preview_raw = summary.get("preview_rows_raw", [])
        if preview_raw:
            preview.raw_lines = [
                "\t".join(str(c) for c in row)[:500]
                for row in preview_raw[:10]
            ]
    elif ft == "json":
        # JSON files: just show keys
        try:
            import json
            ancestry = summary.get("ancestry", "")
            if ancestry and download_dir:
                from pathlib import Path
                fp = Path(download_dir) / ancestry
                if fp.exists():
                    with open(fp, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    preview.raw_lines = [
                        json.dumps({k: type(v).__name__ for k, v in data.items()}, indent=2)[:500]
                    ]
        except Exception:
            pass

    return preview


def compute_shape_range(summaries: list[dict[str, Any]]) -> dict[str, int]:
    """Compute min/max row/col range for a group of file summaries."""
    rows: list[int] = []
    cols: list[int] = []
    for s in summaries:
        shape = _get_shape(s)
        if shape:
            rows.append(shape[0])
            cols.append(shape[1])
        else:
            nr = s.get("n_rows")
            nc = s.get("n_cols")
            if nr is not None and nr > 0:
                rows.append(nr)
            if nc is not None and nc > 0:
                cols.append(nc)

    result: dict[str, int] = {}
    if rows:
        result["min_rows"] = min(rows)
        result["max_rows"] = max(rows)
    if cols:
        result["min_cols"] = min(cols)
        result["max_cols"] = max(cols)
    return result
