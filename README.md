# Scrut-Cell

> GEO single-cell data standardization pipeline — from NCBI GSE accession to analysis-ready AnnData in minutes.

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--alpha-orange)](https://github.com/zhaoshuh/scruti-cell)

---

## What is Scrut-Cell?

Public single-cell datasets on GEO are notoriously inconsistent: metadata fields have 10+ naming variants per concept, expression matrices hide in arbitrary file formats, cell barcodes and gene IDs follow no standard, and batch information is rarely recorded. A single dataset can take hours of manual curation.

**Scrut-Cell automates this.** Give it a GSE accession, get back a clean AnnData object with standardized metadata, aligned barcodes, unified gene symbols, and identified batches — in minutes. And because it uses LLM-based reasoning with a persistent knowledge graph, it gets smarter with every dataset it processes.

---

## Quick Start

```bash
# Install
git clone https://github.com/zhaoshuh/scruti-cell
cd scruticell
pip install -e ".[dev]"

# Set API key
export DEEPSEEK_API_KEY="sk-xxx"

# One-command pipeline (recommended)
scruticell run GSE334035

# Or step-by-step for inspection
scruticell inspect GSE334035
scruticell plan GSE334035
scruticell execute GSE334035

# Rule pipeline (no LLM, fixed heuristics)
scruticell parse GSE334035 -o ./output
```

---

## Architecture

Scrut-Cell has two processing tiers:

### Layer 1 — Rule Pipeline (`parse`)

A 10-node LangGraph DAG with fixed heuristics. No LLM required.

```
download → parse_soft → [literature] → discover_matrix → read_matrix
                                              ↓
          align_cells → unify_genes → validate → output
                                              ↓
                        [confidence < 85%] → reflexion → retry
```

| Node | What it does |
|------|-------------|
| `download` | Fetch SOFT file + supplementary data from NCBI GEO |
| `parse_soft` | Extract GSE/GSM metadata and characteristics |
| `literature` | (parallel, optional) Search PubMed for experimental context |
| `discover_matrix` | Locate expression matrix (priority: h5ad > mtx > csv > rds > loom) |
| `read_matrix` | Read matrix, auto-detect orientation, transpose if needed |
| `align_cells` | Normalize cell barcode format (10x v2/v3, Drop-seq, etc.) |
| `unify_genes` | Map all gene IDs to standard symbols (symbol/ensembl/entrez) |
| `validate` | 4-dimension confidence check (barcodes, genes, metadata, batches) |
| `reflexion` | Three-layer self-correction: KG lookup → LLM diagnosis → MCTS search |
| `output` | Write `.h5ad`, audit log, and provenance |

**Reflexion strategy**: when confidence drops below 85%, the pipeline searches the knowledge graph for similar failure cases, queries the LLM for a repair strategy, falls back to heuristic rules, and as a last resort runs UCB1 tree search across 12 strategy templates.

### Layer 2 — Intelligent Pipeline (`inspect → plan → execute`)

Designed for complex datasets: nested archives, compound metadata values, non-standard matrix formats, multi-sample studies.

```
M1: inspect (Reporter)        M2: plan (PlanOrchestrator)       M3: execute (PlanExecutor)
     ↓                              ↓                                 ↓
  download + decompress        fingerprint → KB match           execute processing plan
  file inspection (6 types)    LLM generates code               sandboxed code execution
  GseReport JSON               ProcessingPlan JSON              one .h5ad per batch
                                                                reflection on failure (2 retries)
```

**M1** — Recursively decompresses archives, inspects all files (h5ad, mtx, csv, rds, loom, etc.), builds a structured fingerprint report.

**M2** — Matches dataset fingerprint against the knowledge graph, then uses LLM to generate a processing plan with field mappings, batch assignments, and executable Python code.

**M3** — Executes the plan step by step in a sandbox (restricted namespace: `pd`, `np`, `re`, `scanpy` only). On failure, reflects and revises up to 2 times.

### Layer 3 — Knowledge Persistence

| Backend | License | Storage |
|---------|---------|---------|
| `LocalKnowledgeBase` (SQLite) | Open-source (default) | `output/knowledge.db` |
| `KnowledgeGraph` (Neo4j) | Enterprise (`SCRUTICELL_LICENSE`) | Neo4j 5.x |

Strategies accumulate: every successful processing plan is stored by fingerprint. Subsequent datasets with matching structures are processed instantly without LLM calls.

---

## CLI Reference

| Command | Description |
|---------|-------------|
| `scruticell run <GSE>` | **One-command pipeline**: inspect → plan → execute |
| `scruticell parse <GSE>` | Rule pipeline: one-shot GSE → AnnData (fixed heuristics) |
| `scruticell inspect <GSE>` | M1: Download + inspect, generate structured report |
| `scruticell plan <GSE>` | M2: KB match + LLM plan generation |
| `scruticell execute <GSE>` | M3: Execute plan → AnnData files |
| `scruticell config` | Show current configuration |
| `scruticell db-clear` | Clear stored strategies from knowledge base |
| `scruticell --version` | Print version |

### `run` options

```
-o, --output PATH        Output directory (default: output/<GSE>/)
--llm-model TEXT         LLM model
--llm-api-key TEXT       API key
--no-download            Skip download, use cache
--no-llm                 Skip LLM, KB matching only
--keep-data              Keep downloaded files and .h5ad
-v, --verbose            Verbose output
```

### `parse` options

```
-o, --output PATH        Output directory (default: cwd)
--llm-model TEXT         LLM model (default: deepseek-chat)
--llm-api-key TEXT       API key (env: DEEPSEEK_API_KEY or OPENAI_API_KEY)
--llm-base-url TEXT      API base URL
--no-literature          Skip PubMed search
--max-retries INT        Max reflexion retries (default: 3)
-v, --verbose            Verbose output
--keep-data              Keep downloaded files and output
```

### `inspect` options

```
-o, --output PATH        Output path (default: output/<GSE>/inspect.json)
--no-download            Skip download, use existing cache
```

### `plan` options

```
-o, --output PATH        Output path (default: output/<GSE>/plan.json)
--report PATH            Use existing inspection report
--llm-model TEXT         LLM model
--no-llm                 Skip LLM, show KB matches only
```

### `execute` options

```
-o, --output PATH        Output directory (default: output/<GSE>/)
--plan-file PATH         Processing plan JSON (default: auto-detect)
--report-file PATH       Inspection report JSON (default: auto-detect)
--no-download            Skip download
--reflect / --no-reflect Enable/disable reflection (default: on)
--keep-data              Keep downloaded files and .h5ad output
```

---

## Output Structure

```
output/<GSE_ID>/
├── download/                              # Cached raw GEO files
├── inspect.json                           # M1: File fingerprints + metadata summary
├── plan.json                              # M2: Matrix mapping + LLM processing code
├── execute.json                           # M3: Provenance + trace + stats
├── metadata.md                            # Per-batch metadata summary table
├── <GSE>_<batch>_scruticell.h5ad          # One AnnData per batch
└── ...
```

---

## Supported Formats

| Format | Extension | Reader |
|--------|-----------|--------|
| AnnData | `.h5ad`, `.h5` | `readers/h5ad.py` |
| 10X MTX | `.mtx`, `.mtx.gz` (+ barcodes + features) | `readers/mtx.py` |
| CSV / TSV | `.csv`, `.tsv`, `.csv.gz`, `.tsv.gz` | `readers/csv_matrix.py` |
| R Seurat / SCE | `.rds`, `.RData` | `readers/rds.py` |
| Loom | `.loom` | `readers/loom.py` |

Every reader returns a uniform structure: `{"matrix", "cell_barcodes", "gene_names", "shape", "is_sparse"}`.

---

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `DEEPSEEK_API_KEY` | — | LLM API key (preferred) |
| `OPENAI_API_KEY` | — | Alternative LLM key |
| `LLM_MODEL` | `deepseek-chat` | Model name |
| `LLM_BASE_URL` | `https://api.deepseek.com` | API endpoint |
| `LLM_TEMPERATURE` | `0.0` | Deterministic output |
| `SCRUTICELL_DATA_DIR` | `output` | Download cache + KB directory |
| `CONFIDENCE_THRESHOLD_AUTO` | `0.85` | Auto-accept threshold |
| `CONFIDENCE_THRESHOLD_WARN` | `0.60` | Trigger reflexion below |
| `SCRUTICELL_LICENSE` | — | Enterprise license key |

Run `scruticell config` to see all current values.

---

## Field Synonyms

Scrut-Cell ships with a 79-line synonym map (`config/field_synonyms.json`) that normalizes the chaotic field naming conventions found in GEO:

```
disease / disease_state / condition / diagnosis / phenotype → disease
tissue / tissue_type / organ / biopsy_site / body_site → tissue
sex / Sex / gender / gender_identity → sex
cell_type / celltype / cell_population → cell_type
donor_id / patient_id / subject_id / individual_id → donor_id
... 77 mappings total across 5 categories
```

---

## Technical Stack

| Layer | Technologies |
|-------|-------------|
| Language | Python 3.10+ |
| Workflow | LangGraph (DAG) |
| LLM | LangChain + OpenAI-compatible API |
| Single-cell | AnnData + Scanpy |
| GEO parsing | GEOparse |
| Numeric | NumPy + SciPy + Pandas |
| Graph DB | Neo4j 5.x (Python driver) |
| R interop | pyreadr |
| Loom | loompy |
| PDF | PyMuPDF (fitz) |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) |
| CLI | Click + Rich |
| Validation | Pydantic |
| Bio | bionty + biopython |
| Testing | pytest + pytest-asyncio + pytest-cov |
| Lint | ruff + mypy |
| Build | hatchling |

---

## Tests

```bash
pytest tests/ -v --cov=scruticell
```

14 test files covering: barcodes, genes, field synonyms, metadata parsing, SOFT parsing, matrix readers, alignment validation, literature extraction, PDF RAG agent, MCTS search, pipeline integration, reflexion, and edge cases.

---

## Development Status

- **Version**: v0.1.0 (Alpha)
- **Stage**: Technical validation
- **Validated**: 1 dataset (expanding to 20 for baseline)

---

## License

**Open-source**: MIT License — Layer 1 rule pipeline + SQLite KB.

**Enterprise**: Subscription — Layer 2 intelligent pipeline + Neo4j KB + MCTS engine. Activated via `SCRUTICELL_LICENSE` environment variable.
