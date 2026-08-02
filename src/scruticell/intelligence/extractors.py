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
from scruticell.utils.tokens import invoke_and_track

from scruticell.config import get_output_path

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

READER_BATCH_SYSTEM_PROMPT = """You are a bioinformatician writing file reader code for GEO scRNA-seq datasets.

You will receive a list of ALL data files with their loose previews (first 30 rows x 30 cols, read as
plain strings). Your task: group related files, then generate a reader_code for each group.

READER CODE SIGNATURE (same for all readers):
def read_matrix(filepath, nrows=None):
    # filepath: str -- actual file path (for 10x groups, pass the directory)
    # nrows: int|None -- max rows, None = all
    return {
        "matrix": np.ndarray,       # float32, (n_cells, n_genes)
        "gene_names": list[str],
        "cell_barcodes": list[str],
        "columns": list[str],
        "preview_rows": list[dict],
        "n_rows": int, "n_cols": int,
        "is_sparse": bool, "shape": tuple,
    }

GROUPING RULES:
- 10X mtx.gz + barcodes.tsv.gz + features.tsv.gz in same directory -> ONE group
  (format="mtx", group_type="10x")
- Standalone CSV/TSV with expression data -> standalone (format="csv", group_type="standalone")
- Metadata-only CSV/TSV (small, no expression values) -> standalone
  (format="csv", group_type="metadata")
- h5/h5ad files -> standalone (format="h5ad", group_type="standalone")

READING RULES:
- 10X mtx: sc.read_10x_mtx(dirpath) -- pass the DIRECTORY with all 3 files
  Use scanpy (import scanpy as sc). .gz handled transparently.
- CSV gene symbols in col 0: pd.read_csv(filepath, index_col=0)
- CSV empty first header: pd.read_csv(filepath, index_col=0)
- CSV comment lines (#): pd.read_csv(filepath, comment="#")
- Multi-header: pd.read_csv(filepath, header=[0,1])
- 10X barcodes: pd.read_csv(filepath, header=None, names=["barcode"])
- 10X features: pd.read_csv(filepath, header=None, sep="\\t",
  names=["gene_id","gene_name","feature_type"])
- Metadata CSV: pd.read_csv(filepath) -- return empty matrix, data in columns/preview
- Always float32 matrix. Only use: pd, np, sc, csv, gzip. NO network, os, subprocess.

OUTPUT SCHEMA:
{
  "readers": [
    {
      "reader_name": "short_unique_name",
      "file_keys": ["exact ancestry keys from the file list below"],
      "format": "h5ad|mtx|csv",
      "group_type": "10x|standalone|metadata",
      "reader_code": "def read_matrix(filepath, nrows=None):\\n    ...",
      "note": "Why this group"
    }
  ]
}
Return ONLY the JSON. No extra text."""


READER_BATCH_USER_PROMPT = """Analyze these data files and generate reader code for each group.

=== ALL DATA FILES AND LOOSE PREVIEWS ===
{files_text}

=== COMPANION FILE MAP (10X detection) ===
{companion_text}

=== TASK ===
1. Group related files (10X mtx+barcode+features form ONE group)
2. For each standalone file, determine if it's expression or metadata
3. Generate reader_code for each group
4. Output the JSON (readers array)

Return ONLY the JSON. No extra text."""


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


