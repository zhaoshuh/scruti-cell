# Plan: Omics Preprocessing — deepagents + Evidence-Gathering

## 确认的设计决策

| 决策 | 结论 |
|------|------|
| 模态分类引擎 | LLM 自主判断 + 证据支撑，不用规则权重 |
| 架构基础 | `deepagents` (LangChain agent harness) — sub-agents, skills, filesystem, memory |
| KB 含义 | **证据搜集**：PubMed 搜索 → 下载论文 PDF → RAG 读方法章节 → 证据报告。不含浏览器/web搜索 |
| 样本划分 | GSM 为主维度，LLM 可覆盖 (合并/拆分/shared) |
| 输出结构 | `group_{label}/scrna_seq/` 平级目录 |
| 文件链接 | 硬链接 + 回退拷贝 |
| 实施顺序 | File Prep → Classifier → Evidence → 优化/CLI |

---

## 12 种模态标签

| 标签 | 中文 | 典型检测信号 |
|------|------|-------------|
| `scrna_seq` | 单细胞转录组 | molecule=RNA, strategy=RNA-Seq, suspension=cell |
| `snrna_seq` | 单核转录组 | nucleus suspension, snRNA-seq |
| `scatac_seq` | 单细胞 ATAC | strategy=ATAC-seq, peaks/fragments, chr:start-end features |
| `multiome` | 多组学联合 | 同时有 RNA + ATAC (10x Multiome) |
| `spatial_transcriptomics` | 空间转录组 | Visium/Slide-seq/MERFISH, tissue_positions.csv |
| `dna_methylation` | DNA 甲基化 | Bisulfite-Seq/snmC-seq, cgXXXXXX features |
| `cite_seq` | CITE-seq 蛋白 | ADT/antibody/hashtag 文件名 |
| `tcr_seq` | TCR 免疫组库 | contig/clonotype 文件 |
| `bcr_seq` | BCR 免疫组库 | BCR/immunoglobulin |
| `perturb_seq` | CRISPR 筛选 | Perturb-seq, sgRNA |
| `proteomics` | 蛋白质组学 | molecule=protein |
| `other` | 未知 | 兜底 |

---

## 模块结构

```
src/scruticell/preprocessing/
├── __init__.py              # API: preprocess_gse(gse_id, output_dir, use_llm)
├── models.py                # Pydantic 数据模型
├── agent.py                 # create_deep_agent 主编排器
│
├── tools/                    # Agent 可调用的工具
│   ├── __init__.py
│   ├── download_tool.py     # 复用 pipeline/nodes/download.py
│   ├── decompress_tool.py   # 复用 intelligence/decompressor.py
│   ├── inspect_tool.py      # 复用 intelligence/inspector.py
│   └── organize_tool.py     # 输出目录构建（硬链接）
│
├── subagents/
│   ├── __init__.py
│   ├── file_prep.py         # CompiledSubAgent: 文件准备
│   ├── evidence.py          # CompiledSubAgent: 证据搜集
│   └── classifier.py        # CompiledSubAgent: 分类决策
│
├── skills/
│   └── modality_knowledge/
│       └── SKILL.md         # 12种模态定义 + 识别特征手册
│
├── context/                  # 信号提取（"观察笔记"）
│   ├── __init__.py
│   ├── metadata.py          # GSM metadata → 结构化文本
│   ├── filenames.py         # 文件名 → 关键词/结构检测
│   └── contents.py          # 文件内容 → 特征ID类型/barcode/列模式
│
├── evidence/                 # 证据搜集（复用现有 literature 模块）
│   ├── __init__.py
│   ├── pubmed.py            # 复用 literature/pubmed.py
│   └── pdf_rag.py           # 复用 literature/pdf_agent.py
│
├── memory/                   # 历史分类结果缓存
│   └── modality_cache.py    # SQLite: 指纹→分类结果，加速将来复用
│
└── prompts.py               # 所有 LLM prompt 模板
```

---

## 主 Agent 执行流程

