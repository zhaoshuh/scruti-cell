# Scrut-Cell 技术路线

## 一、项目定位

Scrut-Cell 是一个 GEO 单细胞数据标准化管道，将 NCBI GEO 的 GSE 编号一键转化为分析就绪的 AnnData 对象。  
核心价值：**把生物学家数小时的人工 curation 缩短至分钟级，且越用越准。**

---

## 二、系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI 入口 (click)                         │
│    preprocess | parse | inspect | plan | execute | config       │
└──────────────────────────────┬──────────────────────────────────┘
                               │
         ┌─────────────────────┼──────────────────────┐
         ▼                     ▼                      ▼
┌──────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Layer 0         │  │  Layer 1        │  │  Layer 2        │
│  组学预处理      │  │  规则管道       │  │  智能管道       │
│  (preprocess)    │  │  (parse)        │  │  (inspect→plan  │
│  LLM Agent       │  │  固定规则 DAG   │  │   →execute)     │
│  信号收集→LLM判决│  │                 │  │                 │
└──────────────────┘  └─────────────────┘  └─────────────────┘
```

### 2.0 输出文件结构

所有智能管道的输出默认统一写入 `output/<GSE_ID>/` 目录：

```
output/<GSE_ID>/
├── preprocessed/                  # Layer 0: 组学分离结果
│   ├── manifest.json              #   预处理清单 (含 sample_groups 逻辑分组)
│   └── <sample_dir>/              #   样本目录 (GSM ID 或推断复合 key)
│       ├── metadata.json          #   样本元数据 (含 gsm_ids, sample_key, key_source)
│       └── <modality>/            #   模态子目录
│           ├── files.json         #   文件清单 (role→path)
│           └── ... (hard link)
├── inspect.json      # M1 勘查报告
├── plan.json         # M2 处理方案
├── execute.json      # M3 执行汇总
├── <GSE_ID>_<GSM_ID>_scruticell.h5ad   # 每个批次一个 AnnData
└── ...
```

> `-o` 参数可覆盖默认路径，兼容向后。

### 2.0 Layer 0: 组学预处理 Agent (`scruticell preprocess`)

基于 `deepagents` 多 Agent 架构，自动下载、解压、检查并分离 GEO 数据到 **样本 × 模态** 的目录树。不进行下游分析——纯数据组织。

#### 2.0.1 整体架构

```
CLI: scruticell preprocess GSE_ID
         │
         ▼
┌────────────────────────────────────────────┐
│  Main Orchestrator Agent (agent.py)         │
│  Model: deepseek-chat (temperature=0.0)      │
│  工具: organize_output                       │
│  子 Agent: file-prep, classifier, evidence   │
└────────────────────────────────────────────┘
         │
         ├──(1)──► file-prep Sub-Agent
         │           下载 → 解压 → 检查 → FileContextReport
         │
         ├──(2)──► evidence Sub-Agent (可选，仅信号冲突时)
         │           PubMed 搜索 → PDF RAG → EvidenceReport
         │
         ├──(3)──► classifier Sub-Agent
         │           样本分组 → 模态分类 → 文件归属 → JSON
         │
         └──(4)──► organize_output 工具
                    构建预处理目录树 (hard link)
```

#### 2.0.2 子 Agent 详细设计

**File Prep Sub-Agent (`subagents/file_prep.py`)**

| 项目 | 说明 |
|------|------|
| 工具 | `download_gse`, `decompress_files`, `inspect_files` |
| 输出 | 紧凑 `FileContextReport` (token 优化) |
| 核心函数 | `inspect_files()` → 协调全部信号提取层 |

`inspect_files()` 内部流程：

```
对所有文件循环:
  1. Inspector.inspect_file() → 文件指纹
  2. extract_metadata_signals() → GSM 元数据信号
  3. extract_filename_signals() → 文件名 + 目录结构信号
  4. extract_content_signals() → 文件内容信号 (特征ID类型/条形码格式)
  5. 构建 FileSignals 对象

