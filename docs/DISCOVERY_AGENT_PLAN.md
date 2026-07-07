# Scrut-Cell Discovery Agent — 科学发现平台设计文档

## 概述

将 Scrut-Cell 从"单数据集 ETL 工具"升级为 **LLM 驱动的科学发现平台**。用户提出一个科学问题（如"阿尔兹海默症的细胞类型异质性"），系统自动搜索 GEO、筛选相关数据集、标准化处理、跨数据集元分析、生成可验证的科学假设。

## 系统架构

```
┌─────────────────────────────────────────────────┐
│  Layer 1: Discovery Agent                        │
│  搜索 + SOFT 摘要分析 + GSE 筛选排序              │
│  输入: 科学问题（自然语言）                       │
│  输出: 100-300 候选 GSE（按相关性/质量排序）      │
└────────────────────┬────────────────────────────┘
                     │ 候选 GSE 列表
┌────────────────────▼────────────────────────────┐
│  Layer 2: Scrut-Cell Pipeline (已有)             │
│  下载 → 解析 → 元数据标准化                       │
│  输入: GSE 列表                                  │
│  输出: .h5ad + metadata.md (per GSE)             │
└────────────────────┬────────────────────────────┘
                     │ metadata.md × N
┌────────────────────▼────────────────────────────┐
│  Layer 3: Hypothesis Generator                   │
│  LLM 批量读 metadata.md → 跨数据集对比分析        │
│  输入: metadata.md 文件列表                      │
│  输出: 5-20 个可验证的科学假设                    │
└────────────────────┬────────────────────────────┘
                     │ 假设 × N
┌────────────────────▼────────────────────────────┐
│  Layer 4: Synthesis Engine                       │
│  对每个假设找支持/反对的数据集 + 统计验证          │
│  输入: 假设 + 全部 .h5ad 文件                    │
│  输出: 论文级报告（Markdown + LaTeX）             │
└────────────────────┬────────────────────────────┘
                     │ 优先验证假设
┌────────────────────▼────────────────────────────┐
│  Layer 5: Experimental Validation                │
│  湿实验验证 (不在代码范围内)                      │
└─────────────────────────────────────────────────┘
```

## Layer 1: Discovery Agent

### 1.1 输入

```
用户输入: "Alzheimer's disease single-cell transcriptomics"
```

### 1.2 搜索策略

**Step A: NCBI Entrez E-utilities 搜索**

```python
# E-utilities esearch
params = {
    "db": "gds",
    "term": '(Alzheimer OR AD) AND ("single cell" OR scRNA OR "single nucleus")',
    "retmax": 2000,
    "retmode": "json",
    "usehistory": "y"
}
# 返回: WebEnv + QueryKey（批量下载用）
#        idlist: [200345678, 200345679, ...]  # GDS IDs
```

**Step B: 获取 GSE ID 映射**

```python
# E-utilities esummary
# GDS ID → GSE ID 映射
# 返回: [{gds_id: 200345678, gse_id: "GSE123456", title: "..."}, ...]
```

**Step C: 批量下载 SOFT 摘要**

仅下载 SOFT 文件的头部元数据（前 10KB），不下载完整文件：

```python
# Range 请求: bytes=0-10240
# 获取: GSE title, summary, overall_design, platform, organism
# 速度: ~1s/GSE（仅 10KB）
```

**Step D: LLM 筛选**

将每个 GSE 的标题 + 摘要 + 实验设计文本发给 LLM 做三级分类：

```
SYSTEM PROMPT:
"Classify each GSE into one of three categories:

A) RELEVANT — single-cell/single-nucleus RNA-seq, 
   human or mouse brain tissue, Alzheimer's or related neurodegeneration

B) UNCERTAIN — relevant but needs more GSM-level detail 
   (e.g., includes some bulk RNA, mixed tissue types)

C) IRRELEVANT — bulk RNA-seq, non-brain tissue, non-Alzheimer

For each entry, output: gse_id, category, confidence (0-1), 
key_metadata (organism, tissue, cell_count_range, platform), 
relevance_notes (why relevant or not)"
```

### 1.3 输出格式

