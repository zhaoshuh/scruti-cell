# Plan: File Metadata Extraction + Attribution System

## 核心问题

GEO 文件的归属是匹配问题——把"从文件里能观察到的标识"与"SOFT 元数据里已知的 GSM 标识"做匹配。

四种观察来源 + 五种已知来源 = 匹配系统。

---

## 一、观察维度：从文件里能提取什么

### 来源 A：文件名

不只是提取 GSM ID。按分隔符深度拆解：

```
文件: Patient1_Treated_Day7_counts.csv
      └─ tokens: ["Patient1", "Treated", "Day7", "counts"]
      └─ 语义匹配:
           Patient1 → donor_id: possible value: "Patient1"
           Treated  → treatment: "treated"
           Day7     → timepoint: "Day7"
           counts   → file_role: "matrix"
```

### 来源 B：列名

```
列名: P1_Treated_Day7
      └─ tokens: ["P1", "Treated", "Day7"]
      └─ 语义匹配:
           P1       → sample_id: "P1"
           Treated  → treatment
           Day7     → timepoint
```

50 个列只需要存储：有哪些唯一 token pattern（P1-P5 × Treated/Control × Day0/Day7 → 20 种组合而不是 50 列逐列存储）。

### 来源 C：列值（分类变量）

```
列名: "sample_id"
列值: [P1, P1, P1, ..., P2, P2, ..., P3, P3, ...]
      └─ unique_values: ["P1", "P2", "P3"]
      └─ frequencies: {"P1": 5000, "P2": 3000, "P3": 4000}
      └─ matched to:
           field_value_maps["donor_id"] = {"P1": ["GSM001"], "P2": ["GSM002"], "P3": ["GSM003"]}
           → 结论: P1 → GSM001, P2 → GSM002, P3 → GSM003
```

**关键设计：不存 12000 行的行级映射。只存值→GSM 的映射表（3 行）。** 下游处理时读 CSV 逐行应用。

### 来源 D：Barcode/行索引

```
barcode 前缀: ["PatientA_Treated_", "PatientB_Control_", "PatientC_Treated_"]
      └─ 对每个前缀做语义解析（复用与列名相同的 ColumnMeta 模型）
      └─ 提取出: PatientA/Treated, PatientB/Control, PatientC/Treated
      └─ matched to field_value_maps
```

---

## 二、已知维度：SOFT 元数据里有什么可匹配的

| 来源 | 信息 | 精确度 |
|------|------|--------|
| SOFT `supplementary_file_N` | 每个 GSM 明确标注了补充文件名 | 精确（filename → GSM） |
| GSM `characteristics` 解析后的值 | `donor_id: P1`, `sample_id: S01`, `tissue: liver` | 可匹配（token → GSM） |
| `!Sample_title` | "Sample 01 transcription profiling" | 中等（需 LLM 或 heuristic 解析） |
| GSM 数量 | N 个 GSM | 用于模式推断（1 file:N GSM → shared; N files:N GSM → 1:1） |
| `!Sample_organism` / `molecule` / `library_strategy` | 技术信息 | 不用于归属，用于模态分类 |

---

## 三、匹配策略：六层确定性 + LLM 兜底

| 层 | 方法 | 输入 | 输出 |
|----|------|------|------|
| L1 | SOFT `supplementary_file_N` | 文件名 | `gsm_ids` (精确) |
| L2 | 文件名正则 `GSM\d+` | 文件名 | `gsm_ids` (精确) |
| L3 | 文件名深度语义匹配 | `FileNameMeta.tokens` → field_value_maps | `gsm_ids` (模糊) |
| L4 | 列名语义匹配 | `ColumnMeta` tokens → field_value_maps | `column_mapping` |
| L5 | 列值匹配 | `ColumnMeta.unique_values` → field_value_maps | `value_mapping` |
| L6 | Barcode 前缀匹配 | `RowIndexMeta.prefix_meta` → field_value_maps | `prefix_mapping` |
| L7 | LLM 分类器 | FileMeta + field_value_maps + 未解决信号 | 最终归属 |

L1-L6 是确定性策略，产生初步 `AttributionResult`。未解决的 ambiguity 作为 context 交给 L7（Classifier LLM）。

---

## 四、归属结果的数据模型

核心认知：归属结果是**映射指令**，不是逐行/逐列的数据复制。下游处理时执行这些指令。

```python
class AttributionResult(BaseModel):
    """How a file maps to GSM(s). Downstream code executes these mappings."""
    ancestry: str
    source: str                       # L1-L7
    confidence: float

    # ── 文件级归属 ──
    file_gsm_ids: list[str] = []      # 整文件属于这些 GSM

    # ── 列级归属 ──
    column_mapping: dict[str, list[str]] = Field(default_factory=dict)
    # {"P1_Treated_Day7": ["GSM001"], "P2_Control_Day0": ["GSM003"]}

    # ── 值级归属（行级拆分） ──
    value_mapping: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    # {"sample_id": {"P1": ["GSM001"], "P2": ["GSM002"]}}

    # ── Barcode 前缀级归属 ──
    prefix_mapping: dict[str, list[str]] = Field(default_factory=dict)
    # {"PatientA_Treated_": ["GSM001"], "PatientB_Control_": ["GSM002"]}
```