紧凑优化:
  L1: build_field_value_maps()        → metadata 字段→值→GSM 去重
  L2+L3: group_file_signals()         → 按信号指纹分组
         └ per_file_gsm_id            → ancestry→GSM 提取 (无推断)
         └ build_content_preview()    → 附加实际数据预览
  L5: 过滤噪声文件 (.pdf/.docx/.png 等)

归因信号收集 (无决策):
  compute_attribution(file_meta):
    L0: ancestry 路径提取 GSM ID       → candidates
    L1: SOFT supplementary_file_N 匹配 → candidates
    Signals: filename/column/barcode tokens → signals dict
```

**Classifier Sub-Agent (`subagents/classifier.py`)**

| 项目 | 说明 |
|------|------|
| 工具 | **无** — 纯 LLM 推理 |
| Prompt | 运行时从 `skills/modality_knowledge/SKILL.md` 注入 |
| 输入 | `FileContextReport` (紧凑格式) |
| 输出 | 紧凑 JSON (GSM 去重) |

分类规则 (均在 prompt 中，无 Python 决策)：

```
1. 文件→样本归属 (1:1 约束)
   优先级:
   (a) 信任 per_file_gsm_id (ancestry 路径提取)
   (b) 使用 attribution.candidates (SOFT ref)
   (c) attribution.signals 交叉匹配 field_value_maps
   (d) 推断复合 key (格式: field1_value__field2_value)

2. 共享文件检测
   同一 ancestry 在 per_file_gsm_id 中对应多个 GSM → 共享文件
   列入 shared_file_groups，organize 时 hard link 到所有组

3. Demux 文件检测
   列头或 barcode 前缀匹配不同样本 token → demux 文件
   标注 has_demux: true + 拆分规则

4. 样本分组
   基于 field_value_maps: 字段值差异 = 不同实验分组
   相同 key + 模态的组合并 (GSM 去重)

5. 模态判定
   参考 SKILL.md 模态定义 + content_preview 数据样本
   置信度 ≥ 0.85 当 metadata + filename + content 信号一致
```

**Evidence Sub-Agent (`subagents/evidence.py`)**

| 项目 | 说明 |
|------|------|
| 工具 | `search_gse_papers`, `read_paper_methods` |
| 触发条件 | metadata 信号矛盾 (classifier 无法确定) |
| 工作流 | PubMed 搜索 → 读摘要 → 如需要 RAG PDF Methods 章节 |
| 输出 | EvidenceReport (classifier 优先信任论文证据) |

**Organize Output (`tools/organize_tool.py`)**

| 项目 | 说明 |
|------|------|
| 输入 | classification_json (classifier 输出) |
| 功能 | 构建 `output/<GSE_ID>/preprocessed/` 目录树（按样本组织：`<sample_dir>/<modality>/`） |
| 独占文件 | hard link 到对应 sample_group/modality 目录 |
| 共享文件 | 同一源文件 hard link 到所有 ref_by_gsm 组 |
| Demux 拆分 | `handle_demux_split()` — LLM code-gen → sandbox → verify → repair → fallback |

#### 2.0.3 质量保证

**RubricMiddleware (实时质量门禁)**
- 每次 classifier 输出后，grader 子 Agent 对照 rubric 评估
- 调用 `verify_classification_tool()` 获取确定性结构检查结果
- `needs_revision` → 反馈注入 Agent 循环 → 重新委托 classifier
- `satisfied` → 通过
- 默认 max_iterations=3

**Verification + Repair (事后审计)**
- `verify_extraction_results()` — 覆盖率/空结果/字段数量
- `verify_demux_results()` — 文件存在/非空/sample_id 数量
- `verify_classification()` — GSM 覆盖率/重叠/modality 缺失/demux_rule 完整性
- `run_with_repair()` — code-gen → execute → verify → retry → fallback 通用模板

**Checkpoint (会话恢复)**
- `MemorySaver` checkpointer — 单次 `invoke()` 内的 LLM 调用失败时恢复
- `config={"configurable": {"thread_id": gse_id}}` — per-GSE 线程隔离

**Code Cache (代码复用)**
- `modality_cache.code_cache` — SQLite 复合主键 `(fingerprint, code_type)`
- `store_code_cache()` / `find_code_cache()` — 缓存 extraction_code / flowchart / demux_code
- 相同 GSE 指纹 → 跳过 LLM 代码生成

#### 2.0.4 关键模型

| 模型 | 作用 |
|------|------|
| `SampleKey` | 归属 key（GSM ID 或推断复合 key），key↔sample_group 1:1 双射 |
| `AttributionResult` | 纯信号收集：`candidates` (L0+L1) + `signals` (token) |
| `FileSignalGroup` | 同指纹文件组，含 `per_file_gsm_id` + `content_preview` |
| `ModalityDecision` | 模态判定，含 `has_shared/has_demux/demux_rule/demux_content_preview` |
| `DemuxRule` | 拆分规则：`method` + `identifier_pattern` + `value_column` + `barcode_column` |
| `CompactSampleGroup` | 样本组，含 `sample_key/key_source/file_prefix` |
| `CompactClassificationResult` | 分类器输出，含 `shared_file_groups` |
| `SharedFileGroup` | 跨样本共享文件 (ancestry + ref_by_gsm) |
| `VerificationCheck` / `VerificationReport` | 验证结果模型（passed + checks + repair_attempts + fallback） |

#### 2.0.4 模态知识 Skill 化

模态定义不再硬编码在 Python 中，而是单一来源：

```
skills/modality_knowledge/SKILL.md   ← 模态定义 (标题/元数据信号/文件模式/内容信号/平台)
prompts.py: build_classifier_prompt() ← 运行时加载 + 注入 prompt 模板
context/metadata.py: _MODALITY_KEYWORDS  ← 元数据关键词 (新增模态时同步更新)
context/filenames.py: _KEYWORD_PATTERNS  ← 文件名关键词 (新增模态时同步更新)
```

新增/修改模态只需编辑 `SKILL.md`，无需改 Python 代码。关键词检测 dict 需同步更新以提供更强的信号强度。

#### 2.0.5 缓存层

```python
modality_cache.py:
  store_classification()      → MD5(gse_id + field_values + signal_patterns) → 决策 JSON
  find_similar_classification() → 指纹匹配 → 跳过 LLM 调用
  clear_cache()                → 清空所有缓存