STEP2_USER = """STEP 2 — Write a metadata extraction function from raw GSM data.

You are given SAMPLED GSM entries (max 20). Each GSM entry has:
- `characteristics`: pre-processed characteristics dict (already key-value pairs)
- `_raw`: full raw GEOparse entry, including:
  - `characteristics_ch1`: string or list of colon-separated pairs
  - `extract_protocol_ch1`: multi-line protocol text (lines joined with newlines)
  - `data_processing`: multi-line processing info (lines joined with newlines)
  - `description`, `title`: multi-line text fields (lists joined with newlines)
  - `organism_ch1`, `molecule_ch1`, `taxid_ch1`: organism & assay info
  - `library_strategy`, `library_selection`, `library_source`, `instrument_model`: technical metadata
  - All raw fields are PLAIN TEXT — do NOT try to parse Python list syntax like brackets.

*** YOUR TASK ***
Write a SINGLE function `extract_metadata(gsm_entry)` that returns a dict of {{field_name: value}}.

*** AVAILABLE IN THE FUNCTION'S EXECUTION ENVIRONMENT ***
- gsm_entry: the GsmInfo object for one GSM
- get_char(gsm_entry, key, default=''): extract from raw characteristics_ch1
- re: Python regex module (re.search, re.match, re.findall, re.compile)
- gsm_entry.organism, gsm_entry.molecule, gsm_entry.source_name, gsm_entry.description
- gsm_entry._raw.get('field', ''): direct access to any raw field
- Standard Python: str methods (split, strip, lower, startswith, find, etc.), int(), bool(), len()

*** WHAT TO EXTRACT — SCAN EVERYTHING ***
1. Direct fields: organism, molecule, library_strategy, instrument_model, taxid, etc.
2. Characteristics: use get_char() for tissue, disease, sex, etc.
3. Parse text fields with simple string methods (split, find, startswith) and re.search():
   - description: use re.search(r'Library name: (.+)', desc) to get library_name
   - title: "p21_3w_1" -> use title.split('_') for condition, age, replicate
   - extract_protocol_ch1: multi-line text, use re.search() to find patterns like
     "3-week-old", "p21 reporter", "tamoxifen", "CD31/CD45/TER119", "FACS"
   - data_processing: "Cell Ranger", "mm10"
   - List fields (description, protocol) are joined with newlines — use re.search() directly,
     do NOT try to strip brackets with regex.

*** RULES ***
- Extract RAW values as-is. Do NOT normalize or transform.
- Use re.search() with r'raw-string' patterns. Do NOT use re.sub() to strip brackets.
- Use str.split() for delimiter-based parsing. Use str.find() or 'in' for simple checks.
- Only include fields with non-empty values for at least one GSM.
- Wrap extraction in try/except for robustness.

*** EXAMPLE ***
def extract_metadata(gsm_entry):
    result = {{}}
    result['organism'] = str(gsm_entry.organism)
    result['tissue'] = get_char(gsm_entry, 'tissue')
    result['molecule'] = str(gsm_entry._raw.get('molecule_ch1', ''))
    result['taxid'] = str(gsm_entry._raw.get('taxid_ch1', ''))

    # Parse title with split (e.g. "p21_3w_1")
    title = str(gsm_entry._raw.get('title', ''))
    parts = title.split('_')
    if len(parts) >= 3:
        result['condition'] = parts[0]
        result['age'] = parts[1]
        result['replicate'] = parts[2]

    # Parse description: 'Library name: p21_3wks_1\n10x Genomics'
    desc = str(gsm_entry._raw.get('description', ''))
    m = re.search(r'Library name: (.+)', desc)
    if m:
        result['library_name'] = m.group(1).strip()
    if '10x Genomics' in desc:
        result['platform'] = '10x Genomics'

    # Parse protocol: multi-line text with experimental details
    protocol = str(gsm_entry._raw.get('extract_protocol_ch1', ''))
    m = re.search(r'(\\d+)-week-old', protocol)
    if m:
        result['age_text'] = m.group(0)
    m = re.search(r'(p21[^ ,\\n]+)', protocol)
    if m:
        result['strain'] = m.group(1)
    if 'tamoxifen' in protocol.lower():
        result['treatment'] = 'tamoxifen'
    if 'FACS' in protocol:
        result['sorting'] = 'FACS'

    return result

*** IMPORTANT ***
- Raw text fields (description, extract_protocol_ch1, data_processing) are PLAIN TEXT
  with lines joined by newlines. They are NOT Python string representations.
  Do NOT use re.sub() or regex to strip [ ] brackets — just search the text directly.

*** BATCH KEYS ***
Identify which fields define experimental batches. Add their names to batch_keys.

GSM DATA (varying / identical fields across ALL GSMs + sampled representatives):
{gsm_json}

OUTPUT: JSON with:
{{
  "extraction_code": "def extract_metadata(gsm_entry):\\n    ...",
  "batch_keys": [...],
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


STEP4_USER = """STEP 4 — Axis metadata strip code (handled by executor, produce a simple pass-through).

Write a no-op function — axis metadata stripping is now handled by the pipeline executor
directly. This step exists for backward compatibility.

The function must be: `def strip_axis_metadata(adata, axis_metadata):`
    return adata

Return ONLY the code, no JSON wrapper, no explanation.

The code starts with: def strip_axis_metadata(adata, axis_metadata):
    return adata"""


STEP5A_USER = """STEP 5a — Determine batch boundaries ONLY.

You have now seen:
- Flowcharts showing the experimental groups (Step 1)
- All GSM metadata, compound splits, and redundancies (Step 2)
- All file roles, filename extractions, matrix candidates, and ORIENTATION analysis (Step 3)

Your ONLY task: decide how cells are grouped into batches.
Do NOT produce matrices, assembly_code, or normalization settings — that is Step 5b's job.

=== BATCH DISCOVERY ===
Always aim for 1:1 mapping between batches and their defining characteristics.

STEP 1 (preferred): One batch per GSM — per_sample / by_gsm_file_map.
  If gsm_file_map maps each GSM to its own file(s):
    → one batch per GSM
    → gsm_id = EXACT single GSM ID string from the report (NOT a list)
    → batch_label = descriptive name derived from metadata
    → defining_fields = metadata fields characterizing this sample
  Use per-GSM unless GSMs are truly identical or the file structure demands otherwise.

