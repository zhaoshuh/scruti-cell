"""Alignment node: aligns cell barcodes and unifies gene names."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import sparse

from scruticell.alignment.barcodes import detect_barcode_format, normalize_barcodes
from scruticell.alignment.genes import build_var_dataframe
from scruticell.pipeline.state import PipelineState

logger = logging.getLogger(__name__)


def align_cells_node(state: PipelineState) -> dict[str, Any]:
    """Align cell barcodes between expression matrix and metadata.

    Performs:
    - Barcode format detection (10X standard with -1 suffix vs bare)
    - Barcode normalization (upper-case, strip suffixes)
    - Builds obs DataFrame with cell barcodes as index
    - Assigns batch per cell (proxy: GSM accession or file-level)

    For single-cell data, each GSM typically represents one sample.
    Cells are assigned to their parent GSM based on:
    - Per-file mapping (one supplementary file per GSM)
    - Sample prefix detection in barcodes
    - Falls back to a single batch if neither is available

    Args:
        state: Pipeline state with cell_barcodes_raw, gsm_entries,
               supplementary_files, and parsed_characteristics.

    Returns:
        Partial state update with obs DataFrame, cell_barcodes_aligned,
        batch_column.
    """
    logger.info("Aligning cell barcodes for %s", state.gse_id)

    barcodes_raw = state.cell_barcodes_raw
    if not barcodes_raw:
        logger.error("No cell barcodes to align")
        state.add_error("No cell barcodes available for alignment")
        return {
            "obs": None,
            "cell_barcodes_aligned": [],
            "batch_column": "batch",
            "alignment_warnings": ["No cell barcodes"],
        }

    barcode_fmt = detect_barcode_format(barcodes_raw)
    logger.info("Detected barcode format: %s", barcode_fmt)

    barcodes_norm = normalize_barcodes(barcodes_raw, fmt=barcode_fmt)

    n_cells = len(barcodes_norm)
    obs = pd.DataFrame(index=range(n_cells))
    obs["barcode"] = barcodes_norm

    batch_col = "batch"
    gsm_ids = list(state.gsm_entries.keys()) if state.gsm_entries else []
    n_gsm = len(gsm_ids)
    n_files = len(state.supplementary_files)
    warnings: list[str] = []

    if state.cell_batch_labels and len(state.cell_batch_labels) == n_cells:
        obs[batch_col] = state.cell_batch_labels
        unique_batches = list(dict.fromkeys(state.cell_batch_labels))
        for batch_id in unique_batches:
            if batch_id in state.parsed_characteristics:
                mask = obs[batch_col] == batch_id
                sub_obs = obs.loc[mask]
                _propagate_metadata_to_subset(obs, mask, state.parsed_characteristics[batch_id])
        logger.info("Using pre-computed batch labels: %d batches for %d cells",
                     len(unique_batches), n_cells)

    elif n_gsm == 1:
        batch_id = gsm_ids[0]
        obs[batch_col] = batch_id
        _propagate_metadata(obs, state.parsed_characteristics.get(batch_id, {}))
        logger.info("Single GSM (%s): all %d cells assigned", batch_id, n_cells)

    elif n_gsm > 1 and n_files == n_gsm:
        _assign_cells_by_file_mapping(state, obs, batch_col, n_cells)
        warnings.append("Cells assigned to GSM by supplementary file mapping")
        logger.info("Cells assigned to GSM by %d supplementary files", n_files)

    elif n_gsm > 1:
        batches = _detect_barcode_sample_prefixes(barcodes_norm, gsm_ids)
        if batches:
            obs[batch_col] = [batches[i] for i in range(n_cells)]
            warnings.append("Cells assigned to GSM by barcode sample prefix")
            logger.info("Barcode sample prefixes matched to GSMs")
        else:
            first_gsm = gsm_ids[0]
            obs[batch_col] = first_gsm
            warnings.append(
                f"Could not map cells to {n_gsm} GSMs; all assigned to {first_gsm}"
            )
            logger.warning("Could not map cells to GSMs; using %s as batch", first_gsm)

    else:
        matrix_file = state.matrix_file
        batch_name = matrix_file.stem if matrix_file else state.gse_id
        obs[batch_col] = batch_name
        warnings.append(f"No GSM entries; using matrix file name as batch: {batch_name}")
        logger.warning("No GSM entries; using %s as batch proxy", batch_name)

    obs.index = barcodes_norm
    obs.index.name = "cell_barcode"

    logger.info("Built obs DataFrame: %d cells, %d columns", *obs.shape)

    return {
        "obs": obs,
        "cell_barcodes_aligned": barcodes_norm,
        "batch_column": batch_col,
        "alignment_warnings": warnings,
    }


def _propagate_metadata(
    obs: pd.DataFrame, characteristics: dict[str, Any]
) -> None:
    """Propagate GSM-level characteristics to all cells in obs."""
    for key, value in characteristics.items():
        if key not in obs.columns and key not in ("source_name", "description"):
            obs[key] = value


def _propagate_metadata_to_subset(
    obs: pd.DataFrame, mask: Any, characteristics: dict[str, Any]
) -> None:
    """Propagate GSM-level characteristics to a subset of cells in obs."""
    for key, value in characteristics.items():
        if key not in obs.columns and key not in ("source_name", "description"):
            if key not in obs.columns:
                obs[key] = None
            obs.loc[mask, key] = value


def _assign_cells_by_file_mapping(
    state: PipelineState, obs: pd.DataFrame, batch_col: str, n_cells: int
) -> None:
    """Assign cells to GSMs based on 1:1 supplementary file to GSM mapping.

    When each GSM has its own expression matrix file, all cells from that
    file belong to that GSM.
    """
    gsm_ids = sorted(state.gsm_entries.keys())
    matrix_file_stem = state.matrix_file.stem.lower() if state.matrix_file else ""

    assigned = False
    for gsm_id in gsm_ids:
        gsm_lower = gsm_id.lower()
        if gsm_lower in matrix_file_stem or matrix_file_stem in gsm_lower:
            obs[batch_col] = gsm_id
            chars = state.parsed_characteristics.get(gsm_id, {})
            _propagate_metadata(obs, chars)
            assigned = True
            break

    if not assigned:
        obs[batch_col] = gsm_ids[0]


def _detect_barcode_sample_prefixes(
    barcodes: list[str], gsm_ids: list[str]
) -> list[str] | None:
    """Try to detect sample prefixes in barcodes and map to GSM IDs.

    E.g., "Sample1_AAACCCAAGAAACTCA-1" -> prefix "Sample1" -> GSM

    First attempts semantic matching (prefix contains GSM ID or matches
    metadata), falls back to positional mapping.
    Returns a list of batch assignments, or None if detection fails.
    """
    sample = barcodes[:min(1000, len(barcodes))]
    prefixes: dict[str, int] = {}
    for bc in sample:
        parts = str(bc).split("_", 1)
        if len(parts) == 2:
            prefix = parts[0]
            prefixes[prefix] = prefixes.get(prefix, 0) + 1

    if len(prefixes) != len(gsm_ids) or not all(v > 5 for v in prefixes.values()):
        return None

    prefix_list = sorted(prefixes.keys())
    gsm_list = sorted(gsm_ids)

    # 1. Try semantic match: prefix contains GSM ID (e.g. "GSM9779490" in prefix)
    mapping: dict[str, str] = {}
    for prefix in prefix_list:
        prefix_lower = prefix.lower()
        for gsm in gsm_list:
            gsm_lower = gsm.lower()
            if gsm_lower in prefix_lower or prefix_lower in gsm_lower:
                mapping[prefix] = gsm
                break

    # 2. Try positional mapping for unmatched prefixes
    unmatched_prefixes = [p for p in prefix_list if p not in mapping]
    unmatched_gsms = [g for g in gsm_list if g not in mapping.values()]
    if len(unmatched_prefixes) == len(unmatched_gsms):
        mapping.update(dict(zip(unmatched_prefixes, unmatched_gsms)))

    if not mapping:
        return None

    return [_prefix_to_gsm(bc, mapping) for bc in barcodes]


def _prefix_to_gsm(barcode: str, mapping: dict[str, str]) -> str:
    parts = str(barcode).split("_", 1)
    if len(parts) == 2:
        return mapping.get(parts[0], "unknown")
    return "unknown"


def unify_genes_node(state: PipelineState) -> dict[str, Any]:
    """Unify gene identifiers across the expression matrix.

    Performs:
    - Gene ID system detection (Symbol, Ensembl, Entrez)
    - Cross-system mapping to unified Symbol via knowledge graph
    - Duplicate gene name resolution
    - Mitochondrial/ribosomal gene flagging
    - Builds standardized var DataFrame

    Args:
        state: Pipeline state with gene_names_raw and expression_matrix.

    Returns:
        Partial state update with var DataFrame, gene_names_unified,
        duplicate_gene_strategy.
    """
    logger.info("Unifying gene identifiers for %s", state.gse_id)

    gene_names_raw = state.gene_names_raw
    if not gene_names_raw:
        logger.error("No gene names to unify")
        state.add_error("No gene names available for unification")
        return {
            "var": None,
            "gene_names_unified": [],
            "duplicate_gene_strategy": "none",
        }

    var = build_var_dataframe(
        gene_names_raw,
        kb=None,
        species=state.organism or "Homo sapiens",
    )

    gene_names_unified = list(var.index)
    n_orig = len(gene_names_raw)
    n_unique = len(gene_names_unified)
    strategy = "first" if n_unique < n_orig else "none"

    if n_unique < n_orig and state.expression_matrix is not None:
        from scruticell.knowledge.gene_mapping import normalize_gene_symbol
        normalized = [normalize_gene_symbol(g) for g in gene_names_raw]
        seen: set[str] = set()
        keep_mask = np.zeros(n_orig, dtype=bool)
        for i, g in enumerate(normalized):
            if g not in seen:
                seen.add(g)
                keep_mask[i] = True
        state.expression_matrix = state.expression_matrix[keep_mask, :]
        gene_names_raw = [gene_names_raw[i] for i in range(n_orig) if keep_mask[i]]
        logger.info("Pruned expression matrix to remove %d duplicate genes", n_orig - n_unique)

    logger.info(
        "Gene unification: %d raw -> %d unique (strategy: %s), "
        "ID system: %s, mito: %d, ribo: %d",
        n_orig, n_unique, strategy,
        var["id_system"].iloc[0] if len(var) > 0 else "unknown",
        var["is_mito"].sum() if "is_mito" in var else 0,
        var["is_ribo"].sum() if "is_ribo" in var else 0,
    )

    return {
        "var": var,
        "gene_names_unified": gene_names_unified,
        "duplicate_gene_strategy": strategy,
    }


def output_node(state: PipelineState) -> dict[str, Any]:
    """Assemble and write the final AnnData object.

    Combines expression matrix, obs, var into an AnnData object,
    writes to .h5ad file, records audit log entries.

    Args:
        state: Pipeline state with all components assembled.

    Returns:
        Partial state update with output_path and audit_log.
    """
    import anndata

    logger.info("Assembling output AnnData for %s", state.gse_id)

    output_dir = state.output_dir or Path.cwd()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    matrix = state.expression_matrix
    obs = state.obs
    var = state.var

    if matrix is None:
        logger.error("No expression matrix for output")
        state.add_error("Cannot create AnnData: no expression matrix")
        return {"output_path": None, "audit_log": []}

    if obs is None or len(obs) == 0:
        n_cols = matrix.shape[1] if sparse.issparse(matrix) else matrix.shape[1]
        obs = pd.DataFrame(index=[f"cell_{i}" for i in range(n_cols)])
        obs["batch"] = state.gse_id
        logger.warning("No obs available; created default obs with %d cells", n_cols)
        state.obs = obs

    if var is None or len(var) == 0:
        n_rows = matrix.shape[0] if sparse.issparse(matrix) else matrix.shape[0]
        var = pd.DataFrame(index=[f"gene_{i}" for i in range(n_rows)])
        logger.warning("No var available; created default var with %d genes", n_rows)
        state.var = var

    matrix_aligned = _align_matrix_to_annotations(matrix, obs, var)

    adata = anndata.AnnData(
        X=matrix_aligned,
        obs=obs,
        var=var,
    )
    adata.obs_names = obs.index.astype(str)
    adata.var_names = var.index.astype(str)

    # Convert object columns to strings for h5ad write compatibility
    for col in adata.obs.columns:
        if adata.obs[col].dtype == object:
            adata.obs[col] = adata.obs[col].astype(str).replace("None", "")
    for col in adata.var.columns:
        if adata.var[col].dtype == object:
            adata.var[col] = adata.var[col].astype(str).replace("None", "")

    if state.gsm_entries:
        adata.uns["gsm_entries"] = dict(state.gsm_entries)
    adata.uns["gse_id"] = state.gse_id
    adata.uns["organism"] = state.organism
    adata.uns["pipeline_version"] = "0.1.0"

    output_path = output_dir / f"{state.gse_id}_scruticell.h5ad"
    adata.write_h5ad(output_path)

    audit_log = [{
        "step": "output",
        "status": "completed",
        "details": {
            "output_path": str(output_path),
            "n_cells": adata.n_obs,
            "n_genes": adata.n_vars,
            "sparse": sparse.issparse(adata.X),
            "organism": state.organism,
        },
    }]

    logger.info("AnnData written: %s (%d cells x %d genes)",
                output_path, adata.n_obs, adata.n_vars)

    return {
        "output_path": output_path,
        "audit_log": audit_log,
    }


def _align_matrix_to_annotations(
    matrix: Any, obs: pd.DataFrame, var: pd.DataFrame
) -> Any:
    """Ensure matrix shape is (n_obs, n_vars) = (cells, genes) for AnnData.

    The internal pipeline stores gene×cell (genes=rows, cells=cols).
    AnnData expects cell×gene (cells=rows, genes=cols).
    This function transposes if needed to match the obs/var dimensions.
    """
    is_sp = sparse.issparse(matrix)
    m_rows = matrix.shape[0] if is_sp else matrix.shape[0]
    m_cols = matrix.shape[1] if is_sp else matrix.shape[1]

    if m_rows == len(obs) and m_cols == len(var):
        return matrix

    if m_rows == len(var) and m_cols == len(obs):
        logger.info("Transposing gene×cell to cell×gene for AnnData")
        if is_sp:
            return matrix.T.tocsr()
        else:
            return np.asarray(matrix).T

    logger.warning(
        "Matrix shape (%d, %d) doesn't match (obs=%d, var=%d). Attempting transpose.",
        m_rows, m_cols, len(obs), len(var),
    )

    if is_sp:
        return matrix.T.tocsr() if matrix.shape[1] == len(obs) else matrix
    matrix_np = np.asarray(matrix)
    if matrix_np.shape[1] == len(obs):
        return matrix_np.T
    return matrix_np
