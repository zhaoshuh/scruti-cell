"""Inspect tool: extracts structured file summaries and builds compact context.

Integrates L1 (field_value_maps), L2 (file_signal_groups), L3 (file_summary_groups),
L5 (noise file filtering) into the FileContextReport.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict
from pathlib import Path

from scruticell.intelligence.inspector import Inspector
from scruticell.parsing.soft import parse_soft_file
from scruticell.parsing.metadata import extract_gsm_metadata
from scruticell.preprocessing.context.metadata import extract_metadata_signals
from scruticell.preprocessing.context.filenames import extract_filename_signals
from scruticell.preprocessing.context.contents import extract_content_signals
from scruticell.preprocessing.context.field_mapper import build_field_value_maps
from scruticell.preprocessing.context.signal_grouper import group_file_signals
from scruticell.preprocessing.context.summary_grouper import build_content_preview, compute_shape_range
from scruticell.preprocessing.context.file_meta import build_file_meta
from scruticell.preprocessing.context.attribution import compute_attribution, build_soft_suppl_refs
from scruticell.preprocessing.models import (
    FileContextReport,
    FileSignals,
    MetadataSignal,
)
from scruticell.utils.paths import ancestry_filename

logger = logging.getLogger(__name__)

# L5: File types that are noise (not data-bearing)
_NOISE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".md", ".txt", ".rtf", ".xml", ".html", ".htm",
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".svg",
}

_GSM_THRESHOLD = 5


def _is_noise_file(file_path: Path, summary: dict) -> bool:
    """L5: Check if a file is metadata/document/noise (not data-bearing)."""
    ext = file_path.suffix.lower()
    if ext in _NOISE_EXTENSIONS:
        return True
    file_type = summary.get("file_type", "unknown")
    if file_type == "other":
        return True
    return False


def _extract_gsm_id(ancestry: str) -> str | None:
    """Extract the first GSM ID from an ancestry path."""
    match = re.search(r"GSM\d+", ancestry)
    return match.group(0) if match else None


def inspect_files(
    download_dir: str,
    soft_file: str | None = None,
    ancestry_paths: list[str] | None = None,
) -> dict:
    """Inspect all files and produce a compact FileContextReport.

    Applies token optimization layers:
      L1: field_value_maps — metadata deduplication
      L2: file_signal_groups — per-file signal grouping
      L3: file_summary_groups — summary grouping
      L5: noise file filtering
    """
    inspector = Inspector()
    root = Path(download_dir)

    # Gather all paths to inspect
    paths_to_inspect: list[tuple[str, Path]] = []
    if ancestry_paths:
        for ap in ancestry_paths:
            p = root / ap
            if p.exists() and p.is_file():
                paths_to_inspect.append((ap, p))
    else:
        for file_path in sorted(root.rglob("*")):
            if file_path.is_file() and not file_path.name.startswith("."):
                anc = str(file_path.relative_to(root))
                paths_to_inspect.append((anc, file_path))

    # ─── Parse SOFT metadata ───
    gse_info: dict = {}
    gsm_metadata_map: dict[str, dict] = {}
    raw_gsm: dict[str, dict] = {}

    if soft_file and Path(soft_file).exists():
        try:
            parsed = parse_soft_file(Path(soft_file))
            raw_gse = parsed.get("gse_metadata", {})
            raw_gsm = parsed.get("gsm_entries", {})
            gse_info = {
                "gse_id": raw_gse.get("title", ""),
                "title": raw_gse.get("title", ""),
                "summary": raw_gse.get("summary", ""),
                "overall_design": raw_gse.get("overall_design", ""),
                "pubmed_id": raw_gse.get("pubmed_id", ""),
                "organism": raw_gse.get("organism", ""),
                "experiment_type": str(raw_gse.get("type", raw_gse.get("experiment_type", ""))),
                "platform_ids": raw_gse.get("platform_ids", []),
            }
            gsm_metadata_map = extract_gsm_metadata(raw_gsm)
        except Exception as e:
            logger.warning("Failed to parse SOFT file: %s", e)

    # ─── Inspect each file and build signals ───
    file_summaries_raw: dict[str, dict] = {}
    per_file_signals: list[FileSignals] = []
    data_file_signals: list[FileSignals] = []   # L5: filtered (noise excluded)
    errors: list[str] = []

    for ancestry, file_path in paths_to_inspect:
        try:
            summary = inspector.inspect_file(file_path, ancestry)
            summary_dict = asdict(summary)

            # L5: Skip noise files from the compact report (but keep raw dict for organizer)
            if _is_noise_file(file_path, summary_dict):
                file_summaries_raw[ancestry] = summary_dict
                continue

            file_summaries_raw[ancestry] = summary_dict

            gsm_id = _extract_gsm_id(ancestry)

            metadata_signal = MetadataSignal()
            if gsm_id and gsm_id in gsm_metadata_map:
                char_dict = gsm_metadata_map[gsm_id]
                raw_entry = raw_gsm.get(gsm_id, {})
                metadata_signal = extract_metadata_signals(gsm_id, raw_entry, char_dict)

            filename_signal = extract_filename_signals(file_path, ancestry)
            content_signal = extract_content_signals(summary_dict)

            signals = FileSignals(
                ancestry=ancestry,
                file_type=summary.file_type,
                metadata=metadata_signal,
                filename=filename_signal,
                content=content_signal,
            )
            per_file_signals.append(signals)
            data_file_signals.append(signals)

        except Exception as e:
            errors.append(f"Failed to inspect {ancestry}: {e}")
            logger.warning("Inspect failed for %s: %s", ancestry, e)

    # ─── Build gsm_entries (fallback for small GSE) ───
    gsm_entries: list[dict] = []
    for gsm_id, meta in gsm_metadata_map.items():
        raw_entry = raw_gsm.get(gsm_id, {})
        gsm_entries.append({
            "gsm_id": gsm_id,
            "title": raw_entry.get("_geo_title", raw_entry.get("title", "")),
            "characteristics": meta,
            "molecule": raw_entry.get("molecule_ch1", ""),
            "library_strategy": raw_entry.get("library_strategy", ""),
            "organism": raw_entry.get("organism_ch1", raw_entry.get("taxid_ch1", "")),
            "description": raw_entry.get("description", ""),
        })

    gse_id = root.parent.name if root.name == "download" else root.name
    total_gsm = len(gsm_metadata_map)

    # ─── Build compact representations ───
    # L1: field_value_maps
    field_value_maps_raw = build_field_value_maps(gsm_metadata_map)

    # L2+L3: file_signal_groups with content_preview + shape_range
    signal_groups = group_file_signals(data_file_signals)

    # Build soft_suppl_refs for L1 attribution
    soft_suppl_refs = build_soft_suppl_refs(raw_gsm)

    # Build field_value_maps for attribution matching (reuse from above)
    fv_dict = {name: fv.model_dump() for name, fv in field_value_maps_raw.items()}

    # Build content_preview + shape_range + file_meta + attribution for each signal group
    for sg in signal_groups:
        group_summaries: list[dict] = []
        rep_name = ancestry_filename(sg.representative.ancestry) if sg.representative.ancestry else ""
        for ancestry in file_summaries_raw:
            if Path(ancestry).name == rep_name:
                group_summaries.append(file_summaries_raw[ancestry])
        if not group_summaries:
            group_summaries = [file_summaries_raw.get(sg.representative.ancestry, {})]

        rep_summary = group_summaries[0] if group_summaries else file_summaries_raw.get(sg.representative.ancestry, {})

        sg.shape_range = compute_shape_range(group_summaries)
        sg.is_sparse = any(
            s.get("h5_sparse") or s.get("mtx_is_sparse")
            for s in group_summaries
        )
        sg.content_preview = build_content_preview(
            rep_summary, sg.representative, str(root)
        )

        # Build FileMeta and compute attribution
        companion = sg.representative.filename.companion_files if sg.representative.filename.companion_files else []
        sg.file_meta = build_file_meta(
            sg.representative.ancestry,
            sg.representative.file_type,
            rep_summary,
            companion,
        )

        sg.attribution = compute_attribution(
            sg.file_meta,
            fv_dict,
            soft_suppl_refs,
        )

    # ─── Metadata extraction context (compact, for LLM) ───
    extraction_context: dict = {}
    experiment_flowchart: str = ""
    if raw_gsm and gse_info:
        try:
            from scruticell.preprocessing.context.extraction_context import build_extraction_context
            extraction_context = build_extraction_context(raw_gsm, gse_info)
        except Exception as e:
            logger.warning("Failed to build extraction context: %s", e)

    # ─── Try LLM metadata extraction (inline, non-blocking) ───
    if extraction_context:
        try:
            from scruticell.preprocessing.memory.modality_cache import find_code_cache, store_code_cache
            from scruticell.preprocessing.prompts import METADATA_EXTRACTION_PROMPT
            from scruticell.preprocessing.tools.extraction_sandbox import run_extraction_metadata
            from langchain_openai import ChatOpenAI
            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

            import json as _json

            # Check code cache first
            cached_flowchart = find_code_cache(
                gse_id, field_value_maps_raw, data_file_signals, "experiment_flowchart")
            cached_extraction = find_code_cache(
                gse_id, field_value_maps_raw, data_file_signals, "extraction_code")

            if cached_flowchart and cached_extraction:
                experiment_flowchart = cached_flowchart
                extraction_code = cached_extraction
                logger.info("Metadata extraction: full cache hit for %s", gse_id)
            else:
                llm = ChatOpenAI(
                    model=LLM_MODEL,
                    temperature=0.0,
                    openai_api_key=LLM_API_KEY,
                    openai_api_base=LLM_BASE_URL,
                )
                ctx_json = _json.dumps(extraction_context, ensure_ascii=False)
                response = llm.invoke(
                    METADATA_EXTRACTION_PROMPT + "\n\n## Data\n\n" + ctx_json
                )
                result_text = str(response.content) if hasattr(response, "content") else str(response)
                if result_text.startswith("```"):
                    lines = result_text.split("\n")
                    result_text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
                result = _json.loads(result_text)

                experiment_flowchart = result.get("experiment_flowchart", "")
                extraction_code = result.get("extraction_code", "")

                # Store in code cache for next time
                if experiment_flowchart:
                    store_code_cache(gse_id, field_value_maps_raw, data_file_signals,
                                     "experiment_flowchart", experiment_flowchart)
                if extraction_code:
                    store_code_cache(gse_id, field_value_maps_raw, data_file_signals,
                                     "extraction_code", extraction_code)

            if extraction_code and raw_gsm:
                # Serialize GSM entries for sandbox (all values must be strings)
                from scruticell.preprocessing.context.extraction_context import _serialize_gsm_entries
                serialized_gsm = _serialize_gsm_entries(raw_gsm)

                # Sandbox: execute extraction code against each GSM
                from scruticell.preprocessing.models import FieldValueMap, ValueGroup
                from scruticell.preprocessing.verification import verify_extraction_results
                extracted_fields: dict[str, dict[str, list[str]]] = {}
                per_gsm_results: dict[str, dict] = {}
                for gsm_id, raw_entry in serialized_gsm.items():
                    try:
                        meta = run_extraction_metadata(extraction_code, raw_entry)
                        if meta:
                            per_gsm_results[gsm_id] = meta
                            for field, value in meta.items():
                                extracted_fields.setdefault(field, {}).setdefault(value, []).append(gsm_id)
                    except Exception as e:
                        logger.debug("Extraction failed for %s: %s", gsm_id, e)

                # Verify extraction quality
                verif = verify_extraction_results(per_gsm_results, len(raw_gsm))
                if not verif.passed:
                    logger.warning("Metadata extraction verification failed: %s", verif.failure_summary())

                # Merge extracted fields into field_value_maps (even if partially failed)
                for field_name, value_map in extracted_fields.items():
                    if field_name not in field_value_maps_raw:
                        fv = FieldValueMap(field_name=field_name, category="extracted")
                        field_value_maps_raw[field_name] = fv
                    else:
                        fv = field_value_maps_raw[field_name]
                    existing_values = {vg.value for vg in fv.values}
                    for val, gsm_ids in value_map.items():
                        if val not in existing_values:
                            fv.values.append(ValueGroup(value=val, gsm_ids=gsm_ids, gsm_count=len(gsm_ids)))
                            existing_values.add(val)
                    fv.distinct_count = len(fv.values)

        except Exception as e:
            logger.warning("Metadata extraction failed (continuing without enrichment): %s", e)

    # Build the report dict
    report_dict = {
        "gse_id": gse_id,
        "download_dir": str(root),     # Pass through to organize_output
        "experiment_flowchart": experiment_flowchart,
        "gse_info": gse_info,
        "total_files": len(data_file_signals),
        "total_gsm_count": total_gsm,
        "errors": errors,
        # L1: Compact metadata
        "field_value_maps": {
            name: fv.model_dump() for name, fv in field_value_maps_raw.items()
        },
        # L2+L3: Compact file signals with content previews
        "file_signal_groups": [sg.model_dump() for sg in signal_groups],
    }

    # Fallbacks (small datasets where compact is overkill)
    if total_gsm <= _GSM_THRESHOLD:
        report_dict["gsm_entries"] = gsm_entries

    # Always keep raw file summaries for organize_tool
    report_dict["file_summaries"] = file_summaries_raw

    return report_dict