STEP 2 (fallback): Find another 1:1 batch-splitting criterion.
  Only when per-GSM doesn't work:
    - All metadata identical across GSMs → merge identical ones, keep per-distinct batch
    - No gsm_file_map → use barcode prefix splits, field combinations, or literature
    - Criterion MUST be 1:1: each batch uniquely identifiable by its defining_fields
    - If any defining_field value appears in multiple batches, use field_combination
    - Use literature context (flowcharts, paper methods) as supplementary evidence

GOLDEN RULE: Every batch MUST be uniquely identifiable by its defining_fields.
Split before merge — downstream analysis can always merge batches later.

GSM Comparison Table (for reference):
{gsm_comparison}

=== GSM-FILE MAPPING ===
{gsm_file_map_text}

=== BARCODE PREFIX SUMMARY ===
{barcode_prefix_text}

=== AXIS METADATA (per-file, columns/rows embedded inside matrix files) ===
{axis_meta_text}

=== EXTERNAL METADATA FILES (supplemental tables, not expression matrices) ===
{metadata_files_text}

=== JOINT DECISION: BATCHES + METADATA FILES ===
Metadata files may contain per-sample or per-gene annotations that help define batches.
A metadata column linking "sample_id" to "condition" is batch-defining evidence.
Decide batches AND metadata_files TOGETHER: metadata informs batch boundaries; batches
determine which GSM each metadata row matches.

LIBRARY STRATEGY (MANDATORY per batch):
Every batch MUST have a detectable sequencing method. If SOFT gave library_strategy,
use it as a defining_field (when it varies) or field_mapping (when identical).
If library_strategy is missing from SOFT, the Step 2 field_mappings should have
inferred it from molecule, instrument, description, or file types.
At minimum, classify each batch by omics type on a best-effort basis.

=== STRATEGY SELECTION RULES ===
Before picking batch_separation_strategy, check the GSM Comparison Table:
  1. DEFAULT: "per_sample" or "by_gsm_file_map" — one batch per GSM.
     A batch is naturally a single GSM sample unless evidence says otherwise.
  2. If ALL metadata is identical across multiple GSMs (true replicates):
      → STILL use per_sample — one batch per GSM.
      → Identical replicates should be kept separate to preserve biological
        variability. Downstream analysis can always merge them later.
      → gsm_id = individual GSM accession string (single, not list).
  3. If ANY value of ANY defining_field appears in MULTIPLE planned batches:
     → batch_separation_strategy MUST be "field_combination"
     → defining_fields MUST list ALL fields whose combination is unique
     Example: genotype=APOE4 in batch2 & batch3 → field_combination,
              defining_fields: ["genotype", "treatment"]
  4. Only use "per_condition" / "per_patient" / "per_timepoint"
     when you verify that each defining_field value maps 1:1 to exactly one batch.
  5. If you cannot reliably assign batch boundaries (e.g. GSM metadata missing),
     or batches can NOT be matched to GSM entries at all:
     → batch_separation_strategy = "per_sample"
     → defining_fields = null (empty list)
     → Each GSM-file gets its own batch; verifier skips metadata checks.

batch_separation_strategy options:
  "per_sample" — each GSM is a separate batch (PREFERRED default)
  "by_gsm_file_map" — one batch per GSM→file mapping
  "per_condition" — grouped by treatment/disease/condition field (requires 1:1 mapping!)
  "per_patient" — grouped by donor/patient field (requires 1:1 mapping!)
  "per_timepoint" — split by time/visit field (requires 1:1 mapping!)
  "by_barcode_prefix" — split by cell barcode prefix
  "single_batch" — all cells in one batch
  "field_combination" — multiple fields together define batches (use when values overlap)

=== OUTPUT SCHEMA ===
{{
  "batches": [{{"gsm_id": "GSM9779490 (EXACT single string, not list)", "file_keys": ["exact key"], "cell_count": 21305, "barcode_prefix": "", "note": "...", "batch_label": "Control_WT", "reasoning": "Explain WHY a separate batch here — reference specific metadata fields, files, or barcode patterns.", "defining_fields": ["treatment"]}}],
  "metadata_files": [{{"reader_path": "readers/xxx.py (copy from file preview)", "match_column": "sample_id", "match_target": "gsm_id|obs_col:col_name|gene_name", "target_type": "obs|var", "columns": ["col1","col2"], "note": "..."}}],
  "field_mappings": [{{"raw_field": "exact field name from characteristics or Step 2", "canonical": "standardized_name", "source": "characteristics|filename|source_name|description|literature|GSM", "note": "..."}}],
  "batch_separation_strategy": "per_sample|by_gsm_file_map|per_condition|per_patient|per_timepoint|by_barcode_prefix|single_batch|field_combination",
  "strategy_notes": "Explain your OVERALL batch strategy here (e.g., split by treatment, merged all controls, etc.)",
  "confidence": 0.95,
  "requires_literature_check": false,
  "literature_check_reason": ""
}}

