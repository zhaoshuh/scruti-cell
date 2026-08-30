"""Organize tool: builds the output directory structure with hard links.

Creates the preprocessed/ output tree organized by sample (GSM or inferred key)
and modality. Groups sample-level info in manifest.json under sample_groups.

File-to-sample attribution is LLM-driven (see preprocessing.attribution); no
rule-based filename matching is used.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# Module-level cache — walk download_dir once, reuse for all scans.
_all_file_cache: dict[str, list[Path]] = {}
_all_file_cache_lock = threading.Lock()


def _safe_name(name: str) -> str:
    """Sanitize a name for use as a filesystem directory."""
    import re
    safe = re.sub(r'[<>:\"/\\|?*]', '_', name).strip(' .')
    # Prevent path-traversal tricks
    safe = safe.replace("..", "__")
    return safe or "unknown"


def _format_from_role(file_path: Path, role: str) -> str:
    suffix = file_path.suffix.lower()
    if suffix in (".h5ad", ".h5"):
        return "h5ad"
    if suffix == ".mtx" or suffix == ".gz" and ".mtx" in file_path.name.lower():
        return "mtx"
    if suffix in (".csv", ".tsv"):
        return "csv"
    if suffix == ".bed":
        return "bed"
    if suffix == ".gz" and ".tsv" in file_path.name.lower():
        return "tsv.gz"
    if suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif"):
        return "png"
    if suffix == ".json":
        return "json"
    if role == "contigs" or role == "clonotypes":
        return "csv"
    if role == "fragments":
        return "tsv.gz"
    if role == "spatial_image":
        return "png"
    return "other"


def _list_all_files(download_root: Path) -> list[Path]:
    """List files under download_root, cached per root."""
    cache_key = str(download_root)
    with _all_file_cache_lock:
        if cache_key in _all_file_cache:
            return _all_file_cache[cache_key]
        files: list[Path] = []
        for fp in sorted(download_root.rglob("*")):
            if fp.is_file() and not fp.name.startswith("."):
                files.append(fp)
        _all_file_cache[cache_key] = files
        return files


def _hardlink_or_copy(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists():
            dst.unlink()
        os.link(str(src), str(dst))
        return True
    except (OSError, NotImplementedError):
        try:
            if dst.exists():
                dst.unlink()
            os.symlink(str(src.resolve()), str(dst))
            return True
        except (OSError, NotImplementedError):
            try:
                import shutil
                shutil.copy2(str(src), str(dst))
                return False
            except Exception as e:
                logger.warning("Failed to copy %s → %s: %s", src, dst, e)
                return False


def _resolve_source(src_str: str, download_root: Path) -> Path | None:
    src = Path(src_str)
    if not src.is_absolute():
        src = download_root / src_str
    return src if src.exists() else None


def _link_file_entry(
    entry: dict, download_root: Path, mod_dir: Path, modality_name: str,
) -> tuple[list[dict], list[dict], int]:
    source_path = entry.get("source_path", "")
    ancestry = entry.get("ancestry", "")
    role = entry.get("role", "other")
    if not source_path:
        return [], [], 0
    src = _resolve_source(source_path, download_root)
    if not src:
        return [], [], 0
    dst = mod_dir / src.name
    fmt = _format_from_role(src, role)
    linked = 1 if _hardlink_or_copy(src, dst) else 0
    mod_files = [{"path": f"{modality_name}/{src.name}", "role": role, "format": fmt, "reader_code": ""}]
    source_files = [{"path": ancestry, "role": role, "format": fmt}]
    return mod_files, source_files, linked


def _attribution_entries(
    attribution: dict,
    resolved_group: dict,
    modality_name: str,
) -> list[dict]:
    """Build link entries for a resolved group + modality from LLM attribution."""
    entries: list[dict] = []
    sample_key = resolved_group.get("sample_key", "")
    seen: set[str] = set()
    for f in attribution.get("files", []) or []:
        if not isinstance(f, dict):
            continue
        if f.get("modality") != modality_name:
            continue
        if f.get("sample_key") != sample_key:
            continue
        ancestry = str(f.get("ancestry", "") or "")
        if not ancestry or ancestry in seen:
            continue
        seen.add(ancestry)
        entries.append({
            "source_path": ancestry,
            "ancestry": ancestry,
            "role": str(f.get("role", "other")),
        })
    for s in attribution.get("shared", []) or []:
        if not isinstance(s, dict):
            continue
        if s.get("modality") != modality_name:
            continue
        if sample_key not in (s.get("sample_keys", []) or []):
            continue
        ancestry = str(s.get("ancestry", "") or "")
        if not ancestry or ancestry in seen:
            continue
        seen.add(ancestry)
        entries.append({
            "source_path": ancestry,
            "ancestry": ancestry,
            "role": str(s.get("role", "other")),
        })
    return entries


def _files_complete(file_entries: list) -> bool:
    """True if every entry has a source and a role (no LLM re-attribution needed)."""
    if not file_entries:
        return False
    return all(isinstance(e, dict) and e.get("source_path") and e.get("role") for e in file_entries)


def _handle_demux(entry: dict, demux_rule: dict, modality_name: str,
                  preprocessed_dir: Path, download_root: Path,
                  content_preview: dict | None = None) -> list[dict]:
    """Split a demux file and link sub-files to sample directories.

    Returns list of {sample_id, output_path, role, format} for updating metadata.
    """
    from scruticell.preprocessing.tools.demux_splitter import handle_demux_split
    from scruticell.preprocessing.verification import verify_demux_results

    source_path = entry.get("source_path", "")
    if not source_path:
        return []
    src = _resolve_source(source_path, download_root)
    if not src:
        return []

    tmp_dir = preprocessed_dir / "_demux_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    split_files = handle_demux_split(
        str(src), str(tmp_dir),
        demux_rule, modality_name,
        content_preview=content_preview,
    )

    # Verify demux output
    verif = verify_demux_results(split_files, demux_rule)
    if not verif.passed:
        logger.warning("Demux verification failed: %s", verif.failure_summary())

    results = []
    for sf in split_files:
        sid = sf.get("sample_id", "unknown")
        out_path = Path(sf.get("output_file", ""))
        if not out_path.exists():
            continue
        results.append({
            "sample_id": sid,
            "output_path": out_path,
            "role": sf.get("role", "other"),
            "format": sf.get("format", "other"),
        })
    return results


def organize_output(
    gse_id: str,
    classification_json: dict,
    download_dir: str,
    output_dir: str = "",
) -> dict:
    """Organize classified files into a structured output directory.

    Output structure (per-sample directories, logical groups in manifest):
      output/<GSE_ID>/preprocessed/
        ├── manifest.json
        ├── <sample_directory>/           # GSM ID or inferred key
        │   ├── metadata.json
        │   └── <modality>/
        │       ├── files.json
        │       └── ... (hard links)
        └── unclassified/
    """
    from scruticell.config import LLM_ATTRIBUTION_ENABLED, get_output_path

    if not output_dir:
        base_dir = get_output_path(gse_id)
    else:
        base_dir = Path(output_dir)

    preprocessed_dir = base_dir / "preprocessed"
    preprocessed_dir.mkdir(parents=True, exist_ok=True)
    download_root = Path(download_dir)

    # Clear module-level cache for fresh run
    with _all_file_cache_lock:
        _all_file_cache.clear()

    errors: list[str] = []
    total_files_linked = 0
    all_sample_dirs: list[dict] = []

    try:
        sample_groups = classification_json.get("sample_groups", [])
        unclassified_files = classification_json.get("unclassified", [])
        shared_file_groups = classification_json.get("shared_file_groups", [])

        # Build dir_name → (gsm_ids, key_source, modalities, note, sample_metadata) mapping
        resolved_groups: list[dict] = []
        for group in sample_groups:
            gsm_ids = group.get("gsm_ids", [])
            if isinstance(gsm_ids, str):
                gsm_ids = [gsm_ids]
            key_source = group.get("key_source", "")
            sample_key = group.get("sample_key", "")
            file_prefix = group.get("file_prefix", "")
            modalities = group.get("modalities", {})
            note = group.get("note", "")
            sample_metadata = group.get("sample_metadata", {})

            # Normalize modalities: LLM may output list-of-dicts
            if isinstance(modalities, list):
                _norm: dict[str, dict] = {}
                for m in modalities:
                    _norm[m.get("modality", "other")] = m
                modalities = _norm

            if key_source == "inferred_composite" and sample_key:
                dir_name = _safe_name(sample_key)
                resolved_groups.append({
                    "dir_name": dir_name,
                    "gsm_ids": gsm_ids,
                    "key_source": key_source,
                    "sample_key": sample_key,
                    "file_prefix": file_prefix,
                    "modalities": modalities,
                    "note": note,
                    "sample_metadata": sample_metadata,
                })
            else:
                for gsm_id in gsm_ids:
                    resolved_groups.append({
                        "dir_name": _safe_name(gsm_id),
                        "gsm_ids": [gsm_id],
                        "key_source": key_source or "gsm_id",
                        "sample_key": gsm_id,
                        "file_prefix": "",
                        "modalities": modalities,
                        "note": note,
                        "sample_metadata": sample_metadata,
                    })

        # Build gsm_id → dir_name for shared file distribution
        gsm_to_dir: dict[str, str] = {}
        for rg in resolved_groups:
            for gsm_id in rg["gsm_ids"]:
                gsm_to_dir[gsm_id] = rg["dir_name"]

        # Decide whether LLM attribution is needed (missing/incomplete files or roles)
        needs_attribution = False
        for rg in resolved_groups:
            for modality_data in rg["modalities"].values():
                if not _files_complete(modality_data.get("files", [])):
                    needs_attribution = True
                    break
            if needs_attribution:
                break

        attribution: dict | None = None
        if needs_attribution and LLM_ATTRIBUTION_ENABLED:
            from scruticell.preprocessing.attribution import llm_attribution
            attribution = llm_attribution(download_root, sample_groups, gse_id)
            if not attribution.get("ok"):
                logger.warning("LLM attribution failed for %s: %s",
                               gse_id, attribution.get("error"))
                attribution = None
            else:
                attribution_uncl = attribution.get("unclassified", []) or []
                if attribution_uncl:
                    unclassified_files = list(unclassified_files or []) + [
                        str(u) for u in attribution_uncl
                        if str(u) not in (unclassified_files or [])
                    ]

        # ── Phase 1: per-sample normal files (collect demux tasks) ──
        demux_tasks: list[dict] = []
        seen_demux_sources: set[str] = set()

        for rg in resolved_groups:
            dir_name = rg["dir_name"]
            sample_dir = preprocessed_dir / dir_name
            sample_dir.mkdir(parents=True, exist_ok=True)

            sample_info = {
                "gse_id": gse_id,
                "dir_name": dir_name,
                "gsm_ids": rg["gsm_ids"],
                "sample_key": rg["sample_key"],
                "key_source": rg["key_source"],
                "note": rg.get("note", ""),
                "sample_metadata": rg.get("sample_metadata", {}),
                "modalities": {},
            }

            for modality_name, modality_data in rg["modalities"].items():
                mod_dir = sample_dir / modality_name
                mod_dir.mkdir(parents=True, exist_ok=True)

                mod_files: list[dict] = []
                source_files: list[dict] = []

                provided = modality_data.get("files", [])
                file_entries: list[dict] = []
                if attribution is not None:
                    file_entries = _attribution_entries(
                        attribution, rg, modality_name,
                    )
                elif _files_complete(provided):
                    file_entries = [
                        {"source_path": e.get("source_path", ""),
                         "ancestry": e.get("ancestry", ""),
                         "role": e.get("role", "other")}
                        for e in provided
                    ]

                for entry in file_entries:
                    if modality_data.get("has_demux"):
                        src = entry.get("source_path", "")
                        if src and src not in seen_demux_sources:
                            seen_demux_sources.add(src)
                            demux_rule = modality_data.get("demux_rule") or {}
                            if isinstance(demux_rule, dict):
                                demux_tasks.append({
                                    "source_path": src,
                                    "ancestry": entry.get("ancestry", ""),
                                    "demux_rule": demux_rule,
                                    "modality": modality_name,
                                    "gsm_ids": rg["gsm_ids"],
                                    "content_preview": modality_data.get("demux_content_preview"),
                                })
                    else:
                        mod_f, src_f, linked = _link_file_entry(entry, download_root, mod_dir, modality_name)
                    mod_files.extend(mod_f)
                    source_files.extend(src_f)
                    total_files_linked += linked

                sample_info["modalities"][modality_name] = {
                    "assay": modality_data.get("assay", ""),
                    "suspension": modality_data.get("suspension", ""),
                    "n_cells": modality_data.get("n_cells"),
                    "n_features": modality_data.get("n_features"),
                    "confidence": modality_data.get("confidence", 0.0),
                    "has_shared": modality_data.get("has_shared", False),
                    "has_demux": modality_data.get("has_demux", False),
                    "files": mod_files,
                    "source_files": source_files,
                }

                with open(mod_dir / "files.json", "w", encoding="utf-8") as f:
                    json.dump(mod_files, f, ensure_ascii=False, indent=2)

            with open(sample_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(sample_info, f, ensure_ascii=False, indent=2)

            all_sample_dirs.append(sample_info)

        # Shared files — hard link into every referencing sample directory
        for sfg in shared_file_groups:
            if not isinstance(sfg, dict):
                continue
            ancestry = sfg.get("ancestry", "") or sfg.get("source_path", "")
            source_path = sfg.get("source_path", "") or ancestry
            modality_name = sfg.get("modality", "other")
            ref_gsm = sfg.get("ref_by_gsm", [])
            if isinstance(ref_gsm, str):
                ref_gsm = [ref_gsm]
            if not source_path or not ref_gsm:
                continue
            src = _resolve_source(source_path, download_root)
            if not src:
                errors.append(f"Shared source file not found: {source_path}")
                continue
            for gsm_id in ref_gsm:
                dir_name = gsm_to_dir.get(gsm_id)
                if not dir_name:
                    continue
                mod_dir = preprocessed_dir / dir_name / modality_name
                mod_dir.mkdir(parents=True, exist_ok=True)
                if _hardlink_or_copy(src, mod_dir / src.name):
                    total_files_linked += 1

        # ── Phase 2: demux post-processing ──
        for task in demux_tasks:
            entry = {"source_path": task["source_path"], "ancestry": task["ancestry"]}
            split_files = _handle_demux(
                entry, task["demux_rule"], task["modality"],
                preprocessed_dir, download_root,
                content_preview=task.get("content_preview"),
            )
            for sf in split_files:
                sid = sf["sample_id"]
                dir_name = gsm_to_dir.get(sid) or _safe_name(sid)
                dst_dir = preprocessed_dir / dir_name / task["modality"]
                dst_dir.mkdir(parents=True, exist_ok=True)
                src = Path(sf["output_path"])
                if _hardlink_or_copy(src, dst_dir / src.name):
                    total_files_linked += 1

                # Update sample metadata with demux sub-file
                for sample_info in all_sample_dirs:
                    if sample_info["dir_name"] == dir_name:
                        mod_info = sample_info["modalities"].setdefault(task["modality"], {})
                        mod_info.setdefault("files", []).append({
                            "path": f"{task['modality']}/{src.name}",
                            "role": sf.get("role", "other"),
                            "format": sf.get("format", "other"),
                            "reader_code": "",
                        })
                        break

        # Unclassified
        if unclassified_files:
            uncl_dir = preprocessed_dir / "unclassified"
            uncl_dir.mkdir(parents=True, exist_ok=True)
            with open(uncl_dir / "unclassified.json", "w", encoding="utf-8") as f:
                json.dump(unclassified_files, f, ensure_ascii=False, indent=2)

        # Build manifest with logical groups (metadata-level)
        logical_groups: list[dict] = []
        for group in sample_groups:
            gsm_ids = group.get("gsm_ids", [])
            if isinstance(gsm_ids, str):
                gsm_ids = [gsm_ids]
            key_source = group.get("key_source", "")
            sample_key = group.get("sample_key", "")
            mods = group.get("modalities", {})
            if isinstance(mods, list):
                mods = {m.get("modality", "other"): m for m in mods if isinstance(m, dict)}
            logical_groups.append({
                "note": group.get("note", ""),
                "gsm_ids": gsm_ids,
                "sample_key": sample_key,
                "key_source": key_source or ("gsm_id" if gsm_ids else "inferred_composite"),
                "dir_name": _safe_name(sample_key) if key_source == "inferred_composite" else None,
                "modalities": list(mods.keys()),
            })

        manifest = {
            "gse_id": gse_id,
            "preprocessed_at": str(Path.cwd()),
            "sample_count": len(all_sample_dirs),
            "group_count": len(logical_groups),
            "sample_groups": logical_groups,
            "unclassified_count": len(unclassified_files),
            "shared_file_count": len(shared_file_groups),
            "total_files_linked": total_files_linked,
            "errors": errors,
        }
        manifest_path = preprocessed_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    except Exception as e:
        errors.append(f"Organize failed: {e}")
        logger.exception("Failed to organize output")

    return {
        "output_dir": str(preprocessed_dir),
        "total_groups": len(all_sample_dirs),
        "total_files_linked": total_files_linked,
        "manifest_path": str(preprocessed_dir / "manifest.json"),
        "errors": errors,
    }
