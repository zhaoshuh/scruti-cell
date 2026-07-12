"""Extracts structured signals from file names and directory structures.

Detects patterns, keywords, and companion-file relationships without
making any classification decisions.

When adding new modalities to skills/modality_knowledge/SKILL.md, also update
_KEYWORD_PATTERNS below to include filename keywords for the new modality.
"""

from __future__ import annotations

from pathlib import Path

from scruticell.preprocessing.models import FilenameSignal

# Filename keywords that hint at data modality or file role
_KEYWORD_PATTERNS: dict[str, str] = {
    "barcodes": "barcodes",
    "barcode": "barcodes",
    "features": "features",
    "genes": "features",
    "matrix": "matrix",
    "counts": "counts",
    "atac": "atac",
    "peaks": "atac",
    "peak": "atac",
    "fragments": "atac",
    "fragment": "atac",
    "adt": "cite_seq",
    "antibody": "cite_seq",
    "hashtag": "cite_seq",
    "hto": "cite_seq",
    "spatial": "spatial",
    "visium": "spatial",
    "tissue_positions": "spatial",
    "slide": "spatial",
    "merfish": "spatial",
    "stereo": "spatial",
    "tcr": "tcr",
    "bcr": "bcr",
    "contig": "v dj",
    "clonotype": "v dj",
    "vdj": "v dj",
    "meth": "methylation",
    "methylation": "methylation",
    "cpg": "methylation",
    "cov": "methylation_cov",
    "methyl": "methylation",
    "sgRNA": "perturb",
    "sgrna": "perturb",
    "guide": "perturb",
    "crispr": "perturb",
    "perturb": "perturb",
    "protein": "protein",
    "peptide": "protein",
    "proteomic": "protein",
    "rna": "rna",
    "scrna": "rna",
    "scrnaseq": "rna",
    "metadata": "metadata",
    "obs": "metadata",
    "var": "metadata",
}


def _detect_filename_keywords(filename: str) -> list[str]:
    """Detect keywords from a lowercased filename stem."""
    name_lower = filename.lower()
    found: list[str] = []
    for keyword, hint in _KEYWORD_PATTERNS.items():
        if keyword in name_lower:
            if hint not in found:
                found.append(hint)
    return found


def _detect_10x_companions(file_path: Path) -> tuple[bool, list[str]]:
    """Check if this file is part of a 10x Genomics three-file set.

    Returns (is_10x, companion_files_list).
    """
    parent = file_path.parent
    name = file_path.name.lower()

    # Check for barcodes companion
    barcodes_candidates = [
        p for p in parent.iterdir()
        if p.suffix.lower() in (".gz", ".tsv")
        and "barcodes" in p.name.lower()
    ]
    # Check for features/genes companion
    features_candidates = [
        p for p in parent.iterdir()
        if p.suffix.lower() in (".gz", ".tsv")
        and ("features" in p.name.lower() or "genes" in p.name.lower())
    ]
    # Check for matrix
    matrix_candidates = [
        p for p in parent.iterdir()
        if p.suffix.lower() in (".gz", ".mtx")
        and "matrix" in p.name.lower()
    ]

    is_10x = False
    companions: list[str] = []

    if "barcodes" in name:
        if features_candidates and matrix_candidates:
            is_10x = True
            companions = [str(fc.name) for fc in features_candidates] + [str(mc.name) for mc in matrix_candidates]
    elif "features" in name or "genes" in name:
        if barcodes_candidates and matrix_candidates:
            is_10x = True
            companions = [str(bc.name) for bc in barcodes_candidates] + [str(mc.name) for mc in matrix_candidates]
    elif "matrix" in name and (name.endswith(".mtx") or name.endswith(".mtx.gz")):
        if barcodes_candidates and features_candidates:
            is_10x = True
            companions = [str(bc.name) for bc in barcodes_candidates] + [str(fc.name) for fc in features_candidates]

    return is_10x, companions


def _detect_subdirectory_hints(ancestry: str) -> str:
    """Look for modality hints in subdirectory names within the ancestry path."""
    parts = ancestry.replace("\\", "/").split("/")
    hints: list[str] = []
    for part in parts:
        part_lower = part.lower()
        if "rna" in part_lower and "atac" not in part_lower:
            hints.append("rna_subdir")
        elif "atac" in part_lower:
            hints.append("atac_subdir")
        elif "spatial" in part_lower:
            hints.append("spatial_subdir")
        elif "adt" in part_lower or "antibody" in part_lower:
            hints.append("protein_subdir")
    return ",".join(hints) if hints else ""


def extract_filename_signals(file_path: Path, ancestry: str) -> FilenameSignal:
    """Extract structured signals from a single file's name and location.

    Args:
        file_path: Path to the file
        ancestry: Full decompression ancestry path (e.g. "GSE12345/RAW.tar_extracted/GSM_001/barcodes.tsv.gz")

    Returns:
        FilenameSignal with keyword hints, companion info, and structure clues
    """
    filename = file_path.name
    keywords = _detect_filename_keywords(filename)
    is_10x, companions = _detect_10x_companions(file_path)
    subdirectory_hint = _detect_subdirectory_hints(ancestry)

    return FilenameSignal(
        keywords=keywords,
        companion_files=companions,
        subdirectory_hint=subdirectory_hint,
        is_10x_format=is_10x,
        raw_name=filename,
    )
