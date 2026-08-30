"""LLM-driven file attribution for preprocessing output.

Given the decompressed download directory and the sample groups produced by the
classifier, asks the LLM to assign every file to a sample + modality + role.
No rule-based matching is used (no substring GSM matching, no filename
keywords, no extension filtering) — the LLM decides, using the sample metadata
and file names as evidence.

On failure (missing API key, timeout, unusable output) attribution returns
``{"ok": False}`` and the caller routes files to unclassified.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Suggested role vocabulary (prompt guidance only; not enforced in code).
_ROLE_VOCAB = (
    "matrix, barcodes, features, peaks, fragments, contigs, clonotypes, "
    "metadata, spatial_positions, spatial_coords, spatial_image, guide_info, other"
)

# Common modality vocabulary for prompt guidance.
_MODALITY_VOCAB = (
    "scrna_seq, snrna_seq, scatac_seq, spatial_transcriptomics, methylation, "
    "tcr_seq, bcr_seq, cite_seq, crispr, proteomics, other"
)


def _collect_files(download_root: Path, limit: int = 2000) -> list[str]:
    files = []
    for fp in sorted(download_root.rglob("*")):
        if fp.is_file() and not fp.name.startswith("."):
            files.append(str(fp.relative_to(download_root)))
            if len(files) >= limit:
                break
    return files


def llm_attribution(
    download_root: Path,
    sample_groups: list[dict],
    gse_id: str,
) -> dict:
    """Ask the LLM to attribute files to samples, modalities, and roles.

    Args:
        download_root: root of decompressed files.
        sample_groups: classifier sample_groups (gsm_ids, sample_key,
                       key_source, file_prefix, modalities, sample_metadata).
        gse_id: GSE accession.

    Returns:
        {
          "ok": bool,
          "files": [{"ancestry", "sample_key", "modality", "role"}],
          "unclassified": [ancestry, ...],
          "error": str | None,
        }
    """
    from langchain_openai import ChatOpenAI

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

    files = _collect_files(download_root)
    if not files:
        return {"ok": False, "files": [], "unclassified": [], "error": "no files found"}

    groups_brief = []
    for g in sample_groups:
        groups_brief.append({
            "sample_key": g.get("sample_key", ""),
            "key_source": g.get("key_source", "gsm_id"),
            "gsm_ids": g.get("gsm_ids", []),
            "file_prefix": g.get("file_prefix", ""),
            "modalities": sorted((g.get("modalities") or {}).keys()),
            "sample_metadata": g.get("sample_metadata", {}),
        })

    prompt = f"""You are organizing a GEO dataset ({gse_id}) that was downloaded and decompressed.

Assign every file below to exactly one sample and one modality, and give it a role.
Use the sample groups and their metadata to decide. Do not guess GSM membership
from short ID prefixes alone — only assign when the file clearly belongs.

## Sample groups
{json.dumps(groups_brief, ensure_ascii=False, indent=2)}

## Files (relative to the download root)
{json.dumps(files, ensure_ascii=False, indent=2)}

## Instructions
- modality should be one of: {_MODALITY_VOCAB}
- role should be one of: {_ROLE_VOCAB}
- sample_key must be one of the sample_keys above. If a file does not belong to any
  sample (e.g. README, figures, GEO SOFT/scripts), put it in "unclassified".
- A file shared by several samples (e.g. a common reference) may be marked once in
  "shared" with the list of sample_keys, plus its modality and role.

Return ONLY JSON (no markdown):
{{
  "files": [{{"ancestry": "...", "sample_key": "...", "modality": "...", "role": "..."}}],
  "shared": [{{"ancestry": "...", "sample_keys": ["..."], "modality": "...", "role": "..."}}],
  "unclassified": ["..."]
}}"""

    try:
        llm = ChatOpenAI(
            model=LLM_MODEL, temperature=0.0,
            openai_api_key=LLM_API_KEY, openai_api_base=LLM_BASE_URL,
        )
        response = llm.invoke(prompt)
        text = str(response.content) if hasattr(response, "content") else str(response)
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        data = json.loads(text)
    except Exception as exc:
        logger.warning("LLM attribution failed for %s: %s", gse_id, exc)
        return {"ok": False, "files": [], "shared": [], "unclassified": [], "error": str(exc)}

    files_out = data.get("files", []) if isinstance(data, dict) else []
    shared_out = data.get("shared", []) if isinstance(data, dict) else []
    unclassified = data.get("unclassified", []) if isinstance(data, dict) else []

    files_out = [
        f for f in files_out
        if isinstance(f, dict) and f.get("ancestry") and f.get("sample_key")
    ]
    if not files_out and not unclassified:
        return {"ok": False, "files": [], "shared": [],
                "unclassified": [], "error": "unusable output"}
    return {
        "ok": True,
        "files": files_out,
        "shared": shared_out,
        "unclassified": [str(u) for u in unclassified],
        "error": None,
    }