```json
{
  "query": "Alzheimer's disease single-cell transcriptomics",
  "total_found": 1432,
  "screened": 300,
  "relevant": {
    "count": 187,
    "gse_list": [
      {
        "gse_id": "GSE174367",
        "title": "Single-nucleus RNA-seq of human AD prefrontal cortex",
        "organism": "Homo sapiens",
        "tissue": "prefrontal cortex",
        "cell_count_estimate": "50,000-100,000",
        "platform": "10X Genomics",
        "relevance_score": 0.95,
        "relevance_notes": "Directly studies AD human brain snRNA-seq",
        "priority": 1
      }
    ]
  },
  "uncertain": {"count": 43, "gse_list": [...]},
  "irrelevant": {"count": 70, "gse_list": [...]}
}
```

### 1.4 实现文件

| 文件 | 职责 |
|---|---|
| `src/scruticell/discovery/__init__.py` | 模块入口 |
| `src/scruticell/discovery/search.py` | NCBI Entrez 搜索 |
| `src/scruticell/discovery/selector.py` | LLM 筛选 + 排序 |
| `src/scruticell/discovery/urls.py` | NCBI API URL 构建 |

## Layer 2: Scrut-Cell Pipeline（无需修改）

已有能力直接复用：
- `scruticell run GSE123456` — 完整处理流程
- 输出: `output/GSE123456/metadata.md` + `.h5ad` 文件

需要新增的能力：
- `scruticell batch-file gse_list.txt` — 从文本文件批量运行
- 每个 GSE 独立运行，互不影响
- 失败自动跳过，记录到错误日志

## Layer 3: Hypothesis Generator

### 3.1 核心设计

LLM 扮演**资深计算生物学家**角色，系统性地分析所有成功处理的 GSE 的 `metadata.md`。

### 3.2 对话结构

**Round 1: 全局景观**

```
PROMPT:
"You are a senior computational biologist specializing in 
Alzheimer's disease. You have standardized metadata from 
{N} single-cell GEO datasets.

For each dataset, you see:
- gse_id, title, organism, tissue, cell_types
- experimental conditions (genotype, treatment, disease_stage)
- batch structure and defining_fields
- gene_id_system, normalization, total_cells, total_genes
- GSM-level metadata (source_name, characteristics)

STEP 1 — MAP THE LANDSCAPE:
Analyze the complete metadata landscape:

1. CELL TYPE DISTRIBUTION:
   - Which cell types appear across datasets? 
   - Which are unique to specific datasets?
   
2. TISSUE/BRAIN REGION COVERAGE:
   - How many datasets cover each brain region?
   - Are there regions over/under-represented?

3. EXPERIMENTAL CONDITIONS:
   - What conditions/genotypes/treatments are studied?
   - How balanced are the comparisons?

4. TECHNICAL HETEROGENEITY:
   - Platform distribution (10X, Smart-seq2, etc.)
   - Sequencing depth, cell counts per dataset

OUTPUT: JSON with structured landscape analysis"
```

**Round 2: 矛盾发现**

```
PROMPT:
"Based on the landscape analysis, identify CONTRADICTIONS
and GAPS across datasets:

1. WHERE DO DATASETS DISAGREE?
   - If GSE_A reports gene X upregulated in microglia of AD patients
     but GSE_B finds no difference → contradiction
   - Can batch effects, cell type definitions, or analysis methods 
     explain the disagreement?

2. WHERE ARE THE BLIND SPOTS?
   - Are there cell types consistently absent?
   - Are there brain regions never studied?

3. WHAT WOULD A CONSENSUS LOOK LIKE?
   - If all datasets were perfectly harmonized, 
     what conclusions would emerge?

OUTPUT: JSON with contradictions and gaps"
```

**Round 3: 假设生成**

```
PROMPT:
"Generate 5-20 SPECIFIC, TESTABLE HYPOTHESES based on 
the landscape and contradictions.

For EACH hypothesis, provide:

1. STATEMENT: A falsifiable scientific claim
2. SUPPORTING GSEs: Which datasets support this hypothesis?
3. CONTRADICTING GSEs: Which datasets contradict it?
4. STATISTICAL BASIS: What patterns in the metadata support this?
5. PROPOSED VALIDATION: How would you experimentally test this?
6. NOVELTY: Is this finding in existing literature?
7. FEASIBILITY: Can this be tested with existing data?
8. IMPACT: If true, what would this change in AD research?

RANK hypotheses by NOVELTY × FEASIBILITY × IMPACT.

OUTPUT: JSON list of ranked hypotheses"
```

### 3.3 输出格式