```
Main Orchestrator Agent (create_deep_agent)

1. write_todos: 分解任务为 6 步
2. 调用 download_tool → 下载 SOFT + supplementary
3. 调用 decompress_tool → 递归解压
4. 派发 File Prep SubAgent:
     输入: download_dir + tree
     内部: Inspector.inspect_file 对每个文件提取指纹
          context/ 提取 metadata/filename/content 信号
     输出: FileContextReport {
         gse_info, gsm_entries, file_summaries,
         per_file_signals: [{metadata_signal, filename_signal, content_signal}]
     }
5. Main Agent 审阅，判断置信度:
     a. 如果高置信 (所有信号一致) → 跳到步骤 7
     b. 如果低置信 (信号矛盾) → 步骤 6
6. 派发 Evidence Gathering SubAgent (仅在不确定时):
     输入: GSE_ID, PMID (来自 SOFT), 不确定的样本组
     内部:
       - PubMed 搜索相关论文 → 获取 PMID
       - 下载 PMC 开放获取 PDF (httpx + 已有机密 headers)
       - PyMuPDF 提取文本
       - sentence-transformers 分块嵌入 + RAG 检索方法章节
       - LLM 基于论文内容回答: "这个 GSE 做了哪些实验/用了哪些技术"
     输出: EvidenceReport {
         source: "PubMed PDF",
         pmids: ["37963925"],
         findings: "GSE215353 使用 snmC-seq2 (甲基化) + 配对的 snRNA-seq"
     }
7. 派发 Classifier SubAgent:
     输入: FileContextReport + EvidenceReport (如有)
     输出: ClassificationResult {
         sample_groups: [
             {label, gsm_ids, note, modalities: {modality: {confidence, files[]}}}
         ],
         unclassified: []
     }
8. 调用 organize_tool → 按分类结果构建输出目录
9. 保存分类结果到 memory/modality_cache (SQLite)
```

---

## 各 SubAgent 定义

### File Prep SubAgent (`subagents/file_prep.py`)

```python
file_prep_subagent = CompiledSubAgent(
    name="file-prep",
    description="分析 GSE 下载目录中的所有文件，提取指纹、元数据、文件信号",
    system_prompt="""
    你是一个 GEO 数据文件分析专家。
    1. 对每个文件调用 inspect_file 获取指纹
    2. 调用 extract_metadata_signals 获取 GSM 元数据信号
    3. 调用 extract_filename_signals 获取文件名信号  
    4. 调用 extract_content_signals 获取文件内容信号
    5. 汇总所有信号为 FileContextReport 返回
    """,
    tools=[inspect_file, extract_metadata_signals, extract_filename_signals, extract_content_signals],
)
```

### Evidence Gathering SubAgent (`subagents/evidence.py`)

```python
evidence_subagent = CompiledSubAgent(
    name="evidence-gathering",
    description="通过搜索 PubMed 并阅读论文来确定 GSE 的真实实验设计",
    system_prompt="""
    你是一个科学文献调研专家。当模态分类存在不确定性时，你需要从论文原文中找到证据。
    1. 调用 search_pubmed 搜索相关论文
    2. 对找到的 PMID，调用 fetch_pubmed_record 获取元数据（检查是否开放获取）
    3. 如果论文在 PMC 上开放获取，调用 download_and_rag 下载 PDF 并检索方法章节
    4. 基于论文内容生成 EvidenceReport，需引用原文段落
    """,
    tools=[search_pubmed, fetch_pubmed_record, download_and_rag],
)
```

### Classifier SubAgent (`subagents/classifier.py`)

```python
classifier_subagent = CompiledSubAgent(
    name="omics-classifier",
    description="综合文件上下文和论文证据，确定每个样本组的模态分类",
    system_prompt="""
    你是一个单细胞多组学数据分类专家。
    输入包含:
      - FileContextReport (文件结构、元数据、信号)
      - EvidenceReport (论文证据，如有)
    
    你需要:
    1. 确定样本分组 (normally one GSM = one group, but adjust as needed)
    2. 为每组确定所有模态 (注意 multiome/CITE-seq 等含多模态)
    3. 将每个文件分配到正确的模态和角色 (matrix/barcodes/features/peaks/metadata)
    4. 对于无法确定的情况，标记 confidence < 0.5 并说明原因
    
    输出严格遵循 ClassificationResult JSON schema：
    {
      "sample_groups": [
        {
          "label": "GSM1234567",
          "gsm_ids": ["GSM1234567"],
          "note": "正常组",
          "modalities": {
            "scrna_seq": {
              "confidence": 0.95,
              "files": [
                {"path": "data/matrix.mtx.gz", "role": "matrix"},
                {"path": "data/barcodes.tsv.gz", "role": "barcodes"},
                {"path": "data/features.tsv.gz", "role": "features"}
              ]
            }
          }
        }
      ],
      "unclassified": []
    }
    """,
)
```