*** METADATA FILES GUIDANCE ***
- metadata_files maps external annotation tables (NOT SOFT characteristics) into obs or var.
- match_target options:
    "gsm_id" → match metadata row to batch by GSM ID (e.g., sample_id == GSM ID)
    "obs_col:col_name" → match by obs column value (e.g., barcode, original_id)
    "gene_name" → match by var.index gene symbols
- If a metadata file's categories include GSM IDs or batch_labels, use those as match_target.
- If the file has per-cell annotations (cell barcodes in a column), use obs_col:cell_barcode.
- columns: which metadata columns to inject (empty = all non-match columns).
- Executor will auto-skip batches where no match is found — you don't need target_batches.

*** BATCH REASONING (REQUIRED) ***
For EACH batch, you MUST provide:
- batch_label: human-readable short name (e.g., "P01_Day0", "Control_Lung")
- reasoning: WHY this forms a separate batch — explain in biological/experimental terms.
  Reference SPECIFIC metadata fields, files, or barcode patterns you observed.
- defining_fields: which GSM fields DIFFER across batches (e.g., ["treatment", "timepoint"])

Return ONLY the JSON. No extra text."""


STEP5B_USER = """STEP 5b — Produce the matrix assembly plan and code.

You have now seen Step 5a's batch boundaries — DO NOT MODIFY THEM.

=== BATCHES (from Step 5a — FIXED, DO NOT MODIFY) ===
{batch_plan_json}

Your ONLY task:
1. Select which files are expression matrices (from Step 3 file_metadata)
2. Determine orientation and whether transpose is needed
3. Write the assembly_code that cleans/assembles each batch's AnnData
4. Set normalization and gene ID system

IMPORTANT for matrices:
- file_key: EXACT ancestry key from the report's file_summaries
- format: "h5ad" for .h5ad and 10X .h5 files, "mtx" for mtx+barcode+features group or standalone mtx, "csv" for csv/tsv
- orientation: Use your Step 3 determination ("gene_x_cell" or "cell_x_gene"). Do NOT use "auto".
- transpose: true if orientation is "cell_x_gene" (to flip to gene×cell)
- reader_path: If the file preview has a non-empty "reader_path", copy it EXACTLY.
  This is pre-generated reader code from the inspect stage — do NOT modify or re-create it.
  Each matrix can only have one reader_path. 10X groups share the same reader_path.
- Each batch from 5a needs its file_keys to match the matrices you select

IMPORTANT for assembly_code signature:
  def assemble_data(adatas: dict[str, AnnData], gsm_map: dict[str, dict]) -> dict[str, AnnData]:
- adatas keys are file_key strings (exact ancestry keys, e.g. "download/.../GSMxxx.h5")
- Each adata value is a raw AnnData with .X as GENE x CELL (genes in rows, cells in columns)
- gsm_map keys are batch_label strings, values: {{"gsm_id": "<GSM ID>"}}
- fk_to_batch mapping (also available in sandbox): file_key → batch_label
  Use fk_to_batch[fk] to know which batch a file belongs to.
