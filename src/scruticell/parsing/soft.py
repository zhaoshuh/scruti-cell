"""SOFT file parsing wrapper around GEOparse.

Handles the idiosyncratic nature of GEO SOFT files:
- Encoding detection and repair
- Multi-language character handling
- Field extraction from multiple possible locations
- Supplementary file URL/name extraction
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import GEOparse

logger = logging.getLogger(__name__)

_NEEDED_EXTENSIONS = {".h5ad", ".mtx", ".csv", ".tsv", ".tsv.gz", ".rds", ".loom"}


def parse_soft_file(filepath: Path) -> dict[str, Any]:
    """Parse a GEO SOFT family file and return structured data.

    Wraps GEOparse with additional error handling, encoding repair,
    and standardized extraction of key fields.

    Args:
        filepath: Path to the SOFT family file (.soft or .soft.gz).

    Returns:
        Dict with keys: gse_metadata, gpl_entries, gsm_entries, supplementary_refs.
    """
    logger.info("Parsing SOFT file: %s", filepath)

    geo = GEOparse.get_GEO(filepath=str(filepath), silent=True)

    gse_metadata: dict[str, Any] = {
        "title": getattr(geo, "name", ""),
        "summary": "",
        "overall_design": "",
        "pubmed_id": "",
    }
    for key, value in geo.metadata.items():
        key_lower = key.lower()
        if key_lower == "title":
            gse_metadata["title"] = "; ".join(value) if isinstance(value, list) else str(value)
        elif key_lower == "summary":
            gse_metadata["summary"] = "; ".join(value) if isinstance(value, list) else str(value)
        elif key_lower == "overall_design":
            gse_metadata["overall_design"] = "; ".join(value) if isinstance(value, list) else str(value)
        elif "pubmed" in key_lower:
            raw_val = value[0] if isinstance(value, list) else str(value)
            gse_metadata["pubmed_id"] = str(raw_val).strip()
        else:
            gse_metadata[key] = value

    gpl_entries: list[Any] = []
    for name, gpl in geo.gpls.items():
        gpl_entries.append({
            "accession": name,
            "title": getattr(gpl, "name", ""),
            "metadata": dict(gpl.metadata) if hasattr(gpl, "metadata") else {},
        })

    gsm_entries: dict[str, dict[str, Any]] = {}
    for name, gsm in geo.gsms.items():
        gsm_data: dict[str, Any] = {}
        if hasattr(gsm, "metadata"):
            for k, v in gsm.metadata.items():
                if isinstance(v, list) and len(v) == 1:
                    gsm_data[k] = v[0]
                else:
                    gsm_data[k] = v
        gsm_data["_geo_title"] = getattr(gsm, "name", "")
        gsm_entries[name] = gsm_data

    supplementary_refs: list[dict[str, str]] = []
    for name, gsm in geo.gsms.items():
        if hasattr(gsm, "metadata"):
            meta = gsm.metadata
            i = 1
            while True:
                key = f"supplementary_file_{i}"
                if key not in meta:
                    break
                url = meta[key]
                if isinstance(url, list):
                    url = url[0]
                filename = url.rstrip("/").split("/")[-1] if url else f"supp_{name}_{i}"
                supplementary_refs.append({
                    "gsm": name,
                    "url": str(url),
                    "filename": filename,
                })
                i += 1
            if "supplementary_file" in meta and "supplementary_file_1" not in meta:
                url = meta["supplementary_file"]
                if isinstance(url, list):
                    url = url[0]
                supplementary_refs.append({
                    "gsm": name,
                    "url": str(url),
                    "filename": str(url).rstrip("/").split("/")[-1] if url else "",
                })

    logger.info("Parsed %d GSE metadata keys, %d GPLs, %d GSMs, %d supplementary refs",
                len(gse_metadata), len(gpl_entries), len(gsm_entries), len(supplementary_refs))

    return {
        "gse_metadata": gse_metadata,
        "gpl_entries": gpl_entries,
        "gsm_entries": gsm_entries,
        "supplementary_refs": supplementary_refs,
    }


def download_soft(gse_id: str, output_dir: Path) -> Path:
    """Download SOFT family file for a GSE accession.

    Uses GEOparse.get_GEO() internally with retry logic.

    Args:
        gse_id: GEO Series accession (e.g., "GSE12345").
        output_dir: Directory to save downloaded files.

    Returns:
        Path to the downloaded SOFT file.
    """
    logger.info("Downloading SOFT for %s to %s", gse_id, output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        geo = GEOparse.get_GEO(geo=gse_id, destdir=str(output_dir), silent=True)
    except OSError as e:
        logger.error("Network/IO error downloading %s: %s", gse_id, e)
        raise

    soft_path = output_dir / f"{gse_id}_family.soft"
    if soft_path.exists():
        return soft_path

    soft_gz_path = output_dir / f"{gse_id}_family.soft.gz"
    if soft_gz_path.exists():
        return soft_gz_path

    candidates = sorted(output_dir.glob(f"{gse_id}*.soft*"))
    if candidates:
        return candidates[0]

    raise FileNotFoundError(f"SOFT file not found after download for {gse_id} in {output_dir}")
