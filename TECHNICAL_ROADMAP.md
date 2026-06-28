# Scrut-Cell 技术路线

## 一、项目定位

Scrut-Cell 是一个 GEO 单细胞数据标准化管道，将 NCBI GEO 的 GSE 编号一键转化为分析就绪的 AnnData 对象。  
核心价值：**把生物学家数小时的人工 curation 缩短至分钟级，且越用越准。**

---

## 二、系统架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI 入口 (click)                         │
│         parse | config | inspect | plan | execute | version     │
└──────────────────────────────┬──────────────────────────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        ▼                      ▼                      ▼
┌───────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  Layer 1      │    │  Layer 2        │    │  Layer 3        │
│  规则管道     │    │  智能管道       │    │  KB 持久化      │
│  (parse)      │    │  (inspect→plan  │    │  SQLite + Neo4j │
│               │    │   →execute)     │    │  双后端         │
└───────────────┘    └─────────────────┘    └─────────────────┘
```

### 2.0 输出文件结构

所有智能管道的输出默认统一写入 `output/<GSE_ID>/` 目录：

```
output/<GSE_ID>/
├── inspect.json      # M1 勘查报告
│   ├── gse_info:         GSE 标题/摘要/实验设计
│   ├── gsm_entries:      各 GSM 特征字段、补充文件列表
│   ├── file_tree:        递归解压后的文件族谱
│   ├── file_summaries:   每个文件的指纹（行列数/列名/数据预览/barcode 格式）
│   └── data_files:       候选矩阵文件列表
│
├── plan.json         # M2 处理方案
│   ├── matrices:        矩阵文件→格式→方向映射
│   ├── batches:         GSM→矩阵分组→细胞数映射
│   ├── field_mappings:  原始字段→规范字段 + 复合值拆分规则
│   ├── processing_code: LLM 生成的元数据标准化 Python 代码
│   ├── assembly_code:   LLM 生成的多样本合并代码
│   └── strategy_notes:  LLM 策略解释
│
├── execute.json      # M3 执行汇总
│   ├── gse_id:          GSE 编号
│   ├── success:         执行成功/失败
│   ├── output_path:     首个 .h5ad 文件路径
│   ├── cells / genes / batches:  关键统计
│   ├── steps:           每步执行状态及错误
│   ├── errors / warnings:  汇总
│   └── trace:           来源追踪文本
│
├── <GSE_ID>_<GSM_ID>_scruticell.h5ad   # 每个批次一个 AnnData
└── ...
```

> `-o` 参数可覆盖默认路径，兼容向后。

### 2.1 Layer 1: 规则管道 (`scruticell parse`)

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
    ▼
NCBI GEO FTP ──→ download_dir/ (GSE12345/)
    │
    ├── GSE12345_family.soft.gz  ──→ SOFT 解析 ──→ GSE/GSM 元数据
    │
    ├── GSM1234567_*.h5          ──→ 矩阵读取 ──→ 表达矩阵 (gene×cell)
    ├── GSM1234568_*.h5
    └── ...
    │
    ▼
细胞对齐 + 基因统一 + 批次标注
    │
    ▼
4 维置信度验证
    │
    ├── ≥ 0.85 ──→ output.h5ad (规则管道)
    │
    ├── < 0.85 ──→ reflexion (KG→LLM→MCTS) ──→ 重试
    │
     └── (智能管道)
          inspect ──→ output/<GSE_ID>/inspect.json (勘查报告)
          plan    ──→ output/<GSE_ID>/plan.json    (含 LLM processing_code)
          execute ──→ output/<GSE_ID>/execute.json (provenance + trace + stats)
                   ──→ output/<GSE_ID>/<GSE_ID>_<GSM>_scruticell.h5ad (每批次一个)
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
