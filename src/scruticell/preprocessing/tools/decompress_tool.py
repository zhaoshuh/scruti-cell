"""Decompress tool: recursively extracts nested archives in the download directory.

Wraps the existing Decompressor from intelligence/.
"""

from __future__ import annotations

import logging
from pathlib import Path

from scruticell.intelligence.decompressor import Decompressor

logger = logging.getLogger(__name__)


def decompress_files(download_dir: str) -> dict:
    """Recursively decompress all archive files (.tar.gz, .zip, .gz, .bz2) in a directory.

    Produces a flattened file tree with ancestry paths tracking where each file
    came from through the decompression chain. 10x Genomics companion files
    (barcodes.tsv.gz + features.tsv.gz + matrix.mtx.gz) are preserved as-is
    and NOT further decompressed.

    Args:
        download_dir: Path to the directory containing downloaded GEO files

    Returns:
        A dict with keys:
            root_dir: str — the root directory processed
            total_leaves: int — total number of leaf files after extraction
            file_tree: list[dict] — flattened tree entries with {ancestry, filename, is_directory}
            ancestry_paths: list[str] — full ancestry paths for each leaf file
    """
    root = Path(download_dir)
    if not root.exists():
        return {
            "root_dir": str(root),
            "total_leaves": 0,
            "file_tree": [],
            "ancestry_paths": [],
            "error": f"Directory not found: {download_dir}",
        }

    decompressor = Decompressor()
    tree = decompressor.decompress(root)

    leaves = list(tree.leaves())
    file_tree: list[dict] = []
    ancestry_paths: list[str] = []

    for leaf in leaves:
        ancestry = leaf.ancestry
        ancestry_paths.append(ancestry)
        file_tree.append({
            "ancestry": ancestry,
            "filename": leaf.original_name,
            "is_directory": leaf.path.is_dir() if leaf.path else False,
            "path": str(leaf.path) if leaf.path else "",
            "archive_type": leaf.archive_type,
        })

    return {
        "root_dir": str(root),
        "total_leaves": len(leaves),
        "file_tree": file_tree,
        "ancestry_paths": ancestry_paths,
    }
