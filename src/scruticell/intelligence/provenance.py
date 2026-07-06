"""Origin trace report: where metadata came from, how it was verified,
and how each batch's matrix/cells/genes/metadata were assembled.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────


@dataclasses.dataclass
class MetadataTrace:
    """Origin of a single obs column."""
    column_name: str
    sample_value: str
    source_path: list[str]          # e.g. ["GSM characteristics", "step2_gsm"]
    extraction_method: str          # "direct" | "compound_split" | "regex_extract" | "literature_orc" | "literature_global"
    derived_from: str = ""          # parent column if compound split
    confidence: float = 1.0
    verified_by: str = ""           # "LLM analysis" | "literature ORC" | "none"
    excluded: bool = False
    exclusion_reason: str = ""


@dataclasses.dataclass
class BatchTrace:
    """How one GSM batch was assembled."""
    gsm_id: str
    batch_label: str
    matrix_file: str
    matrix_format: str
    original_shape: tuple[int, int]
    orientation: str                # "gene_x_cell" → "cell_x_gene (transposed)"
    cells: int
    barcode_format: str
    genes: int
    genes_pruned: int
    gene_id_system: str
    metadata_injected: list[MetadataTrace] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)
    batch_reasoning: str = ""           # LLM explanation of why this is a separate batch
    defining_fields: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class VerificationLog:
    """How metadata was validated across all steps."""
    llm_steps_run: list[str] = dataclasses.field(default_factory=list)
    literature_available: bool = False
    literature_pmid: str = ""
    orc_items_verified: int = 0
    orc_items_total: int = 0
    fields_excluded: list[dict[str, str]] = dataclasses.field(default_factory=list)
    compound_splits: int = 0
    kb_matches: int = 0


@dataclasses.dataclass
class TraceReport:
    """Complete origin trace for a GSE processing run."""
    gse_id: str
    batches: list[BatchTrace] = dataclasses.field(default_factory=list)
    verification: VerificationLog = dataclasses.field(default_factory=VerificationLog)
    overall_confidence: float = 0.0
    total_cells: int = 0
    total_genes: int = 0
    batch_separation_strategy: str = ""   # from plan

    def to_dict(self) -> dict[str, Any]:
        return {
            "gse_id": self.gse_id,
            "batches": [dataclasses.asdict(b) for b in self.batches],
            "verification": dataclasses.asdict(self.verification),
            "overall_confidence": self.overall_confidence,
            "total_cells": self.total_cells,
            "total_genes": self.total_genes,
        }

    def format_text(self) -> str:
        """Human-readable provenance text suitable for CLI output."""
        lines: list[str] = []
        sep = "-" * 60
        lines.append(f"\n{sep}")
        lines.append(f"  TRACE: {self.gse_id}")
        lines.append(sep)
        v = self.verification
        lines.append(f"  LLM Steps Run: {', '.join(v.llm_steps_run) if v.llm_steps_run else 'none'}")
        lit = "available" if v.literature_available else "not available"
        lines.append(f"  Literature: {lit} (PMID={v.literature_pmid or 'N/A'})")
        lines.append(f"  ORC Verified: {v.orc_items_verified}/{v.orc_items_total} items")
        if v.fields_excluded:
            lines.append(f"  Fields Excluded: {len(v.fields_excluded)}")
            for fe in v.fields_excluded:
                lines.append(f"    - {fe.get('field', '?')}: {fe.get('reason', '?')}")
        lines.append(f"  Compound Splits: {v.compound_splits}")
        lines.append(f"  KB Matches: {v.kb_matches}")

        for b in self.batches:
            lines.append(f"\n  -- BATCH: {b.gsm_id} --")
            if b.batch_reasoning:
                lines.append(f"  Why batch: {b.batch_reasoning}")
            if b.defining_fields:
                lines.append(f"  Differs in: {', '.join(b.defining_fields)}")
            lines.append(f"  Matrix: {b.matrix_file[-60:]}")
            lines.append(f"  Format: {b.matrix_format} | Orientation: {b.orientation}")
            lines.append(f"  Cells: {b.cells} | Genes: {b.genes} (pruned: {b.genes_pruned})")
            lines.append(f"  Gene ID system: {b.gene_id_system}")
            if b.warnings:
                for w in b.warnings:
                    lines.append(f"  Warning: {w}")
            if b.metadata_injected:
                lines.append(f"  Metadata injected ({len(b.metadata_injected)} columns):")
                for mt in b.metadata_injected:
                    if mt.excluded:
                        lines.append(f"    X {mt.column_name:20s} excluded: {mt.exclusion_reason}")
                        continue
                    path_str = " -> ".join(mt.source_path)
                    lines.append(
                        f"    + {mt.column_name:20s} [{path_str}]"
                    )
                    if mt.derived_from:
                        lines.append(f"       derived from: {mt.derived_from}")
                    lines.append(f"       value: {mt.sample_value}")
            else:
                lines.append(f"  Metadata: none")

        lines.append(f"\n  -- OVERALL --")
        lines.append(f"  Cells: {self.total_cells} | Genes: {self.total_genes}")
        lines.append(f"  Batches: {len(self.batches)} | Confidence: {self.overall_confidence:.0%}")
        lines.append(sep)
        return "\n".join(lines)

    def format_markdown(self) -> str:
        """Markdown format suitable for documentation and sharing."""
        md: list[str] = []
        md.append(f"# Trace Report: {self.gse_id}\n")
        v = self.verification

        md.append("## Verification\n")
        md.append(f"- **LLM Steps:** {', '.join(v.llm_steps_run) if v.llm_steps_run else 'none'}")
        lit = "available" if v.literature_available else "not available"
        md.append(f"- **Literature:** {lit} (PMID: {v.literature_pmid or 'N/A'})")
        md.append(f"- **ORC Verified:** {v.orc_items_verified}/{v.orc_items_total} items")
        if v.fields_excluded:
            md.append(f"- **Fields Excluded:** {len(v.fields_excluded)}")
            for fe in v.fields_excluded:
                md.append(f"  - `{fe.get('field', '?')}`: {fe.get('reason', '?')}")
        md.append(f"- **Compound Splits:** {v.compound_splits}")
        md.append(f"- **KB Matches:** {v.kb_matches}")
        md.append("")

        for b in self.batches:
            md.append(f"## Batch: {b.gsm_id}\n")
            if b.batch_reasoning:
                md.append(f"**Why this batch:** {b.batch_reasoning}\n")
            if b.defining_fields:
                md.append(f"**Differs in:** {', '.join(b.defining_fields)}\n")
            md.append(f"| Property | Value |")
            md.append(f"|----------|-------|")
            md.append(f"| Matrix file | `{b.matrix_file}` |")
            md.append(f"| Format | {b.matrix_format} |")
            md.append(f"| Orientation | {b.orientation} |")
            md.append(f"| Cells | {b.cells} |")
            md.append(f"| Genes | {b.genes} (pruned: {b.genes_pruned}) |")
            md.append(f"| Gene ID system | {b.gene_id_system} |")
            md.append("")

            if b.warnings:
                md.append("**Warnings:**")
                for w in b.warnings:
                    md.append(f"- {w}")
                md.append("")

            if b.metadata_injected:
                md.append(f"### Metadata ({len(b.metadata_injected)} columns)\n")
                md.append(f"| Column | Source | Method | Value |")
                md.append(f"|--------|--------|--------|-------|")
                for mt in b.metadata_injected:
                    if mt.excluded:
                        continue
                    path_str = " > ".join(mt.source_path)
                    method = mt.extraction_method
                    if mt.derived_from:
                        method += f" (from `{mt.derived_from}`)"
                    val = mt.sample_value[:50] if mt.sample_value else "-"
                    md.append(f"| `{mt.column_name}` | {path_str} | {method} | {val} |")
                md.append("")
            else:
                md.append("*No metadata injected.*\n")

        md.append("## Overall\n")
        md.append(f"| Property | Value |")
        md.append(f"|----------|-------|")
        md.append(f"| Cells | {self.total_cells} |")
        md.append(f"| Genes | {self.total_genes} |")
        md.append(f"| Batches | {len(self.batches)} |")
        md.append(f"| Confidence | {self.overall_confidence:.0%} |")
        md.append("")
        return "\n".join(md)


# ──────────────────────────────────────────────
# Builder
# ──────────────────────────────────────────────


class ProvenanceBuilder:
    """Builds a TraceReport from execution state, plan, and report."""

    def __init__(
        self,
        report: Any,
        plan: Any,
        state: Any,
    ):
        self._report = report
        self._plan = plan
        self._state = state

    def build(self) -> TraceReport:
        report = TraceReport(gse_id=self._report.gse_id)
        report.overall_confidence = self._state.overall_confidence()
        report.batch_separation_strategy = getattr(
            self._plan, "batch_separation_strategy", ""
        ) or ""

        # ── Verification log
        report.verification = self._build_verification()

        # ── Per-batch trace
        per_batch = getattr(self._state, "per_batch_adatas", None) or {}
        if not per_batch:
            return report

        for batch_label, adata in per_batch.items():
            bt = self._build_batch_trace(batch_label, adata)
            report.batches.append(bt)
            report.total_cells += bt.cells
            if not report.total_genes:
                report.total_genes = bt.genes

        return report

    # ── Verification log ────────────────────────────

    def _build_verification(self) -> VerificationLog:
        v = VerificationLog()
        plan = self._plan

        # LLM steps (always present if plan exists via multi-step)
        v.llm_steps_run = ["step1:flowchart", "step2:gsm", "step3:file", "step4:lit", "step5:plan"]

        # Literature status
        lit_meta = getattr(plan, "_literature_metadata", None) or {}
        v.literature_available = bool(lit_meta)
        v.literature_pmid = lit_meta.get("_pubmed_id", "")

        # ORC verified items
        orc_items = [
            fm for fm in (getattr(plan, "_verified_field_mappings", None) or [])
            if _safe_get(fm, "_src", "").startswith("step4_orc")
        ]
        v.orc_items_verified = len(orc_items)
        v.orc_items_total = v.orc_items_verified  # only verified items in the plan

        # Excluded fields
        for fm in getattr(plan, "field_mappings", []) or []:
            raw = _safe_get(fm, "raw_field", "?")
            note = (_safe_get(fm, "note", "") or "").lower()
            if "redundant" in note:
                v.fields_excluded.append({"field": raw, "reason": "redundant"})
            elif "ambiguous" in note or "uncertain" in note:
                v.fields_excluded.append({"field": raw, "reason": note})

        # Compound splits
        for fm in getattr(plan, "_verified_field_mappings", []) or [] + (
            getattr(plan, "field_mappings", []) or []
        ):
            vs = _safe_get(fm, "value_split", {}) or {}
            if isinstance(vs, dict) and vs and not vs.get("delimiter") and not vs.get("pattern"):
                v.compound_splits += 1

        return v

    # ── Batch trace ─────────────────────────────────

    def _build_batch_trace(self, batch_label: str, adata: Any) -> BatchTrace:
        plan = self._plan
        report = self._report

        # Matrix mapping from plan
        gsm_id = batch_label
        batch_def = None
        for b in getattr(plan, "batches", []) or []:
            if _safe_get(b, "gsm_id", "") == gsm_id:
                batch_def = b
                break

        file_key = ""
        matrix_format = ""
        orientation = ""
        if batch_def and _safe_get(batch_def, "file_keys", []):
            file_key = batch_def.file_keys[0] if hasattr(batch_def, "file_keys") else batch_def["file_keys"][0]

        matrix_shape = (0, 0)
        for m in getattr(plan, "matrices", []) or []:
            fk = _safe_get(m, "file_key", "")
            if fk == file_key:
                matrix_format = _safe_get(m, "format", "?")
                transpose = _safe_get(m, "transpose", False)
                orientation = _safe_get(m, "orientation", "auto")
                if orientation == "gene_x_cell":
                    orientation = "gene_x_cell -> cell_x_gene (transposed)"
                break

        barcode_fmt = ""
        for fk, summary in getattr(report, "file_summaries", {}).items():
            if fk.endswith(file_key.split("GSE")[-1].split("/")[-1].split(".mx")[0]):
                barcode_fmt = _safe_get(summary, "barcode_format", "") or ""
                break

        file_name = file_key.split("/")[-1] if file_key else ""
        final_genes = int(adata.n_vars) if hasattr(adata, "n_vars") else 0
        # Estimate gene pruning from shape: original genes (from matrix) - final genes
        orig_genes = matrix_shape[0] if matrix_shape else 0
        genes_pruned = orig_genes - final_genes if orig_genes > final_genes else 0

        bt = BatchTrace(
            gsm_id=gsm_id,
            batch_label=batch_label,
            matrix_file=file_name,
            matrix_format=matrix_format,
            original_shape=matrix_shape,
            orientation=orientation,
            cells=int(adata.n_obs) if hasattr(adata, "n_obs") else 0,
            barcode_format=barcode_fmt,
            genes=final_genes,
            genes_pruned=genes_pruned,
            gene_id_system=_safe_get(plan, "gene_id_system", "?") or "?",
            batch_reasoning=_safe_get(batch_def, "reasoning", "") if batch_def else "",
            defining_fields=_safe_get(batch_def, "defining_fields", []) if batch_def else [],
        )

        # Metadata trace
        if hasattr(adata, "obs") and adata.obs is not None:
            bt.metadata_injected = self._trace_metadata(batch_label, adata)

        return bt

    def _trace_metadata(self, batch_label: str, adata: Any) -> list[MetadataTrace]:
        """Trace origin of each obs column."""
        plan = self._plan
        traces: list[MetadataTrace] = []

        verified_mappings = getattr(plan, "_verified_field_mappings", None) or []
        field_mappings = getattr(plan, "field_mappings", None) or []

        # Build lookup of canonical -> source info (prefer step2_gsm over step3)
        canonical_src: dict[str, dict] = {}
        for fm in verified_mappings:
            canon = _safe_get(fm, "canonical", "")
            src = _safe_get(fm, "_src", "") or "step2_gsm"
            raw = _safe_get(fm, "raw_field", "")
            if canon and (canon not in canonical_src or src.startswith("step2")):
                canonical_src[canon] = {"_src": src, "raw_field": raw}
            if raw and raw != canon and (raw not in canonical_src or src.startswith("step2")):
                canonical_src[raw] = {"_src": src, "raw_field": raw, "canonical": canon}

        # Source path labels
        src_labels = {
            "step2_gsm": "GSM characteristics",
            "step3_filename": "filename extraction",
            "step4_orc_step2_gsm": "literature ORC",
            "step4_orc_step3_filename": "literature ORC",
            "step4_literature_global": "literature (global)",
            "step4_code": "processing code",
        }

        # Derived columns from verified_field_mappings' value_split
        _derived_parents: dict[str, str] = {}
        for fm in verified_mappings:
            vs = fm.get("value_split", {}) if isinstance(fm, dict) else getattr(fm, "value_split", {})
            if vs and isinstance(vs, dict):
                parent = fm.get("raw_field", "") if isinstance(fm, dict) else getattr(fm, "raw_field", "")
                if parent:
                    for child in vs:
                        _derived_parents[child] = parent

        # Also check filename_extractions
        filename_comps: dict[str, Any] = {}
        for fe in getattr(plan, "_filename_extractions", []) or []:
            comps = fe.get("components", {})
            if comps.get("gsm_id") == batch_label:
                filename_comps = comps
                break

        # Build set of axis_metadata column names for tracing
        axis_cols: set[str] = set()
        for am in getattr(plan, "_axis_metadata", []) or []:
            if isinstance(am, dict):
                cn = am.get("column_name", "")
                if cn:
                    axis_cols.add(cn)
                for sf in am.get("sub_fields", []):
                    if isinstance(sf, dict):
                        axis_cols.add(sf.get("canonical", ""))

        for col in adata.obs.columns:
            val = str(adata.obs[col].iloc[0]) if hasattr(adata.obs, "iloc") else "?"

            src_info = canonical_src.get(col, {})
            src = src_info.get("_src", "")

            source_path: list[str] = []
            extraction_method = "direct"
            derived_from = ""

            if col == "batch":
                source_path = ["executor: batch assignment"]
            elif src.startswith("step4"):
                source_path = ["Step 4: literature verification"]
                if "orc" in src:
                    extraction_method = "literature_orc"
                else:
                    extraction_method = "literature_global"
            elif src.startswith("step3") and col not in filename_comps:
                source_path = ["GSM: filename extraction"]
                extraction_method = "filename_parse"
            elif src.startswith("step2"):
                source_path = ["GSM: GSM characteristics"]
            elif col in _derived_parents:
                parent = _derived_parents[col]
                source_path = ["GSM: compound split"]
                extraction_method = "compound_split"
                if parent in canonical_src:
                    derived_from = parent
                elif parent in adata.obs.columns:
                    derived_from = parent
            elif src:
                src_lbl = src_labels.get(src, src)
                source_path = [f"GSM: {src_lbl}"]
            elif col in axis_cols:
                source_path = ["matrix axis metadata"]
                extraction_method = "axis_strip"
                # Check if compound split
                for am in (getattr(plan, "_axis_metadata", []) or []):
                    if not isinstance(am, dict):
                        continue
                    for sf in am.get("sub_fields", []):
                        if isinstance(sf, dict) and sf.get("canonical") == col:
                            extraction_method = "compound_split"
                            derived_from = am.get("column_name", "")
                            break
            else:
                # Unknown: check if value is non-empty to see if processing_code created it
                sample_vals = adata.obs[col].unique()
                non_empty = [v for v in sample_vals if str(v).strip() and v is not None]
                if len(non_empty) > 0 and non_empty != ["nan"]:
                    source_path = ["processing code"]
                    extraction_method = "derived"
                else:
                    source_path = ["unknown"]
                    extraction_method = "unknown"

            mt = MetadataTrace(
                column_name=col,
                sample_value=val[:80],
                source_path=source_path,
                extraction_method=extraction_method,
                derived_from=derived_from,
                confidence=1.0,
                verified_by="LLM analysis" if source_path else "none",
            )
            traces.append(mt)

        return traces


def _safe_get(obj: Any, attr: str, default: Any = "") -> Any:
    """Get attribute or dict key from either a dataclass or dict."""
    if obj is None:
        return default
    if hasattr(obj, attr):
        return getattr(obj, attr, default)
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default
