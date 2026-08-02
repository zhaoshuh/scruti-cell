# Plan: Unified Data Reading & Validation Layer

## 核心问题

现有 reader 分散在 5 个文件里，各自的检测逻辑独立且不全面。需要：
- 统一的读入前检测（header / delimiter / orientation / value type）
- 读入后验证（NaN / 负数 / 全零行 / 正确性）
- 跨文件一致性检查（同 GSE 多个文件 orientation 一致 / gene 列表对齐）
- 检测结果记录在 per-file metadata 中，供后续使用

---

## 一、读入前检测：Pre-Read Inspection

### 1.1 Header Detection（改进现有）

**现状问题**：
- 现有 CSV header 检测只看第一格是否为空 → 很多文件不适用
- features.tsv.gz v3 第三列的 "feature_type" 没有头 → token 引擎看不到它
- barcodes.tsv.gz 被误判为有 header（第一行是 barcode string 被匹配为 gene_name pattern）

**改进方案**：两层检测

层A：硬规则（特定格式已知规范）
| 文件类型 | 规范 | 强制 |
|---------|------|------|
| `features.tsv.gz` (10x) | 无 header | no_header=True |
| `barcodes.tsv.gz` (10x) | 无 header | no_header=True |
| `tissue_positions_list.csv` (v1 Visium) | 无 header | no_header=True |
| `tissue_positions_list.csv` (v2 Visium) | 有 header | check first cell |
| `filtered_contig_annotations.csv` (10x VDJ) | **有 header** (barcode,is_cell,contig_id,...) | check first cell |
| `peaks.bed` | 无 header（或有 comment 行 `#`/`track`） | skip comment lines |
| `.cov.gz` | 无 header | no_header=True |

层B：通用启发式（格式未知时）
```python
def detect_header(filepath, first_n_rows=30):
    # 取前N行
    rows = read_first_n_rows(filepath, first_n_rows)

    # 信号1：第一行数据类型 vs 第二行数据类型
    #   - 全是字符串 + 第二行有数字 → 第一行是 header
    #   - 第一行有数字 → 不是 header

    # 信号2：第一行的长度 vs 后续行的长度
    #   - 第一行比其他行明显长/短 → 是 header

    # 信号3：内容模式检测
    #   - 如果 70%+ 的值匹配 barcode pattern (16 ACGT chars) → 不是 header
    #   - 如果 70%+ 的值匹配 gene symbol / ENSG pattern → 不是 header
    #   - 如果包含 "gene_id", "barcode", "sample" → 是 header

    # 信号4：独特度
    #   - 第一行的 unique values 显著多于第二行 → 是 header
```

### 1.2 Delimiter Detection（改进现有）

**现状**：只检测 `,` `\t` `;`
**缺失**：空格分隔（MARS-seq）、固定宽度

**改进**：
```python
def detect_delimiter(filepath, sample_lines=10):
    candidates = {',': 0, '\t': 0, ';': 0, ' ': 0}
    for line in read_sample_lines(filepath, sample_lines):
        for delim in candidates:
            candidates[delim] += line.count(delim)

    # 空格检测特殊处理：如果空格数是 tab 的 5+ 倍才用空格
    if candidates[' '] > candidates['\t'] * 5 and candidates[' '] > candidates[','] * 5:
        return ' '
    # 否则选最多的非空格分隔符
    best = max({'逗号': candidates[','], 'tab': candidates['\t'], '分号': candidates[';']})
    ...
```

### 1.3 Orientation Detection（改进现有）

**现状**：`_is_gene_x_cell` 基于 gene_names 和 shape 匹配，但没有处理 `features.tsv.gz` 的行数 = 基因数 → 间接推方向。

**改进**：
```python
def detect_orientation(file_summary, companion_files):
    # 信号1：直接比 shape vs features/barcodes 文件中的行数
    features_rows = companion_file_row_count("features.tsv.gz")
    barcodes_rows = companion_file_row_count("barcodes.tsv.gz")
    if matrix_rows == features_rows and matrix_cols == barcodes_rows:
        return "gene_x_cell"  # correct, transpose needed for AnnData
    if matrix_rows == barcodes_rows and matrix_cols == features_rows:
        return "cell_x_gene"  # already cell×gene

    # 信号2：gene name pattern in first column vs first row
    # 信号3：value magnitude (genes usually > cells in count range)
```

### 1.4 Value Type Detection（新增）