### 三种映射的语义和下游处理

**1. `file_gsm_ids`**：整文件归属。有值时 `column_mapping` 和 `value_mapping` 应为空。

```python
# 下游: 
if attr.file_gsm_ids:
    # 整个文件 hardlink 到该 GSM 的目录
```

**2. `column_mapping`**：同一文件的不同列属于不同 GSM。

```python
# 下游:
df = pd.read_csv("combined_matrix.csv")
for col_name, gsm_ids in attr.column_mapping.items():
    if col_name in df.columns:
        subset = df[col_name]
        # 写入 gsm_ids[0] 的 AnnData
```

**3. `value_mapping`**：根据分类列的值拆分行。

```python
# 下游:
df = pd.read_csv("combined_matrix.csv")
column = list(attr.value_mapping.keys())[0]
for value, gsm_ids in attr.value_mapping[column].items():
    mask = df[column] == value
    subset = df[mask]
    # 这 5000 行 → gsm_ids[0] 的 AnnData
```

**4. `prefix_mapping`**：根据 barcode 前缀拆分。

```python
# 下游:
for prefix, gsm_ids in attr.prefix_mapping.items():
    mask = [bc.startswith(prefix) for bc in barcodes]
    subset = matrix[:, mask]
    # 这些细胞 → gsm_ids[0] 的 AnnData
```

---

## 五、FileMeta：每个文件的完整元数据画像

```python
class FileNameMeta(BaseModel):
    """All metadata parsed from filename tokens."""
    tokens: list[str]
    gsm_ids: list[str], sample_ids: list[str], donor_ids: list[str]
    treatments: list[str], timepoints: list[str], conditions: list[str]
    tissues: list[str], replicates: list[str]
    unrecognized: list[str]
    raw: str


class ColumnMeta(BaseModel):
    """Metadata from a column header OR a barcode prefix OR a unique value."""
    original: str
    tokens: list[str]
    # 语义标记（与 FileNameMeta 相同分类体系）
    sample_ids: list[str], donor_ids: list[str], treatments: list[str]
    timepoints: list[str], conditions: list[str], tissues: list[str]
    replicates: list[str]
    unrecognized: list[str]
    dtype: str                    # "str" | "int64" | "float64" | "mixed"


class ColumnSummary(BaseModel):
    """Summary for a single column (header + values)."""
    header_meta: ColumnMeta
    unique_count: int
    unique_values: list[str]    # 前 20 个唯一值 (分类变量) 或 [](连续变量)
    value_frequencies: dict[str, int]
    is_categorical: bool        # unique_count ≤ 30
    value_hints: list[dict]     # unique values 的语义匹配结果


class RowIndexMeta(BaseModel):
    """Metadata from row index / barcode / first column."""
    barcode_format: str
    prefixes: list[str]
    prefix_counts: dict[str, int]
    prefix_meta: list[ColumnMeta]   # 每个前缀的语义解析
    feature_id_type: str
    feature_samples: list[str]
    n_rows: int


class FileMeta(BaseModel):
    """Complete metadata portrait of a single file."""
    ancestry: str
    file_type: str
    filename: FileNameMeta
    columns: list[ColumnSummary]     # 最多 50 列
    row_index: RowIndexMeta
    companion_files: list[str]
```

---

## 六、归属匹配流程（extract → match → aggregate）

### Step 1: extract（per-file）

```
对每个文件:
  FileMeta = extract_file_meta(file_path, file_summary, content_preview)
  
  FileMeta.filename:
    - 按分隔符拆 tokens → 语义匹配 → FileNameMeta
    
  FileMeta.columns:
    - 对每个列 (最多 50 列):
      - 列名 → tokens → ColumnMeta
      - 列值采样 → unique_values, frequencies, is_categorical
      - 如果 is_categorical: 对 unique_values 做语义匹配
      
  FileMeta.row_index:
    - barcode_prefixes → 对每个 prefix 做语义匹配
    - feature_id_type 检测
```

### Step 2: match（per-file → per-GSM）