```json
{
  "analysis_timestamp": "2026-07-03T12:00:00",
  "total_datasets_analyzed": 156,
  "landscape": {
    "cell_types": {
      "microglia": {"count": 89, "datasets": ["GSE123", ...]},
      "astrocytes": {"count": 76, ...},
      "oligodendrocytes": {"count": 65, ...},
      "neurons_excitatory": {"count": 52, ...},
      "neurons_inhibitory": {"count": 48, ...}
    },
    "brain_regions": {
      "prefrontal_cortex": 45,
      "hippocampus": 32,
      "entorhinal_cortex": 18
    },
    "platforms": {
      "10X_Genomics": 120,
      "Smart_seq2": 18,
      "Drop_seq": 12,
      "other": 6
    }
  },
  "contradictions": [
    {
      "id": "C001",
      "finding": "Microglial activation markers show discordant expression",
      "gene": "TREM2",
      "support_up": ["GSE174367", "GSE168408"],
      "support_unchanged": ["GSE184674", "GSE157827"],
      "possible_explanation": "Disease stage heterogeneity; GSE174367 is late-stage AD"
    }
  ],
  "hypotheses": [
    {
      "rank": 1,
      "hypothesis": "Microglia exhibit region-specific transcriptional heterogeneity in AD that is masked when pooling across brain regions",
      "supporting_gses": ["GSE174367", "GSE168408", "GSE157827", "GSE160936"],
      "contradicting_gses": [],
      "statistical_basis": "4/6 datasets with region metadata show microglia cluster separately by region (ARI > 0.3). Only 1/6 datasets explicitly controlled for region as a covariate",
      "proposed_validation": "Re-analyze GSE174367 and GSE168408 with region as a covariate; use GSE160936 (spatial transcriptomics) to cross-validate region-specific microglial signatures",
      "novelty_score": 0.85,
      "feasibility_score": 0.90,
      "impact_score": 0.80,
      "composite_score": 0.85,
      "literature_check": "No existing meta-analysis covers region-specific microglial heterogeneity in AD"
    }
  ]
}
```

### 3.4 实现文件

| 文件 | 职责 |
|---|---|
| `src/scruticell/discovery/hypothesis.py` | Hypothesis Generator 核心引擎 |
| `src/scruticell/discovery/analyzer.py` | 跨数据集分析（Round 1-3） |
| `src/scruticell/discovery/prompts.py` | LLM prompts 模板 |

## Layer 4: Synthesis Engine

### 4.1 职责

对于 Layer 3 产出的排名靠前的假设，提取统计证据并生成论文级报告。

### 4.2 处理流程

```
For each hypothesis:
  1. Scan supporting GSEs' .h5ad files
     → Extract obs columns matching the hypothesis variables
     → Compute basic statistics (cell counts per condition, etc.)
  
  2. Cross-reference with literature
     → PubMed search for hypothesis keywords
     → Extract relevant prior findings
     → Compare: is this novel?

  3. Generate figures
     → Batch-cell count barplots
     → Metadata coverage heatmaps
     → Dataset similarity dendrograms

  4. Write report
     → Structured paper sections (Abstract, Introduction, Methods, 
       Results, Discussion, Supplementary)
     → LaTeX + Markdown output
     → Reference list with PubMed DOIs
```

### 4.3 输出

```
output/discovery/alzheimer_single_cell/
├── search_results.json          # Layer 1 输出
├── processed/                   # Layer 2 输出（已有格式）
│   ├── GSE174367/metadata.md
│   └── ...
├── analysis.json                # Layer 3 输出
├── figures/
│   ├── cell_type_distribution.png
│   ├── brain_region_coverage.png
│   ├── dataset_similarity.png
│   └── hypothesis_xyz_evidence.png
├── report.md                    # 论文草稿 (Markdown)
├── report.tex                   # 论文草稿 (LaTeX)
└── supplementary/
    ├── table_s1_gse_list.csv
    ├── table_s2_metadata_fields.csv
    └── table_s3_hypotheses.csv
```

### 4.4 实现文件

| 文件 | 职责 |
|---|---|
| `src/scruticell/discovery/synthesis.py` | Synthesis 主控制器 |
| `src/scruticell/discovery/statistics.py` | 跨数据集统计计算 |
| `src/scruticell/discovery/reporter.py` | Markdown/LaTeX 报告生成 |
| `src/scruticell/discovery/figures.py` | matplotlib/seaborn 图表生成 |

## Layer 5: CLI 集成

