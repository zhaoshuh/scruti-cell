"""Multi-step metadata extraction orchestrator — 5-step continuous conversation.

All 5 steps run in ONE LLM conversation so context and reasoning naturally accumulate.

Phase 1 — Metadata Pipeline (Steps 1-4):
  Step 1: GSE text → flowcharts
  Step 2: GSM data → field_mappings + compound + redundancy
  Step 3: Files → filename parsing + file roles
  Step 4: Generate metadata_code (deferred execution, not run here)

Phase 2 — Matrix/Batch Pipeline (Step 5):
  Step 5: With cleaned metadata context, produce:
          matrices, batches, assembly_code, gene_id_system, normalization
"""

from __future__ import annotations

import json
import logging
from typing import Any

from scruticell.intelligence.reporter import GseReport

logger = logging.getLogger(__name__)


def _build_llm(model: str | None = None) -> Any:
    """Build a ChatOpenAI instance from config."""
    from langchain_openai import ChatOpenAI

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE
    kwargs: dict[str, Any] = {"model": model or LLM_MODEL, "temperature": LLM_TEMPERATURE}
    if LLM_BASE_URL:
        kwargs["openai_api_base"] = LLM_BASE_URL
    if LLM_API_KEY:
        kwargs["openai_api_key"] = LLM_API_KEY
    return ChatOpenAI(**kwargs)