```python
def detect_value_type(matrix_sample, file_type):
    """Detect what kind of values are in the matrix."""

    if all integers:
        if no negative values:
            return "raw_counts"
        else:
            return "integer_unknown"

    if all 0-1 range:
        return "methylation_beta"  # or binary

    if 0-10 range, dense around 0:
        return "log_normalized"

    if values proportional to library size, sum to ~1M:
        return "tpm_fpkm"

    if negative values present:
        return "normalized_with_negatives"  # e.g. SCTransform residuals

    return "unknown"
```

### 1.5 Companion File Validation（新增）

```python
def validate_10x_triplet(directory):
    """Check the 10x companion files are consistent."""
    barcodes_rows = count_lines(barcodes_file)
    features_rows = count_lines(features_file)
    matrix_shape = read_mtx_header(matrix_file)

    issues = []
    if matrix_shape[0] != features_rows:
        issues.append(f"MTX rows ({matrix_shape[0]}) != features lines ({features_rows})")
    if matrix_shape[1] != barcodes_rows:
        issues.append(f"MTX cols ({matrix_shape[1]}) != barcodes lines ({barcodes_rows})")

    return issues
```

---

## 二、读入后验证：Post-Read Validation

### 2.1 矩阵完整性检查

```python
class MatrixValidator:

    def validate(self, matrix, gene_names, cell_barcodes, file_summary):
        issues = []

        # NaN check
        if has_nan(matrix):
            issues.append("NaN values detected")

        # Negative values (inappropriate for raw counts)
        if file_summary.assay_type == "raw_counts" and has_negative(matrix):
            issues.append("Negative values in raw count matrix")

        # All-zero rows (genes with no expression)
        zero_gene_count = count_all_zero_rows(matrix)
        if zero_gene_count > len(gene_names) * 0.01:
            issues.append(f"{zero_gene_count} all-zero genes")

        # All-zero columns (empty cells)
        zero_cell_count = count_all_zero_columns(matrix)
        if zero_cell_count > 0:
            issues.append(f"{zero_cell_count} all-zero cells (possible empty droplets)")

        # Dimension mismatch
        if matrix.shape[0] != len(cell_barcodes):
            issues.append("Row count != cell barcode count")
        if matrix.shape[1] != len(gene_names):
            issues.append("Column count != gene count")

        return ValidationReport(issues=issues, quality_score=...)
```

### 2.2 Gene ID 一致性检查

```python
def validate_gene_ids(gene_names, expected_species):
    issues = []
    detected_system = detect_gene_id_system(gene_names[:1000])

    if detected_system == "ensembl":
        # Check for version suffixes
        has_versions = any("." in g for g in gene_names[:100])
        if has_versions:
            issues.append("Ensembl IDs contain version suffixes (.XX)")
        # Check species prefix
        if expected_species == "Mus musculus" and any(g.startswith("ENSG") for g in gene_names[:100]):
            issues.append("Human Ensembl IDs (ENSG) found in mouse dataset")

    # Duplicate genes
    dups = find_duplicates(gene_names)
    if dups:
        issues.append(f"{len(dups)} duplicate gene names")
        # This is expected for probe-level data — not always an error

    return issues
```

### 2.3 跨文件一致性（同 GSE 多文件）

```python
def validate_cross_file_consistency(per_file_results):
    """Check consistency across multiple matrix files in the same GSE."""

    # Check all orientations consistent
    orientations = {r.orientation for r in per_file_results}
    if len(orientations) > 1:
        issue("Mixed orientations detected across files")

    # Check gene overlap
    gene_sets = [set(r.gene_names) for r in per_file_results]
    pairwise_overlap = [len(a & b) / max(len(a), len(b)) for a, b in ...]
    if min(pairwise_overlap) < 0.8:
        issue("Low gene overlap across matrix files")

    # Check barcode uniqueness across files
    all_barcodes = [bc for r in per_file_results for bc in r.cell_barcodes]
    if len(all_barcodes) != len(set(all_barcodes)):
        issue("Duplicate cell barcodes across files")
```

---

## 三、ReadResult：统一读取产物