### 5.1 新增命令

```
scruticell discover alzheimer \
  --organism "Homo sapiens" \
  --min-datasets 50 \
  --max-datasets 300 \
  --output output/discovery/alzheimer/

scruticell batch-process output/discovery/alzheimer/search_results.json \
  --max-parallel 4 \
  --timeout 3600

scruticell hypothesize output/discovery/alzheimer/ \
  --hypothesis-count 20 \
  --output output/discovery/alzheimer/analysis.json

scruticell synthesize output/discovery/alzheimer/ \
  --output-format latex \
  --output output/discovery/alzheimer/report.tex
```

### 5.2 CLI 实现

| 文件 | 职责 |
|---|---|
| `src/scruticell/cli.py` | 新增 4 个子命令 |
| `src/scruticell/cli_discovery.py` | 新建: discovery 命令组 |

## 代码结构

```
src/scruticell/discovery/
├── __init__.py          # 模块入口
├── search.py            # NCBI Entrez 搜索
├── selector.py          # LLM 筛选 + 排序
├── batch.py             # 批量处理控制器
├── hypothesis.py        # Hypothesis Generator
├── analyzer.py          # 跨数据集分析
├── synthesis.py         # Synthesis 主控制
├── statistics.py        # 统计计算
├── reporter.py          # 报告生成
├── figures.py           # 图表生成
├── prompt_builder.py    # LLM prompt 模板
└── urls.py              # NCBI API URL 构建
```

## 实现阶段

### Phase 1: Discovery Agent（2-3 天）

| 任务 | 产出 |
|---|---|
| `search.py` — NCBI Entrez 搜索 | `scruticell discover` |
| `selector.py` — LLM 读 SOFT 摘要 + 三级分类 | 候选 GSE 列表 (JSON) |
| `urls.py` — NCBI API URL 工具 | 辅助模块 |

### Phase 2: 批量处理（1 天）

| 任务 | 产出 |
|---|---|
| `batch.py` — 批量 run 控制器 | `scruticell batch-process` |
| 失败重试 + 进度保存 | 断点续跑 |

### Phase 3: Hypothesis Generator（3-5 天）

| 任务 | 产出 |
|---|---|
| 多轮 LLM 对话引擎 | Round 1-3 |
| 假设格式定义 + 排序 | `analysis.json` |
| 消融实验支持 | 对比: 单 prompt vs 多轮对话 |

### Phase 4: Synthesis（3-5 天）

| 任务 | 产出 |
|---|---|
| `statistics.py` — 跨数据集统计 | 证据提取 |
| `reporter.py` — 论文级报告 | `.md` + `.tex` |
| `figures.py` — 自动图表 | 论文用图 |

### Phase 5: 调优 + 验证（1-2 周）

| 任务 | 产出 |
|---|---|
| 跑真实 AD 数据集 | 100-300 GSE |
| 筛选假设 | 5-10 个主要发现 |
| 补充验证 | FigShare/Zenodo 数据上传 |

## 技术依赖

现有依赖已覆盖大部分：
- `httpx` — NCBI API 请求
- `GEOparse` — SOFT 解析
- `langchain-openai` — LLM 对话
- `scanpy` / `anndata` — 单细胞数据
- `pandas` / `numpy` — 统计计算
- `matplotlib` / `seaborn` — 图表生成

无需新增第三方依赖。

## 评估框架 — 可量化证据体系

### 为什么需要

Nature Communications 的 reviewer 不接受"我们尽力了"——他们接受数字。以下每个方法对应一个 reviewer 必然会问的问题，并提供可放进补充材料的定量答案。

### 0. 元数据标准化准确率（已有 pipeline 即可测）

**问题**: 你的自动化元数据提取到底有多准？

**方法**: 
1. 随机选 50-100 个 GSE，人工从 SOFT 文件中提取 gold-standard 元数据标注
2. 用 pipeline 自动提取同一批 GSE 的元数据
3. 逐字段计算 precision / recall / F1

**输出指标**:
```
field_mappings 准确率:
  整体: Precision=0.92  Recall=0.87  F1=0.89
  基因型: Precision=0.95  Recall=0.91  F1=0.93
  组织:   Precision=0.88  Recall=0.84  F1=0.86
  处理:   Precision=0.90  Recall=0.85  F1=0.87
  ...
```

