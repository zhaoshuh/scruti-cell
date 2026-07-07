"""Recursive archive decompression with path tree tracking.

Handles: .gz, .tar.gz, .tgz, .zip, .bz2, .7z
Records the full decompression ancestry for each file.
"""

from __future__ import annotations

import gzip
import logging
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_GZIP_EXTS = {".gz"}
_TAR_EXTS = {".tar.gz", ".tgz", ".tar.bz2", ".tar"}
_ZIP_EXTS = {".zip"}
_BZ2_EXTS = {".bz2"}
_ARCHIVE_EXTS = _GZIP_EXTS | _TAR_EXTS | _ZIP_EXTS | _BZ2_EXTS

_MAX_DEPTH = 5


@dataclass
class DecompressionNode:
    """One node in the decompression tree."""

    path: Path
    original_name: str
    parent: DecompressionNode | None = None
    depth: int = 0
    archive_type: str = ""  # "gzip", "tar", "zip", "bz2", "raw"
    children: list[DecompressionNode] = field(default_factory=list)
    error: str = ""

    @property
    def ancestry(self) -> str:
        """Path-like lineage from root to this node."""
        parts: list[str] = []
        node: DecompressionNode | None = self
        while node is not None:
            parts.append(node.original_name)
            node = node.parent
        return "/".join(reversed(parts))

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def flatten(self) -> list[DecompressionNode]:
        """Return all nodes in DFS order."""
        result = [self]
        for child in self.children:
            result.extend(child.flatten())
        return result

    def leaves(self) -> list[DecompressionNode]:
        """Return only leaf nodes (final extracted files)."""
        if not self.children:
            return [self]
        leaves: list[DecompressionNode] = []
        for child in self.children:
            leaves.extend(child.leaves())
        return leaves


class Decompressor:
    """Recursively decompress archives, building a path tree.

    Usage:
        tree = Decompressor().decompress(Path("downloads/GSE12345"))
        for leaf in tree.leaves():
            print(leaf.ancestry, "→", leaf.path)
    """

    def __init__(self, max_depth: int = _MAX_DEPTH):
        self._max_depth = max_depth

    def decompress(self, root_dir: Path) -> DecompressionNode:
        """Decompress all archives recursively under root_dir.

        Returns the root DecompressionNode. Original files are left in place;
        extracted files appear as children in the tree.
        """
        if not root_dir.is_dir():
            raise ValueError(f"Not a directory: {root_dir}")

        root = DecompressionNode(
            path=root_dir,
            original_name=root_dir.name,
            depth=0,
            archive_type="raw",
        )

        for entry in sorted(root_dir.iterdir()):
            child = self._process_entry(entry, root)
            if child is not None:
                root.children.append(child)

        return root

    def _process_entry(
        self, entry: Path, parent: DecompressionNode
    ) -> DecompressionNode | None:
        """Process a file or directory entry."""
        if entry.name.startswith("."):
            return None

        if entry.is_dir():
            node = DecompressionNode(
                path=entry,
                original_name=entry.name,
                parent=parent,
                depth=parent.depth + 1,
                archive_type="raw",
            )
            for sub in sorted(entry.iterdir()):
                child = self._process_entry(sub, node)
                if child is not None:
                    node.children.append(child)
            return node

        name_lower = entry.name.lower()
        archive_type = self._detect_archive(name_lower)

        if archive_type:
            entry_parent = entry.parent
            _TENX_COMPANIONS = {"barcodes.tsv.gz", "features.tsv.gz", "genes.tsv.gz"}
            if name_lower in _TENX_COMPANIONS:
                if entry_parent and (entry_parent / "matrix.mtx.gz").exists():
                    archive_type = ""
            elif name_lower == "matrix.mtx.gz":
                if entry_parent:
                    has_barcodes = (entry_parent / "barcodes.tsv.gz").exists()
                    has_features = (entry_parent / "features.tsv.gz").exists() or (entry_parent / "genes.tsv.gz").exists()
                    if has_barcodes and has_features:
                        archive_type = ""

        if archive_type and parent.depth < self._max_depth:
            try:
                extracted = self._extract(entry, archive_type)
                node = DecompressionNode(
                    path=entry,
                    original_name=entry.name,
                    parent=parent,
                    depth=parent.depth + 1,
                    archive_type=archive_type,
                )
                for item in extracted:
                    child = self._process_entry(item, node)
                    if child is not None:
                        node.children.append(child)
                return node
            except Exception as exc:
                logger.warning("Failed to extract %s: %s", entry, exc)
                return DecompressionNode(
                    path=entry,
                    original_name=entry.name,
                    parent=parent,
                    depth=parent.depth + 1,
                    archive_type=archive_type,
                    error=str(exc),
                )

        return DecompressionNode(
            path=entry,
            original_name=entry.name,
            parent=parent,
            depth=parent.depth + 1,
            archive_type="raw",
        )

    def _detect_archive(self, name_lower: str) -> str:
        """Detect archive type from filename."""
        if name_lower.endswith(".tar.gz") or name_lower.endswith(".tgz"):
            return "tar"
        if name_lower.endswith(".tar.bz2"):
            return "tar"
        if name_lower.endswith(".tar"):
            return "tar"
        if name_lower.endswith(".zip"):
            return "zip"
        if name_lower.endswith(".bz2"):
            return "bz2"
        if name_lower.endswith(".gz"):
            return "gzip"
        return ""

    def _extract(self, filepath: Path, archive_type: str) -> list[Path]:
        """Extract archive to a sibling directory, return extracted paths."""
        dest_dir = filepath.parent / f"{filepath.stem}_extracted"
        dest_dir.mkdir(exist_ok=True)

        if archive_type == "gzip":
            out_name = filepath.stem
            out_path = dest_dir / out_name
            with gzip.open(filepath, "rb") as f_in:
                with open(out_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            return [out_path]

        elif archive_type == "tar":
            with tarfile.open(filepath, "r:*") as tar:
                tar.extractall(path=dest_dir)
            return list(dest_dir.iterdir())

        elif archive_type == "zip":
            with zipfile.ZipFile(filepath, "r") as zf:
                zf.extractall(path=dest_dir)
            return list(dest_dir.iterdir())

        elif archive_type == "bz2":
            import bz2
            out_name = filepath.stem
            out_path = dest_dir / out_name
            with bz2.open(filepath, "rb") as f_in:
                with open(out_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            return [out_path]

        return []