---

## Skills: modality_knowledge/SKILL.md

```markdown
# Modality Knowledge

## 12 Supported Modalities

### scrna_seq (Single-Cell RNA-seq)
- Metadata signals: molecule="total RNA"|"polyA RNA", library_strategy="RNA-Seq"
- File patterns: barcodes.tsv.gz + features.tsv.gz + matrix.mtx.gz (10x), *.h5ad
- Content signals: features are gene symbols (e.g. BRCA1) or ENSG IDs (ENSGXXXXXXXXXXX)
- Known platforms: 10x Chromium v2/v3, Smart-seq2, Drop-seq, CEL-seq2, inDrop, Seq-Well

### snrna_seq (Single-Nucleus RNA-seq)  
- Like scrna_seq but suspension_type="nucleus"
- Title/description often contains "single-nucleus" or "snRNA"

### scatac_seq (Single-Cell ATAC-seq)
- Metadata: library_strategy="ATAC-seq", molecule="genomic DNA"
- File patterns: peaks.bed, fragments.tsv.gz, *.h5 (snapATAC/signac)
- Content: features are genomic intervals (chr1:1000-2000)

### multiome (10x Multiome RNA+ATAC)
- Simultaneous RNA-seq and ATAC-seq from same cells
- File structure: separate "rna/" and "atac/" subdirectories, or per-GSM pairs
- Common in recent (2023+) datasets

### spatial_transcriptomics (Visium/Slide-seq/MERFISH)
- Metadata: often library_strategy="OTHER", title contains "Visium"|"Slide-seq"|"MERFISH"|"spatial"
- File patterns: tissue_positions_list.csv, spatial/ subdirectory, *.h5ad with spatial key
- 10x Visium: filtered_feature_bc_matrix/ under each spatial sample

### dna_methylation (scBS-seq/snmC-seq/WGBS)
- Metadata: library_strategy="Bisulfite-Seq"|"MethylC-seq"|"snmC-seq"
- Content: feature IDs are CpG sites (cgXXXXXXX), values are methylation ratios (0-1)
- Files: *.cov.gz, *.bedGraph methylation calls

### cite_seq (CITE-seq Antibody-Based Protein)
- Metadata: title/description contains "CITE-seq"|"TotalSeq"|"ADT"|"antibody"
- File patterns: ADT_*.csv|tsv, antibody_barcodes.csv, hashtag/HTO files
- Content: features are short protein names (CD3, CD4, CD8...) not gene symbols
- Note: always co-occurs with scrna_seq (antibody + transcriptome)

### tcr_seq (TCR Repertoire)
- Metadata: title contains "TCR"|"T-cell receptor", library_strategy usually "OTHER"
- File patterns: filtered_contig_annotations.csv, clonotypes.csv, vdj_*.csv
- Platforms: 10x V(D)J, SMARTer TCR

### bcr_seq (BCR Repertoire)
- Like tcr_seq but title contains "BCR"|"B-cell receptor"|"immunoglobulin"
- File patterns: filtered_contig_annotations.csv, clonotypes.csv

### perturb_seq (CRISPR Screen)
- Metadata: title contains "Perturb-seq"|"CRISPR"|"CROP-seq"
- File patterns: guide_assignment.csv, sgRNA_info.csv, protospacer_calls.csv
- Content: features include sgRNA names

### proteomics (Mass-Spec / Protein Arrays)
- Metadata: molecule="protein"|"peptide", instrument often "LC-MS/MS"
- File patterns: protein_groups.csv, peptide_groups.csv, protein abundance matrices
- Does NOT require single-cell resolution

### other
- Catch-all for uncategorizable data
- Only used when evidence gathering also fails
```