**代码需要**:
- `tests/evaluation/field_accuracy.py` — 人工标注文件加载 + 字段级对比
- `tests/evaluation/FIELD_ACCURACY_TEMPLATE.tsv` — 标注模板（N 行 × 每行一个 GSE × 手工标注字段）

### 1. 饱和度分析（Saturation Analysis）

**Reviewer 问**: 你怎么知道你采样够了？156 个 GSE 之外会不会有更重要的发现？

**方法**:
```
1. 从候选 GSE 中随机抽取子集: 10, 20, 40, 80, 120, 156 个
2. 每个子集独立运行 Hypothesis Generator
3. 记录每个子集产出的唯一假设数
4. 画曲线: X 轴 = 数据集数量, Y 轴 = 累计唯一假设数
5. 拟合 Michaelis-Menten 饱和模型: y = Vmax * x / (Km + x)
```

**判断标准**:
- 曲线在 X < 总数据集数处趋平 → 采样饱和，足够
- 曲线在最大 X 处仍上升 → 需要更多数据
- 报告 Vmax 和饱和点（95% Vmax 时的数据集数）

**输出图表**: `figures/saturation_curve.png` — 论文 Figure 2

**代码需要**:
- `src/scruticell/discovery/evaluation/saturation.py` — 子集采样 + 累计假设计数 + 曲线拟合 + 绘图

### 2. 扰动测试（Perturbation Test）

**Reviewer 问**: 你的假设是否依赖于某些特定的数据集？如果去掉几个关键 GSE，假设还成立吗？

**方法**:
```
1. 从 156 个 GSE 中随机去掉 20%（约 31 个），重复 10 次
2. 每次重新运行 Hypothesis Generator → 产出假设列表
3. 对比每次产出的假设与原始假设的重叠度

4. 对每个假设，计算"健壮性分数":
   - 该假设在多少次扰动中仍然出现？ / 10
   - 健壮性 = 1.0 → 不依赖特定数据集
   - 健壮性 = 0.3 → 几乎只出现在原始集中 → 标注为"low-robustness"
```

**输出指标**:
```
假设排名变化:
  H1 (Region-specific microglia): 健壮性=1.0 ✓
  H2 (Astrocyte subtype switch):  健壮性=0.9 ✓
  H3 (TREM2 expression discordance): 健壮性=0.6 ⚠ 低健壮
  H4 (Sex-specific AD markers):     健壮性=0.8 ✓
  ...
```

**输出图表**: `figures/perturbation_robustness.png` — 论文 Figure 3(b)

**代码需要**:
- `src/scruticell/discovery/evaluation/perturbation.py` — 随机去电子集 + 假设对比 + 健壮性分数

### 3. 互补搜索 + 并集验证（Complementary Search Validation）

**Reviewer 问**: 你的搜索策略有没有遗漏？怎么证明搜寻全面？

**方法**:
```
搜索策略 A (广域):  "Alzheimer single cell RNA seq"
  产出: {GSE1...GSE156}, 排除: {GSE_x1...GSE_xN}

搜索策略 B (聚焦):  "AD scRNA-seq microglia astrocyte"  
  产出: {GSE10, GSE50, GSE201, ..., GSE350}
  A 未覆盖但 B 找到: ~30 个

搜索策略 C (文献溯源):  PubMed 搜索 "Alzheimer single-cell" 
  → 提取所有 GSE accession → 去重
  A 和 B 都未覆盖但 C 找到: ~15 个

搜索策略 D (关联搜索):  从 A 中已知数据的 "related datasets" 
  字段扩展 → 补充 ~20 个
```

**判断标准**:
- 并集大小 = |A ∪ B ∪ C ∪ D|
- 遗漏率 = (并集 - A) / 并集
- 专家审查 B/C/D 中 A 未覆盖的数据集 → 判断它们是否真正相关
- 如果大多数真正相关 → A 有严重遗漏，需扩大搜索
- 如果大多数不相关 → A 的搜索已经足够全面

**输出指标**:
```
搜索策略覆盖度:
  初始搜索(A): 156 个相关 GSE
  互补搜索(B+C+D): 新增 45 个候选
  专家审查: 38/45 (84%) 确实相关但不包含在初始搜索中
  修正后总集: 156 + 38 = 194 GSE
  遗漏率: 38/194 = 19.6%
```

**代码需要**:
- `src/scruticell/discovery/evaluation/search_coverage.py` — 多策略搜索 + 并集计算 + 专家标注接口