```

缓存仅存决策 (sample_groups + modalities)，不存文件列表 (organize 阶段从本地文件系统重建)。

#### 2.0.6 核心设计原则

| 原则 | 说明 |
|------|------|
| **Python 收集信号，LLM 做判决** | `attribution.py` 只做 L0+L1 确定性提取。所有 token 匹配、共享/demux 检测、key 推断均由 classifier prompt 驱动 |
| **1:1 文件归属** | 每个文件归属唯一 key。key ↔ sample_group 双射。不允许一个文件属于多个样本 |
| **模态 Skill 化** | `SKILL.md` 是单一真相来源，运行时注入 prompt |
| **内容指纹缓存** | 相同 GSE 只需一次 LLM 调用 |

基于 LangGraph 的 10 节点 DAG，全程使用固定规则，无需 LLM。

```
download → parse_soft → discover_matrix → read_matrix
                            │                   │
          literature ───────┘                   │
                                                │
     align_cells → unify_genes → validate → output
                                                    │
                              [fail] → reflexion → parse_soft
```

**节点职责：**

| 节点 | 职责 |
|------|------|
| `download` | 从 NCBI GEO FTP 下载 SOFT 文件和补充数据 |
| `parse_soft` | 解析 SOFT 文件，提取 GSE/GSM 元数据、特征字段 |
| `literature` | （并行）从 PubMed 搜索相关文献，提取实验上下文 |
| `discover_matrix` | 在补充文件中搜索表达矩阵（优先 h5ad > mtx > csv > rds > loom） |
| `read_matrix` | 读取矩阵文件，自动检测方向（gene×cell / cell×gene）并转置 |
| `align_cells` | 检测并标准化细胞 barcode 格式（10x v2/v3、Drop-seq 等） |
| `unify_genes` | 统一基因 ID（symbol/ensembl/entrez），去重、标准化 |
| `validate` | 4 维置信度验证（barcode、基因、元数据、批次） |
| `reflexion` | 置信度低于阈值时触发：KG→LLM→启发式三层自修正 |
| `output` | 写入 .h5ad 文件到 `-o` 指定目录（默认 cwd） |

**Reflexion 三层自修正策略：**

```
1. KG 查找：在知识图谱中搜索相似失败案例及其修复策略
2. LLM 诊断：LLM 分析错误并生成策略 JSON（强制转置/重新解析字段/调整批次等）
3. 启发式规则：关键词匹配（dimension→转置, batch→GSM标题, barcode→去前缀）
4. MCTS 兜底：retry_count ≥ 2 时，用 UCB1 搜索最佳策略模板组合
```

### 2.2 Layer 2: 智能管道 (`scruticell inspect → plan → execute`)

三步工作流，LLM 深度参与。核心理念：**勘查→规划→执行**。

```
            ┌──────────┐
   inspect  │ Reporter │  勘查：下载、解压、文件指纹
            └────┬─────┘
                 │ GseReport JSON
            ┌────▼─────┐
   plan      │Orchestra│  规划：KB匹配 + LLM生成代码
            └────┬─────┘
                 │ ProcessingPlan JSON
            ┌────▼─────┐
   execute  │ Executor │  执行：安全沙箱执行+失败反省
            └──────────┘
