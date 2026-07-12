# Scrut-Cell

> Automated multi-omics preprocessing from public single-cell, spatial, and epigenomic datasets — from NCBI GSE accession to analysis-ready AnnData in minutes.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--alpha-orange)](https://github.com/zhaoshuh/scruti-cell)

---

## What is Scrut-Cell?

GEO hosts thousands of datasets spanning scRNA-seq, snRNA-seq, scATAC-seq, spatial transcriptomics, DNA methylation,
CITE-seq, TCR/BCR repertoire, and CRISPR perturbation screens. Each modality has completely different file formats,
metadata conventions, and preprocessing requirements. A multi-omics integration — combining RNA with ATAC, or spatial
with methylation — can take **days of manual curation per dataset**.

**Scrut-Cell automates this.** Give it a GSE accession, it automatically:
1. Identifies all **sample groups** (GSM-level separation with 1:1 file-to-sample mapping)
2. Detects and classifies **12 omics modalities** per sample (skill-based, extensible)
3. Separates files into an organized directory tree by sample × modality
4. Handles shared files (hard-linked across groups) and demux files
5. No downstream analysis — output is ready for any tool you choose

Powered by an LLM Agent (deepagents) with skill-based modality knowledge and evidence gathering from PubMed papers.

---

## How Preprocessing Works

```
CLI: scruticell preprocess GSE12345
              │
              ▼
┌────────────────────────────────────────────────┐
│  Main Orchestrator Agent (agent.py)            │
│  Model: deepseek-chat (temp=0.0)               │
│                                                │
│  Delegates via `task` tool:                    │
│    1. file-prep  (download + decompress +      │
│                   inspect → FileContextReport) │
│    2. evidence   (PubMed search → paper RAG)   │
│       [optional, only on signal conflict]      │
│    3. classifier (sample × modality → JSON)    │
│    4. organize_output tool (build directory)   │
└────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────┐
│  1. File Prep Sub-Agent (file_prep.py)         │
│  Tools: download_gse, decompress_files,        │
│         inspect_files                          │
│                                                │
│  Produces compact FileContextReport:           │
│    • field_value_maps      metadata 去重映射    │
│    • file_signal_groups    按信号指纹分组        │
│      └ per_file_gsm_id    ancestry→GSM 提取     │
│      └ attribution         L0(ancestry)+L1(SOFT)│
│      └ content_preview     实际数据样本          │
│      └ file_meta           深度文件元数据        │
│    • file_summaries        原始文件指纹          │
└────────────────────────────────────────────────┘
              │ (only if metadata signals conflict)
              ▼
┌────────────────────────────────────────────────┐
│  2. Evidence Sub-Agent (evidence.py)           │
│  Tools: search_gse_papers, read_paper_methods  │
│                                                │
│  Searches PubMed → reads abstracts             │
│  Downloads PDF → RAG on Methods section        │
│  Returns EvidenceReport (trusted over metadata)│
└────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────┐
│  3. Classifier Sub-Agent (classifier.py)       │
│  Zero tools — pure LLM reasoning               │
│  Prompt runtime-injected from SKILL.md         │
│                                                │
│  Classification rules:                         │
│    A. File→Sample (1:1 constraint)             │
│       per_file_gsm_id > candidates > signals   │
│       → infer composite key if needed           │
│    B. Shared files detection                   │
│       Same file used by multiple GSM           │
│    C. Demux files detection                    │
│       One file = multiple samples (column/row)  │
│    D. Sample grouping (field_value_maps)       │
│    E. Modality detection (SKILL.md + preview)  │
│                                                │
│  Output: Compact JSON with                     │
│    • sample_groups   (GSM-去重, 含 files)       │
│    • shared_file_groups                        │
│    • unclassified                              │
│    • reasoning                                  │
└────────────────────────────────────────────────┘
              │
              ▼
┌────────────────────────────────────────────────┐
│  4. organize_output Tool                       │
│  Builds: output/<GSE>/preprocessed/             │
│    <sample_dir>/                                 │
│      {modality}/                                │
│        files.json + hard links                  │
│        (shared files linked to all dirs)        │
│    manifest.json (logical groups metadata)      │
└────────────────────────────────────────────────┘
```

### Key Design Principles

| Principle | Description |
|-----------|-------------|
| **Skill-based modalities** | Modality definitions live in `skills/modality_knowledge/SKILL.md`. Adding a new modality requires editing only this file. No Python code changes needed. |
| **1:1 file-to-sample mapping** | Every file belongs to exactly one sample key. Key ↔ sample group is a bijection. Priority: ancestry path → SOFT ref → token signals → inferred composite key. |
| **Signal collection, not decision** | Python code in `attribution.py` only collects L0 (ancestry path) + L1 (SOFT references) signals. All judgment (token matching, shared/demux detection, key inference) is deferred to the LLM classifier via prompt. |
| **Cache by content fingerprint** | SQLite cache (`modality_cache.py`) stores classification decisions keyed by MD5 fingerprint of `gse_id + field_values + signal_patterns`. Identical datasets skip the LLM call entirely. |

---

## Supported Modalities

Defined in `skills/modality_knowledge/SKILL.md` (single source of truth — easily extensible):

| Category | Modality | Example Technologies |
|----------|----------|---------------------|
| Transcriptomics | scRNA-seq, snRNA-seq | 10x Chromium v2/v3, Smart-seq2, Drop-seq, CEL-seq2 |
| Epigenomics | scATAC-seq, DNA Methylation | 10x ATAC, snATAC-seq, Bisulfite-Seq, snmC-seq2 |
| Spatial | Spatial Transcriptomics | Visium, Slide-seq, MERFISH, Stereo-seq |
| Proteomics | CITE-seq (antibody-based protein) | TotalSeq, ADT |
| Immune repertoire | TCR-seq, BCR-seq | 10x V(D)J |
| Functional genomics | CRISPR Perturb-seq | Perturb-seq, CROP-seq, sci-Plex |
| Multi-omics | 10x Multiome (RNA + ATAC) | Simultaneous RNA + ATAC from same cells |

---

## Quick Start

```bash
# Install
git clone https://github.com/zhaoshuh/scruti-cell
cd scruticell
pip install -e ".[dev]"

# Set API key
export DEEPSEEK_API_KEY="sk-xxx"

# Preprocess: separate by sample and modality (no downstream analysis)
scruticell preprocess GSE215353

# One-command full pipeline: preprocess → curate → export AnnData
scruticell run GSE334035

# Rule pipeline (no LLM, fixed heuristics)
scruticell parse GSE334035 -o ./output
```

---

## Architecture

Scrut-Cell has three processing tiers:

### Layer 0 — Omics Preprocessing (`preprocess`)

An LLM Agent (deepagents) that downloads, decompresses, inspects, and separates GEO data by sample and modality.
No downstream analysis — pure data organization.

**Signal collection (Python, deterministic):**
- **L0**: Extract GSM IDs from ancestry paths (`GSM\d+` regex)
- **L1**: Match filenames against SOFT `supplementary_file_N` references
- **L3**: Group files by signal fingerprint (`file_type|feature_id_type|barcode_format|keywords`)
- **L5**: Filter noise files (`.pdf`, `.docx`, `.png`, etc.)

**All judgment deferred to LLM (via classifier prompt):**
- File→sample ownership (1:1, trust ancestry > SOFT > tokens > inferred key)
- Shared file detection (same file used by multiple GSM)
- Demux file detection (merged files requiring column/row/pre-per_sample_key splitting)
- Sample group formation (from `field_value_maps`)
- Modality classification (from `SKILL.md` + `content_preview`)

**Output:**
```
output/<GSE_ID>/preprocessed/
├── manifest.json
├── <gsm_id>/                        # Per-sample directory (GSM ID or inferred key)
│   ├── metadata.json
│   └── <modality>/
│       ├── files.json
│       └── ... (hard links)
├── unclassified/
└── modality_cache.db
```

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
| `scruticell preprocess <GSE>` | Download + classify + separate by sample × modality (skill-based) |
| `scruticell run <GSE>` | **One-command pipeline**: inspect → plan → execute |
| `scruticell parse <GSE>` | Rule pipeline: one-shot GSE → AnnData (fixed heuristics) |
| `scruticell inspect <GSE>` | M1: Download + inspect, generate structured report |
| `scruticell plan <GSE>` | M2: KB match + LLM plan generation |
| `scruticell execute <GSE>` | M3: Execute plan → AnnData files |
| `scruticell config` | Show current configuration |
| `scruticell db-clear` | Clear stored strategies from knowledge base |
| `scruticell --version` | Print version |

---

## Output Structure

```
output/<GSE_ID>/
├── preprocessed/                  # Layer 0: modality-separated files
│   ├── manifest.json
│   └── <sample_dir>/               # Per-sample (GSM ID or inferred key)
│       ├── metadata.json
│       └── <modality>/
│           ├── files.json
│           └── ... (hard links)
├── inspect.json                   # M1: File fingerprints + metadata summary
├── plan.json                      # M2: Matrix mapping + LLM processing code
├── execute.json                   # M3: Provenance + trace + stats
├── metadata.md                    # Per-batch metadata summary table
├── <GSE>_<batch>_scruticell.h5ad  # One AnnData per batch
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

## Technical Stack

| Layer | Technologies |
|-------|-------------|
| Language | Python 3.11+ |
| Agent harness | deepagents (sub-agents, filesystem, skills, memory) |
| Workflow | LangGraph (DAG) |
| LLM | LangChain + OpenAI-compatible API |
| Single-cell / Multi-omics | AnnData + Scanpy |
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

## Development Status

- **Version**: v0.1.0 (Alpha)
- **Stage**: Multi-omics preprocessing (skill-based) + intelligent pipeline
- **Modalities**: 12 (skill-based, extensible via `skills/modality_knowledge/SKILL.md`)
- **Test Suite**: 195 tests, all passing

---

## License

**Open-source**: MIT License — Layer 0 preprocessing + Layer 1 rule pipeline + SQLite KB.

**Enterprise**: Subscription — Layer 2 intelligent pipeline + Neo4j KB + MCTS engine. Activated via `SCRUTICELL_LICENSE` environment variable.