### 4. 假阴性审计（False Negative Audit）

**Reviewer 问**: 你的筛选有没有把真正相关的数据集误排除了？

**方法**:
```
1. 从系统标记为 "IRRELEVANT" 的 GSE 中随机抽 50 个
2. 让 2-3 个领域专家（盲审）判断每个是否真的不相关
3. 计算假阴性率 = 专家认为相关的数量 / 50

4. 如果假阴性率 > 10% → 调整 LLM 筛选阈值或 prompt
5. 论文中报告: "筛选假阴性率为 X%，主要遗漏类型为 [类型说明]"
```

**输出指标**:
```
假阴性审计:
  随机抽取: 50 个被排除的 GSE
  专家一致"不相关": 46 个
  专家一致"相关": 2 个（均为 bulk RNA-seq with single-cell component）
  专家分歧: 2 个
  假阴性率: 4/50 = 8%
  主要遗漏模式: 混合 bulk+single-cell 数据集（3/4 遗漏案例）
```

**代码需要**:
- `tests/evaluation/false_negative_audit.py` — 随机抽样 + 专家审查数据管理
- `tests/evaluation/FALSE_NEGATIVE_AUDIT_TEMPLATE.tsv` — 盲审标注模板

### 5. 假设质量专家评估（Blind Hypothesis Review）

**Reviewer 问**: 你生成的假设质量如何？LLM 不会在自娱自乐吧？

**方法**:
```
1. 将系统产出的 15 个假设 + 手工元分析产出的 5 个假设（混在一起，去标识）
2. 发 2-3 个 AD 领域专家，请他们 blind review:
   - Novelty: 这个想法是新的吗？（1-5）
   - Feasibility: 用现有数据能验证吗？（1-5）
   - Impact: 如果成立，对 AD 研究影响多大？（1-5）
   - Overall: 总体上这个假设值得深入研究吗？（1-5）

3. 统计:
   - 系统产出的假设 vs 手工标注假设的得分对比
   - 专家间一致性（ICC 或 Fleiss' Kappa）
```

**输出指标**:
```
假设质量对比:
              系统产出(mean±SD)    手工标注(mean±SD)    p值
  Novelty      3.8±0.6             3.2±0.8            0.04
  Feasibility  4.1±0.5             4.0±0.6            0.32
  Impact       3.5±0.7             3.3±0.9            0.18
  Overall      3.8±0.5             3.5±0.7            0.11

专家间一致性: ICC=0.78 (good agreement)
```

**代码需要**:
- `tests/evaluation/blind_review.py` — 假设去标识 + 专家评分数据管理 + 统计对比
- `tests/evaluation/BLIND_REVIEW_TEMPLATE.tsv` — 盲审评分模板

### 6. 留出验证（Holdout Validation）

**Reviewer 问**: 你的假设能不能泛化到没见过的数据？

**方法**:
```
1. 从候选 GSE 中预留 20%（约 31 个）作为 holdout 集
2. 只用剩余的 125 个生成假设
3. 对每个假设，检查 holdout 集是否支持/反驳

4. 分类:
   - Supported: holdout 数据中也有同样趋势
   - Inconclusive: holdout 数据中信号弱
   - Contradicted: holdout 数据中方向相反
```

**输出指标**:
```
留出验证结果:
  H1 (Region-specific microglia):    Supported (4/4 holdout GSEs) ✓
  H2 (Astrocyte subtype switch):     Supported (3/3 holdout GSEs) ✓ 
  H3 (TREM2 discordance):            Inconclusive (1/3 holdout GSEs) ⚠
  H4 (Sex-specific AD markers):      Supported (2/2 holdout GSEs) ✓
  H5 (Oligodendrocyte early involvement): Contradicted (0/2 holdout GSEs) ✗
  
  总体验证率: 80% (4/5 hypotheses supported or inconclusive)
```

### 7. 批次划分正确率（已有 pipeline 即可测）

**Reviewer 问**: 你的系统自动划分的批次对不对？

**方法**:
1. 随机选 20-30 个 GSE，人工标注正确的批次划分
2. 对比系统自动划分 vs 人工标注
3. 计算:
   - 批次数一致率（系统 = 人工的占比）
   - 批次划分错误类型分布（误合并 / 误拆分 / 错误分组）
   - 按 batch_separation_strategy 分层的准确率

