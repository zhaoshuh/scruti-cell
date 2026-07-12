"""Download tool: fetches SOFT and supplementary files for a GSE accession."""

from __future__ import annotations

import logging
from pathlib import Path

from scruticell.pipeline.nodes.download import download_node
from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


def download_gse(gse_id: str, output_dir: str = "") -> dict:
    """Download SOFT metadata and supplementary data files from NCBI GEO for a given GSE accession.

    This tool fetches:
    - The SOFT file (GSE metadata with GSM sample entries)
    - Supplementary files (expression matrices, etc.) linked in the SOFT file

    Args:
        gse_id: NCBI GEO accession ID (e.g. "GSE215353")
        output_dir: Optional custom output directory (default: "output/<GSE_ID>")

    Returns:
        A dict with keys:
            download_dir: str — path to the download directory
            soft_file: str | None — path to the SOFT file if successfully downloaded
            supplementary_files: list[str] — paths to downloaded supplementary files
            errors: list[str] — any download errors
    """
    if not output_dir:
        output_directory = Path.cwd() / "output" / gse_id
    else:
        output_directory = Path(output_dir)

    state = PipelineState(
        gse_id=gse_id,
        output_dir=output_directory,
    )

    result = download_node(state)

    return {
        "download_dir": str(result.get("download_dir", "")),
        "soft_file": str(result.get("soft_file", "")) if result.get("soft_file") else None,
        "supplementary_files": [str(p) for p in result.get("supplementary_files", [])],
        "errors": result.get("download_errors", []),
        "file_gsm_map": result.get("file_gsm_map", {}),
    }
