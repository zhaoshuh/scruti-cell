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
For each expression matrix file, analyze the preview data (preview_rows, columns, h5_shape, n_rows, n_cols) to determine:
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
represents one batch. Per-GSM files take priority over any GSE-level merged file.

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

OUTPUT: JSON with:
{{
  "matrices": [{{"file_key": "EXACT ancestry key from report", "format": "h5", "orientation": "gene_x_cell", "transpose": false, "note": "..."}}],
  "batches": [{{"gsm_id": "GSM9779490", "file_keys": ["exact key"], "cell_count": 21305, "barcode_prefix": "", "note": "reason for this batch", "batch_label": "Control_WT", "reasoning": "Explain WHY a separate batch here.", "defining_fields": ["condition"]}}],
  "assembly_code": "def assemble_data(adatas, gsm_map):\\n    # adatas is dict of {{gsm_id: adata}}\\n    # gsm_map is dict of {{gsm_id: batch_name}}\\n    # Clean and return EACH adata individually. Do NOT merge.\\n    # Return: dict[str, AnnData] where keys are batch labels\\n    # End with: return {{batch_label: adata for batch_label, adata in adatas.items()}}",
  "gene_id_system": "symbol",
  "normalize_genes": true,
  "drop_duplicate_genes": true,
  "normalization": "raw_counts",
  "is_raw_counts": true,
  "strategy_notes": "summary of all findings",
  "confidence": 0.95,
  "requires_literature_check": false,
  "literature_check_reason": ""
}}

IMPORTANT for matrices.orientation:
Use the orientation you determined in Step 3 ("gene_x_cell" or "cell_x_gene"). Do NOT use "auto".
If orientation is "cell_x_gene", set "transpose": true to flip to gene×cell internally.

IMPORTANT for matrices.file_key and batches.file_keys:
Use the EXACT ancestry key from the report's file_summaries.
Do not use short filenames — look them up in the report data you received before.

IMPORTANT for barcode_prefix:
Only fill if actual prefixes were detected. For standard 10X data with empty barcode_prefixes, use "".

IMPORTANT for assembly_code:
Each adata in the `adatas` dict is a raw AnnData object with .X as GENE x CELL (rows=genes, cols=cells).
Write code that cleans/filters each adata individually.
The function MUST return a dict[str, AnnData] — one adata per batch. Do NOT merge matrices together.
Each dict entry will be saved as a separate .h5ad file.
When creating sc.AnnData() or accessing adata.obs/.var, use adata.T or matrix.T to convert to cell×gene first.
Example: adata = sc.AnnData(expression_matrix.T, obs=obs, var=var)
Do NOT call sc.read_10x_h5, sc.read_10x_mtx, or any file reading functions.
Do NOT call sc.concat, np.hstack, or sp.hstack to merge adatas. Keep them separate.

*** BATCH SPLITTING PRIORITY ***
If gsm_file_map in the DATA is present and non-empty:
- Each GSM ID with its own file(s) = one batch
- Do NOT use GSE-level merged files if per-GSM files exist
If gsm_file_map is empty or absent:
- All cells go into a single batch
- Flag batch_separation as "unavailable" in strategy_notes

*** BATCH REASONING (NEW) ***
For EACH batch in the output, you MUST provide a batch_label, reasoning, and defining_fields:
- batch_label: human-readable short name combining key distinguishing info (e.g., "APOE4_Day7")
- reasoning: WHY this forms a separate batch — explain in biological/experimental terms.
  Example: "Different treatment: DMSO control vs Drug X; all other metadata identical"
  Example: "Independent patient sample; different donor_id, same tissue and protocol"
  Example: "Different timepoint (Day 0 vs Day 7) from same patient cohort"
- defining_fields: which GSM characteristic fields DIFFER across batches (e.g., ["treatment", "timepoint"])
Also output "batch_separation_strategy" at top level:
  "per_sample" / "per_condition" / "per_timepoint" / "per_treatment" / "by_gsm_file_map" / "single_batch"

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

            # ---- Literature Verification (NEW) ----
            needs_verification = self._has_unresolved()
            if needs_verification:
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
            messages.append(("human", STEP5_USER))
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
            result.append({
                "gsm_id": gsm.gsm_id,
                "characteristics": gsm.characteristics,
                "source_name": gsm.source_name,
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
            truncated_rows = [
                {c: str(row.get(c, ""))[:100] for c in cols}
                for row in rows[:50]
            ]
            previews[k] = {
                "filename": v.get("filename", k),
                "file_type": v.get("file_type", "?"),
                "extension": v.get("extension", ""),
                "n_rows": v.get("n_rows"), "n_cols": v.get("n_cols"),
                "columns": cols, "preview_rows": truncated_rows,
                "h5_shape": v.get("h5_shape"),
                "h5_sparse": v.get("h5_sparse"),
                "barcode_format": v.get("barcode_format", ""),
                "barcode_samples": v.get("barcode_samples", [])[:10],
                "barcode_prefixes": v.get("barcode_prefixes", []),
                "barcode_prefix_counts": v.get("barcode_prefix_counts", {}),
            }
        gsm_summary = []
        for gsm in report.gsm_entries:
            chars = gsm.characteristics
            gsm_summary.append({
                "gsm_id": gsm.gsm_id,
                "source_name": gsm.source_name[:100],
                "treatment": chars.get("treatment", chars.get("treatment_protocol_ch1", ""))[:100],
                "genotype": chars.get("genotype", "")[:50],
                "tissue": chars.get("tissue", "")[:100],
                "cell_type": chars.get("cell type", chars.get("cell_type", ""))[:100],
            })
        return {"data_files": data_files, "previews": previews, "gsm_summary": gsm_summary,
                "gsm_file_map": report.gsm_file_map}

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
        return "\n".join(lines)