**输出指标**:
```
批次划分正确率 (N=30 GSEs):
  总体一致率: 87% (26/30)
  按策略:
    per_sample:      95% (19/20)
    field_combination: 67% (4/6)
    其他:             75% (3/4)
  
  错误类型:
    误合并: 2 例 (用户认为应该拆开的被合并了)
    误拆分: 1 例 (用户认为应该合并的被拆开了)
    错误分组: 1 例 (分组逻辑对但分配错了 GSM)
```

**代码需要**:
- `tests/evaluation/batch_accuracy.py` — 人工标注 vs 系统划分对比
- `tests/evaluation/BATCH_ACCURACY_TEMPLATE.tsv` — 批次标注模板

### 8. 评估阶段集成到 CLI

```
scruticell evaluate discovery output/discovery/alzheimer/ \
  --holdout-ratio 0.2 \
  --perturbation-rounds 10 \
  --saturation-steps 6 \
  --expert-review-file tests/evaluation/experts.tsv

scruticell evaluate pipeline output/benchmark_results/ \
  --field-accuracy 50 \
  --batch-accuracy 30
```

**代码需要**:
- `src/scruticell/discovery/evaluation/__init__.py` — 评估模块入口
- `src/scruticell/cli_evaluate.py` — 新增 `scruticell evaluate` 命令组

### 9. 时间投入估算

| 评估组件 | 开发时间 | 运行时间 | 人工参与 |
|---|---|---|---|
| 元数据准确率 | 2 天 | 1 天（自动化） | 2 天（标注 50 GSE） |
| 饱和度分析 | 1 天 | 2-3 天（全自动，N 次 Hypothesis Generator） | 0 |
| 扰动测试 | 1 天 | 1-2 天（全自动，10 次重跑） | 0 |
| 互补搜索 + 并集 | 1 天 | 0.5 天（全自动） | 1 天（专家审查 50 个） |
| 假阴性审计 | 0.5 天 | 0（全自动抽样） | 1 天（专家审查 50 个） |
| 假设质量盲审 | 0.5 天 | 0 | 3 天（找专家 + 等待反馈） |
| 留出验证 | 1 天 | 1 天（验证假设） | 0 |
| 批次划分正确率 | 1 天 | 0.5 天（自动化） | 1 天（标注 30 GSE） |
| **总计** | **8 天** | **~7 天** | **~8 天** |

### 10. 论文材料清单

| 材料 | 对应章节 | 评估方法编号 |
|---|---|---|
| 元数据标准化准确率 | Results §1 | #0 |
| 搜索策略覆盖分析 | Results §2 | #3, #4 |
| 饱和度曲线 | Results §3 | #1 |
| 假设列表（排序 + 健壮性） | Results §4 | #2, #6 |
| 批次划分正确率 | Results §5 | #7 |
| 假设质量专家盲审 | Results §6 | #5 |
| Perturbation 热图 | Supplementary Fig S1 | #2 |
| 假阴性审计表 | Supplementary Table S3 | #4 |
| 留出验证详情 | Supplementary Table S4 | #6 |
| 完整 GSE 列表 | Supplementary Table S1 | — |

## 论文叙事框架

```
Title: "LLM-Guided Multi-Agent System for Hypothesis Generation
        from Single-Cell GEO Datasets: Application to Alzheimer's Disease"

Abstract:
  我们构建了一个 LLM 驱动的多 agent 系统，自主搜索、筛选、标准化了
  GEO 中 {N} 个阿尔兹海默症单细胞数据集。系统通过跨数据集元分析
  发现了 {M} 个可验证的科学假设，其中 {K} 个在独立数据集中得到
  计算验证。我们进一步通过实验验证了 [最关键的发现]。

Introduction:
  - GEO 中单细胞数据爆炸增长但元数据非标准化
  - 手工跨数据集分析不可扩展
  - LLM agent 可以自动化这个过程

Results:
  - 系统筛选了 1,432 个搜索结果 → 156 个相关数据集
  - 产出了 15 个科学假设，其中 3 个在独立的保留数据集中验证
  - [具体生物学发现]

Discussion:
  - 方法的局限性
  - 对其他疾病的可推广性
  - 与手工元分析的对比

Methods:
  - Architecture (Layers 1-5)
  - LLM configuration
  - Statistical methods
  - Validation strategy

Data Availability:
  - All code: GitHub
  - All processed data: Zenodo/FigShare
  - Analysis scripts: reproducibility package
```