- Filename-derived metadata (condition, replicate, etc.) is ALREADY in adata.obs — do NOT re-inject
- Write code that cleans/filters each adata individually
- Return dict[str, AnnData] keyed by batch_label (from plan's batch_labels, e.g. "p21_3wks_1").
  Do NOT use file_key as output keys. Use batch_label.
- Each return key MUST match a batch_label in the plan so downstream steps can match batches.
- Use adata.T or matrix.T to convert to cell×gene before creating AnnData
- Do NOT call sc.read_10x_h5, sc.read_10x_mtx, or any file reading functions
- Do NOT call sc.concat, np.hstack, or sp.hstack to merge adatas

=== OUTPUT SCHEMA ===
{{
  "matrices": [{{"file_key": "EXACT ancestry key from report", "format": "h5ad", "orientation": "gene_x_cell", "transpose": false, "reader_path": "readers/xxx.py (copy from file preview or empty)", "note": "..."}}],
  "assembly_code": "def assemble_data(adatas, gsm_map):\\n    ...",
  "gene_id_system": "symbol|ensembl_gene_id|entrez_id",
  "normalize_genes": true,
  "drop_duplicate_genes": true,
  "normalization": "raw_counts",
  "is_raw_counts": true,
  "confidence": 0.95
}}

Note: metadata_files were already defined in Step 5a. Do NOT re-output them here.
Return ONLY the JSON. No extra text."""


# ============================================================
# Reader code generation (inspect stage, independent of 5-step)
# ============================================================


def generate_all_readers(
    files_info: list[dict[str, Any]],
    companion_map: dict[str, list[str]],
    model: str | None = None,
) -> dict[str, Any]:
    """Call LLM to generate reader code for ALL files at once.

    Args:
        files_info: list of dicts with keys:
            - ancestry: full path key
            - filename, extension, size_bytes
            - preview_raw: list[list[str]]
            - file_type: "csv", "mtx", "h5", etc.
        companion_map: dict mapping ancestry -> list of sibling ancestry keys
            (for 10X mtx+barcode+features detection)
        model: LLM model override.

    Returns:
        Dict with "readers" key (list of reader definitions), or {"error": "..."} on failure.
    """
    import json as _json

    from langchain_openai import ChatOpenAI

    from scruticell.config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, LLM_TEMPERATURE

    # Build files text
    parts: list[str] = []
    for fi in files_info:
        raw = fi.get("preview_raw", [])
        preview_text = "\n".join(
            "\t".join(str(v) for v in row) for row in (raw or [])
        )
        parts.append(
            f"File: {fi.get('filename', '?')} | "
            f"Key: {fi.get('ancestry', '?')} | "
            f"Type: {fi.get('file_type', '?')} | "
            f"Size: {fi.get('size_bytes', 0) / (1024 * 1024):.1f} MB | "
            f"Ext: {fi.get('extension', '?')}\n"
            f"Preview ({len(raw or [])} rows x {(raw[0] and len(raw[0])) or 0} cols):\n"
            f"{preview_text or '(empty)'}\n"
        )
    files_text = "\n".join(parts)

    # Build companion text
    comp_lines: list[str] = []
    for parent, siblings in companion_map.items():
        sib_names = [s.rsplit("/", 1)[-1] for s in siblings]
        comp_lines.append(f"{parent}  ->  {', '.join(sib_names)}")
    companion_text = "\n".join(comp_lines) if comp_lines else "(none)"

    prompt = READER_BATCH_USER_PROMPT.format(
        files_text=files_text,
        companion_text=companion_text,
    )

    llm_kwargs: dict[str, Any] = {
        "model": model or LLM_MODEL,
        "temperature": LLM_TEMPERATURE,
    }
    if LLM_BASE_URL:
        llm_kwargs["openai_api_base"] = LLM_BASE_URL
    if LLM_API_KEY:
        llm_kwargs["openai_api_key"] = LLM_API_KEY

    llm = ChatOpenAI(**llm_kwargs)

    try:
        messages = [
            ("system", READER_BATCH_SYSTEM_PROMPT),
            ("human", prompt),
        ]
        response = invoke_and_track(llm, messages, "generate_readers")
        content = str(response.content).strip()

        if content.startswith("```"):
            lines = content.split("\n")
            content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        result = _json.loads(content)
        if not isinstance(result, dict) or "readers" not in result:
            return {"error": f"Invalid LLM response format: {content[:200]}"}
        return result

    except _json.JSONDecodeError as e:
        return {"error": f"JSON parse error: {e}"}
    except Exception as exc:
        return {"error": f"Generation failed: {exc}"}


# ============================================================
# 5-Step Continuous Conversation Orchestrator
# ============================================================

class MultiStepExtractor:
    """5+2 step continuous conversation with accumulated LLM context.

    Steps 1-3 extract metadata. Step 4 generates metadata code.
    Step 5a determines batch boundaries. Step 5b produces matrix assembly plan.
    """

    def __init__(self):
        self._context: dict[str, Any] = {}
        self._metadata_code: str = ""
        self._cached_messages: list = []  # preserved across extract_all calls
        self._cached_files_json: dict = {}

    def extract_all(
        self, report: GseReport, mismatch_context: str = "",
        skip_steps_1to4: bool = False,
    ) -> dict[str, Any]:
        """Run all 5 steps in one continuous LLM conversation.

        When skip_steps_1to4=True, preserves the cached conversation from
        a previous run and only re-invokes Step 5 with mismatch context.
        """
        self._context = {"gse_id": report.gse_id}

        # Build data payloads
        gse_json = self._build_gse_json(report)
        gsm_json = self._build_gsm_json(report)
        files_json = self._build_files_json(report)

        # cache for potential re-use
        self._cached_files_json = files_json

        try:
            llm = _build_llm()

            # ---- Fast-path: skip Steps 1-4, re-run Steps 5a+5b ----
            if skip_steps_1to4 and self._cached_messages:
                logger.info("Skipping Steps 1-4, re-running Steps 5a+5b with mismatch context")
                messages = list(self._cached_messages)
                if mismatch_context:
                    messages.append(("human", mismatch_context))
                gsm_comp_text = self._format_gsm_comparison(files_json)
                gsm_map_text = self._format_gsm_file_map(files_json.get("gsm_file_map", {}))
                barcode_text = self._format_barcode_summary(files_json.get("barcode_prefix_summary", {}))

                # Step 5a: batch discovery
                step5a_prompt = STEP5A_USER.format(
                    gsm_comparison=gsm_comp_text,
                    gsm_file_map_text=gsm_map_text,
                    barcode_prefix_text=barcode_text,
                    axis_meta_text=self._format_axis_metadata(files_json),
                    metadata_files_text=self._format_metadata_categories(files_json),
                )
                messages.append(("human", step5a_prompt))
                resp = _extract_code_blocks(str(
                    invoke_and_track(llm, messages, "step5a_replan").content))
                self._context["batch_plan"] = _parse_json_or_none(resp) or {}

                # Step 5b: matrix/code assembly (with batch plan from 5a as fixed input)
                batch_plan_json = json.dumps(self._context["batch_plan"], indent=2, ensure_ascii=False)
                step5b_prompt = STEP5B_USER.format(batch_plan_json=batch_plan_json)
                messages.append(("ai", resp))
                messages.append(("human", step5b_prompt))
                resp = _extract_code_blocks(str(
                    invoke_and_track(llm, messages, "step5b_replan").content))
                self._context["assembly_plan"] = _parse_json_or_none(resp) or {}
                return self._context

            # ---- Full pipeline: Steps 1-5 ----
            messages = [("system", SYSTEM_PROMPT)]

            # ---- Step 1: Flowcharts ----
            logger.info("Step 1/5: flowcharts from GSE text...")
            messages.append(("human", STEP1_USER.format(gse_json=json.dumps(gse_json, indent=2))))
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step1_flowchart").content))
            self._context["flowcharts"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # ---- Step 2: GSM metadata ----
            logger.info("Step 2/5: GSM metadata (characteristics + source_name)...")
            messages.append(("human", STEP2_USER.format(gsm_json=json.dumps(gsm_json, indent=2))))
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step2_gsm").content))
            self._context["gsm_extract"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # Save GSM extraction results to JSON
            try:
                from pathlib import Path
                gse_id = report.gse_id
                out_dir = get_output_path(gse_id)
                out_dir.mkdir(parents=True, exist_ok=True)
                json_path = out_dir / "gsm_meta_extractions.json"
                json_path.write_text(
                    json.dumps(self._context["gsm_extract"], indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                logger.info("GSM meta extractions saved to %s", json_path)
            except Exception:
                logger.warning("Failed to save GSM meta extractions to JSON", exc_info=True)

            # ---- Step 3: File analysis ----
            logger.info("Step 3/5: file analysis (names + previews)...")
            messages.append(("human", STEP3_USER.format(files_json=json.dumps(files_json, indent=2))))
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step3_file").content))
            self._context["file_extract"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # Save filename_extractions.json — keyed by base filename for injection
            try:
                from pathlib import Path as _Path
                fe_json = self._context["file_extract"].get("filename_extractions", [])
                fe_by_file: dict[str, dict] = {}
                for fe in fe_json:
                    if isinstance(fe, dict):
                        fname = fe.get("filename", "")
                        comps = fe.get("components", {})
                        if fname and comps:
                            fe_by_file[fname] = {k: str(v) for k, v in comps.items() if v}
                if fe_by_file:
                    gse_id = report.gse_id
                    out_dir = get_output_path(gse_id)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    json_path = out_dir / "filename_extractions.json"
                    json_path.write_text(
                        json.dumps(fe_by_file, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    logger.info("Filename extractions saved to %s (%d files)", json_path, len(fe_by_file))
            except Exception:
                pass

            # Save axis_meta CSVs (one per file, for batch separation + injection)
            try:
                from pathlib import Path as _Path
                fm_json = self._context["file_extract"].get("file_metadata", [])
                gse_id = report.gse_id
                out_dir = get_output_path(gse_id) / "axis_meta"
                out_dir.mkdir(parents=True, exist_ok=True)
                saved = 0
                for fm in fm_json:
                    if not isinstance(fm, dict):
                        continue
                    fname = fm.get("filename", "")
                    am_list = fm.get("axis_metadata", [])
                    if not am_list:
                        continue
                    rows: list[str] = ["column_name,categories,axis,position,action,compound_value,delimiter,sub_fields"]
                    for am in am_list:
                        if not isinstance(am, dict):
                            continue
                        cats = ",".join(str(c)[:80] for c in am.get("categories", [])[:10])
                        sfs = am.get("sub_fields", [])
                        sf_str = "|".join(
                            f"{sf.get('canonical','')}::{sf.get('delimiter','_')}::{sf.get('position',0)}"
                            for sf in sfs if isinstance(sf, dict)
                        ) if sfs else ""
                        rows.append(
                            f"{am.get('column_name','')},{cats},{am.get('axis','')},"
                            f"{am.get('position','')},{am.get('action','')},"
                            f"{am.get('compound_value',False)},{am.get('delimiter','_')},{sf_str}"
                        )
                    csv_path = out_dir / f"{fname}.csv"
                    csv_path.write_text("\n".join(rows), encoding="utf-8")
                    saved += 1
                if saved:
                    logger.info("Axis meta CSVs saved to %s (%d files)", out_dir, saved)
            except Exception:
                pass

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
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step4_metadata").content))
            self._metadata_code = resp if resp.startswith("def strip_axis_metadata") else ""
            if not self._metadata_code:
                # Try to extract function from response
                import re
                m = re.search(r'(def strip_axis_metadata\(.*?return adata\s*)', resp, re.DOTALL)
                if m:
                    self._metadata_code = m.group(1)
            messages.append(("ai", resp))

            # ---- Re-plan context injection (from prior verification failure) ----
            if mismatch_context:
                messages.append(("human", mismatch_context))

            # ---- Step 5a: Batch discovery ----
            logger.info("Step 5a/5: batch discovery...")
            gsm_comp_text = self._format_gsm_comparison(files_json)
            gsm_map_text = self._format_gsm_file_map(files_json.get("gsm_file_map", {}))
            barcode_text = self._format_barcode_summary(files_json.get("barcode_prefix_summary", {}))
            meta_text = self._format_metadata_categories(files_json)
            step5a_prompt = STEP5A_USER.format(
                gsm_comparison=gsm_comp_text,
                gsm_file_map_text=gsm_map_text,
                barcode_prefix_text=barcode_text,
                axis_meta_text=self._format_axis_metadata(files_json),
                metadata_files_text=meta_text,
            )
            messages.append(("human", step5a_prompt))
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step5a_batch").content))
            self._context["batch_plan"] = _parse_json_or_none(resp) or {}
            messages.append(("ai", resp))

            # ---- Step 5b: Matrix/code assembly (batches fixed from 5a) ----
            logger.info("Step 5b/5: matrix/code assembly...")
            batch_plan_json = json.dumps(self._context["batch_plan"], indent=2, ensure_ascii=False)
            step5b_prompt = STEP5B_USER.format(batch_plan_json=batch_plan_json)
            messages.append(("human", step5b_prompt))
            resp = _extract_code_blocks(str(
                invoke_and_track(llm, messages, "step5b_matrix").content))
            self._context["assembly_plan"] = _parse_json_or_none(resp) or {}

            # Cache conversation for potential re-plan (skip Steps 1-4)
            self._cached_messages = list(messages)

            return self._context

        except Exception as exc:
            logger.warning("Multi-step conversation failed", exc_info=True)
            # Retry once on connection/network errors
            if ("connection" in str(exc).lower() or "timeout" in str(exc).lower()) \
                    and "llm" in vars() and "messages" in vars():
                import time as _time
                _time.sleep(5)
                try:
                    resp = _extract_code_blocks(str(
                        invoke_and_track(llm, messages, "step_retry").content))
                    self._context["final_plan"] = _parse_json_or_none(resp) or {}
                    self._cached_messages = list(messages)
                    logger.info("Multi-step conversation succeeded on retry")
                except Exception as exc2:
                    logger.error("Multi-step conversation retry also failed: %s", exc2)
            return self._context

    @property
    def metadata_code(self) -> str:
        """The metadata processing code generated in Step 4."""
        return self._metadata_code

    def generate_final_plan(self, report: GseReport) -> dict | None:
        """Merge Step 5a batch plan + Step 5b assembly plan into final plan dict."""
        batch_plan = self._context.get("batch_plan", {})
        assembly_plan = self._context.get("assembly_plan", {})

        if not batch_plan and not assembly_plan:
            # Fallback: old single-step format
            return self._context.get("final_plan")

        if not isinstance(batch_plan, dict):
            batch_plan = {}
        if not isinstance(assembly_plan, dict):
            assembly_plan = {}

        merged = dict(batch_plan)
        merged.update(assembly_plan)
        return merged

    # ---- Data builders (unchanged) ----

    def _build_gse_json(self, report: GseReport) -> dict:
        return {
            "gse_id": report.gse_id,
            "title": report.gse_info.title,
            "summary": report.gse_info.summary[:3000],
            "overall_design": report.gse_info.overall_design[:1000],
        }

    def _build_gsm_json(self, report: GseReport) -> dict:
        # Field scan: collect all fields and their unique values across ALL GSMs
        all_fields: set[str] = set()
        field_values: dict[str, set[str]] = {}
        for gsm in report.gsm_entries:
            for k, v in gsm.characteristics.items():
                all_fields.add(k)
                field_values.setdefault(k, set()).add(str(v)[:150] if v else "")

        varying: list[str] = [f for f, vs in field_values.items() if len(vs) > 1]
        identical: list[str] = [f for f, vs in field_values.items() if len(vs) == 1]

        # Smart sampling: max 20 GSMs covering all unique values of varying fields.
        # When all fields are identical, 2 samples suffice to prove uniformity.
        max_sample = 20 if varying else 2
        sampled: list[dict] = []
        seen_ids: set[str] = set()

        for field in sorted(varying):
            for val in sorted(field_values[field])[:3]:
                for gsm in report.gsm_entries:
                    if gsm.gsm_id in seen_ids:
                        continue
                    if str(gsm.characteristics.get(field, ""))[:150] == val:
                        seen_ids.add(gsm.gsm_id)
                        sampled.append(self._gsm_to_dict(gsm))
                        break
                if len(sampled) >= max_sample:
                    break
            if len(sampled) >= max_sample:
                break

        if len(sampled) < max_sample:
            for gsm in report.gsm_entries:
                if gsm.gsm_id not in seen_ids:
                    sampled.append(self._gsm_to_dict(gsm))
                    if len(sampled) >= max_sample:
                        break

        return {
            "total_gsm_count": len(report.gsm_entries),
            "sampled_count": len(sampled),
            "all_fields": sorted(all_fields),
            "varying_fields": sorted(varying),
            "identical_fields": sorted(identical),
            "gsm_sample": sampled,
        }

    @staticmethod
    def _gsm_to_dict(gsm: Any) -> dict[str, Any]:
        chars = {}
        for k, v in gsm.characteristics.items():
            chars[k] = str(v)[:150] if v else ""
        raw_data: dict[str, Any] = {}
        if hasattr(gsm, "_raw") and gsm._raw:
            raw_chars = gsm._raw.get("characteristics_ch1", [])
            if isinstance(raw_chars, list):
                raw_data["characteristics_ch1"] = [str(x)[:200] for x in raw_chars]
            elif isinstance(raw_chars, str) and raw_chars.strip():
                raw_data["characteristics_ch1"] = [raw_chars.strip()[:200]]
            for k in ("organism_ch1", "molecule_ch1", "taxid_ch1",
                       "source_name_ch1", "description", "title",
                       "library_strategy", "instrument_model",
                       "extract_protocol_ch1", "data_processing",
                       "library_selection", "library_source"):
                if k in gsm._raw and gsm._raw[k]:
                    val = gsm._raw[k]
                    if isinstance(val, list):
                        raw_data[k] = "\n".join(str(v)[:200] for v in val)
                    else:
                        raw_data[k] = str(val)[:200]
        return {
            "gsm_id": gsm.gsm_id,
            "characteristics": chars,
            "source_name": gsm.source_name[:200],
            "description": gsm.description[:300] if gsm.description else "",
            "organism": gsm.organism[:100],
            "molecule": gsm.molecule[:100],
            "library_strategy": gsm.library_strategy[:100],
            "_raw": raw_data,
        }

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
                if isinstance(row, dict):
                    row_arrays.append([str(row.get(c, ""))[:50] for c in cols])
                elif isinstance(row, list):
                    row_arrays.append([str(v)[:50] for v in row[:50]])
            previews[k] = {
                "filename": v.get("filename", k),
                "file_type": v.get("file_type", "?"),
                "extension": v.get("extension", ""),
                "n_rows": v.get("n_rows"), "n_cols": v.get("n_cols"),
                "columns": cols,
                "preview_rows": row_arrays,
                "reader_path": v.get("reader_path", ""),
                "column_categories": v.get("column_categories", {}),
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

    @staticmethod
    def _format_axis_metadata(files_json: dict) -> str:
        """Format axis metadata for Step 5a LLM context — one line per file's metadata column."""
        fm_list = files_json.get("file_metadata", [])
        if not fm_list:
            return "(none — no metadata columns found inside matrix axes)"
        lines: list[str] = []
        for fm in fm_list:
            if not isinstance(fm, dict):
                continue
            fname = fm.get("filename", "")
            am_list = fm.get("axis_metadata", [])
            if not am_list:
                continue
            for am in am_list:
                if not isinstance(am, dict):
                    continue
                cats = am.get("categories", [])
                cats_str = ", ".join(str(c)[:60] for c in cats[:6]) if cats else "?"
                lines.append(
                    f"  {fname}: column={am.get('column_name','?')} "
                    f"axis={am.get('axis','?')} "
                    f"categories=[{cats_str}] "
                    f"action={am.get('action','?')}"
                )
                if am.get("compound_value") and am.get("sub_fields"):
                    sfs = am["sub_fields"]
                    sf_parts = []
                    for sf in sfs:
                        if isinstance(sf, dict):
                            sf_parts.append(
                                f"{sf.get('canonical','?')}(pos={sf.get('position','?')},"
                                f"delim={sf.get('delimiter','_')})"
                            )
                    lines.append(f"    → compound split: {', '.join(sf_parts)}")
        return "\n".join(lines) if lines else "(none)"

    @staticmethod
    def _format_metadata_categories(files_json: dict) -> str:
        """Format external metadata file categories for Step 5a LLM context."""
        previews = files_json.get("previews", {})
        meta_files: list[str] = []
        for k, v in previews.items():
            cats = v.get("column_categories", {})
            if not cats:
                continue
            parts = [f"File: {v.get('filename', k)}"]
            rp = v.get("reader_path", "")
            if rp:
                parts.append(f"  reader_path: {rp}")
            parts.append(f"  n_cols: {v.get('n_cols', 0)}, n_rows: {v.get('n_rows', 0)}")
            for col_name, unique_vals in cats.items():
                vals_str = ", ".join(unique_vals[:20])
                parts.append(f"  column '{col_name}': [{vals_str}]")
            meta_files.append("\n".join(parts))
        if not meta_files:
            return "(no external metadata files detected)"
        return "\n\n".join(meta_files)

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