```python
class ReadResult(BaseModel):
    """Complete result of reading + validating a matrix file."""

    # ── The data ──
    matrix: Any = None
    cell_barcodes: list[str] = []
    gene_names: list[str] = []     # 基因/CpG/peak/蛋白 — depends on modality
    is_sparse: bool = False
    shape: tuple = (0, 0)

    # ── Detection results ──
    header_detected: bool = False
    header_names: list[str] = []
    delimiter: str = ""
    orientation_detected: str = ""    # "gene_x_cell" | "cell_x_gene" | "unknown"
    orientation_source: str = ""      # "companion_files" | "heuristic" | "manual"
    value_type: str = ""              # "raw_counts" | "log_normalized" | "tpm" | "methylation_beta" | ...
    gene_id_system: str = ""          # "ensembl" | "symbol" | "entrez" | ...
    barcode_format: str = ""

    # ── Validation ──
    validation_issues: list[str] = []
    quality_score: float = 1.0        # 0-1, lower = more issues

    # ── Provenance ──
    reader_used: str = ""             # "h5ad" | "mtx" | "csv" | "rds" | "loom" | "methylation" | ...

    # ── Intra-file structure ──
    companion_files: list[str] = []   # for 10x triplets
    n_features_in_companion: int = 0  # features.tsv.gz line count
    n_barcodes_in_companion: int = 0  # barcodes.tsv.gz line count
```

---

## 四、Reader 统一入口

```python
def read_matrix_file(filepath, expected_modality=None) -> ReadResult:
    """Unified reading entry point. Detects format, reads, validates."""

    # Step 1: Format detection
    fmt = detect_format(filepath)  # uses Inspector's file_type + extensions

    # Step 2: Reader dispatch
    reader_map = {
        "h5ad": read_h5ad,
        "h5": read_h5ad,      # 10x HDF5
        "mtx": read_mtx,
        "csv": read_csv_matrix,
        "rds": read_rds,
        "loom": read_loom,
        "cov": read_methylation_cov,
    }
    raw_result = reader_map[fmt](filepath)

    # Step 3: Post-read validation
    result = ReadResult(**raw_result, reader_used=fmt)
    result = validate_read(result, expected_modality)

    # Step 4: Auto-correct if possible
    if result.orientation_detected == "gene_x_cell":
        result.matrix = result.matrix.T
        result.orientation_detected = "cell_x_gene"
        result.orientation_source = "auto_corrected"

    return result
```

---

## 五、集成到 inspect_tool

现有的 `inspect_files` 只做轻量指纹。改为：对每个数据文件（不是噪音文件），调用 `read_matrix_file` 的 **pre-read 检测部分**（不加载全矩阵），注入到 `file_signal_groups[].content_preview`。

```python
# 在 inspect_tool 中：
if is_data_file(file_summary):
    pre_read = pre_read_inspect(filepath, file_summary)
    # pre_read 是一个轻量 ReadResult（matrix=None，只含检测结果）
    content_preview.orientation_hint = pre_read.orientation_detected
    content_preview.value_type = pre_read.value_type
    content_preview.header_detected = pre_read.header_detected
```

完整读取发生在 Layer 1/2（用户执行 `scruticell run` 或 `scruticell standardize` 时）。

---

## 六、改动清单

| 文件 | 内容 |
|------|------|
| `preprocessing/readers/validator.py`（新建） | Pre-read 检测 + Post-read 验证 + 跨文件一致性 |
| `preprocessing/readers/__init__.py` | 新增 `read_matrix_file` 统一入口 |
| `models.py` | 新增 `ReadResult` 模型 |
| `tools/inspect_tool.py` | 集成 pre-read 检测到 content_preview |
| `context/summary_grouper.py` | content_preview 增加 pre-read 字段 |
| 现有 readers (h5ad/mtx/csv/rds/loom) | 不改，保持现有逻辑。validator 是包装层 |
| 现有 `alignment/barcodes.py` | 不改，复用 `detect_barcode_format` |
| 现有 `alignment/genes.py` + `knowledge/gene_mapping.py` | 不改，复用 `detect_gene_id_system` |

---

## 七、处理边界

| 边界情况 | 处理 |
|---------|------|
| 方阵（基因数=细胞数） | 标记 `orientation=ambiguous`，不自动转置，交给 Layer 2 LLM 决定 |
| 所有 reader 都失败 | 标记 `format=unreadable`, 记录 error |
| 10x 三件套缺少一个文件 | 标记 `companion_missing`, 允许单独读 mtx（无基因名/barcode） |
| 混合分隔符文件 | 按行采样，逐行检测，选多数行的分隔符 |
| 非常大的文件 (>5GB) | 不预读，标记 `size=too_large`，让 Layer 2 处理 |
| 二进制格式 (h5ad/RDS/Loom) | 不做 pre-read 内容检测，只读结构信息 |
| 列数/行数变化（CSV 不同行列数不等） | 标记 `malformed_csv` |

---

> 注：完整的矩阵加载和校正仍由 Layer 1/2 的现有 pipeline 处理。此层专注于 **检测 + 报告**，让 inspect 阶段的 LLM 有足够信息判断文件正确性。
