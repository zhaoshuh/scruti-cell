# Plan: Field-Value Mapping Optimization for Token Efficiency

## 问题

当前 FileContextReport 按 GSM 逐条列出所有 metadata + 文件信号 + 文件摘要，大量冗余：

```json
// TOKEN WASTE — 50 GSM each with 10x triplet (150 files total)
{
  "gsm_entries": [   // 50 entries, mostly identical field values
    {"gsm_id": "GSM001", "molecule": "total RNA", "tissue": "lung", ...},
    {"gsm_id": "GSM002", "molecule": "total RNA", "tissue": "lung", ...},
    ...
  ],
  "per_file_signals": [  // 150 entries, but only 3 unique signal patterns
    {"ancestry": "GSM001/barcodes.tsv.gz", "file_type": "csv", ...},
    {"ancestry": "GSM001/features.tsv.gz", "file_type": "csv", ...},
    {"ancestry": "GSM001/matrix.mtx.gz",  "file_type": "mtx", ...},
    {"ancestry": "GSM002/barcodes.tsv.gz", ...},  // identical to above!
    ...
  ],
  "file_summaries": {  // 150 entries with detailed Inspected output
    "GSM001/matrix.mtx.gz": {"mtx_shape": [3000,20000], ...},
    "GSM002/matrix.mtx.gz": {"mtx_shape": [3100,20000], ...},  // nearly identical
    ...
  }
}
```

50 GSM × 150 files → ~112K chars → ~28K tokens。每个 LLM 调用都在重复消费。

## 完整方案：12 层去重优化

---

### L1 — 元数据去重 (field_value_maps) ✅

逐 GSM 的 characteristics → 去重映射表

```json
{
  "field_value_maps": {
    "tissue": {"lung": ["GSM001","GSM002"], "liver": ["GSM003"]},
    "disease": {"cancer": ["GSM001","GSM003"], "normal": ["GSM002"]}
  }
}
```

**节省**：50 GSM × 8 字段 → 8 字段 × 3 值/字段 = ~90%

---

### L2 — 文件信号去重 (file_signal_groups) ← 新发现

150 条 FileSignals 只有 3 种独特模式（barcodes/features/matrix）。按信号指纹分组：

```json
{
  "file_signal_groups": [
    {
      "pattern": "10x_barcodes",
      "representative": {FileSignals},  // 一份代表性信号
      "file_count": 50,
      "gsm_ids": ["GSM001",...,"GSM050"],
      "ancestry_pattern": "{GSM}/barcodes.tsv.gz"
    },
    {
      "pattern": "10x_features",
      "representative": {FileSignals},
      "file_count": 50,
      "gsm_ids": ["GSM001",...,"GSM050"],
      "ancestry_pattern": "{GSM}/features.tsv.gz"
    },
    {
      "pattern": "10x_matrix", 
      "representative": {FileSignals},
      "file_count": 50,
      "gsm_ids": ["GSM001",...,"GSM050"],
      "ancestry_pattern": "{GSM}/matrix.mtx.gz"
    }
  ]
}
```

**关键**：每个 group 保留 `gsm_ids`（GSM→文件关联不被去重破坏）。

**节省**：150 条 → 3 条 = ~98%

---

### L3 — 文件摘要去重 (file_summary_groups) ← 新发现

Inspector 的详细摘要按文件类型分组，只报告范围：

```json
{
  "file_summary_groups": {
    "barcodes": {"count": 50, "format": "csv", "n_rows": "2870-3210"},
    "features": {"count": 50, "format": "csv", "n_rows": "32000-33000"},
    "matrices": {"count": 50, "format": "mtx", "shape_range": [2000,30000]},
    "h5ad_files": {"count": 2, "format": "h5ad", "h5_shape": [[15000,20000]]},
    "other_files": {"count": 3, "formats": ["pdf","txt","xlsx"]}
  }
}
```

**节省**：150 条详细摘要 → 5 组汇总 ≈ 90%

---

### L4 — 证据搜集上下文剪枝 ← 新发现

Main Agent 触发 evidence 时传完整 FileContextReport（28K tokens）。Evidence 只需要：
- gse_id + title + pubmed_id + organism  
- 矛盾信号摘要（"metadata 说是 Bisulfite-Seq 但文件看起来像 RNA-seq"）