---

## 数据模型 (`models.py`)

```python
from pydantic import BaseModel
from pathlib import Path

class PreprocessingResult(BaseModel):
    gse_id: str
    output_dir: Path
    sample_groups: list["SampleGroup"]
    unclassified: list["UnclassifiedFile"]
    evidence: "EvidenceReport | None"
    llm_call_count: int
    cache_hit: bool
    errors: list[str]

class SampleGroup(BaseModel):
    label: str
    gsm_ids: list[str]
    note: str
    modalities: dict[str, "ModalityGroup"]

class ModalityGroup(BaseModel):
    modality: str
    confidence: float
    files: list["FileEntry"]

class FileEntry(BaseModel):
    source_path: str
    ancestry: str
    role: str  # matrix/barcodes/features/peaks/metadata/other
    signals: dict  # context signals extracted

class FileContextReport(BaseModel):
    gse_id: str
    gse_info: dict
    gsm_entries: list[dict]
    file_summaries: dict[str, dict]  # ancestry → FileSummary
    per_file_signals: list[dict]

class EvidenceReport(BaseModel):
    source: str  # "pubmed_pdf" | "pubmed_abstract" | "none"
    pmids: list[str]
    findings: str
    excerpts: list[str]  # 原文引用
    confidence_impact: str  # 证据对分类置信度的影响说明

class ClassificationResult(BaseModel):
    sample_groups: list[dict]  # matches SampleGroup shape
    unclassified: list[dict]
    reasoning: str

class UnclassifiedFile(BaseModel):
    source_path: str
    reason: str
```

---

## 实施顺序

### Phase 1: 基础 (先能跑)

| 步骤 | 文件 | 内容 |
|------|------|------|
| 1.1 | `models.py` | 所有 Pydantic 数据模型 |
| 1.2 | `context/metadata.py` | GSM 元数据提取为文本信号 |
| 1.3 | `context/filenames.py` | 文件名关键词检测 + 配套文件关系 |
| 1.4 | `context/contents.py` | 文件内容特征提取 (ID类型/barcode/列模式) |
| 1.5 | `tools/download_tool.py` | 复用 download_node |
| 1.6 | `tools/decompress_tool.py` | 复用 Decompressor |
| 1.7 | `tools/inspect_tool.py` | 复用 Inspector |

### Phase 2: Agent 骨架

| 步骤 | 文件 | 内容 |
|------|------|------|
| 2.1 | `prompts.py` | 所有 prompt 模板 |
| 2.2 | `subagents/file_prep.py` | File Prep 子代理 (工具 + prompt) |
| 2.3 | `subagents/classifier.py` | Classifier 子代理 (工具 + prompt) |
| 2.4 | `agent.py` | Main Agent 编排 (仅含 File Prep + Classifier) |
| 2.5 | `tools/organize_tool.py` | 输出目录构建 |
| 2.6 | `__init__.py` | `preprocess_gse()` 公开 API |

### Phase 3: 证据搜集

| 步骤 | 文件 | 内容 |
|------|------|------|
| 3.1 | `evidence/pubmed.py` | 抽象复用 literature/pubmed.py |
| 3.2 | `evidence/pdf_rag.py` | 抽象复用 literature/pdf_agent.py |
| 3.3 | `subagents/evidence.py` | Evidence Gathering 子代理 |
| 3.4 | `agent.py` | 集成 Evidence 到 Main Agent 流程 |

### Phase 4: 优化 + CLI

| 步骤 | 文件 | 内容 |
|------|------|------|
| 4.1 | `memory/modality_cache.py` | SQLite 指纹→分类结果缓存 |
| 4.2 | `skills/modality_knowledge/SKILL.md` | 模态知识技能文件 |
| 4.3 | `cli.py` | `scruticell preprocess <GSE>` 命令 |
| 4.4 | `pyproject.toml` | 添加 `deepagents` 依赖 |
