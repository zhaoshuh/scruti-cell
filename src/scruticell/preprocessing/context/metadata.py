"""Extracts structured signals from GSM metadata.

Produces MetadataSignal instances — text descriptions of what the SOFT
metadata says about each sample. These are OBSERVATIONS, not classifications.

When adding new modalities to skills/modality_knowledge/SKILL.md, also update
_MODALITY_KEYWORDS below to include keywords for the new modality.
"""

from __future__ import annotations

from typing import Any

from scruticell.preprocessing.models import MetadataSignal


# Keywords that strongly indicate a modality in the title/description
_MODALITY_KEYWORDS: dict[str, str] = {
    "single-nucleus": "nucleus_rna",
    "single nucleus": "nucleus_rna",
    "snRNA-seq": "nucleus_rna",
    "snRNAseq": "nucleus_rna",
    "snRNA": "nucleus_rna",
    "scRNA-seq": "rna",
    "scRNAseq": "rna",
    "scRNA": "rna",
    "single-cell RNA": "rna",
    "ATAC-seq": "atac",
    "scATAC-seq": "atac",
    "scATAC": "atac",
    "ATAC": "atac",
    "Multiome": "multiome",
    "Multi-ome": "multiome",
    "multi-omics": "multiome",
    "Visium": "spatial",
    "Slide-seq": "spatial",
    "Slide-seqV2": "spatial",
    "MERFISH": "spatial",
    "Stereo-seq": "spatial",
    "spatial transcriptom": "spatial",
    "CITE-seq": "cite_seq",
    "TotalSeq": "cite_seq",
    "ADT": "cite_seq",
    "antibody-derived tag": "cite_seq",
    "TCR": "tcr",
    "T-cell receptor": "tcr",
    "BCR": "bcr",
    "B-cell receptor": "bcr",
    "immunoglobulin": "bcr",
    "VDJ": "v dj",
    "Perturb-seq": "perturb",
    "CRISPR screen": "perturb",
    "CRISPRi": "perturb",
    "CRISPRa": "perturb",
    "CROP-seq": "perturb",
    "Bisulfite-Seq": "methylation",
    "Bisulfite-seq": "methylation",
    "WGBS": "methylation",
    "RRBS": "methylation",
    "MethylC-seq": "methylation",
    "snmC-seq": "methylation",
    "snmC-seq2": "methylation",
    "methylation": "methylation",
    "proteomics": "protein",
    "LC-MS": "protein",
    "mass spectrometry": "protein",
}


def _normalize_suspension(metadata: dict[str, Any], characteristics: dict[str, Any]) -> str:
    """Detect whether the sample is single-cell or single-nucleus."""
    # Check explicit suspension fields in characteristics
    suspension_fields = ["suspension_type", "suspension", "sample_type", "preparation"]
    for key in suspension_fields:
        val = str(characteristics.get(key, "")).lower()
        if "nucleus" in val or "nuclei" in val:
            return "nucleus"
        if val in ("cell", "cells", "single cell"):
            return "cell"

    # Check molecule field
    molecule = str(metadata.get("molecule_ch1", "")).lower()
    if "nuclear" in molecule:
        return "nucleus"

    return "unknown"


def _detect_keywords(text: str) -> list[str]:
    """Scan text for modality-indicating keywords."""
    if not text:
        return []
    text_lower = text.lower()
    found: list[str] = []
    for keyword, hint in _MODALITY_KEYWORDS.items():
        if keyword.lower() in text_lower:
            if hint not in found:
                found.append(hint)
    return found


def extract_metadata_signals(
    gsm_id: str,
    metadata: dict[str, Any],
    characteristics: dict[str, Any],
) -> MetadataSignal:
    """Extract structured signals from one GSM's metadata.

    Args:
        gsm_id: GSM accession (e.g. "GSM1234567")
        metadata: Raw GEO metadata dict (from GEOparse)
        characteristics: Parsed characteristics key-value dict

    Returns:
        MetadataSignal with all observed signals (no classification decisions)
    """
    molecule = str(metadata.get("molecule_ch1", "") or "").strip()
    library_strategy = str(metadata.get("library_strategy", "") or "").strip()
    library_source = str(metadata.get("library_source", "") or "").strip()
    title = str(metadata.get("title", "") or metadata.get("_geo_title", "") or "").strip()
    description = str(metadata.get("description", "") or "").strip()
    organism = str(
        metadata.get("organism_ch1", "")
        or metadata.get("taxid_ch1", "")
        or ""
    ).strip()
    suspension = _normalize_suspension(metadata, characteristics)

    # Build keywords from title and description together
    keywords = _detect_keywords(f"{title} {description}")

    # Also check characteristics for hints
    for val in characteristics.values():
        extra = _detect_keywords(str(val))
        for kw in extra:
            if kw not in keywords:
                keywords.append(kw)

    return MetadataSignal(
        gsm_id=gsm_id,
        molecule=molecule,
        library_strategy=library_strategy,
        library_source=library_source,
        title=title,
        description=description,
        organism=organism,
        suspension=suspension,
        keywords=keywords,
    )