→ Main agent prompt 增加规则：**传递给 evidence 的内容不超过 500 chars**

**节省**：28K → 0.5K = 98%

---

### L5 — 噪音文件过滤 ← 新发现

`per_file_signals` 包含 PDF/TXT/MD 等非数据文件。对分类无帮助。

→ 在传给 classifier 前过滤：file_type ∉ {"other"} 且包含数据信号（n_rows > 0 或 h5_shape 或 mtx_shape）

**节省**：~10%（非数据文件占比）

---

### L6 — 缓存存储剪枝 ← 新发现

当前缓存存储完整分类结果（含每个文件的路径列表）。缓存命中时加载回 context 消耗 token。

→ 缓存只存 `{sample_groups: [{label, gsm_ids, modalities: {name: confidence}}]}`，不存文件映射

**节省**：缓存命中时的加载 token ≈ 95%

---

### L7 — 协议/技术字段自动跳过

`extract_protocol_ch1`、`library_construction_protocol_ch1` 等数百字技术描述，所有 GSM 完全一样但被逐条复制。

→ field_value_maps 构建时：字段名匹配 `protocol|construction|library_preparation|data_processing` → 全局只保留一份代表值

---

### L8 — Main Agent 的 deepagent 上下文传递 ← 最深发现

**deepagent 的设计约束**：sub-agent 有独立 context window，看不到 main agent 的对话历史。Main agent 调用 classifier 时必须把所有输入放在 `task()` 的 description 参数中。

这意味着 FileContextReport 被 **隐式复制**：出现在 file-prep 的返回值中 → 又被 main agent 复制到 classifier 的 task description 中。

**如果不做 L1-L3 优化**：28K token 的报告被复制 2 次 = 56K token 浪费。

**做 L1-L3 后**：500 token 的紧凑报告被复制 2 次 = 1K token。不再构成问题。

→ Main agent prompt 增加：**"Pass the compact FileContextReport directly — do not summarize or expand it."**

---

### L9 — Main Agent 思考冗余 ← 最深发现

Main agent 在审阅 FileContextReport 后会在 reasoning 中复述内容：
"The report shows tissue=lung for GSM001-050, disease=cancer for 30 GSM..."

这些 token 无法完全消除（LLM 需要"思考输出"），但可以减少：

→ Main agent prompt 增加：**"Be concise. Reference sections by name (e.g., 'field_value_maps shows 2 tissue types'). Do not repeat full content."**

**节省**：~30-50% reasoning token

---

### L10 — Summarization middleware 保护 ← 最深发现

deepagent 有内置 summarization middleware。当对话超过 context 限制时，自动摘要历史。

如果报告过大被摘要 → 关键细节丢失 → classifier 基于不完整信息分类 → 错误。

→ 紧凑报告（500 tokens）永远不触发 summarization → 保证信息完整

**这不是 token 节省，而是正确性保障**。

---

### L11 — Evidence 查询从 field_value_maps 构建 ← 新发现

Evidence gathering 的 PubMed 查询应利用去重后的精确值：

```python
# 之前：用 gse_id + 乱取的 metadata 关键词
# 之后：从 field_value_maps 取覆盖最广的 clinical 字段值
def build_evidence_query(gse_info, field_value_maps):
    top_tissue = most_frequent_value(field_value_maps["tissue"])
    top_organism = field_value_maps.get("organism", {}).most_common()
    return f"{gse_id} {top_tissue} {top_organism} single-cell sequencing"
```

→ Evidence prompt 更新：**"Use field_value_maps to identify the most distinctive values for your query"**

---

### L12 — GSM→文件关联保护 ← 最深发现

L2 的文件信号分组可能破坏 GSM→文件映射。Classifier 需要知道"GSM001 有哪些文件"才能正确分组。

→ 每个 `file_signal_group` 保留 `gsm_ids` 和 `ancestry_pattern`，使得 classifier 能推断完整的 GSM→文件映射，而不需要逐条列出。

---

## 优化前 vs 优化后（50 GSM, 10x triplet）

### 优化前 FileContextReport

```
gsm_entries:          50 条 × 150 chars  =  7,500 chars
per_file_signals:    150 条 × 200 chars  = 30,000 chars
file_summaries:      150 条 × 500 chars  = 75,000 chars
─────────────────────────────────────────────────
合计                                     = 112,500 chars  (~28,000 tokens)
× 2 (main agent 复制到 classifier)       = 56,000 tokens
× 1.5 (main agent reasoning 复述)        = 14,000 tokens
─────────────────────────────────────────────────
总计                                     ≈ 70,000 tokens
```