```

**M1 — 勘查 (Inspector/Reporter)：**

- `Decompressor`：递归解压 `.tar.gz` / `.zip`，生成文件族谱树
- `Inspector`：6 类文件指纹（h5/mtx/csv/rds/loom/其他），含 barcode 前缀分析
- `Reporter`：生成 `GseReport` JSON，包含文件树、内容摘要、GSM 元数据

**M2 — 规划 (PlanOrchestrator)：**

1. **指纹提取**：从 GseReport 提取 `GseFingerprint`（物种、矩阵格式、GSM 数、字段名）
2. **KB 匹配**：`StrategyMatcher` 在知识图谱中搜索相似案例（相似度 ≥ 0.5）
3. **文献增强**：`LiteratureEnricher` 从 PubMed 搜索相关文献，提取生物上下文
4. **LLM 规划生成**：`PlanGenerator` 调 LLM 生成 `ProcessingPlan`，包含：
   - `matrices`：矩阵文件→格式→方向映射
   - `batches`：GSM→矩阵分组→细胞数映射
   - `field_mappings`：原始字段名→规范字段名，含复合值拆分规则
   - `processing_code`：Python 函数（仅用 pd/np/re），处理元数据标准化
   - `assembly_code`：Python 函数（可用 scanpy），处理多样本合并
5. **规划验证**：`PlanValidator` 执行 6 条结构检查 + 3 轮 LLM 修正

**M3 — 执行 (PlanExecutor/ExecutionMonitor)：**

- `PlanExecutor`：按 9 步顺序执行规划（读矩阵→建 AnnData→对齐→统一基因→字段映射→自定义代码→验证→输出）
- LLM 生成代码在安全沙箱内执行（受限 namespace，仅 pd/np/re/scanpy）
- 输出 AnnData，每个批次一个文件：`output/<GSE_ID>/<GSE_ID>_<batch_label>_scruticell.h5ad`
- `ExecutionMonitor`：失败时自动反省，最多 2 轮自动重试（LLM 修正 plan + 重新执行）
- 最终执行汇总写入 `output/<GSE_ID>/execute.json`（合并 provenance + trace + 统计）

### 2.3 Layer 3: KB 持久化

双后端设计，根据 license tier 自动切换：

| 后端 | License | 功能 |
|------|---------|------|
| LocalKnowledgeBase (SQLite) | 开源版 | 错误签名存储、策略查找 |
| KnowledgeGraph (Neo4j) | 企业版 | MERGE upsert、success_count/fail_count 累计、相似性搜索 |

核心功能：
- **策略累积**：每次成功的处理策略存入 KB，相同指纹的数据集第二次可秒级复用
- **失败学习**：失败案例 + 修正策略存入 KB，后续同类错误自动应用修复
- **排序复用**：策略按 `success_count` 降序排列，越成功的策略优先级越高

---

## 三、核心模块

### 3.1 矩阵读取器 (`scruticell.readers`)

支持 6 种格式的矩阵读取：

| 格式 | 实现 | 说明 |
|------|------|------|
| `.h5ad` / `.h5` | `readers/h5ad.py` | AnnData / 10X HDF5 直接读取 |
| `.mtx` / `.mtx.gz` | `readers/mtx.py` | 10X 三件套（barcodes + features + matrix） |
| `.csv` / `.tsv` | `readers/csv_matrix.py` | 逗号/制表符分隔，自动推断分隔符和列名行 |
| `.rds` / `.RData` | `readers/rds.py` | 通过 `pyreadr` 读取 R 格式 |
| `.loom` | `readers/loom.py` | 通过 `loompy` 读取 |

所有 reader 返回统一结构：`{"matrix", "cell_barcodes", "gene_names", "shape", "is_sparse"}`

### 3.2 字段解析器 (`scruticell.parsing`)

- `soft.py`：GEOparse 封装，解析 SOFT 格式的 GSE/GSM 元数据
- `field_resolver.py`：77 条字段同义映射表（如 `"tissue"` ↔ `"tissue type"` ↔ `"tissue_source"`）
- `metadata.py`：将 GSM characteristics 的 key:value 解析为结构化字典

### 3.3 对齐模块 (`scruticell.alignment`)

- `barcodes.py`：检测 barcode 格式（10x v2 16bp、v3 16bp、Drop-seq 12bp 等），标准化为统一格式
- `genes.py`：基因 ID 映射（symbol↔ensembl↔entrez），基于 `bionty` 和自建映射表

### 3.4 知识模块 (`scruticell.knowledge`)

- `graph.py`：KnowledgeGraph (Neo4j) / LocalKnowledgeBase (SQLite) 双后端实现
- `schema.py`：Neo4j 图 schema 定义（FailureCase 节点 + 关系）
- `gene_mapping.py`：基因符号标准化（大小写、后缀清理、版本号剥离）
- `field_synonyms.py`：字段同义映射的编程接口

### 3.5 MCTS 搜索引擎 (`scruticell.mcts`)

- `node.py`：MCTS 树节点定义
- `search.py`：UCB1 策略搜索，从 12 个策略模板中组合最优方案
- `evaluate.py`：策略评估函数（置信度改善 + 执行成本）

### 3.6 文献增强 (`scruticell.literature`)

- `pubmed.py`：NCBI E-utilities API 封装，搜索/获取 PubMed 文献
- `extractor.py`：11 组正则规则 + LLM，从论文摘要中提取实验元信息
- `pdf_agent.py`：**PDF RAG 引擎**，下载论文 PDF → PyMuPDF 提取文本 → 按段落切块 (500 字符) → all-MiniLM-L6-v2 本地向量化 → Cosine 相似度检索 Top 3 段落 → LLM 定向回答缺失字段。按 PMID 缓存 30 天
  - 触发条件：Step 2/3 缩写置信度 < 0.7、文件名拆解信心 < 0.7、或摘要提取结果稀疏 (< 3 字段)
  - Token 节省：仅检索 Top 3 段落 (~1500 token) 而非全文，比全文 LLM 省 ~92%

### 3.7 许可证系统 (`scruticell.license`)

- `is_enterprise()`：检测环境变量 `SCRUTICELL_LICENSE`，企业版含有特定后缀
- 开源版默认使用 SQLite 后端，企业版自动切换 Neo4j

---

## 四、配置系统

通过环境变量或运行时参数覆盖：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_MODEL` | `deepseek-chat` | 大语言模型名称 |
| `LLM_TEMPERATURE` | `0.0` | 确定性输出（管道场景） |
| `LLM_BASE_URL` | `https://api.deepseek.com` | LLM API 地址 |
| `LLM_API_KEY` | `$DEEPSEEK_API_KEY` 或 `$OPENAI_API_KEY` | API 密钥 |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j 地址 |
| `NEO4J_USER` / `NEO4J_PASSWORD` | `neo4j` / `scruticell` | Neo4j 认证 |
| `DATA_DIR` | `G:/scruticell/data` | 下载缓存目录 |
| `CONFIDENCE_THRESHOLD_AUTO` | `0.85` | 置信度高于此值自动通过 |
| `CONFIDENCE_THRESHOLD_WARN` | `0.60` | 低于此值触发 reflexion |
| `DOWNLOAD_TIMEOUT` | `600s` | FTP 下载超时 |
| `DOWNLOAD_RETRIES` | `3` | FTP 重试次数 |