```
对每个文件:
  attr = AttributionResult(ancestry=...)
  
  # L1: SOFT supplementary 引用
  if filename in soft_suppl_refs:
      attr.file_gsm_ids = [soft_suppl_refs[filename]]
      attr.source = "soft_ref"
      attr.confidence = 1.0
      return attr
  
  # L2: 文件名正则 GSM ID
  if FileMeta.filename.gsm_ids:
      attr.file_gsm_ids = FileMeta.filename.gsm_ids
      attr.source = "filename_gsm"
      attr.confidence = 1.0
      return attr
  
  # L3: 文件名语义 → field_value_maps 匹配
  for token_category in [donor_ids, sample_ids]:
      for token in FileMeta.filename[token_category]:
          match_gsm = lookup_in_field_value_maps(token, field_value_maps)
          if match_gsm:
              attr.file_gsm_ids.append(match_gsm)
  
  # L4: 列名语义 → field_value_maps 匹配
  for col in FileMeta.columns:
      for token in col.header_meta.all_semantic_tokens():
          match_gsm = lookup_in_field_value_maps(token, field_value_maps)
          if match_gsm and col.header_meta.original not in attr.column_mapping:
              attr.column_mapping[col.header_meta.original] = [match_gsm]
  
  # L5: 列值语义 → field_value_maps 匹配
  for col in FileMeta.columns:
      if col.is_categorical:
          for val in col.unique_values:
              match_gsm = lookup_in_field_value_maps(val, field_value_maps)
              if match_gsm:
                  attr.value_mapping.setdefault(col.header_meta.original, {})[val] = [match_gsm]
  
  # L6: Barcode 前缀 → field_value_maps 匹配
  for pm in FileMeta.row_index.prefix_meta:
      for token in pm.all_semantic_tokens():
          match_gsm = lookup_in_field_value_maps(token, field_value_maps)
          if match_gsm and pm.original not in attr.prefix_mapping:
              attr.prefix_mapping[pm.original] = [match_gsm]
  
  return attr
```

### Step 3: aggregate（合并到 signal groups）

```
对每个 FileSignalGroup:
  归属 = 该 group 中每个文件的 AttributionResult 的聚合
  
  如果所有文件的 file_gsm_ids 相同 → 简单的 1:1 或 N:1 映射
  如果文件有 column_mapping → 标记需要列级拆分
  如果文件有 value_mapping → 标记需要行级拆分
  如果文件有 prefix_mapping → 标记需要 barcode 拆分
```

---

## 七、未归属文件的处理

L1-L6 全失败的文件 → `AttributionResult.file_gsm_ids = [], source = "none"`

Classifer LLM 收到这些信息：

```
## Unattributed files:
- File: raw_data.csv
  FileNameMeta: tokens=["raw", "data"], unrecognized=["raw", "data"]
  ColumnSummary: column "Unnamed_0" (int64, unique_count=12000)
  No semantic matches found at any level.

## Available context:
- field_value_maps: donor_id has 3 values mapped to GSM001-003
- file_signal_groups: 1 matrix file, 3 barcode files, 3 feature files
- GSM with files: GSM001, GSM002, GSM003 (each has barcodes+features+matrix triplet)
```

LLM 看到 mode：
> *"3 个 GSM 已经有各自的 barcodes+features+matrix。raw_data.csv 不属于任何已知模式。可能是未清洗的原始数据。归类为 unclassified。"*

或者：
> *"GSM count = 50, files = 50 matrix files named sample_01 through sample_50。虽然有 50 个文件且 50 个 GSM，但没有明确的 1:1 映射键。建议按文件名数字顺序对应 GSM 的 submission 顺序。"*

---

## 八、改动清单

| 文件 | 内容 |
|------|------|
| `models.py` | 新增 `FileMeta`, `FileNameMeta`, `ColumnMeta`, `ColumnSummary`, `RowIndexMeta`, `AttributionResult` |
| `context/file_meta.py`（新建） | 文件名深度解析、列名+值解析、barcode 前缀解析 |
| `context/attribution.py`（新建） | L1-L6 确定性匹配 + `compute_attribution()` |
| `context/semantic.py`（新建） | 统一的 token→语义匹配表（共享于 filename/column/barcode 解析） |
| `tools/inspect_tool.py` | 集成 file_meta 提取 → 调用 attribution → 注入 FileSignalGroup |
| `models.py` `FileSignalGroup` | 新增 `attribution: AttributionResult \| None` |
| `prompts.py` Classifier | 增加归属推理指令 + 未归属文件处理 |
| `organize_tool.py` | metadata.json 输出 attribution 映射 |

---

## 九、关键设计决策

1. **不写磁盘 meta 文件** — FileMeta 是内存结构，随 FileContextReport 流向 Classifier
2. **value_mapping 只存映射表，不存逐行分配** — P1→GSM001 (5000 rows)，不是 5000 个单独的 (row, GSM) 对
3. **L1-L6 确定性策略尽力而为，LLM 兜底** — 不确定的文件不做任何假设
4. **归属与模态分类同一个 Classifier 调用** — 它们互相依赖
5. **语义匹配表共享** — FileNameMeta, ColumnMeta, prefix_meta 用同一个匹配引擎
