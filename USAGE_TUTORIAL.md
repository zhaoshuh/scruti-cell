# Scrut-Cell 使用教程

## 目录

1. [输出文件结构](#输出文件结构)
2. [快速开始](#快速开始)
3. [命令行接口](#命令行接口)
4. [规则管道：一条命令出结果](#规则管道一条命令出结果)
5. [智能管道：勘查→规划→执行](#智能管道勘查规划执行)
6. [配置管理](#配置管理)
7. [许可证](#许可证)
8. [常见问题](#常见问题)

---

## 输出文件结构

所有智能管道 (inspect / plan / execute) 的输出默认统一到 `output/<GSE_ID>/` 目录下：

```
output/<GSE_ID>/
├── inspect.json                              # 勘查报告（M1：文件指纹 + 元数据摘要）
├── plan.json                                 # 处理方案（M2：矩阵映射 + LLM 生成代码）
├── execute.json                              # 执行汇总（M3：provenance + trace + 统计）
├── <GSE_ID>_<GSM_ID>_scruticell.h5ad         # AnnData 输出（每个批次一个）
├── <GSE_ID>_<GSM_ID>_scruticell.h5ad
└── ...
```

> `-o` 参数可覆盖默认路径。

---

## 前置软件与环境

### 必需

| 软件 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.10 ~ 3.12 | 推荐使用 Conda 管理环境 |
| LLM API Key | — | DeepSeek 或 OpenAI-compatible API |
| **磁盘空间** | ≥ 10 GB 空闲 | GEO 下载缓存 + 嵌入模型（首次约 80 MB） |

### 可选（企业版功能）

| 软件 | 版本要求 | 说明 |
|------|---------|------|
| Neo4j | 5.x Community/Enterprise | 知识图谱持久化与策略累积 |
| Java | 17+ | Neo4j 运行时依赖 |

### Pip 依赖清单

```
PyMuPDF           # PDF 文本提取
sentence-transformers  # 本地向量嵌入（all-MiniLM-L6-v2, ~80 MB 首次下载）
torch             # 嵌入模型运行时
langgraph         # 管道 DAG 引擎
langchain + langchain-openai  # LLM 接口
anndata + scanpy  # 单细胞数据
GEOparse          # GEO SOFT 文件解析
pandas + numpy + scipy  # 数值计算
neo4j             # 图数据库客户端
bionty            # 基因/物种标准化
biopython         # 生物信息工具
click + rich      # CLI 框架
pydantic          # 数据验证
```

### 安装

```bash
# 克隆仓库
git clone https://github.com/scruti-cell/scruticell
cd scruticell

# 推荐：用 Conda 创建环境（防止 C 盘空间不足）
conda create -p G:\anaconda\envs\scruticell python=3.11 -y
conda activate G:\anaconda\envs\scruticell

# 安装全部依赖（指定缓存目录避免占用 C 盘）
pip install -e ".[dev]" --cache-dir G:\pip-cache

# 首次运行时会自动下载嵌入模型到 HF_HOME（约 80 MB）
```

### Neo4j 安装（企业版）

```bash
# 1. 下载并解压到任意目录
# https://neo4j.com/download/

# 2. 修改 conf/neo4j.conf，将数据目录指向非系统盘
# server.directories.data=G:\\scruticell\\neo4j\\data

# 3. 启动
bin\neo4j console
# 或
bin\neo4j install-service && bin\neo4j start
```

### 设置 LLM API Key

```bash
# 需要设置以下环境变量之一
export DEEPSEEK_API_KEY="sk-xxx"
# 或
export OPENAI_API_KEY="sk-xxx"

# 推荐：设置缓存目录到非系统盘
export TMP=G:\temp
export TEMP=G:\temp
export PIP_CACHE_DIR=G:\pip-cache
export TORCH_HOME=G:\torch_cache
export HF_HOME=G:\huggingface_cache
```

### 验证安装

```bash
scruticell version
# Scrut-Cell version 0.1.0

scruticell config
# 显示当前配置
```

---

## 命令行接口

Scrut-Cell 提供 7 个子命令：

```
scruticell run <GSE_ID>       一键管道：inspect + plan + execute
scruticell parse <GSE_ID>     规则管道：一站式 GSE→AnnData
scruticell inspect <GSE_ID>   智能管道-勘查：生成结构化报告
scruticell plan <GSE_ID>      智能管道-规划：LLM 生成处理方案
scruticell execute <GSE_ID>   智能管道-执行：按方案生成 AnnData
scruticell config             查看当前配置
scruticell db-clear           清空知识库
scruticell --version          显示版本号
```

---

## 一键管道：一条命令出结果

**推荐用法**。自动完成勘查→规划→执行三步，适合所有数据集。

```bash
# 最简单用法
scruticell run GSE334035

# 指定输出目录
scruticell run GSE334035 -o ./my_results

# 跳过 LLM（仅 KB 匹配，不调用 LLM）
scruticell run GSE334035 --no-llm

# 保留下载的原始数据
scruticell run GSE334035 --keep-data
```

**参数说明**：

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

**输出结构**：

```
output/<GSE_ID>/
├── inspect.json                                     # 勘查报告
├── plan.json                                        # 处理方案
├── execute.json                                     # 执行汇总
├── metadata.md                                      # 批次元数据表
├── <GSE>_<batch>_scruticell.h5ad                    # 每批次 AnnData
└── ...
```

**分步执行**：如果需要查看中间结果（如 LLM 生成的 plan 代码），可以分三步运行：

```bash
scruticell inspect GSE334035   # → output/GSE334035/inspect.json
scruticell plan GSE334035      # → output/GSE334035/plan.json
scruticell execute GSE334035   # → output/GSE334035/execute.json + .h5ad
```

---

## 规则管道：一条命令出结果（旧版）

适合结构标准的数据集（单样本、标准 10X h5 格式）。

### 基本用法

```bash
scruticell parse GSE334035 -o ./output
```

### 参数说明

```
Usage: scruticell parse [OPTIONS] GSE_ID

Options:
  -o, --output PATH      输出目录（默认：当前目录）
  --llm-model TEXT       LLM 模型名称（默认：deepseek-chat）
  --llm-api-key TEXT     LLM API Key（默认从环境变量读取）
  --llm-base-url TEXT    LLM API 地址
  --no-literature       跳过 PubMed 文献搜索
  --max-retries INTEGER  最大反省重试次数（默认：3）
  -v, --verbose          显示详细日志
  --keep-data            保留下载的原始数据和输出文件
```

### 输出文件

成功运行后，输出目录包含：
- `GSE334035_scruticell.h5ad`：分析就绪的 AnnData 文件
- `audit_*.json`：处理审计日志
- `*_provenance.json`：来源追踪信息

### 管道流程

```
[Download]...   OK    ← 从 NCBI GEO 下载数据
[Parse SOFT]... OK    ← 解析元数据
[Literature]... OK    ← 搜索文献（可选）
     │
[Discover Matrix]... OK   ← 发现矩阵文件
[Read Matrix]...    OK    ← 读取矩阵
[Align Cells]...    OK    ← 对齐细胞 barcode
[Unify Genes]...    OK    ← 统一基因 ID
[Validate]...       OK    ← 置信度验证
     │
[Output]...         OK    ← 写入 .h5ad

=== 结果 ===
Cells:    54198
Genes:    38584
Organism: Homo sapiens
Retries:  0
Confidence: 100%
  barcode: 100%
  gene_id: 100%
  metadata: 100%
  batch: 100%
  Audit log: output/audit_20250101_120000.json
```

### 反省重试

当置信度低于 85% 时，管道自动触发 Reflexion 重试：

```
[Validate]... WARNING  ← 置信度低于阈值
--- Reflexion retry 1/3 ---
[Reflexion]...  OK     ← KG/LLM/启发式 生成修正策略
Strategy: Force matrix transpose
  [Discover Matrix]... OK
  ...
[Validate]...    OK
```

---

## 智能管道：勘查→规划→执行

适合结构复杂的数据集（多样本、嵌套压缩包、复合字段元信息、非标准矩阵格式）。

智能管道分三步，每步都可以独立运行和查看中间结果。

### 第一步：勘查（inspect）

```bash
# 下载并勘查数据集 → 默认写入 output/<GSE_ID>/inspect.json
scruticell inspect GSE334035

# 指定自定义输出文件
scruticell inspect GSE334035 -o ./custom/inspect.json

# 使用已有下载目录
scruticell inspect GSE334035 --no-download
```

**输出：** 生成 `inspect.json`，包含：
- `gse_info`：GSE 级别的标题、摘要、实验设计描述
- `gsm_entries`：每个 GSM 的特征字段、补充文件列表
- `file_tree`：解压后的文件族谱树
- `file_summaries`：每个数据文件的内容摘要（行列数、列名、数据预览、barcode 格式等）
- `data_files`：候选数据文件列表（LLM 后续决定哪些是矩阵）

```bash
=== Inspection Report: GSE334035 ===
Files inspected:   15
Data files:        3
GSM entries:       3
```

### 第二步：规划（plan）

```bash
# 从勘查报告生成规划 → 默认写入 output/<GSE_ID>/plan.json
scruticell plan GSE334035 \
  --report ./output/GSE334035/inspect.json

# 指定自定义输出文件
scruticell plan GSE334035 \
  --report ./output/GSE334035/inspect.json \
  -o ./custom/plan.json

# 仅查看 KB 匹配，不调用 LLM
scruticell plan GSE334035 \
  --report ./output/GSE334035/inspect.json \
  --no-llm
```

**输出：** 生成 `plan.json`，包含 LLM 生成的处理方案：

- `matrices`：哪些文件是表达矩阵（含格式、方向、是否需转置）
- `batches`：每个 GSM 对应哪些矩阵文件
- `field_mappings`：原始 GEO 字段→规范字段名的映射（如 `"tissue: liver"` → `tissue="liver"`）
- `processing_code`：LLM 生成的 Python 代码，对元数据做标准化：
  ```python
  def process(obs, var, adata):
      # 字段重命名
      obs.rename(columns={'characteristics_ch1': 'tissue'}, inplace=True)

      # 复合值拆分：'Male_Control_72yr' → sex='Male', condition='Control', age=72
      obs['sex'] = obs['raw_field'].str.split('_').str[0]
      obs['condition'] = obs['raw_field'].str.split('_').str[1]

      # 全局元数据
      obs['organism'] = 'Homo sapiens'

      return obs, var, adata
  ```
- `assembly_code`：LLM 生成的装配代码（多样本合并）
- `strategy_notes`：LLM 的策略解释
- `confidence`：LLM 对规划方案的置信度

**KB 匹配：** 如果之前处理过相似结构的数据集，规划阶段会显示匹配结果：

```
KB Matches: 2
  - GSE12345 (score: 0.85, success: 3, fail: 0)
  - GSE67890 (score: 0.72, success: 1, fail: 1)
```

### 第三步：执行（execute）

```bash
# 最简单用法：自动查找 output/<GSE_ID>/ 下的 plan.json + inspect.json
scruticell execute GSE334035

# 指定自定义输出目录
scruticell execute GSE334035 -o ./custom

# 显式指定 plan 和 report 文件
scruticell execute GSE334035 \
  --plan-file ./custom/plan.json \
  --report-file ./custom/inspect.json

# 跳过下载，使用已有数据
scruticell execute GSE334035 --no-download

# 关闭反射重试
scruticell execute GSE334035 --no-reflect

# 保留所有数据文件
scruticell execute GSE334035 --keep-data
```

**输出：**
- 每个批次一个 `.h5ad` 文件：`{GSE_ID}_{batch_label}_scruticell.h5ad`
- `execute.json`：执行汇总（provenance + trace + cells/genes/batches 统计）

> 如果 `output/<GSE_ID>/plan.json` 或 `inspect.json` 不存在，会报错提示先运行对应的上一步命令。

```
=== Execution: SUCCESS ===
Output:   output/GSE334035/GSE334035_GSM9779490_scruticell.h5ad
Cells:    54198
Genes:    38584
Batches:  3
Provenance: output/GSE334035/execute.json

[Step 4 — Metadata Strategy]
  obs['organism'] = 'Homo sapiens'
  obs['tissue'] = 'Forebrain organoid'
  ...

[Step 5 — Assembly Strategy]
  merged = sc.concat(adatas.values(), ...)
  ...
```

### 批量处理脚本

```powershell
# batch_run.ps1
$GSE_LIST = @(
    "GSE334035",
    "GSE123456",
    "GSE234567"
)

foreach ($gse in $GSE_LIST) {
    Write-Host "=== Processing $gse ==="

    scruticell inspect $gse -o "./output/$gse/inspect.json"
    if (-not $?) { Write-Host "Inspect failed for $gse"; continue }

    scruticell plan $gse `
        --report "./output/$gse/inspect.json" `
        -o "./output/$gse/plan.json"
    if (-not $?) { Write-Host "Plan failed for $gse"; continue }

    scruticell execute $gse

    Write-Host ""
}
```

---

## 配置管理

### 数据存储路径

所有运行时数据统一存储在 `G:\scruticell\` 下：

| 目录 | 内容 |
|------|------|
| `G:\scruticell\data\` | GEO 下载缓存 + SQLite 知识库 (`knowledge.db`) |
| `G:\scruticell\cache\pdf\` | PDF RAG 文献提取缓存 (按 PMID 存储) |
| `G:\scruticell\neo4j\data\` | Neo4j 图数据库文件 |

### 查看当前配置

```bash
scruticell config
```

输出：

```
Scrut-Cell Configuration:
  Data directory:     G:\scruticell\data
  LLM model:          deepseek-chat
  LLM temperature:    0.0
  LLM base URL:       https://api.deepseek.com
  LLM API key:        ***
  Neo4j URI:          bolt://localhost:7687
  Download timeout:   600s
  Download retries:   3
  Confidence auto:    0.85
  Confidence warn:    0.60
```

### 自定义 LLM

使用 OpenAI 或其他兼容 API：

```bash
export OPENAI_API_KEY="sk-xxx"

scruticell parse GSE334035 \
  --llm-model "gpt-4o" \
  --llm-base-url "https://api.openai.com/v1"
```

---

## 许可证

Scrut-Cell 提供两个版本：

| 版本 | 许可证 | 功能 |
|------|--------|------|
| **开源版** | MIT，免费 | Layer 1 规则管道 + SQLite KB |
| **企业版** | 订阅 | Layer 2 智能管道 + Neo4j KB + MCTS |

企业版许可证通过环境变量设置：

```bash
export SCRUTICELL_LICENSE="your-org-name-a1b2c3d4-ENT-SCRUTICELL"
```

---

## 常见问题

### Q: 需要安装 Neo4j 吗？

A: 开源版不需要。规则管道（`parse`）和企业版除外的智能管道步骤（`inspect`/`plan`/`execute`）都可以在无 Neo4j 的情况下运行，只是无法使用知识图谱的匹配累积功能。

### Q: 如何修改存储路径？

A: 编辑 `src/scruticell/config.py` 中的 `DATA_DIR` 变量：

```python
DATA_DIR = Path("G:/scruticell/data")  # 改为任意路径
```

Neo4j 的数据路径在 `conf/neo4j.conf` 中：

```ini
server.directories.data=G:\\scruticell\\neo4j\\data
```

### Q: 嵌入模型有多大？

A: 首次运行 PDF RAG 功能时会自动下载 `all-MiniLM-L6-v2`（约 80 MB），存储在 `HF_HOME` 环境变量指定的目录（默认 `~/.cache/huggingface`）。建议设置 `export HF_HOME=G:\huggingface_cache` 避免占用 C 盘。

### Q: 支持哪些 LLM 提供商？

A: 任何 OpenAI-compatible API。目前工具默认使用 DeepSeek。设置环境变量 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY`，或通过 `--llm-api-key` 参数指定。

### Q: 处理失败怎么办？

A: 
1. 查看 `audit_*.json` 日志文件了解错误详情
2. 使用 `--verbose` / `-v` 查看完整日志
3. 规则管道下 `--max-retries` 可增加重试次数
4. 智能管道下 `execute` 命令默认开启反射重试（`--reflect`）
5. 检查下载缓存：`G:\scruticell\data\<GSE_ID>\`

### Q: 支持哪些物种？

A: 目前支持人类（Homo sapiens）和小鼠（Mus musculus）。基因 ID 的标准化基于 `bionty` 数据库，理论上支持 bionty 覆盖的全部物种。

### Q: 输出文件很大怎么办？

A: 
- 默认 `--keep-data` 为 false，管道完成后会自动删除下载的文件和 .h5ad 输出文件，仅保留 output 目录（inspect.json、plan.json、execute.json）
- 如需保留输出 .h5ad，使用 `--keep-data` 参数
- 稀疏矩阵默认使用 scipy sparse CSR 格式存储

### Q: 支持从 SOFT 文件直接读取矩阵吗？

A: 不支持。规则管道需要下载 Supplementary files（如 .h5、.h5ad、.mtx 文件）。智能管道会从压缩包中递归解压查找矩阵文件。