---

## 五、技术栈

| 层次 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 工作流引擎 | LangGraph (DAG) |
| LLM 接口 | LangChain + OpenAI-compatible API |
| 单细胞数据 | AnnData + Scanpy |
| GEO 解析 | GEOparse |
| 数值计算 | NumPy + SciPy + Pandas |
| 图数据库 | Neo4j (Python driver) |
| R 格式读取 | pyreadr |
| Loom 格式 | loompy |
| PDF 提取 | PyMuPDF (fitz) |
| 本地嵌入 | sentence-transformers (all-MiniLM-L6-v2) |
| CLI 框架 | Click + Rich |
| 数据验证 | Pydantic |
| 进度条 | tqdm |
| 异步 HTTP | httpx |
| 基因数据库 | bionty |
| 生物信息 | biopython |
| 测试 | pytest + pytest-asyncio + pytest-cov |
| 代码检查 | ruff + mypy |
| 构建 | hatchling |

---

## 六、数据流

```
GSE accession
    │
    ├──[Layer 0: preprocess]
    │   ├── Download + decompress
    │   ├── SOFT 解析 → GSE/GSM 元数据
    │   ├── 文件检查 → 信号指纹分组
    │   ├── LLM classifier → sample × modality JSON
    │   ├── organize → 预处理目录树 (hard link)
    │   └── modality_cache (SQLite 指纹缓存)
    │
    ├──[Layer 1: parse]
    │   ├── NCBI GEO FTP → download_dir (GSE12345/)
    │   ├── GSE12345_family.soft.gz → SOFT 解析 → GSE/GSM 元数据
    │   ├── GSM*.h5/mtx/csv → 矩阵读取 → 表达矩阵 (gene×cell)
    │   ├── 细胞对齐 + 基因统一 + 批次标注
    │   ├── 4 维置信度验证
    │   ├── ≥ 0.85 → output.h5ad
    │   └── < 0.85 → reflexion (KG→LLM→MCTS) → 重试
    │
    └──[Layer 2: inspect→plan→execute]
        ├── inspect → output/<GSE_ID>/inspect.json
        ├── plan    → output/<GSE_ID>/plan.json (LLM processing_code)
        └── execute → output/<GSE_ID>/execute.json
                    → output/<GSE_ID>/<GSE>_<batch>_scruticell.h5ad
```

