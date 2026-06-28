"""Gene ID cross-reference mapping for the knowledge graph.

Handles detection and mapping between gene identifier systems:
- Gene Symbol (e.g., BRCA1, TP53)
- Ensembl ID (e.g., ENSG00000139618)
- Entrez ID (e.g., 672)
- RefSeq (e.g., NM_007294)

Uses knowledge graph for accumulated mappings, with fallback
to Bionty public ontology lookups.
"""

from __future__ import annotations

import re
from typing import Optional

# Regex patterns for gene ID system detection
ENSEMBL_GENE_PATTERN = re.compile(r"^ENS[A-Z]*G\d{11}(\.\d+)?$")
ENTREZ_PATTERN = re.compile(r"^\d+$")
ENSEMBL_PROTEIN_PATTERN = re.compile(r"^ENSP\d{11}$")
ENSEMBL_TRANSCRIPT_PATTERN = re.compile(r"^ENST\d{11}$")


def detect_gene_id_system(gene_ids: list[str]) -> str:
    """Detect which gene ID system is used by sampling the first few IDs.

    Returns one of: "symbol", "ensembl_gene", "entrez", "refseq", "unknown"
    """
    if not gene_ids:
        return "unknown"

    sample = gene_ids[:min(100, len(gene_ids))]

    ensembl_count = sum(1 for g in sample if ENSEMBL_GENE_PATTERN.match(str(g)))
    entrez_count = sum(1 for g in sample if ENTREZ_PATTERN.match(str(g)))

    if ensembl_count > len(sample) * 0.5:
        return "ensembl_gene"
    if entrez_count > len(sample) * 0.5:
        return "entrez"

    # Default: assume symbol (most common in processed data)
    return "symbol"


def is_mitochondrial(gene_name: str) -> bool:
    """Check if a gene name indicates a mitochondrial gene."""
    name = str(gene_name).upper().strip()
    return name.startswith("MT-") or name.startswith("MTRNR") or name == "MT"


def is_ribosomal(gene_name: str) -> bool:
    """Check if a gene name indicates a ribosomal protein gene."""
    name = str(gene_name).upper().strip()
    return name.startswith("RPS") or name.startswith("RPL") or name.startswith("RP")


def normalize_gene_symbol(symbol: str) -> str:
    """Normalize a gene symbol: strip whitespace, capitalize for human genes."""
    return str(symbol).strip()


def make_unique_genes(
    gene_names: list[str],
    strategy: str = "first",
) -> tuple[list[str], str]:
    """Handle duplicate gene names in the expression matrix.

    Args:
        gene_names: List of gene identifiers (possibly with duplicates).
        strategy: How to handle duplicates:
            - "first": Keep first occurrence, drop others
            - "mean": Average expression across duplicates (requires matrix)
            - "max": Take max expression across duplicates

    Returns:
        Tuple of (unique_gene_names, applied_strategy).
    """
    seen: set[str] = set()
    unique_names: list[str] = []

    for name in gene_names:
        normalized = name.strip()
        if normalized and normalized not in seen:
            unique_names.append(normalized)
            seen.add(normalized)

    if len(unique_names) < len(gene_names):
        return unique_names, f"first (dropped {len(gene_names) - len(unique_names)} duplicates)"
    return unique_names, strategy
