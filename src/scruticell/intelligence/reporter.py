"""Structured reporter: packages inspection results as LLM-ready JSON.

Combines:
1. Decompression tree (path lineages)
2. File summaries (Inspector output)
3. SOFT metadata (GEOparse parsed data)

Produces a GseReport dataclass that serializes to clean JSON.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from scruticell.intelligence.decompressor import DecompressionNode, Decompressor
from scruticell.intelligence.inspector import Inspector

logger = logging.getLogger(__name__)


@dataclass
class GsmInfo:
    """Structured summary of one GSM entry."""
    gsm_id: str
    title: str = ""
    source_name: str = ""
    characteristics: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    organism: str = ""
    molecule: str = ""
    supplementary_files: list[str] = field(default_factory=list)


@dataclass
class GseInfo:
    """Structured summary of GSE-level metadata."""
    gse_id: str
    title: str = ""
    summary: str = ""
    overall_design: str = ""
    pubmed_id: str = ""
    organism: str = ""
    platform_ids: list[str] = field(default_factory=list)


@dataclass
class GseReport:
    """Complete structured report for a GSE accession.

    Designed to be serialized to JSON and fed to an LLM for
    batch-matrix-metadata planning.
    """
    gse_id: str
    gse_info: GseInfo = field(default_factory=lambda: GseInfo(""))
    gsm_entries: list[GsmInfo] = field(default_factory=list)

    # Decompression tree (flattened leaves)
    file_tree: list[dict[str, Any]] = field(default_factory=list)

    # File summaries keyed by ancestry path
    file_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Statistics
    total_files: int = 0
    # ALL data files (not pre-filtered as matrix candidates — LLM decides)
    data_files: list[str] = field(default_factory=list)
    # Mapping: GSM ID -> list of file ancestry keys
    gsm_file_map: dict[str, list[str]] = field(default_factory=dict)
    # Per-file content summaries for LLM independent judgment
    file_summaries: dict[str, dict[str, Any]] = field(default_factory=dict)

    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return _serialize(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GseReport:
        """Reconstruct a GseReport from a serialized dict."""
        report = cls(gse_id=data.get("gse_id", ""))
        if "gse_info" in data and isinstance(data["gse_info"], dict):
            gi = data["gse_info"]
            report.gse_info = GseInfo(
                gse_id=gi.get("gse_id", ""),
                title=gi.get("title", ""),
                summary=gi.get("summary", ""),
                overall_design=gi.get("overall_design", ""),
                pubmed_id=gi.get("pubmed_id", ""),
                organism=gi.get("organism", ""),
                platform_ids=gi.get("platform_ids", []),
            )
        if "gsm_entries" in data:
            report.gsm_entries = [
                GsmInfo(
                    gsm_id=g.get("gsm_id", ""),
                    title=g.get("title", ""),
                    source_name=g.get("source_name", ""),
                    characteristics=g.get("characteristics", {}),
                    description=g.get("description", ""),
                    organism=g.get("organism", ""),
                    molecule=g.get("molecule", ""),
                    supplementary_files=g.get("supplementary_files", []),
                )
                for g in data["gsm_entries"]
            ]
        if "file_tree" in data:
            report.file_tree = data["file_tree"]
        if "file_summaries" in data:
            report.file_summaries = data["file_summaries"]
        report.total_files = data.get("total_files", 0)
        report.data_files = data.get("data_files", []) or data.get("matrix_candidates", [])  # backward compat
        report.gsm_file_map = data.get("gsm_file_map", {})
        report.errors = data.get("errors", [])
        return report


def _extract_base_filename(ancestry: str) -> str:
    """Extract original filename from ancestry path.

    'GSE328098/RAW_extracted/GSM9672365_matrix.mtx' -> 'GSM9672365_matrix.mtx'
    """
    parts = ancestry.replace("\\", "/").split("/")
    for part in reversed(parts):
        if "." in part and not part.endswith("_extracted"):
            return part
    return parts[-1]


def _extract_gsm_file_map(
    file_summaries: dict[str, dict[str, Any]],
) -> dict[str, list[str]]:
    """Build gsm_file_map by extracting GSM IDs from ancestry paths."""
    import re
    gsm_file_map: dict[str, list[str]] = {}
    for ancestry in file_summaries:
        m = re.search(r"GSM\d+", ancestry)
        if m:
            gsm = m.group(0)
            gsm_file_map.setdefault(gsm, []).append(ancestry)
    return gsm_file_map


def _serialize(obj: Any) -> Any:
    """Recursively convert non-serializable types to JSON-compatible values."""
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, Path):
        return str(obj)
    if obj is None:
        return None
    return obj


class Reporter:
    """Build a GseReport from a downloaded GSE directory.

    Usage:
        reporter = Reporter()
        report = reporter.build_report("GSE12345", download_dir)
        print(report.to_json())
    """

    def __init__(self):
        self._decompressor = Decompressor()
        self._inspector = Inspector()

    def build_report(
        self,
        gse_id: str,
        download_dir: Path,
        gsm_entries: dict[str, dict[str, Any]] | None = None,
        gse_metadata: dict[str, Any] | None = None,
        file_gsm_map: dict[str, str] | None = None,
    ) -> GseReport:
        """Build a complete structured report.

        Args:
            gse_id: GEO accession ID.
            download_dir: Directory containing downloaded files.
            gsm_entries: Parsed GSM metadata from SOFT file.
            gse_metadata: Parsed GSE-level metadata from SOFT file.

        Returns:
            GseReport ready for serialization.
        """
        report = GseReport(gse_id=gse_id)

        # GSE metadata
        if gse_metadata:
            report.gse_info = self._build_gse_info(gse_id, gse_metadata)

        # GSM entries
        if gsm_entries:
            report.gsm_entries = [
                self._build_gsm_info(gsm_id, data)
                for gsm_id, data in gsm_entries.items()
            ]

        # Decompression
        try:
            tree = self._decompressor.decompress(download_dir)
            report.file_tree = self._build_file_tree(tree)
        except Exception as exc:
            report.errors.append(f"Decompression failed: {exc}")

        # Inspection
        try:
            leaves = tree.leaves() if tree else []
        except Exception:
            report.errors.append("No files to inspect")
            return report

        report.total_files = len(leaves)

        for leaf in leaves:
            if leaf.path.is_dir():
                continue
            ancestry = leaf.ancestry
            try:
                summary = self._inspector.inspect_file(leaf.path, ancestry)
                d = self._inspector.to_dict(summary)
                report.file_summaries[ancestry] = d

                # List all data files (LLM decides which are matrices)
                if summary.file_type in ("h5", "h5ad", "mtx", "rds", "loom", "csv"):
                    report.data_files.append(ancestry)
            except Exception as exc:
                report.errors.append(f"Inspection failed for {ancestry}: {exc}")

        # Deduplication (GSE RAW.tar may extract same files as GSM downloads)
        try:
            report.file_summaries = self._inspector.deduplicate(
                dict(report.file_summaries)
            )
        except Exception as exc:
            logger.debug("Deduplication skipped: %s", exc)

        # Build gsm_file_map from file_gsm_map (or extract from filenames)
        if file_gsm_map:
            gsm_file_map: dict[str, list[str]] = {}
            for ancestry in report.file_summaries:
                base = _extract_base_filename(ancestry)
                if base in file_gsm_map:
                    gsm = file_gsm_map[base]
                    gsm_file_map.setdefault(gsm, []).append(ancestry)
        else:
            # Fallback: extract GSM ID from filenames
            gsm_file_map = _extract_gsm_file_map(report.file_summaries)
        report.gsm_file_map = gsm_file_map
        # Remove __GSE__ entries if per-GSM files are available
        gsm_entries_only = {k: v for k, v in gsm_file_map.items() if k != "__GSE__"}
        if gsm_entries_only:
            report.gsm_file_map = gsm_entries_only

        return report

    def _build_gse_info(self, gse_id: str, metadata: dict[str, Any]) -> GseInfo:
        """Extract GSE-level info from parsed SOFT metadata."""
        return GseInfo(
            gse_id=gse_id,
            title=str(metadata.get("title", "")),
            summary=str(metadata.get("summary", "")),
            overall_design=str(metadata.get("overall_design", "")),
            pubmed_id=str(metadata.get("pubmed_id", "")),
            organism=str(metadata.get("organism", "")),
            platform_ids=self._extract_list(metadata.get("platform_ids", [])),
        )

    def _build_gsm_info(self, gsm_id: str, data: dict[str, Any]) -> GsmInfo:
        """Extract GSM-level info from parsed data."""
        chars: dict[str, Any] = {}
        if isinstance(data.get("characteristics"), dict):
            chars = data["characteristics"]
        else:
            # Data might already be the characteristics dict directly
            for k, v in data.items():
                if k not in ("title", "source_name", "description", "organism",
                             "molecule", "geo_accession"):
                    chars[k] = v

        suppl: list[str] = []
        for k, v in data.items():
            if "supplementary" in k.lower() and v:
                if isinstance(v, list):
                    suppl.extend(str(x) for x in v)
                else:
                    suppl.append(str(v))

        gsm_info = GsmInfo(
            gsm_id=gsm_id,
            title=str(data.get("title", "")),
            source_name=str(data.get("source_name", "")),
            characteristics=chars,
            description=str(data.get("description", "")),
            organism=str(data.get("organism", "")),
            molecule=str(data.get("molecule", "")),
            supplementary_files=suppl,
        )
        return gsm_info

    def _build_file_tree(self, root: DecompressionNode) -> list[dict[str, Any]]:
        """Flatten the decompression tree to a list of leaf entries."""
        result: list[dict[str, Any]] = []
        for leaf in root.leaves():
            result.append({
                "ancestry": leaf.ancestry,
                "filename": leaf.path.name if not leaf.path.is_dir() else f"{leaf.path.name}/",
                "archive_type": leaf.archive_type if leaf.archive_type != "raw" else None,
                "depth": leaf.depth,
                "is_directory": leaf.path.is_dir(),
            })
        return result

    def _extract_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]
