"""Plan-driven execution engine with self-reflection.

PlanExecutor: executes a ProcessingPlan using existing pipeline nodes.
ExecutionMonitor: wraps execution with error reflection and plan revision.
ProvenanceRecorder: captures complete processing trace.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scruticell.intelligence.planner import (
    FieldMapping,
    ProcessingPlan,
)
from scruticell.intelligence.reporter import GseReport
from scruticell.pipeline.state import PipelineState
from scruticell.utils.tracing import traceable

logger = logging.getLogger(__name__)

_MAX_EXECUTION_RETRIES = 2


@dataclass
class ExecutionStep:
    """One step in the execution provenance trace."""
    step: str
    status: str  # "started", "completed", "failed"
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class ExecutionResult:
    """Result of plan execution."""
    success: bool
    output_path: Path | None = None
    n_cells: int = 0
    n_genes: int = 0
    n_batches: int = 0
    steps: list[ExecutionStep] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    batch_metadata: list[dict] = field(default_factory=list)


class ProvenanceRecorder:
    """Records every processing decision for reproducibility."""

    def __init__(self):
        self._steps: list[ExecutionStep] = []

    def record(self, step: str, status: str, details: dict | None = None, error: str = "") -> None:
        self._steps.append(ExecutionStep(
            step=step,
            status=status,
            details=details or {},
            error=error,
        ))

    def to_dict(self) -> list[dict[str, Any]]:
        return [
            {
                "step": s.step,
                "status": s.status,
                "timestamp": s.timestamp,
                "details": s.details,
                "error": s.error,
            }
            for s in self._steps
        ]

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class PlanExecutor:
    """Execute a ProcessingPlan to produce an AnnData file.

    Reuses existing pipeline nodes (readers, alignment, output)
    but drives them with explicit mappings from the plan instead
    of heuristic guessing.

    Usage:
        executor = PlanExecutor()
        result = executor.execute(report, plan, output_dir)
        if result.success:
            print(f"Output: {result.output_path}")
    """

    def __init__(self):
        self._recorder = ProvenanceRecorder()

    @property
    def provenance(self) -> ProvenanceRecorder:
        return self._recorder

    @traceable(name="PlanExecutor.execute", tags=["intelligence", "execution"])
    def execute(
        self,
        report: GseReport,
        plan: ProcessingPlan,
        output_dir: Path,
        state: PipelineState | None = None,
    ) -> ExecutionResult:
        """Execute a processing plan.

        Args:
            report: Inspection report with file summaries.
            plan: Processing plan from LLM.
            output_dir: Where to write the output .h5ad.
            state: Optional existing pipeline state (for reuse).

        Returns:
            ExecutionResult with output path and provenance.
        """
        if state is None:
            state = PipelineState(gse_id=plan.gse_id, output_dir=output_dir)

        result = ExecutionResult(success=False)
        result.steps = []

        try:
            self._recorder.record("execution", "started", {
                "gse_id": plan.gse_id,
                "strategy_notes": plan.strategy_notes,
                "confidence": plan.confidence,
            })
            self._recorder.record("execution", "started", {
                "gse_id": plan.gse_id,
                "strategy_notes": plan.strategy_notes,
                "confidence": plan.confidence,
            })

            # 1. Read all matrices
            self._recorder.record("read_matrices", "started", {
                "count": len(plan.matrices),
            })
            matrices_data, file_to_gsm = self._read_all_matrices(report, plan, state)
            if not matrices_data:
                result.errors.append("No matrices could be read")
                self._recorder.record("read_matrices", "failed")
                return result
            self._recorder.record("read_matrices", "completed", {
                "matrices_read": len(matrices_data),
                "file_to_gsm": file_to_gsm,
            })

            # 2. Build per-batch AnnData objects (no merge — keep samples separate)
            self._recorder.record("build_adata", "started", {
                "matrices": [m["file_key"] for m in matrices_data],
            })
            per_batch: dict[str, Any] = {}  # {batch_label: AnnData}
            state.gene_names_raw = list(matrices_data[0]["gene_names"]) if matrices_data else []

            for i, data in enumerate(matrices_data):
                fk = data.get("file_key", "")
                gsm_id = file_to_gsm.get(fk, f"batch_{i}")
                from scipy import sparse as sp

                # Read raw data
                matrix = data["matrix"]
                gene_names = list(data["gene_names"] or [])
                cell_barcodes = list(data["cell_barcodes"] or [])
                for j, bc in enumerate(cell_barcodes):
                    if not isinstance(bc, str):
                        cell_barcodes[j] = str(bc)

                # Determine orientation from plan
                m_def = plan.matrices[i] if i < len(plan.matrices) else None
                need_transpose = getattr(m_def, "transpose", False) if m_def else False

                # Transpose to gene×cell internally if needed (after this, matrix is gene×cell)
                if need_transpose:
                    if sp.issparse(matrix):
                        matrix = matrix.T.tocsr()
                    else:
                        matrix = matrix.T

                # Convert gene names to strings, cell barcodes to strings
                for j, bc in enumerate(cell_barcodes):
                    if not isinstance(bc, str):
                        cell_barcodes[j] = str(bc)

                # Build obs for this sample
                import pandas as pd
                obs = pd.DataFrame(index=cell_barcodes)
                obs["batch"] = gsm_id

                # Inject GSM characteristics into obs (whitelisted by verified_field_mappings)
                verified_mappings = plan._verified_field_mappings or plan.field_mappings
                allowed_raw = set()
                for fm in verified_mappings:
                    raw = fm.raw_field if hasattr(fm, "raw_field") else fm.get("raw_field", "")
                    # Strip LLM prefixes like "characteristics.", "gsm.", "obs."
                    for prefix in ("characteristics.", "gsm.", "obs.", "meta."):
                        if raw.startswith(prefix):
                            raw = raw[len(prefix):]
                            break
                    allowed_raw.add(raw)
                allowed_raw.update({"source_name", "description"})
                for gsm_entry in report.gsm_entries:
                    if gsm_entry.gsm_id == gsm_id:
                        for k, v in gsm_entry.characteristics.items():
                            if k in allowed_raw:
                                obs[k] = str(v) if v else ""
                        if "source_name" in allowed_raw:
                            obs["source_name"] = gsm_entry.source_name
                        obs_desc = gsm_entry.description[:200] if gsm_entry.description else ""
                        if "description" in allowed_raw:
                            obs["description"] = obs_desc
                        break

                # Inject filename-derived metadata (Step 3, per-GSM)
                _skip_fe = {"gsm_id", "role", "file_type", "format", "extension"}
                for fe in plan._filename_extractions:
                    if not isinstance(fe, dict):
                        continue
                    fe_comps = fe.get("components", {})
                    if fe_comps.get("gsm_id") == gsm_id:
                        for k, v in fe_comps.items():
                            if v and k not in _skip_fe and k not in obs.columns:
                                obs[k] = str(v)
                        break

                # Inject literature-derived metadata (Step 4, per-experiment)
                for k, v in plan._literature_metadata.items():
                    if (not k.startswith("_") and k not in obs.columns
                            and isinstance(v, (str, int, float, bool))):
                        obs[k] = str(v)

                # Inject literature-verified items (Step 4 ORC, per-GSM matched)
                for vm in verified_mappings:
                    vm_gsm = vm.get("gsm_id", "") if isinstance(vm, dict) else getattr(vm, "gsm_id", "")
                    if vm_gsm != gsm_id:
                        continue
                    src = vm.get("_src", "") if isinstance(vm, dict) else getattr(vm, "_src", "")
                    if not src.startswith("step4"):
                        continue
                    canonical = vm.get("canonical", "") if isinstance(vm, dict) else getattr(vm, "canonical", "")
                    raw = vm.get("raw_field", "") if isinstance(vm, dict) else getattr(vm, "raw_field", "")
                    if canonical and canonical not in obs.columns and raw not in obs.columns:
                        obs[canonical] = str(canonical)

                # Build var for this sample
                var = pd.DataFrame(index=gene_names)
                var["original_id"] = gene_names

                # Strip axis metadata columns/rows (Step 3 LLM-identified)
                axis_meta = getattr(plan, "_axis_metadata", None) or []
                if axis_meta and matrix is not None:
                    import numpy as np
                    col_mask = np.ones(matrix.shape[1], dtype=bool)
                    row_mask = np.ones(matrix.shape[0], dtype=bool)

                    for am in axis_meta:
                        if not isinstance(am, dict):
                            continue
                        pos = am.get("position", -1)
                        ax = am.get("axis", "")
                        col_name = am.get("column_name", "")
                        action = am.get("action", "")
                        compound = am.get("compound_value", False)
                        sub_fields = am.get("sub_fields", []) if compound else []

                        if ax == "columns" and 0 <= pos < matrix.shape[1]:
                            # Extract column values (one per cell)
                            if sp.issparse(matrix):
                                col_vals = np.asarray(matrix[:, pos].toarray()).flatten()
                            else:
                                col_vals = np.asarray(matrix[:, pos]).flatten()

                            if sub_fields:
                                for sf in sub_fields:
                                    canon = sf.get("canonical", "")
                                    delim = sf.get("delimiter", "_")
                                    sf_pos = sf.get("position", 0)
                                    if canon:
                                        obs[canon] = [
                                            str(v).split(delim)[sf_pos].strip()
                                            if delim in str(v)
                                            and len(str(v).split(delim)) > sf_pos
                                            else str(v)
                                            for v in col_vals
                                        ]
                                        logger.info(
                                            "Axis metadata: %s -> %s (compound split, col %d)",
                                            col_name, canon, pos
                                        )
                            elif col_name:
                                obs[col_name] = [str(v) for v in col_vals]
                                logger.info("Axis metadata: %s -> obs[%s] (col %d)",
                                            col_name, col_name, pos)

                            if action in ("strip_to_obs", "") and col_name:
                                col_mask[pos] = False

                        elif ax == "rows" and 0 <= pos < matrix.shape[0]:
                            # Extract row values (one per gene)
                            if sp.issparse(matrix):
                                row_vals = np.asarray(matrix[pos, :].toarray()).flatten()
                            else:
                                row_vals = np.asarray(matrix[pos, :]).flatten()

                            if sub_fields:
                                for sf in sub_fields:
                                    canon = sf.get("canonical", "")
                                    delim = sf.get("delimiter", "_")
                                    sf_pos = sf.get("position", 0)
                                    if canon:
                                        var[canon] = [
                                            str(v).split(delim)[sf_pos].strip()
                                            if delim in str(v)
                                            and len(str(v).split(delim)) > sf_pos
                                            else str(v)
                                            for v in row_vals
                                        ]
                            elif col_name:
                                var[col_name] = [str(v) for v in row_vals]
                                logger.info("Axis metadata: %s -> var[%s] (row %d)",
                                            col_name, col_name, pos)

                            if action in ("strip_to_var", "") and col_name:
                                row_mask[pos] = False

                    # Apply column/row removal (only if actually stripped)
                    if not col_mask.all():
                        if sp.issparse(matrix):
                            matrix = matrix[:, col_mask]
                        else:
                            matrix = matrix[:, col_mask]
                    if not row_mask.all():
                        if sp.issparse(matrix):
                            matrix = matrix[row_mask, :]
                        else:
                            matrix = matrix[row_mask, :]

                # Create AnnData — readers already output cell×gene
                import anndata
                n_cells = len(cell_barcodes) if cell_barcodes else 0

                if matrix.shape[0] != n_cells:
                    # Try transposing — orientation may be reversed
                    flipped = matrix.T.tocsr() if sp.issparse(matrix) else matrix.T
                    if flipped.shape[0] == n_cells:
                        logger.warning(
                            "Fixed orientation for GSM %s: transposed %s → %s",
                            gsm_id, matrix.shape, flipped.shape,
                        )
                        matrix = flipped
                    else:
                        logger.error(
                            "Skipping matrix for GSM %s: cell count mismatch "
                            "(matrix has %d cells, %d barcodes found). "
                            "This sample's supplementary files may be corrupted.",
                            gsm_id, matrix.shape[0], n_cells,
                        )
                        continue

                # Sparse cell×gene matrix: enforce csr for AnnData
                if sp.issparse(matrix):
                    adata = anndata.AnnData(X=matrix.tocsr(), obs=obs, var=var)
                else:
                    adata = anndata.AnnData(X=matrix, obs=obs, var=var)

                per_batch[gsm_id] = adata

            state.per_batch_adatas = per_batch
            n_total_cells = sum(a.n_obs for a in per_batch.values())
            logger.info("Built %d per-batch AnnData objects (%d total cells)", len(per_batch), n_total_cells)
            self._recorder.record("build_adata", "completed", {
                "n_batches": len(per_batch),
                "n_cells": n_total_cells,
            })

            # Also set obs/var on state from the first adata for validation compatibility
            if per_batch:
                first_key = next(iter(per_batch))
                state.obs = per_batch[first_key].obs
                state.var = per_batch[first_key].var
                state.expression_matrix = per_batch[first_key].X.T if sp.issparse(per_batch[first_key].X) else per_batch[first_key].X.T
                state.matrix_shape = per_batch[first_key].shape
                state.cell_barcodes_raw = list(per_batch[first_key].obs_names)
                state.cell_batch_labels = [first_key] * per_batch[first_key].n_obs

            # 3. Align cells (batch assignment from plan) — not needed per-sample
            self._recorder.record("align_cells", "completed", {
                "n_batches": len(per_batch),
            })

            # 4. Unify genes — apply per-batch (gene sets may differ)
            self._recorder.record("unify_genes", "started")
            if per_batch and plan.drop_duplicate_genes:
                import numpy as np
                from scruticell.knowledge.gene_mapping import normalize_gene_symbol
                total_dropped = 0
                for batch_label, adata in per_batch.items():
                    genes = list(adata.var_names)
                    normalized = [normalize_gene_symbol(g) for g in genes]
                    seen: set[str] = set()
                    keep_mask = np.zeros(len(genes), dtype=bool)
                    for idx, g in enumerate(normalized):
                        if g not in seen:
                            seen.add(g)
                            keep_mask[idx] = True
                    dropped = len(genes) - keep_mask.sum()
                    if dropped > 0:
                        adata._inplace_subset_var(keep_mask)
                        total_dropped += dropped
                if total_dropped > 0:
                    logger.info("Pruned %d duplicate genes across all batches", total_dropped)
            self._recorder.record("unify_genes", "completed")

            # 5. Custom processing code (LLM-generated, dataset-specific)
            #    Runs BEFORE field_mappings so processing_code sees original column names
            if plan.processing_code:
                self._recorder.record("custom_code", "started")
                try:
                    per_batch = self._execute_processing_code_per_batch(per_batch, plan)
                    self._recorder.record("custom_code", "completed")
                except Exception as exc:
                    logger.warning("Custom processing code failed: %s", exc)
                    self._recorder.record("custom_code", "failed", error=str(exc))

            # 6. Apply field mappings — final standardization on processed adatas
            self._recorder.record("apply_field_mappings", "started", {
                "mappings": len(plan.field_mappings),
            })
            for batch_label, adata in per_batch.items():
                self._apply_field_mappings_to_adata(adata, plan)
            self._recorder.record("apply_field_mappings", "completed")

            # 7. Run assembly_code (Step 5: LLM-generated, now returns dict)
            if plan.assembly_code:
                self._recorder.record("assembly_code", "started")
                try:
                    per_batch = self._execute_assembly_code_per_batch(per_batch, plan, matrices_data, file_to_gsm)
                    self._recorder.record("assembly_code", "completed")
                except Exception as exc:
                    logger.warning("Assembly code failed: %s", exc)
                    self._recorder.record("assembly_code", "failed", error=str(exc))

            state.per_batch_adatas = per_batch

            # 8. Validate
            self._recorder.record("validate", "started")
            from scruticell.pipeline.nodes.validation import validate_node
            val_result = validate_node(state)
            for k, v in val_result.items():
                setattr(state, k, v)
            self._recorder.record("validate", "completed", {
                "passed": state.validation_passed,
                "confidence": state.overall_confidence(),
                "scores": state.confidence_scores,
            })

            # 9. Output — write one .h5ad per batch
            self._recorder.record("output", "started")
            output_paths = []
            batch_metadata = []
            for batch_label, adata in sorted(per_batch.items()):
                # Convert object columns to strings
                for col in adata.obs.columns:
                    if adata.obs[col].dtype == object:
                        adata.obs[col] = adata.obs[col].astype(str).replace("None", "")
                for col in adata.var.columns:
                    if adata.var[col].dtype == object:
                        adata.var[col] = adata.var[col].astype(str).replace("None", "")
                adata.obs_names = adata.obs_names.astype(str)
                adata.var_names = adata.var_names.astype(str)

                safe_label = batch_label.replace("/", "_").replace("\\", "_").replace(" ", "_")
                output_path = output_dir / f"{report.gse_id}_{safe_label}_scruticell.h5ad"
                logger.info("Writing per-batch h5ad: %s (%d cells x %d genes)", output_path, adata.n_obs, adata.n_vars)
                adata.write_h5ad(output_path)
                output_paths.append(output_path)

                # Collect per-batch metadata for summary
                meta: dict[str, str | int] = {
                    "file": output_path.name,
                    "cells": adata.n_obs,
                    "genes": adata.n_vars,
                }
                # Inject batch reasoning from plan if available
                for b in (plan.batches or []):
                    if getattr(b, "gsm_id", "") == batch_label and getattr(b, "reasoning", ""):
                        meta["batch_split_reason"] = b.reasoning
                        if getattr(b, "defining_fields", []):
                            meta["diff_in"] = ", ".join(b.defining_fields)
                        break
                for col in adata.obs.columns:
                    vals = adata.obs[col].dropna().unique()
                    if 0 < len(vals) <= 20:
                        meta[col] = ", ".join(str(v)[:80] for v in sorted(vals)[:5])
                batch_metadata.append(meta)

            result.batch_metadata = batch_metadata

            state.output_path = output_paths[0] if output_paths else None
            self._recorder.record("output", "completed", {
                "paths": [str(p) for p in output_paths],
                "n_batches": len(output_paths),
            })

            result.success = bool(output_paths)
            result.output_path = state.output_path
            result.n_cells = n_total_cells
            result.n_batches = len(output_paths)
            if per_batch:
                first_key = next(iter(per_batch))
                result.n_genes = per_batch[first_key].n_vars
            result.warnings = state.alignment_warnings

            # 10. Origin trace report
            try:
                from scruticell.intelligence.provenance import ProvenanceBuilder
                builder = ProvenanceBuilder(report, plan, state)
                trace = builder.build()
                trace_path = output_dir / f"{report.gse_id}_trace.json"
                trace_path.write_text(
                    json.dumps(trace.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info("Origin trace report written to: %s", trace_path)
                # Also write markdown
                trace_md_path = output_dir / f"{report.gse_id}_trace.md"
                trace_md_path.write_text(trace.format_markdown(), encoding="utf-8")
                logger.info("Origin trace markdown written to: %s", trace_md_path)
                # Attach formatted text to result for CLI output
                result._trace_text = trace.format_text()
            except Exception as exc:
                logger.warning("Failed to build origin trace report: %s", exc)

            # 11. Store run + steps + batches in knowledge base
            try:
                from scruticell.knowledge.graph import LocalKnowledgeBase
                kb = LocalKnowledgeBase()
                run_id = f"{report.gse_id}_{int(time.time())}"
                kb.store_run(
                    run_id=run_id,
                    gse_id=report.gse_id,
                    success=result.success,
                    confidence=float(n_total_cells > 0),
                    n_cells=result.n_cells,
                    n_genes=result.n_genes,
                    n_batches=result.n_batches,
                )
                for i, s in enumerate(result.steps):
                    kb.store_execution_step(
                        run_id=run_id,
                        step_name=s.step,
                        status=s.status,
                        step_index=i,
                        details_json=json.dumps(s.details) if s.details else "{}",
                        error=s.error,
                    )
                for bm in result.batch_metadata:
                    kb.store_batch_output(
                        run_id=run_id,
                        batch_label=bm.get("file", ""),
                        file_name=bm.get("file", ""),
                        n_cells=int(bm.get("cells", 0)),
                        n_genes=int(bm.get("genes", 0)),
                        metadata_json=json.dumps(bm, ensure_ascii=False),
                    )
            except Exception as exc:
                logger.warning("Failed to store run in KB: %s", exc)

        except Exception as exc:
            logger.exception("Execution failed: %s", exc)
            result.errors.append(str(exc))
            self._recorder.record("execution", "failed", error=str(exc))

        result.steps = self._recorder._steps.copy()
        return result

    def _read_all_matrices(
        self, report: GseReport, plan: ProcessingPlan, state: PipelineState,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Read all expression matrices specified in the plan.

        Returns:
            (matrices_data, file_to_gsm_mapping)
        """
        from scruticell.pipeline.nodes.matrix import _read_matrix

        matrices_data: list[dict[str, Any]] = []
        file_to_gsm: dict[str, str] = {}

        # Build file→GSM mapping from plan batches
        for batch in plan.batches:
            for fk in batch.file_keys:
                file_to_gsm[fk] = batch.gsm_id

        for matrix_def in plan.matrices:
            fk = matrix_def.file_key
            fmt = matrix_def.format

            # Find the actual file path
            filepath = self._resolve_file_path(fk, state)

            if filepath is None:
                logger.warning("Could not resolve file path for: %s", fk)
                continue

            logger.info("Reading matrix: %s (format: %s)", filepath, fmt)
            try:
                read_fmt = "h5ad" if fmt == "h5" else fmt
                # MTX reader expects a directory, not the matrix.mtx.gz file
                if fmt == "mtx":
                    if filepath.is_file():
                        matrix_hint = filepath.name
                        filepath = filepath.parent
                        from scruticell.readers.mtx import read_mtx as _read_mtx_direct
                        data = _read_mtx_direct(filepath, matrix_hint)
                    else:
                        from scruticell.readers.mtx import read_mtx as _read_mtx_direct
                        data = _read_mtx_direct(filepath, None)
                else:
                    data = _read_matrix(filepath, read_fmt)
            except Exception as exc:
                logger.error("Failed to read %s: %s", filepath, exc)
                continue

            # Reader already outputs cell×gene (AnnData convention) — no orientation fix needed
            data["file_key"] = fk
            matrices_data.append(data)

        return matrices_data, file_to_gsm

    def _resolve_file_path(self, file_key: str, state: PipelineState) -> Path | None:
        """Resolve a file_key from the report to an actual Path using download_dir only."""
        download_dir = state.download_dir or Path.cwd()

        # Extract the base filename from the file_key
        # e.g., "GSE312357/GSM9344289...tar.gz/filtered/.../matrix.mtx" -> "GSM9344289...tar.gz"
        fk_parts = file_key.replace("\\", "/").split("/")
        base_name = ""
        for part in fk_parts:
            if any(part.lower().endswith(ext) for ext in (
                ".h5", ".h5ad", ".tar.gz", ".tgz", ".mtx.gz", ".csv.gz", ".csv", ".rds", ".loom",
            )):
                base_name = part
                break
        if not base_name:
            for part in reversed(fk_parts):
                if "." in part:
                    base_name = part
                    break

        # Find the actual file in download_dir
        actual_file: Path | None = None
        for p in download_dir.rglob("*"):
            if p.is_file() and p.name == base_name:
                actual_file = p
                break
        if actual_file is None:
            for sf in state.supplementary_files:
                if sf.name == base_name:
                    actual_file = sf
                    break
        if actual_file is None:
            fp = Path(file_key)
            if fp.exists():
                return fp
            return None

        sf_str = str(actual_file)

        # Handle .tar.gz: decompress and find inner matrix file
        if sf_str.endswith(".tar.gz") or sf_str.endswith(".tgz"):
            extracted_dir = actual_file.parent / f"{actual_file.stem}_extracted"
            if not extracted_dir.exists():
                import tarfile
                logger.info("Decompressing: %s", actual_file)
                with tarfile.open(actual_file, "r:*") as tar:
                    tar.extractall(path=extracted_dir)
            for inner in extracted_dir.rglob("matrix.mtx*"):
                if inner.is_file():
                    return inner
            for inner in extracted_dir.rglob("*.h5"):
                if inner.is_file():
                    return inner
            for inner in extracted_dir.rglob("*.h5ad"):
                if inner.is_file():
                    return inner
            return actual_file

        # .h5 / .h5ad / .mtx.gz / .csv / .rds / .loom: return as-is
        return actual_file

    def _merge_matrices(
        self,
        matrices_data: list[dict[str, Any]],
        file_to_gsm: dict[str, str],
        plan: ProcessingPlan,
        state: PipelineState,
    ) -> tuple[Any, list[str], list[str]] | tuple[None, None, None]:
        """Merge multiple matrices into one gene x cell matrix.

        Returns:
            (merged_matrix, all_barcodes, all_batch_labels)
        """
        from scipy import sparse as sp

        all_matrices: list[Any] = []
        all_barcodes: list[str] = []
        all_batch_labels: list[str] = []
        reference_genes: list[str] = []

        for i, data in enumerate(matrices_data):
            matrix = data["matrix"]
            cell_barcodes = data["cell_barcodes"]
            gene_names = data["gene_names"]
            fk = data.get("file_key", "")

            if i == 0:
                reference_genes = list(gene_names)
            else:
                from scruticell.pipeline.nodes.matrix import _align_genes_to_reference
                matrix = _align_genes_to_reference(matrix, gene_names, reference_genes)

            # Ensure gene x cell orientation
            from scruticell.pipeline.nodes.matrix import _is_gene_x_cell
            if not _is_gene_x_cell(reference_genes if i > 0 else gene_names, matrix.shape):
                if sp.issparse(matrix):
                    matrix = matrix.T.tocsr()
                else:
                    matrix = matrix.T

            batch_label = file_to_gsm.get(fk, f"batch_{i}")
            all_matrices.append(matrix)
            all_barcodes.extend(cell_barcodes)
            all_batch_labels.extend([batch_label] * len(cell_barcodes))

        if not all_matrices:
            return None, None, None

        if sp.issparse(all_matrices[0]):
            merged = sp.hstack(all_matrices, format="csr")
        else:
            merged = np.hstack(all_matrices)

        return merged, all_barcodes, all_batch_labels

    def _align_cells_from_plan(
        self, state: PipelineState, plan: ProcessingPlan, report: GseReport,
    ) -> PipelineState:
        """Align cells and assign batches according to the plan."""
        from scruticell.alignment.barcodes import detect_barcode_format, normalize_barcodes

        barcodes_raw = state.cell_barcodes_raw
        if not barcodes_raw:
            logger.warning("No cell barcodes to align")
            return state

        barcode_fmt = detect_barcode_format(barcodes_raw)
        barcodes_norm = normalize_barcodes(barcodes_raw, fmt=barcode_fmt)

        n_cells = len(barcodes_norm)
        obs = pd.DataFrame(index=range(n_cells))
        obs["barcode"] = barcodes_norm

        # Use plan's batch labels
        if state.cell_batch_labels and len(state.cell_batch_labels) == n_cells:
            obs["batch"] = state.cell_batch_labels

            # Propagate GSM metadata per batch
            for batch in plan.batches:
                gsm_id = batch.gsm_id
                mask = obs["batch"] == gsm_id
                if mask.any():
                    for gsm in report.gsm_entries:
                        if gsm.gsm_id == gsm_id:
                            for key, value in gsm.characteristics.items():
                                if key not in obs.columns and value:
                                    obs[key] = None
                                    obs.loc[mask, key] = str(value)
                            break

        obs.index = barcodes_norm
        obs.index.name = "cell_barcode"
        state.obs = obs

        logger.info("Aligned %d cells with %d batches", n_cells,
                     obs["batch"].nunique() if "batch" in obs.columns else 0)
        return state

    def _unify_genes_from_plan(
        self, state: PipelineState, plan: ProcessingPlan,
    ) -> PipelineState:
        """Unify gene identifiers according to the plan."""
        gene_names_raw = state.gene_names_raw
        if not gene_names_raw:
            logger.warning("No gene names to unify")
            return state

        from scruticell.alignment.genes import build_var_dataframe

        var = build_var_dataframe(
            gene_names_raw,
            kb=None,
            species="Homo sapiens",
        )

        n_orig = len(gene_names_raw)
        n_unique = len(var)

        if plan.drop_duplicate_genes and n_unique < n_orig and state.expression_matrix is not None:
            from scruticell.knowledge.gene_mapping import normalize_gene_symbol
            normalized = [normalize_gene_symbol(g) for g in gene_names_raw]
            seen: set[str] = set()
            keep_mask = np.zeros(n_orig, dtype=bool)
            for i, g in enumerate(normalized):
                if g not in seen:
                    seen.add(g)
                    keep_mask[i] = True
            state.expression_matrix = state.expression_matrix[keep_mask, :]
            logger.info("Pruned %d duplicate genes", n_orig - n_unique)

        state.var = var
        state.gene_names_unified = list(var.index)
        return state

    def _apply_field_mappings(self, state: PipelineState, plan: ProcessingPlan) -> None:
        """Apply field name standardizations and compound value splits to obs."""
        if state.obs is None:
            return

        for fm in plan.field_mappings:
            # 1. Simple field rename
            if fm.raw_field in state.obs.columns and fm.canonical not in state.obs.columns:
                state.obs.rename(columns={fm.raw_field: fm.canonical}, inplace=True)
                logger.info("Renamed field: %s -> %s", fm.raw_field, fm.canonical)

            # 2. Compound value splitting
            if fm.value_split and fm.raw_field in state.obs.columns:
                col = state.obs[fm.raw_field]
                self._split_compound_value(state, fm, col)

        # 3. Run auto-detect on remaining compound-like columns
        self._auto_split_detect(state)

    def _split_compound_value(
        self, state: PipelineState, fm: FieldMapping, col: Any,
    ) -> None:
        """Extract sub-fields from a compound value column."""

        vs = fm.value_split
        delimiter = vs.get("delimiter", "")
        pattern = vs.get("pattern", "")
        position = vs.get("position", -1)
        literal = vs.get("literal", "")
        canonical = fm.canonical

        if position >= 0 and delimiter:
            # Positional split: e.g., "Male_Control_72yr" split by "_" position 0
            state.obs[canonical] = col.astype(str).apply(
                lambda v: v.split(delimiter)[position]
                if delimiter in str(v) and len(str(v).split(delimiter)) > position
                else v
            )

        elif pattern:
            # Regex extraction
            state.obs[canonical] = col.astype(str).str.extract(pattern)
            state.obs[canonical] = state.obs[canonical].fillna(col)

        elif literal:
            # Literal separator (e.g., colon: "tissue: liver" -> extract after literal)
            parts = col.astype(str).str.split(literal, n=1, regex=False)
            state.obs[canonical] = parts.str[1].str.strip() if parts.str.len().max() > 1 else col

        logger.info("Split compound value: %s -> %s (split=%s)", fm.raw_field, canonical, vs)

    def _auto_split_detect(self, state: PipelineState) -> None:
        """Auto-detect and split remaining underscore-delimited compound columns."""
        if state.obs is None:
            return

        common_patterns = {
            # column_name -> [(position, sub_canonical_name), ...]
            "tissue": [(0, "cell_type")],
            "cell type": [(0, "cell_line")],
        }

        for col_name, splits in common_patterns.items():
            if col_name not in state.obs.columns:
                continue
            col = state.obs[col_name].astype(str)
            for pos, sub_name in splits:
                if sub_name not in state.obs.columns:
                    state.obs[sub_name] = col.apply(
                        lambda v: v.split("_")[pos]
                        if "_" in str(v) and len(str(v).split("_")) > pos
                        else None
                    )
                    logger.info("Auto-split: %s -> %s (position %d)", col_name, sub_name, pos)

    def _execute_processing_code(
        self, state: PipelineState, plan: ProcessingPlan,
    ) -> PipelineState:
        """Execute LLM-generated custom processing code in a sandbox.

        The code is a function `process(obs, var, adata) -> (obs, var, adata)`.
        It runs in a restricted namespace with only pd, np, re available.
        """
        import re

        import numpy as np
        import pandas as pd

        if not state.obs is not None and len(state.obs) > 0:
            logger.warning("No obs data for custom code execution")
            return state
        if state.var is None:
            logger.warning("No var data for custom code execution")
            return state

        # Build a temporary AnnData for the custom code to work with
        import anndata
        from scipy import sparse

        matrix = state.expression_matrix
        obs = state.obs.copy() if state.obs is not None else pd.DataFrame()
        var = state.var.copy() if state.var is not None else pd.DataFrame()

        # Create adata in cell x gene orientation
        if matrix is not None:
            if sparse.issparse(matrix):
                m_shape = matrix.shape
            else:
                m_shape = matrix.shape
            if m_shape[0] == len(var) and m_shape[1] == len(obs):
                # gene x cell -> transpose to cell x gene
                if sparse.issparse(matrix):
                    adata = anndata.AnnData(X=matrix.T.tocsr(), obs=obs, var=var)
                else:
                    adata = anndata.AnnData(X=matrix.T, obs=obs, var=var)
            elif m_shape[0] == len(obs) and m_shape[1] == len(var):
                adata = anndata.AnnData(X=matrix, obs=obs, var=var)
            else:
                adata = anndata.AnnData(X=matrix, obs=obs, var=var)
        else:
            adata = anndata.AnnData(obs=obs, var=var)

        # Sandbox namespace
        sandbox = {
            "pd": pd, "np": np, "re": re,
            "obs": obs, "var": var, "adata": adata,
            "__builtins__": {
                "True": True, "False": False, "None": None,
                "int": int, "float": float, "str": str, "bool": bool,
                "list": list, "dict": dict, "set": set, "tuple": tuple,
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "map": map, "filter": filter,
                "print": print, "isinstance": isinstance,
                "Exception": Exception, "ValueError": ValueError,
                "TypeError": TypeError, "KeyError": KeyError,
                    "__import__": __import__,
            },
        }

        code = plan.processing_code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        logger.info("Executing custom processing code (%d chars)", len(code))

        local_ns: dict = {}
        exec(code, sandbox, local_ns)

        process_fn = local_ns.get("process") or local_ns.get("process_metadata")
        if process_fn is None:
            logger.warning("No process() / process_metadata() function found in custom code")
            return state

        try:
            obs, var, adata = process_fn(obs, var, adata)
        except TypeError:
            obs, var = process_fn(obs, var)

        # Update state with modified data
        state.obs = obs
        state.var = var

        # Extract matrix from adata back to state (gene x cell)
        X = adata.X
        if sparse.issparse(X):
            state.expression_matrix = X.T.tocsr()
        else:
            state.expression_matrix = X.T

        state.matrix_shape = state.expression_matrix.shape
        state.is_sparse = sparse.issparse(state.expression_matrix)

        logger.info(
            "Custom code executed: obs=%s, var=%s, matrix=%s",
            state.obs.shape if state.obs is not None else (0, 0),
            state.var.shape if state.var is not None else (0, 0),
            state.matrix_shape,
        )
        return state

    def _execute_assembly_code(
        self, state: PipelineState, plan: ProcessingPlan,
        matrices_data: list[dict[str, Any]], file_to_gsm: dict[str, str],
    ) -> PipelineState:
        """Execute LLM-generated assembly code (Step 5) in sandbox."""
        import re

        import numpy as np
        import pandas as pd
        import scanpy as sc

        # Build adatas dict from read matrices
        adatas: dict[str, Any] = {}
        gsm_map: dict[str, str] = {}
        for data in matrices_data:
            fk = data.get("file_key", "")
            batch_name = file_to_gsm.get(fk, f"batch_{len(adatas)}")
            gsm_map[batch_name] = batch_name

            # Build a temporary AnnData with the read data
            import anndata
            from scipy import sparse as sp
            matrix = data["matrix"]
            gene_names = data["gene_names"]
            cell_barcodes = data["cell_barcodes"]

            # Transpose to cell x gene for AnnData
            if sp.issparse(matrix):
                X = matrix.T.tocsr()
            else:
                X = matrix.T
            obs = pd.DataFrame(index=cell_barcodes)
            var = pd.DataFrame(index=gene_names)
            adata = anndata.AnnData(X=X, obs=obs, var=var)
            adatas[batch_name] = adata

        # Sandbox for assembly code
        sandbox = {
            "sc": sc, "pd": pd, "np": np, "re": re,
            "adatas": adatas, "gsm_map": gsm_map,
            "__builtins__": {
                "True": True, "False": False, "None": None,
                "int": int, "float": float, "str": str, "bool": bool,
                "list": list, "dict": dict, "set": set, "tuple": tuple,
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "print": print, "isinstance": isinstance,
                "Exception": Exception, "ValueError": ValueError,
                "__import__": __import__,
            },
        }

        code = plan.assembly_code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Make processing_code's process_metadata available to assembly_code
        if plan.processing_code:
            try:
                exec(plan.processing_code.strip(), sandbox)
            except Exception:
                pass

        logger.info("Executing assembly code (%d chars)", len(code))
        local_ns: dict = {}
        exec(code, sandbox, local_ns)

        assemble_fn = local_ns.get("assemble_data")
        if assemble_fn is None:
            raise ValueError("No assemble_data() function found in assembly code")

        adata = assemble_fn(adatas, gsm_map)

        # Update state with assembled data
        state.obs = adata.obs
        state.var = adata.var
        X = adata.X
        if sp.issparse(X):
            state.expression_matrix = X.T.tocsr()
        else:
            state.expression_matrix = X.T
        state.matrix_shape = state.expression_matrix.shape
        state.is_sparse = sp.issparse(state.expression_matrix)

        logger.info("Assembly code executed: %s", adata.shape)
        return state

    def _apply_fixed_merge(
        self, state: PipelineState, matrices_data: list[dict[str, Any]],
        file_to_gsm: dict[str, str], plan: ProcessingPlan,
    ) -> PipelineState:
        """Fallback: fixed merge logic when assembly_code is absent or fails."""
        # Only re-merge if expression_matrix is not already set (from step 3)
        if state.expression_matrix is None:
            merged, all_barcodes, all_batch_labels = self._merge_matrices(
                matrices_data, file_to_gsm, plan, state,
            )
            if merged is not None:
                state.expression_matrix = merged
                state.cell_barcodes_raw = all_barcodes
                state.cell_batch_labels = all_batch_labels
                state.gene_names_raw = list(matrices_data[0]["gene_names"]) if matrices_data else []
                state.is_sparse = matrices_data[0]["is_sparse"] if matrices_data else False
                state.matrix_shape = merged.shape
        return state

    def _apply_field_mappings_to_adata(self, adata: Any, plan: ProcessingPlan) -> None:
        """Apply field name standardizations + compound value splits to a single AnnData's obs."""
        if adata is None or adata.obs is None:
            return

        obs = adata.obs
        verified_mappings = plan._verified_field_mappings or plan.field_mappings

        for fm in verified_mappings:
            raw = fm.raw_field if hasattr(fm, "raw_field") else fm.get("raw_field", "")
            canonical = fm.canonical if hasattr(fm, "canonical") else fm.get("canonical", "")
            vs = fm.value_split if hasattr(fm, "value_split") else fm.get("value_split", {})
            if not vs:
                vs = {}

            # 1. Rename raw → canonical if raw exists and canonical doesn't
            if raw in obs.columns and canonical not in obs.columns:
                obs.rename(columns={raw: canonical}, inplace=True)
            elif raw not in obs.columns and canonical not in obs.columns:
                # Column referenced by mapping doesn't exist yet — skip
                continue

            # 2. Compound value splitting
            source_col = canonical if canonical in obs.columns else raw
            if vs and isinstance(vs, dict) and source_col in obs.columns:
                col = obs[source_col].astype(str)
                delimiter = vs.get("delimiter", "")
                pattern = vs.get("pattern", "")
                position = vs.get("position", -1)
                literal = vs.get("literal", "")

                if position >= 0 and delimiter:
                    parts = col.str.split(delimiter, regex=False)
                    obs[canonical] = parts.str[position].str.strip()
                    logger.info("Split compound value: %s[%d] -> %s (delim=%s)",
                                source_col, position, canonical, delimiter)
                elif pattern:
                    extracted = col.str.extract(pattern, expand=False)
                    if extracted is not None:
                        obs[canonical] = extracted.fillna(col).astype(str)
                        logger.info("Split compound value: %s -> %s (pattern=%s)",
                                    source_col, canonical, pattern)
                elif literal:
                    parts = col.str.split(literal, n=1, regex=False)
                    obs[canonical] = parts.str[1].str.strip()
                    logger.info("Split compound value: %s -> %s (literal=%s)",
                                source_col, canonical, literal)

        # 3. Auto-detect remaining underscore-delimited columns
        self._auto_split_adata(adata)

    def _auto_split_adata(self, adata: Any) -> None:
        """Auto-detect and split underscore-delimited compound columns in adata.obs."""
        obs = adata.obs
        common_targets = {
            "tissue": [(0, "cell_source")],
            "cell type": [(0, "cell_line")],
        }
        for col_name, splits in common_targets.items():
            if col_name not in obs.columns:
                continue
            col = obs[col_name].astype(str)
            for pos, sub_name in splits:
                if sub_name not in obs.columns:
                    obs[sub_name] = col.apply(
                        lambda v: v.split("_")[pos]
                        if "_" in str(v) and len(str(v).split("_")) > pos
                        else None
                    )

    def _execute_processing_code_per_batch(
        self, per_batch: dict[str, Any], plan: ProcessingPlan,
    ) -> dict[str, Any]:
        """Execute LLM-generated processing code on each per-batch AnnData."""
        import re

        import numpy as np
        import pandas as pd

        code = plan.processing_code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        result: dict[str, Any] = {}
        for batch_label, adata in per_batch.items():
            obs = adata.obs.copy()
            var = adata.var.copy()
            sandbox = {
                "pd": pd, "np": np, "re": re,
                "obs": obs, "var": var, "adata": adata,
                "__builtins__": {
                    "True": True, "False": False, "None": None,
                    "int": int, "float": float, "str": str, "bool": bool,
                    "list": list, "dict": dict, "set": set, "tuple": tuple,
                    "len": len, "range": range, "enumerate": enumerate,
                    "zip": zip, "map": map, "filter": filter,
                    "print": print, "isinstance": isinstance,
                    "Exception": Exception, "ValueError": ValueError,
                    "TypeError": TypeError, "KeyError": KeyError,
                    "__import__": __import__,
                },
            }
            local_ns: dict = {}
            exec(code, sandbox, local_ns)
            process_fn = local_ns.get("process") or local_ns.get("process_metadata")
            if process_fn:
                try:
                    obs, var, _ = process_fn(obs, var, adata)
                except TypeError:
                    obs, var = process_fn(obs, var)
            adata.obs = obs
            adata.var = var
            result[batch_label] = adata
        return result

    def _execute_assembly_code_per_batch(
        self, per_batch: dict[str, Any], plan: ProcessingPlan,
        matrices_data: list[dict[str, Any]], file_to_gsm: dict[str, str],
    ) -> dict[str, Any]:
        """Execute LLM-generated assembly code that returns dict[str, AnnData]."""
        import re

        import numpy as np
        import pandas as pd
        import scanpy as sc

        # Build adatas dict and gsm_map from per_batch AnnData objects
        adatas: dict[str, Any] = {}
        gsm_map: dict[str, str] = {}
        for batch_label, adata in per_batch.items():
            adatas[batch_label] = adata
            gsm_map[batch_label] = batch_label

        sandbox = {
            "sc": sc, "pd": pd, "np": np, "re": re,
            "adatas": adatas, "gsm_map": gsm_map,
            "__builtins__": {
                "True": True, "False": False, "None": None,
                "int": int, "float": float, "str": str, "bool": bool,
                "list": list, "dict": dict, "set": set, "tuple": tuple,
                "len": len, "range": range, "enumerate": enumerate,
                "zip": zip, "print": print, "isinstance": isinstance,
                "Exception": Exception, "ValueError": ValueError,
                "__import__": __import__,
            },
        }

        code = plan.assembly_code.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            code = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Make processing_code's process_metadata available to assembly_code
        if plan.processing_code:
            try:
                exec(plan.processing_code.strip(), sandbox)
            except Exception:
                pass

        logger.info("Executing assembly code (%d chars)", len(code))
        local_ns: dict = {}
        exec(code, sandbox, local_ns)

        assemble_fn = local_ns.get("assemble_data")
        if assemble_fn is None:
            logger.warning("No assemble_data() function found in assembly code")
            return per_batch

        result = assemble_fn(adatas, gsm_map)
        if isinstance(result, dict):
            return result

        # If assembly code returned a single AnnData (old behavior), warn and wrap
        logger.warning("Assembly code returned a single AnnData instead of dict — wrapping")
        if hasattr(result, "obs"):
            return {list(per_batch.keys())[0]: result}
        return per_batch