---

## 七、当前版本与状态

- **版本**：v0.1.0
- **开发阶段**：M0 — 技术验证
- **已验证数据集**：GSE334035（1 个，需扩展到 20 个做批量基线测试）
- **下一步**：批量测试 20 个不同结构的数据集，建立通过率基线

---

## 八、待解决的技术债

| 严重程度 | 问题 | 预计修复 |
|---------|------|---------|
| 高 | 仅完整测试 1 个数据集，无批量基线 | 2-3 天 |
| 高 | 文献参照 PubMed 网络未跑通 | 1 天 |
| 中 | Neo4j 启动偶尔锁文件残留 | 0.5 天 |
| 低 | 嵌入模型 (all-MiniLM-L6-v2) 首次下载 80MB，需网络 | 无需修复 |
| 低 | SQLite/Neo4j 双后端查询字段不完全一致 | 1 天 |

### 存储路径

所有运行时数据统一存储在 `G:\scruticell\` 下；管道输出默认写入项目 `output/` 目录：

| 目录 | 内容 |
|------|------|
| `G:\scruticell\data\` | GEO 下载缓存 + SQLite knowledge.db |
| `G:\scruticell\cache\pdf\` | PDF RAG 文献提取缓存 |
| `G:\scruticell\neo4j\data\` | Neo4j 图数据库文件 |
| `output/<GSE_ID>/` | 智能管道输出（inspect.json / plan.json / execute.json / .h5ad） |
