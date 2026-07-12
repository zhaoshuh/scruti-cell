# Scrut-Cell 使用教程

## 目录

1. [快速开始](#快速开始)
2. [多组学预处理](#多组学预处理) ← **新增**
3. [一键管道](#一键管道)
4. [规则管道](#规则管道)
5. [智能管道](#智能管道)
6. [配置管理](#配置管理)
7. [常见问题](#常见问题)

---

## 快速开始

### 安装

```bash
git clone https://github.com/zhaoshuh/scruti-cell
cd scruticell
pip install -e ".[dev]"
```

### 设置 API Key

```bash
export DEEPSEEK_API_KEY="sk-xxx"
```

### 验证

```bash
scruticell --version
# Scrut-Cell version 0.1.0

scruticell config
# 显示当前配置
```

---

## 多组学预处理（preprocess）

**新增命令**。从 GSE 编号出发，自动下载、解压、识别 12 种组学模态（scRNA-seq、ATAC-seq、空间转录组、DNA 甲基化、CITE-seq、TCR/BCR、Multiome 等），将文件按**样本×模态**分离。不进行下游分析——输出是组织好的文件树。

### 基本用法

```bash
# 预处理 GSE215353：自动分离 scRNA-seq 和 DNA 甲基化文件
scruticell preprocess GSE215353

# 指定输出目录
scruticell preprocess GSE215353 -o ./my_results

# 保留下载的原始数据
scruticell preprocess GSE215353 --keep-data

# 详细日志
scruticell preprocess GSE215353 -v
```

### 输出结构

```
output/<GSE_ID>/preprocessed/
├── manifest.json              # 完整预处理清单 (含 sample_groups 逻辑分组)
├── GSM4797916/                # 样本目录 (key = GSM ID 或推断 key)
│   ├── metadata.json          # 样本元数据 (含 gsm_ids, sample_key, key_source)
│   ├── spatial_transcriptomics/
│   │   ├── files.json         # 文件清单及角色
│   │   ├── tissue_positions_list.csv
│   │   ├── scalefactors_json.json
│   │   └── tissue_hires_image.png
│   └── scrna_seq/
│       └── ...
├── donor_P1__untreated/       # 推断 key 场景 (复合键)
│   ├── metadata.json
│   └── scrna_seq/
├── GSM4797918/
│   └── ...
└── unclassified/              # 无法分类的文件
    └── unclassified.json
```

共享文件通过 hard link 出现在所有引用它的样本目录中。`manifest.json` 的 `sample_groups` 记录逻辑分组。

### 工作原理

Scrut-Cell 使用 LLM Agent（deepagents）编排整个预处理流程。核心原则：**Python 收集信号，LLM 做判决**。

```
┌─────────────────────────────────────────────────────────────┐
│  1. File Prep Sub-Agent (下载 + 解压 + 检查)                 │
│     downloads → decompresses → inspects all files           │
│     输出: 紧凑 FileContextReport                              │
│       ├── field_value_maps    (metadata 去重, 识别实验分组)    │
│       ├── file_signal_groups  (同指纹文件合并)                 │
│       │   ├── per_file_gsm_id  (ancestry→GSM, 无推断)        │
│       │   ├── attribution       (L0 ancestry + L1 SOFT ref)  │
│       │   └── content_preview   (实际数据样本)                 │
│       └── file_summaries      (原始文件指纹, 供 organize 使用)  │
└─────────────────────────────────────────────────────────────┘
         │
         ├─(信号冲突时)→ Evidence Sub-Agent (PubMed→PDF RAG)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  2. Classifier Sub-Agent (纯 LLM 推理, 零工具)               │
│     模态知识从 skills/modality_knowledge/SKILL.md 运行时注入   │
│     分类规则:                                                 │
│       A. 文件→样本归属 (1:1 约束)                             │
│          优先级: per_file_gsm_id > candidates > tokens       │
│          无法匹配 → 推断复合 key                              │
│       B. 共享文件检测 (同一文件被多个样本使用)                  │
│       C. Demux 文件检测 (合并文件需拆分)                      │
│       D. 样本分组 (field_value_maps 值差异)                   │
│       E. 模态判定 (SKILL.md + content_preview)               │
│     置信度: ≥0.85 (信号一致) / 0.5-0.85 (有冲突) / <0.5 (严重不足)│
└─────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│  3. organize_output Tool (构建输出目录树)                     │
│     output/<GSE_ID>/preprocessed/                             │
│       group_<key>/                    (key = GSM ID 或推断 key)│
│         {modality}/                                            │
│           files.json + hard link                              │
│       manifest.json                                           │
│     共享文件 hard link 到所有引用组                              │
└─────────────────────────────────────────────────────────────┘
```

**关键设计：**
- **模态 Skill 化**：`skills/modality_knowledge/SKILL.md` 是模态定义的唯一来源。新增模态只需编辑此文件，无需改代码。
- **1:1 文件归属**：每个文件归属唯一 key，key↔sample_group 是双射。不允许一个文件属于多个样本。
- **信号收集 vs 判决分离**：`attribution.py` 只收集 L0+L1 信号。所有匹配/推断由 classifier prompt 完成。
- **指纹缓存**：相同 GSE 的内容指纹 → 跳过 LLM 调用直接返回。

整个流程对 12 种组学模态做了 token 优化：50 个 GSM 的元数据从 ~28,000 tokens 压缩到 ~500 tokens。`per_file_gsm_id` 提供准确的 ancestry→GSM 映射，消除了模糊匹配带来的多归属问题。

### 支持的 12 种组学模态

模态定义位于 `skills/modality_knowledge/SKILL.md`（运行时加载）。支持扩展新模态——编辑该文件即可。

---

## 一键管道（run）

**推荐用法**。自动完成勘查→规划→执行，输出分析就绪的 AnnData。

```bash
# 最简单用法
scruticell run GSE334035

# 指定输出目录
scruticell run GSE334035 -o ./my_results

# 跳过 LLM（仅 KB 匹配）
scruticell run GSE334035 --no-llm

# 保留下载数据
scruticell run GSE334035 --keep-data
```

### 参数说明

```
Usage: scruticell run [OPTIONS] GSE_ID

Options:
  -o, --output PATH      输出目录（默认：output/<GSE_ID>/）
  --llm-model TEXT       LLM 模型名称
  --llm-api-key TEXT     LLM API Key
  --no-download          跳过下载，使用已有缓存
  --no-llm               跳过 LLM 规划，仅显示 KB 匹配
  --keep-data            保留下载文件和输出 .h5ad
  -v, --verbose          显示详细日志
```

### 输出结构

```
output/<GSE_ID>/
├── preprocessed/         # (Layer 0) 组学分离结果
├── inspect.json          # 勘查报告
├── plan.json             # 处理方案
├── execute.json          # 执行汇总
├── metadata.md           # 批次元数据表
├── <GSE>_<batch>_scruticell.h5ad  # 每批次 AnnData
└── ...
```

---

## 规则管道（parse / standardize）

适合结构标准的数据集（单一样本、标准 10X h5 格式）。不需要 LLM，全用固定规则。

```bash
scruticell parse GSE334035 -o ./output

# 跳过 PubMed 文献搜索
scruticell parse GSE334035 --no-literature

# 增加重试次数
scruticell parse GSE334035 --max-retries 5
```

### 管道流程

```
[Download]      ← 从 NCBI GEO 下载数据
[Parse SOFT]    ← 解析 SOFT 元数据 + GSM 特征字段
[Literature]    ← (可选) PubMed 文献搜索
[Discover Matrix]  ← 发现矩阵文件 (h5ad > mtx > csv > rds > loom)
[Read Matrix]   ← 读取矩阵，自动检测方向
[Align Cells]   ← 标准化细胞 barcode (10x v2/v3, Drop-seq)
[Unify Genes]   ← 统一基因 ID (symbol/ensembl/entrez)
[Validate]      ← 4 维置信度验证 (barcode, 基因, 元数据, 批次)
[Reflexion]     ← 置信度 < 85% 时：KG→LLM→启发式→MCTS 自修正
[Output]        ← 写入 .h5ad
```

---

## 智能管道（inspect → plan → execute）

适合结构复杂的数据集（多样本、嵌套压缩包、复合字段元信息、非标准矩阵）。

### Step 1: inspect（勘查）

```bash
scruticell inspect GSE334035

# 使用已有下载缓存
scruticell inspect GSE334035 --no-download
```

输出 `inspect.json`：GSE 元数据、GSM 条目、文件族谱树、每个文件的指纹（行列数、列名、barcode 格式等）。

### Step 2: plan（规划）

```bash
scruticell plan GSE334035

# 仅查看 KB 匹配，不调用 LLM
scruticell plan GSE334035 --no-llm
```

输出 `plan.json`：LLM 生成的处理方案，含矩阵映射、批次分配、字段映射，以及 Python 处理代码。

### Step 3: execute（执行）

```bash
scruticell execute GSE334035

# 关闭失败反射重试
scruticell execute GSE334035 --no-reflect
```

输出：每批次一个 `.h5ad` + `execute.json`（provenance + trace + 统计）。

### 批量处理

```powershell
$GSE_LIST = @("GSE334035", "GSE123456", "GSE234567")

foreach ($gse in $GSE_LIST) {
    scruticell inspect $gse
    if (-not $?) { continue }
    scruticell plan $gse
    if (-not $?) { continue }
    scruticell execute $gse
}
```

---

## 配置管理

### 查看配置

```bash
scruticell config
```

### 关键环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEEPSEEK_API_KEY` | — | LLM API key |
| `LLM_MODEL` | `deepseek-chat` | 模型名称 |
| `LLM_BASE_URL` | `https://api.deepseek.com` | API 地址 |
| `LLM_TEMPERATURE` | `0.0` | 确定性输出 |
| `CONFIDENCE_THRESHOLD_AUTO` | `0.85` | 自动通过阈值 |
| `SCRUTICELL_LICENSE` | — | 企业版 License |

### 自定义 LLM

```bash
export OPENAI_API_KEY="sk-xxx"
scruticell parse GSE334035 --llm-model "gpt-4o" --llm-base-url "https://api.openai.com/v1"
```

---

## 常见问题

### Q: 需要安装 Neo4j 吗？

A: 不需要。开源版使用 SQLite，preprocess/parse/inspect/plan/execute 都可以在无 Neo4j 的情况下运行。

### Q: 支持哪些组学模态？

A: 12 种：scRNA-seq、snRNA-seq、ATAC-seq、10x Multiome、空间转录组、DNA 甲基化、CITE-seq、TCR-seq、BCR-seq、Perturb-seq、蛋白质组学、未知。模态定义在 `skills/modality_knowledge/SKILL.md`，编辑此文件即可新增模态。

### Q: 如何添加新的组学模态？

A:
1. 编辑 `skills/modality_knowledge/SKILL.md`，按模板添加新模态的 metadata signals、file patterns、content signals
2. (可选) 在 `context/metadata.py` 的 `_MODALITY_KEYWORDS` 中添加元数据关键词
3. (可选) 在 `context/filenames.py` 的 `_KEYWORD_PATTERNS` 中添加文件名关键词
4. 无需修改任何 Python 逻辑代码

### Q: 支持哪些文件格式？

A: `.h5ad`, `.h5` (10x AnnData/HDF5), `.mtx` (10x 三件套), `.csv/.tsv`, `.rds` (Seurat/SCE), `.loom`。

### Q: 支持哪些物种？

A: 人类和小鼠。基因 ID 基于 bionty 数据库。

### Q: 处理失败怎么办？

A:
1. 使用 `-v` 查看完整日志
2. `parse` 命令下 `--max-retries` 可增加重试次数
3. `execute` 命令默认开启反射重试（`--no-reflect` 关闭）
4. 检查下载缓存：`G:\scruticell\data\<GSE_ID>\`

### Q: 文件如何归属到样本？

A: 每个文件通过 4 层优先级归属到唯一样本 key：
1. **ancestry 路径** — 最高优先级。例如 `GSM001/barcodes.tsv.gz` → GSM001
2. **SOFT supplementary 引用** — GEO 元数据中明确列出的文件归属
3. **文件名/列头 Token** — 从文件名和列头提取的样本 ID 与 metadata 交叉匹配
4. **推断复合 key** — 以上均无法匹配时，从文件名/内容推断。例如 `donor_P1__treatment_control`

所有判断由 LLM classifier 完成。key 与样本组之间是严格的 1:1 双射关系。共享文件（如多个样本共用同一 features.tsv.gz）会被标记并通过 hard link 分发。

### Q: 嵌入模型有多大？

A: PDF RAG 功能首次运行时下载 `all-MiniLM-L6-v2`（约 80 MB），缓存到 `HF_HOME` 目录。