class ExecutionMonitor:
    """Wrap execution with error reflection and plan revision.

    On execution failure, captures the error context and
    optionally calls LLM to revise the plan.
    """

    def __init__(self):
        self._executor = PlanExecutor()

    @traceable(name="ExecutionMonitor.execute_with_reflection", tags=["intelligence", "reflection"])
    def execute_with_reflection(
        self,
        report: GseReport,
        plan: ProcessingPlan,
        output_dir: Path,
        state: PipelineState | None = None,
        max_retries: int = _MAX_EXECUTION_RETRIES,
    ) -> ExecutionResult:
        """Execute a plan with automatic reflection on failure."""
        for attempt in range(max_retries + 1):
            logger.info("Execution attempt %d/%d", attempt + 1, max_retries + 1)

            result = self._executor.execute(report, plan, output_dir, state)

            if result.success:
                return result

            if attempt >= max_retries:
                logger.error("Max retries exhausted")
                return result

            logger.warning("Execution failed (attempt %d), reflecting...", attempt + 1)

            # Try LLM reflection to fix the plan
            revised_plan = self._reflect(report, plan, result)
            if revised_plan is not None:
                plan = revised_plan
                logger.info("Plan revised for retry")
            else:
                logger.warning("Could not revise plan, retrying as-is")

        return result

    def _reflect(
        self, report: GseReport, plan: ProcessingPlan, result: ExecutionResult,
    ) -> ProcessingPlan | None:
        """Use LLM to reflect on execution failure and revise plan."""
        try:
            from langchain_openai import ChatOpenAI

            from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL

            error_context = json.dumps({
                "errors": result.errors,
                "warnings": result.warnings,
                "steps_completed": [s.step for s in result.steps if s.status == "completed"],
                "steps_failed": [s.step for s in result.steps if s.status == "failed"],
            }, indent=2)

            prompt = f"""The following processing plan failed during execution.

PLAN:
{plan.to_json()}

EXECUTION ERRORS:
{error_context}

ORIGINAL REPORT:
{report.to_json()}

CRITICAL: The pipeline stores expression matrices as GENE x CELL (rows=genes, columns=cells).
When writing processing_code or assembly_code that uses scanpy/anndata:
- sc.AnnData(X) expects CELL x GENE, so you MUST transpose first: sc.AnnData(X.T)
- Alternatively use matrix.T to convert before creating AnnData
- obs (cell metadata) has n_cells rows, var (gene metadata) has n_genes rows

Analyze the errors and produce a REVISED plan JSON that fixes the issues.
If the error mentions dimension mismatch between obs and X, the fix is almost always
to transpose the matrix before creating AnnData. Only change what's necessary.
Return ONLY the JSON object."""

            llm_kwargs: dict[str, Any] = {
                "model": LLM_MODEL,
                "temperature": 0.0,
            }
            if LLM_BASE_URL:
                llm_kwargs["openai_api_base"] = LLM_BASE_URL
            if LLM_API_KEY:
                llm_kwargs["openai_api_key"] = LLM_API_KEY

            llm = ChatOpenAI(**llm_kwargs)
            response = llm.invoke(prompt)
            content = str(response.content).strip()
            if content.startswith("```"):
                lines = content.split("\n")
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            return ProcessingPlan.from_llm_response(plan.gse_id, content)
        except Exception as exc:
            logger.warning("LLM reflection failed: %s", exc)
            return None