### 优化后 FileContextReport

```
field_value_maps:     8 字段 × 3 值     =    600 chars
file_signal_groups:   3 组 × 200 chars  =    600 chars
file_summary_groups:  5 组 × 100 chars  =    500 chars
gse_info:                                 =    500 chars
─────────────────────────────────────────────────
合计                                     =  2,200 chars  (~550 tokens)
× 2 (main agent 复制到 classifier)       =  1,100 tokens
× 1.2 (简洁 reasoning)                   =    200 tokens
─────────────────────────────────────────────────
总计                                     ≈  1,300 tokens

节省: 98%
```

---

## 数据模型新增

```python
class ValueGroup(BaseModel):
    value: str
    gsm_ids: list[str]
    gsm_count: int

class FieldValueMap(BaseModel):
    field_name: str
    category: str         # clinical / sample / experimental / technical
    values: list[ValueGroup]
    distinct_count: int

class FileSignalGroup(BaseModel):
    pattern: str                                    # e.g. "10x_barcodes"
    representative: FileSignals
    file_count: int
    gsm_ids: list[str]                              # preserves GSM→file linkage
    ancestry_pattern: str                           # e.g. "{GSM}/barcodes.tsv.gz"

class FileSummaryGroup(BaseModel):
    pattern: str                                    # e.g. "matrices"
    count: int
    format: str
    shape_range: dict[str, int] | None = None       # {"min_cells": 2000, "max_cells": 5000}
    is_sparse: bool = False
    representative_ancestry: str = ""

class FileContextReport(BaseModel):
    gse_id: str
    gse_info: dict
    # Compact metadata (replaces per-GSM gsm_entries when GSM > _THRESHOLD)
    field_value_maps: dict[str, FieldValueMap]
    # Compact file signals (replaces per-file per_file_signals)
    file_signal_groups: list[FileSignalGroup]
    # Compact file summaries (replaces detailed file_summaries dict)
    file_summary_groups: dict[str, FileSummaryGroup]
    # Fallback: full per-GSM entries (only present when GSM ≤ _GSM_THRESHOLD=5)
    gsm_entries: list[dict]
    total_files: int
    total_gsm_count: int
    errors: list[str]
```

---

## 实施顺序 (更新版)

| 步骤 | 文件 | 内容 | 对应层级 |
|------|------|------|---------|
| 1 | `models.py` | 新增 `ValueGroup`, `FieldValueMap`, `FileSignalGroup`, `FileSummaryGroup`；更新 `FileContextReport` | L1-L3 |
| 2 | `context/field_mapper.py` | 新建：`build_field_value_maps()` | L1, L7 |
| 3 | `context/signal_grouper.py` | 新建：`group_file_signals()` | L2, L12 |
| 4 | `context/summary_grouper.py` | 新建：`group_file_summaries()` | L3 |
| 5 | `tools/inspect_tool.py` | 集成 L1-L3 去重逻辑；噪音文件过滤 | L1-L5 |
| 6 | `prompts.py` | 更新 4 套 prompt：Classifier/File-prep 接受紧凑格式；Evidence 查询优化；Main agent 简洁指令 | L4, L8-L9, L11 |
| 7 | `agent.py` | 更新 MAIN_AGENT 行为指令 | L4, L8-L9 |
| 8 | `subagents/file_prep.py` | 更新 system_prompt 输出紧凑格式 | — |
| 9 | `memory/modality_cache.py` | 缓存只存分类决策 + fingerprint | L6 |

## 风险/边界

- **少量 GSM（≤5）**：保留 `gsm_entries` fallback → 逐条格式更直观
- **字段值完全唯一**（如 barcode/patient_id）：field_mapper 标记 "non-deduplicable" → 跳过
- **多格式混合数据集**：file_signal_groups 按 `(file_type, content_signal_fingerprint)` 分组 → 可能产生 5-10 组而不是 3 组，仍是大幅压缩
- **L2 的 GSM→文件映射**：`file_signal_groups` 必须保留 `gsm_ids` 和 `ancestry_pattern` → classifier 据此推导完整映射