def _parse_json_or_none(response: str | None) -> dict | None:
    if response is None:
        return None
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        import re
        match = re.search(r'\{.*\}', response, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None


def _extract_code_blocks(content: str) -> str:
    """Strip ``` fences from LLM response if present."""
    c = content.strip()
    if c.startswith("```python"):
        lines = c.split("\n")
        c = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    elif c.startswith("```json"):
        lines = c.split("\n")
        c = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    elif c.startswith("```mermaid"):
        lines = c.split("\n")
        c = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    elif c.startswith("```"):
        lines = c.split("\n")
        c = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return c


# ============================================================
# Prompt templates
# ============================================================

SYSTEM_PROMPT = """You are a bioinformatician working through a GEO scRNA-seq dataset pipeline. We will proceed through 5 steps in one continuous conversation. At each step, I will give you a task and data. Respond with ONLY the JSON output requested for that step. Do NOT plan ahead into later steps until asked.

The five steps are:
1. Create Mermaid flowcharts from the GSE text
2. Extract metadata from GSM data (characteristics + source_name + description)
3. Analyze supplementary files (filenames + content previews)
4. Generate metadata processing code
5. Produce the matrix/batch assembly plan + assembly code

After all 5 steps are complete, we will be done."""


STEP1_USER = """STEP 1 — Create flowcharts from GSE text.

Read the title, summary, and overall_design. Create one or more Mermaid flowcharts (graph TD or graph LR) showing:
- Experimental groups and their relationships (use SPECIFIC names: "Ceria-TPP 150uM" not "treatment drug", "APOE3" not "genotype A")
- The experimental workflow (sample source → treatment → sequencing)
- Group hierarchies (which factors define each group)

GSE DATA:
{gse_json}

OUTPUT: JSON with a "flowcharts" array:
{{"flowcharts": [{{"title": "Experimental groups", "chart": "graph TD\\n    A[APOE3] --> B[Vehicle]\\n    C[APOE4] --> D[Vehicle]\\n    C --> E[Ceria-TPP]\\n    B --> F[scRNA-seq]\\n    D --> F\\n    E --> F"}}]}}
Return ONLY the JSON. No extra text."""


STEP2_USER = """STEP 2 — Extract and standardize metadata from GSM data.

For each GSM entry, analyze ALL three sources together: characteristics dict, source_name, and description.

*** COMPOUND VALUE DETECTION (YOU find them, no pre-flagging) ***
For EVERY value, detect if it contains multiple pieces of information:
- "hiPSC (APOE3)" → tissue + genotype (redundant if genotype field exists)
- "Ceria-TPP (150 uM, 24 h)" → drug + dose + duration
- "Library name: Sample1" → extract after colon
- "Male_Control_72yr" → sex + condition + age

*** CROSS-FIELD REDUNDANCY ***
Compare values across fields within the SAME GSM. If two fields encode the same info, mark redundancy.

*** ABBREVIATIONS ***
If you find abbreviations you can expand, do so. If uncertain (confidence<0.9), mark ambiguous.
EVERY abbreviation MUST include its gsm_id (which GSM it came from) and source_field
(which field/column/file it was extracted from) so downstream literature verification
knows exactly what context to look up.

GSM DATA:
{gsm_json}

OUTPUT: JSON with:
{{
  "field_mappings": [{{"raw_field":..., "canonical":..., "source":..., "value_split":{{}}, "note":...}}],
  "redundant_fields": [{{"field_a":..., "field_b":..., "shared_info":..., "action":...}}],
  "batch_keys": [...],
  "inconsistencies_with_flowchart": [...],
  "ambiguous_abbreviations": [{{"raw_value":..., "best_guess":..., "confidence":..., "gsm_id":..., "source_field":..., "needs_literature_verify":true}}],
  "notes": "..."
}}
Return ONLY the JSON. No extra text."""


STEP3_USER = """STEP 3 — Analyze supplementary files.

For each data file, determine its role (expression_matrix, barcode_list, features_list, etc).

*** FILENAME COMPOUND EXTRACTION ***
Extract all embedded information from filenames using delimiter patterns (underscore, dash):
- "GSM9779490_E3_vehicle_filtered_feature_bc_matrix.h5" → gsm_id="GSM9779490", genotype="E3", treatment="vehicle", role="matrix"
- "Ctrl1_RNASeq_rep2.csv.gz" → group="Ctrl1", assay="RNASeq", replicate="rep2"

*** ORIENTATION AND AXIS METADATA — CRITICAL ***
For each expression matrix file, analyze the preview data to determine:
preview_rows is a COLUMNAR format: a list of row arrays, where each row array's
elements correspond to the columns list by position. Example:
  columns=["gene","count","cell_type"]  →  preview_rows=[["ACTB","5","neuron"],...]
So preview_rows[0][0] is the first row's "gene" value, etc.
1. ORIENTATION: Is the matrix gene×cell or cell×gene?
   - Look at preview row/column labels. Gene names look like: BRCA1, TP53, ENSG00000..., ENSMUSG00000... etc.
   - Cell barcodes look like: AAACCCAAGAAACTCA-1 (14-16bp ACGT sequences with optional -N suffix).
   - Genes are usually 20k-40k in count. Cells are usually 1k-50k.
   - If the row-labels are gene names AND row count ≈ gene count → gene×cell.
   - If the column headers are gene names AND column count ≈ gene count → cell×gene.
2. AXIS METADATA: Check if any axis labels are NEITHER gene names NOR cell barcodes.
   - Do some column names look like metadata fields (e.g. "batch", "sample_id", "condition", "treatment")?
   - Are there extra rows or columns that don't match the dominant gene/barcode pattern?
   - EACH metadata column/row MUST include: position (0-based index within that axis),
     column_name (the header text), sample_values (up to 5 distinct categories found),
     action ("strip_to_obs" for cell-level metadata, "strip_to_var" for gene-level metadata).
3. COMPOUND VALUES: Some metadata columns encode multiple pieces of info.
   - "Male_Control_72yr" → sex="Male", condition="Control", age="72yr" (delimiter "_")
   - "PatientA_Treated_Day7" → patient="PatientA", treatment="Treated", timepoint="Day7"
   - If compound: set compound_value=true and provide sub_fields with canonical names.
4. SOURCE TRACING: For each metadata column, note the source file and GSM it belongs to.

Cross-reference file features with gsm_summary to validate assignments.

*** FILE-SAMPLE MAPPING (gsm_file_map) ***
If gsm_file_map is present and non-empty, each GSM ID with its own file(s)
represents one batch BY DEFAULT — but you may override this in Step 5 if
GSM-level metadata suggests merging or splitting.

*** BARCODE PREFIX AS BATCH CLUE ***
If barcode_prefix_summary has >1 distinct prefix, these represent
different sample sources merged via cellranger aggr or similar.
Note this as a potential batch boundary signal for Step 5.

DATA:
{files_json}

OUTPUT: JSON with:
{{
  "matrix_files": [...],
  "non_matrix_files": [...],
  "file_metadata": [{{"filename":..., "gsm_id":..., "group":..., "role":..., "gene_id_system":..., "normalization":...,
    "orientation": "gene_x_cell"|"cell_x_gene", "orientation_confidence": 0.0-1.0,
    "axis_metadata": [{{"axis":"rows"|"columns", "position":0, "column_name":"sample_info",
      "categories":["Male_Control_72yr","Female_Patient_65yr"], "category_count":2,
      "compound_value":false, "sub_fields":[{{"canonical":"...","position":0,"delimiter":"_"}}],
      "source":"filename:col_N", "confidence":0.95, "needs_literature_verify":false,
      "action":"strip_to_obs"|"strip_to_var"}}]}}],
  "filename_extractions": [{{"filename":..., "components":{{...}}, "confidence":...}}],
  "groups_from_files": [...],
  "inconsistencies_with_flowchart": [...],
  "notes": "..."
}}
Return ONLY the JSON. No extra text."""


STEP4_USER = """STEP 4 — Generate metadata processing code.

Based on your Steps 2+3 analysis, write Python code that transforms obs and var DataFrames.

The function must be: `def process_metadata(obs, var):`
- Apply ALL field renames from Step 2 field_mappings
- Split ALL compound values you detected in Step 2 (use str.split, str.extract, conditional masks)
- Remove redundant columns you identified
- Clean whitespace from all string columns
- Add any new columns derived from abbreviations you expanded
- Use only: pd (pandas), np (numpy), re. No file I/O, network, os, subprocess.
- End with "return obs, var"

IMPORTANT: obs has N_CELLS rows (cell metadata). var has N_GENES rows (gene metadata).
You are working with DataFrames, not AnnData objects. Do NOT create sc.AnnData() or access .X.

Since we are in a continuous conversation, you already have all the context from Steps 2+3.
Do NOT repeat the analysis — just write the code. Return ONLY the code, no JSON wrapper, no explanation.

The code starts with: def process_metadata(obs, var):
"""


STEP5_USER = """STEP 5 — Final: produce the matrix/batch assembly plan.

You have now seen:
- Flowcharts showing the experimental groups (Step 1)
- All GSM metadata, compound splits, and redundancies (Step 2)
- All file roles, filename extractions, matrix candidates, and ORIENTATION analysis (Step 3)
- The metadata code that will clean obs/var (Step 4)

Now produce the final structure that identifies and assembles the expression matrices.

=== BATCH DISCOVERY ===
GSM_ID does NOT always equal batch. You MUST analyze ALL available clues
to determine the true batch boundaries. Consider these priority-ordered signals:

1. GSM METADATA DIFFERENCES (strongest signal):
   Review the varying_fields list above. Fields whose values differ across GSMs
   may define batches. But also consider:
   - Should GSMs with different values of a field be SEPARATE batches?
     Example: treatment=Control vs treatment=Drug → YES, separate
   - Should GSMs with the same field value be MERGED into one batch?
     Example: 3 GSMs all tissue=brain → maybe merge into batch "brain"
   - Are there GSMs that share a common donor/patient but differ in timepoint?
     → group by patient, with timepoint as a batch subdivision

2. FILE PATTERNS:
   - If gsm_file_map maps each GSM to its own file(s), that's a STARTING POINT
     but you may GROUP multiple GSM batches together if metadata justifies it
   - If one matrix file serves multiple GSMs, use barcode_prefixes to split them
   - If filename contains sample identifiers (from Step 3), cross-reference with GSM metadata

3. BARCODE PREFIXES:
   - If barcode_prefix_summary has >1 distinct prefix, each prefix = separate sample source
   - Used in cellranger aggr where 1 file = multiple samples

4. LITERATURE CONTEXT:
   - Step 1 flowcharts were built from GSE summary text describing experimental design.
     Revisit them — the authors often explicitly state group structure there.
   - If LITERATURE VERIFICATION RESULTS appear above (from the paper's methods section),
     pay special attention to:
     a. Any explicit group definitions in verified metadata (e.g., "n=3 per condition")
     b. The disease_context, tissue_context, or treatment_context from the paper
     c. Any resolved abbreviations that clarify group labels (e.g., "E3"→"APOE3 genotype")
   - Ask yourself: "Based on the paper's description, how many groups are there?
     What factor(s) define the groups? Which GSMs belong to which group?"
   - If the paper mentions a specific experimental design (e.g., "paired design",
     "randomized controlled trial", "case-control"), that should inform batch structure.

5. FIELD COMBINATIONS:
   - Sometimes no single field defines batches, but a COMBINATION does
   - Example: donor_id + timepoint together define batches (Patient_A_Day0 vs Patient_B_Day0)
   - batch_label should encode the combination: "P01_Day0", "P01_Day7", etc.

GSM Comparison Table (for reference):
{gsm_comparison}

=== GSM-FILE MAPPING ===
{gsm_file_map_text}

=== BARCODE PREFIX SUMMARY ===
{barcode_prefix_text}

=== OUTPUT SCHEMA ===
{{
  "matrices": [{{"file_key": "EXACT ancestry key from report", "format": "h5", "orientation": "gene_x_cell", "transpose": false, "note": "..."}}],
  "batches": [{{"gsm_id": "GSM9779490", "file_keys": ["exact key"], "cell_count": 21305, "barcode_prefix": "", "note": "...", "batch_label": "Control_WT", "reasoning": "Explain WHY a separate batch here — reference specific metadata fields, files, or barcode patterns.", "defining_fields": ["treatment"]}}],
  "assembly_code": "def assemble_data(adatas, gsm_map):\\n    ...",
  "gene_id_system": "symbol",
  "normalize_genes": true,
  "drop_duplicate_genes": true,
  "normalization": "raw_counts",
  "is_raw_counts": true,
  "strategy_notes": "Explain your OVERALL batch strategy here (e.g., split by treatment, merged all controls, etc.)",
  "batch_separation_strategy": "per_condition|per_patient|per_timepoint|per_sample|by_gsm_file_map|by_barcode_prefix|single_batch",
  "confidence": 0.95,
  "requires_literature_check": false,
  "literature_check_reason": ""
}}

batch_separation_strategy options (pick the BEST description):
  "per_sample" — each GSM is a separate biological sample
  "per_condition" — grouped by treatment/disease/condition field
  "per_patient" — grouped by donor/patient field
  "per_timepoint" — split by time/visit field
  "by_gsm_file_map" — one batch per GSM→file mapping
  "by_barcode_prefix" — split by cell barcode prefix
  "single_batch" — all cells in one batch
  "field_combination" — multiple fields together define batches

IMPORTANT for matrices.orientation:
Use the orientation you determined in Step 3 ("gene_x_cell" or "cell_x_gene"). Do NOT use "auto".
If orientation is "cell_x_gene", set "transpose": true to flip to gene×cell internally.

IMPORTANT for matrices.file_key and batches.file_keys:
Use the EXACT ancestry key from the report's file_summaries.
Do not use short filenames.

IMPORTANT for assembly_code:
Each adata in the `adatas` dict is a raw AnnData object with .X as GENE x CELL.
Write code that cleans/filters each adata individually.
The function MUST return a dict[str, AnnData] — one adata per batch. Do NOT merge matrices together.
Each dict entry will be saved as a separate .h5ad file.
When creating sc.AnnData(), use adata.T or matrix.T to convert to cell×gene first.
Do NOT call sc.read_10x_h5, sc.read_10x_mtx, or any file reading functions.
Do NOT call sc.concat, np.hstack, or sp.hstack to merge adatas.

*** BATCH REASONING (REQUIRED) ***
For EACH batch, you MUST provide:
- batch_label: human-readable short name (e.g., "P01_Day0", "Control_Lung")
- reasoning: WHY this forms a separate batch — explain in biological/experimental terms.
  Reference SPECIFIC metadata fields, files, or barcode patterns you observed.
  Example: "Different treatment: DMSO vs Drug X; all other metadata identical"
  Example: "Independent patient P03; different donor but same tissue and protocol"
  Example: "Split by barcode prefix AAACCCA-1 vs GGGTTTA-2; same matrix file"
- defining_fields: which GSM fields DIFFER across batches (e.g., ["treatment", "timepoint"])

Return ONLY the JSON. No extra text."""


# ============================================================
# 5-Step Continuous Conversation Orchestrator
# ============================================================

class MultiStepExtractor:
    """5-step continuous conversation with accumulated LLM context.

    Steps 1-3 extract metadata. Step 4 generates metadata code.
    Step 5 produces the final matrix/batch assembly plan.
    """

    def __init__(self):
        self._context: dict[str, Any] = {}
        self._metadata_code: str = ""

    def extract_all(self, report: GseReport) -> dict[str, Any]:
        """Run all 5 steps in one continuous LLM conversation."""
        self._context = {"gse_id": report.gse_id}

        # Build data payloads
        gse_json = self._build_gse_json(report)
        gsm_json = self._build_gsm_json(report)
        files_json = self._build_files_json(report)

        try:
            llm = _build_llm()
            messages = [("system", SYSTEM_PROMPT)]

            # ---- Step 1: Flowcharts ----
            logger.info("Step 1/5: flowcharts from GSE text...")
            messages.append(("human", STEP1_USER.format(gse_json=json.dumps(gse_json, indent=2))))
            resp = _extract_code_blocks(str(llm.invoke(messages).content))
            self._context["flowcharts"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # ---- Step 2: GSM metadata ----
            logger.info("Step 2/5: GSM metadata (characteristics + source_name)...")
            messages.append(("human", STEP2_USER.format(gsm_json=json.dumps(gsm_json, indent=2))))
            resp = _extract_code_blocks(str(llm.invoke(messages).content))
            self._context["gsm_extract"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # ---- Step 3: File analysis ----
            logger.info("Step 3/5: file analysis (names + previews)...")
            messages.append(("human", STEP3_USER.format(files_json=json.dumps(files_json, indent=2))))
            resp = _extract_code_blocks(str(llm.invoke(messages).content))
            self._context["file_extract"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # ---- Literature Verification ----
            gsm_count = len(report.gsm_entries)
            needs_verification = self._has_unresolved()
            # Also check literature when PMID exists and multi-GSM (design info)
            has_pmid = bool(report.gse_info.pubmed_id or report.gse_info.title)
            check_design = has_pmid and gsm_count > 1
            if needs_verification or check_design:
                pubmed_id = report.gse_info.pubmed_id
                title = report.gse_info.title
                if pubmed_id or title:
                    logger.info("Literature verification: checking paper for unresolved items...")
                    try:
                        from scruticell.literature.pdf_agent import PDFAgent
                        agent = PDFAgent()
                        gaps = self._collect_verification_gaps()
                        pubmed_id = pubmed_id or ""
                        result = agent.extract(pubmed_id=pubmed_id, gaps=gaps)
                        if result:
                            verify_context = self._build_verification_context(result)
                            messages.append(("human", verify_context))
                            self._context["literature_verified"] = result
                            logger.info(
                                "Literature verified: %d abbreviations, %d fields",
                                len(result.get("abbreviations", {})),
                                len(result.get("metadata", {})),
                            )
                        else:
                            logger.info("Literature verification: no PDF available, skipping")
                    except Exception as exc:
                        logger.warning("Literature verification failed: %s", exc)
                else:
                    logger.info("Literature verification: no PMID/title, skipping")

            # ---- Step 4: Metadata code generation ----
            logger.info("Step 4/5: metadata code generation...")
            messages.append(("human", STEP4_USER))
            resp = _extract_code_blocks(str(llm.invoke(messages).content))
            self._metadata_code = resp if resp.startswith("def process_metadata") else ""
            if not self._metadata_code:
                # Try to extract function from response
                import re
                m = re.search(r'(def process_metadata\(.*?return obs, var\s*)', resp, re.DOTALL)
                if m:
                    self._metadata_code = m.group(1)
            messages.append(("ai", resp))

            # ---- Step 5: Matrix/batch assembly ----
            logger.info("Step 5/5: matrix/batch assembly plan...")
            # Build GSM comparison table + file map text for explicit injection
            gsm_comp_text = self._format_gsm_comparison(files_json)
            gsm_map_text = self._format_gsm_file_map(files_json.get("gsm_file_map", {}))
            barcode_text = self._format_barcode_summary(files_json.get("barcode_prefix_summary", {}))
            step5_prompt = STEP5_USER.format(
                gsm_comparison=gsm_comp_text,
                gsm_file_map_text=gsm_map_text,
                barcode_prefix_text=barcode_text,
            )
            messages.append(("human", step5_prompt))
            resp = _extract_code_blocks(str(llm.invoke(messages).content))
            self._context["final_plan"] = _parse_json_or_none(resp) or {}

            return self._context

        except Exception as exc:
            logger.warning("Multi-step conversation failed: %s", exc)
            return self._context

    @property
    def metadata_code(self) -> str:
        """The metadata processing code generated in Step 4."""
        return self._metadata_code

    def generate_final_plan(self, report: GseReport) -> dict | None:
        """Return the Step 5 output as the final plan dict."""
        return self._context.get("final_plan")

    # ---- Data builders (unchanged) ----

    def _build_gse_json(self, report: GseReport) -> dict:
        return {
            "gse_id": report.gse_id,
            "title": report.gse_info.title,
            "summary": report.gse_info.summary[:3000],
            "overall_design": report.gse_info.overall_design[:1000],
        }

    def _build_gsm_json(self, report: GseReport) -> list[dict]:
        result = []
        for gsm in report.gsm_entries:
            chars = {}
            for k, v in gsm.characteristics.items():
                chars[k] = str(v)[:150] if v else ""
            result.append({
                "gsm_id": gsm.gsm_id,
                "characteristics": chars,
                "source_name": gsm.source_name[:150],
                "description": gsm.description[:200] if gsm.description else "",
            })
        return result

    def _build_files_json(self, report: GseReport) -> dict:
        data_files = [df for df in report.data_files]
        previews = {}
        for k, v in report.file_summaries.items():
            if v.get("file_type") == "soft":
                continue
            cols = v.get("columns", [])[:50]
            rows = v.get("preview_rows", [])
            # Columnar format: avoid repeating column names per row (~50% token saving)
            row_arrays = []
            for row in rows[:50]:
                row_arrays.append([str(row.get(c, ""))[:50] for c in cols])
            previews[k] = {
                "filename": v.get("filename", k),
                "file_type": v.get("file_type", "?"),
                "extension": v.get("extension", ""),
                "n_rows": v.get("n_rows"), "n_cols": v.get("n_cols"),
                "columns": cols,
                "preview_rows": row_arrays,
                "h5_shape": v.get("h5_shape"),
                "h5_sparse": v.get("h5_sparse"),
                "barcode_format": v.get("barcode_format", ""),
                "barcode_samples": v.get("barcode_samples", [])[:10],
                "barcode_prefixes": v.get("barcode_prefixes", []),
                "barcode_prefix_counts": v.get("barcode_prefix_counts", {}),
            }
        # Dynamic GSM summary: collect ALL fields across all GSMs
        all_fields = set()
        for gsm in report.gsm_entries:
            all_fields.update(gsm.characteristics.keys())
        gsm_summary = []
        for gsm in report.gsm_entries:
            row = {"gsm_id": gsm.gsm_id}
            for f in sorted(all_fields):
                row[f] = gsm.characteristics.get(f, "")[:150]
            gsm_summary.append(row)
        # Cross-GSM comparison: detect fields that differ
        varying_fields = []
        identical_fields = []
        for f in sorted(all_fields):
            vals = set()
            for gsm in report.gsm_entries:
                v = gsm.characteristics.get(f, "")
                vals.add(v)
            if len(vals) > 1:
                varying_fields.append(f)
            else:
                identical_fields.append(f)
        # Barcode prefix summary
        barcode_prefixes_all = {}
        for _k, v in report.file_summaries.items():
            for pfx, cnt in (v.get("barcode_prefix_counts", {}) or {}).items():
                barcode_prefixes_all[pfx] = barcode_prefixes_all.get(pfx, 0) + cnt
        return {
            "data_files": data_files, "previews": previews,
            "gsm_summary": gsm_summary,
            "gsm_full_fields": sorted(all_fields),
            "varying_fields": varying_fields,
            "identical_fields": identical_fields,
            "gsm_file_map": report.gsm_file_map,
            "barcode_prefix_summary": barcode_prefixes_all,
        }

    # ---- Step 5 data formatters ----

    @staticmethod
    def _format_gsm_comparison(files_json: dict) -> str:
        """Build a human-readable GSM comparison table showing field differences."""
        gsm_summary = files_json.get("gsm_summary", [])
        varying = files_json.get("varying_fields", [])
        identical = files_json.get("identical_fields", [])
        all_fields = files_json.get("gsm_full_fields", [])

        if not gsm_summary:
            return "(no GSM data)"

        lines = ["**Fields that DIFFER across GSMs (batch-defining candidates):**"]
        if varying:
            for f in varying:
                vals = []
                for g in gsm_summary:
                    vals.append(f"  {g['gsm_id']}={g.get(f, '?')}")
                lines.append(f"  {f}:")
                lines.extend(vals)
        else:
            lines.append("  (none — all fields identical across GSMs)")

        lines.append("")
        lines.append("**Fields IDENTICAL across all GSMs:**")
        if identical:
            for f in identical:
                v = gsm_summary[0].get(f, "?") if gsm_summary else "?"
                lines.append(f"  {f}: {v}")
        else:
            lines.append("  (none)")
        return "\n".join(lines)

    @staticmethod
    def _format_gsm_file_map(gsm_file_map: dict) -> str:
        """Format the GSM→file mapping as readable text."""
        if not gsm_file_map:
            return "(empty — no per-GSM file mapping available. "
            "All sample-level data is in shared/multi-sample files. "
            "Use metadata fields to determine batches.)"
        lines = ["Each GSM has its own file(s):"]
        for gsm_id, files in sorted(gsm_file_map.items()):
            file_list = files if isinstance(files, list) else [files]
            lines.append(f"  {gsm_id} → {', '.join(str(f) for f in file_list[:3])}")
        return "\n".join(lines)

    @staticmethod
    def _format_barcode_summary(prefixes: dict) -> str:
        """Format barcode prefix summary."""
        if not prefixes:
            return "(none — no barcode prefixes detected. "
            "If GSM-level files exist, each file likely corresponds to one sample. "
            "If only one matrix file exists and no barcode prefixes, "
            "the entire matrix is a single batch.)"
        lines = ["Barcode prefix distribution (possible multi-sample merge):"]
        for pfx, cnt in sorted(prefixes.items(), key=lambda x: -x[1]):
            lines.append(f"  {pfx}: {cnt} cells")
        if len(prefixes) > 1:
            lines.append(f"  → {len(prefixes)} distinct prefixes found — "
                          "each likely a separate sample source.")
        return "\n".join(lines)

    def _has_unresolved(self) -> bool:
        """Check if Step 2 or Step 3 produced low-confidence results."""
        gsm_extract = self._context.get("gsm_extract", {})
        if isinstance(gsm_extract, dict):
            for abbr in gsm_extract.get("ambiguous_abbreviations", []):
                if isinstance(abbr, dict):
                    if abbr.get("needs_literature_verify") or (abbr.get("confidence") or 0) < 0.7:
                        return True

        file_extract = self._context.get("file_extract", {})
        if isinstance(file_extract, dict):
            for ex in file_extract.get("filename_extractions", []):
                if isinstance(ex, dict) and (ex.get("confidence") or 0) < 0.7:
                    return True
            for fm in file_extract.get("file_metadata", []):
                if isinstance(fm, dict) and (fm.get("orientation_confidence") or 0) < 0.7:
                    return True
        return False

    def _collect_verification_gaps(self) -> dict:
        """Collect uncertain items with full context (gsm_id, source, filename) for ORC verification."""
        items: list[dict] = []

        gsm_extract = self._context.get("gsm_extract", {})
        if isinstance(gsm_extract, dict):
            # Step 2 uncertain abbreviations — carry gsm_id + source_field
            for abbr in gsm_extract.get("ambiguous_abbreviations", []):
                if isinstance(abbr, dict) and abbr.get("needs_literature_verify"):
                    items.append({
                        "raw_value": abbr.get("raw_value", ""),
                        "best_guess": abbr.get("best_guess", ""),
                        "confidence": abbr.get("confidence", 0),
                        "gsm_id": abbr.get("gsm_id", ""),
                        "source_field": abbr.get("source_field", ""),
                        "source_step": "step2_gsm",
                        "context": (
                            f"GSM {abbr.get('gsm_id', '?')} field "
                            f"'{abbr.get('source_field', '?')}' = "
                            f"'{abbr.get('raw_value', '')}'"
                        ),
                    })

        file_extract = self._context.get("file_extract", {})
        if isinstance(file_extract, dict):
            # Step 3 low-confidence filename extractions — carry filename + gsm_id
            for fe in file_extract.get("filename_extractions", []):
                if isinstance(fe, dict) and (fe.get("confidence") or 0) < 0.7:
                    comps = fe.get("components", {})
                    items.append({
                        "raw_value": str(comps),
                        "best_guess": "",
                        "confidence": fe.get("confidence") or 0,
                        "gsm_id": comps.get("gsm_id", ""),
                        "filename": fe.get("filename", ""),
                        "source_step": "step3_filename",
                        "context": (
                            f"Filename '{fe.get('filename', '?')}' extracted components: "
                            f"{comps}"
                        ),
                    })

            # Step 3 axis_metadata uncertainties (from file_metadata)
            for fm in file_extract.get("file_metadata", []):
                if isinstance(fm, dict) and (fm.get("orientation_confidence") or 0) < 0.7:
                    items.append({
                        "raw_value": fm.get("orientation", ""),
                        "best_guess": fm.get("orientation", ""),
                        "confidence": fm.get("orientation_confidence", 0),
                        "filename": fm.get("filename", ""),
                        "source_step": "step3_orientation",
                        "context": (
                            f"File '{fm.get('filename', '?')}' orientation uncertain: "
                            f"{fm.get('orientation', '?')} "
                            f"(confidence={fm.get('orientation_confidence', 0)})"
                        ),
                    })
                # Per-column axis metadata needing literature verification
                for am in fm.get("axis_metadata", []):
                    if not isinstance(am, dict):
                        continue
                    if am.get("needs_literature_verify") or (am.get("confidence") or 0) < 0.7:
                        cats = am.get("categories", [])
                        items.append({
                            "raw_value": cats[0] if cats else am.get("column_name", ""),
                            "best_guess": am.get("suspected_meaning", am.get("column_name", "")),
                            "confidence": am.get("confidence") or 0,
                            "gsm_id": fm.get("gsm_id", ""),
                            "filename": fm.get("filename", ""),
                            "source_field": am.get("column_name", ""),
                            "source_step": "step3_axis",
                            "context": (
                                f"File '{fm.get('filename', '?')}' column "
                                f"'{am.get('column_name', '?')}' (position {am.get('position', '?')}) "
                                f"categories: {cats[:5] if cats else '?'}"
                            ),
                        })

        # Add experimental design question when multi-GSM (batch discovery aid)
        items.append({
            "raw_value": "experimental_design",
            "best_guess": "",
            "confidence": 0.5,
            "gsm_id": "",
            "source_field": "_experimental_design",
            "source_step": "step5_batch",
            "context": (
                "From the paper's methods section, determine the experimental group "
                "structure: How many groups? What defines each group (treatment, "
                "timepoint, genotype)? Which samples/GSMs belong to which group? "
                "Is it a paired/crossover/factorial design? "
                "This information is critical for correct batch assignment."
            ),
        })

        return {"items": items}

    def _build_verification_context(self, result: dict) -> str:
        """Build context message for LLM conversation from verified_items."""
        lines = ["LITERATURE VERIFICATION RESULTS (from paper):", ""]

        verified_items = result.get("verified_items", [])
        if verified_items:
            lines.append("Resolved ambiguous items:")
            for vi in verified_items:
                if isinstance(vi, dict):
                    lines.append(
                        f"  - '{vi.get('raw_value', '?')}' = '{vi.get('resolved', '?')}' "
                        f"[GSM={vi.get('gsm_id', '?')}, "
                        f"source={vi.get('source_step', '?')}, "
                        f"confidence={vi.get('confidence', 0):.0%}]"
                    )
            lines.append("")
        else:
            # Fallback for old-style result format
            abbrs = result.get("abbreviations", {})
            if abbrs:
                lines.append("Confirmed abbreviations:")
                for raw, resolved in abbrs.items():
                    lines.append(f"  - '{raw}' = {resolved}")
                lines.append("")

        metadata = result.get("metadata", {})
        if metadata:
            lines.append("Extracted metadata from paper:")
            for key, value in metadata.items():
                lines.append(f"  - {key}: {value}")
            lines.append("")
        lines.append("Use this verified information in the following steps.")
        lines.append("")
        lines.append("USE THIS FOR BATCH GROUPING: The experimental design described")
        lines.append("above determines how samples should be grouped into batches.")
        lines.append("In Step 5, align your batch_separation_strategy with this design.")
        return "\n".join(lines)
