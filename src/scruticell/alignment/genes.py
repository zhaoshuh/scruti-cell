"""Gene name unification for expression matrices.

Handles:
- Gene ID system detection (Symbol, Ensembl, Entrez)
- Cross-system mapping to unified Symbol
- Duplicate gene name resolution
- Mitochondrial/ribosomal gene flagging
- Deprecated symbol -> current symbol mapping
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import numpy as np
import pandas as pd

from scruticell.knowledge.gene_mapping import (
    detect_gene_id_system,
    is_mitochondrial,
    is_ribosomal,
    make_unique_genes,
    normalize_gene_symbol,
)

logger = logging.getLogger(__name__)

MYGENE_AVAILABLE = False
try:
    import mygene
    MYGENE_AVAILABLE = True
except ImportError:
    pass


def build_var_dataframe(
    gene_names: list[str],
    kb: Optional[Any] = None,
    species: str = "Homo sapiens",
) -> pd.DataFrame:
    """Build a standardized var DataFrame from raw gene identifiers.

    Args:
        gene_names: Raw gene identifiers from the expression matrix.
        kb: Optional knowledge graph for gene ID mapping lookups.
        species: Species name for mapping context.

    Returns:
        DataFrame with index = unified gene symbols, columns:
        - gene_symbol: unified symbol
        - original_id: raw identifier
        - id_system: detected ID system
        - is_mito: mitochondrial gene flag
        - is_ribo: ribosomal gene flag
    """
    if not gene_names:
        return pd.DataFrame()

    id_system = detect_gene_id_system(gene_names)
    logger.info("Detected gene ID system: %s", id_system)

    var = pd.DataFrame(index=range(len(gene_names)))
    var["original_id"] = [str(g) for g in gene_names]
    var["id_system"] = id_system

    if id_system == "symbol":
        var["gene_symbol"] = [normalize_gene_symbol(g) for g in gene_names]
    elif id_system in ("ensembl_gene", "entrez"):
        mapped = map_gene_ids_to_symbol(
            [str(g) for g in gene_names], id_system, kb
        )
        var["gene_symbol"] = [
            mapped.get(str(g), str(g).split(".")[0]) for g in gene_names
        ]
    else:
        var["gene_symbol"] = var["original_id"]

    var["is_mito"] = [is_mitochondrial(g) for g in var["gene_symbol"]]
    var["is_ribo"] = [is_ribosomal(g) for g in var["gene_symbol"]]

    unique_names, strategy = make_unique_genes(
        var["gene_symbol"].tolist(), strategy="first"
    )

    if len(unique_names) < len(var):
        logger.warning(
            "Dropped %d duplicate gene names (strategy: %s)",
            len(var) - len(unique_names), strategy,
        )
        var = var.drop_duplicates(subset="gene_symbol", keep="first")
        var = var.set_index("gene_symbol")
    else:
        var = var.set_index("gene_symbol")

    return var


def map_gene_ids_to_symbol(
    gene_ids: list[str],
    source_system: str,
    kb: Optional[Any] = None,
) -> dict[str, str]:
    """Map gene IDs from a non-symbol system to gene symbols.

    Three-tier lookup:
    1. Knowledge graph cache
    2. mygene.info API
    3. Local heuristic fallback
    """
    if not gene_ids:
        return {}

    result: dict[str, str] = {}
    remaining = list(gene_ids)

    if kb is not None:
        for gid in remaining[:]:
            mapping = kb.find_gene_mapping(gid, source_system)
            if mapping and mapping.get("target_symbol"):
                result[gid] = mapping["target_symbol"]
        remaining = [g for g in remaining if g not in result]

    if remaining and source_system == "ensembl_gene":
        online = _map_via_mygene(remaining, kb)
        result.update(online)
        remaining = [g for g in remaining if g not in result]

    if remaining:
        heuristic = _map_via_heuristic(remaining)
        result.update(heuristic)

    return result


def _map_via_mygene(
    gene_ids: list[str], kb: Optional[Any] = None
) -> dict[str, str]:
    """Map Ensembl gene IDs to symbols using mygene.info."""
    if not MYGENE_AVAILABLE:
        return {}

    try:
        mg = mygene.MyGeneInfo()
        query_ids = [g.split(".")[0] for g in gene_ids]
        response = mg.querymany(
            query_ids, scopes="ensembl.gene", fields="symbol",
            species="human", returnall=False,
        )

        result: dict[str, str] = {}
        for item in response:
            query = item.get("query", "")
            symbol = item.get("symbol")
            if query and symbol:
                matched_id = next(
                    (g for g in gene_ids if g.startswith(query)), query
                )
                result[matched_id] = symbol
                if kb:
                    kb.store_gene_mapping(
                        matched_id, "ensembl_gene", symbol, gse_id="mygene"
                    )

        logger.info("mygene mapped %d/%d Ensembl IDs", len(result), len(gene_ids))
        return result
    except Exception as exc:
        logger.warning("mygene lookup failed: %s", exc)
        return {}


def _map_via_heuristic(gene_ids: list[str]) -> dict[str, str]:
    """Simple heuristic: use ID stripped of version suffix as gene name."""
    result: dict[str, str] = {}
    for gid in gene_ids:
        clean = str(gid).strip().split(".")[0]
        if clean.isupper() and not clean.isdigit():
            result[gid] = clean
        elif clean.isalnum():
            result[gid] = clean
    return result
