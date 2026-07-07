# Fix: Matrix Format Normalization

## Problem

LLM 被指示输出的 format 字符串（`10x_mtx`, `h5`, `tsv`, `metadata_csv`）与下游 `_read_matrix()` 能识别的格式（`h5ad`, `mtx`, `csv`, `rds`, `loom`）不一致。executor 只对 `h5→h5ad` 做了单向映射，`10x_mtx` 未经映射直接传给 dispatch 导致 `Unknown matrix format: 10x_mtx`。

## Changes (3 files)

### 1. `src/scruticell/intelligence/executor.py` (lines 783-804)

在 reader_path 检查之后、fallback dispatch 之前，添加格式归一化映射。

```python
# 替换这行:
read_fmt = "h5ad" if fmt == "h5" else fmt

# 替换为:
_FORMAT_NORMALIZE = {
    "10x_mtx": "mtx",
    "10x": "mtx",
    "h5": "h5ad",
    "tsv": "csv",
    "metadata_csv": "csv",
}
fmt = _FORMAT_NORMALIZE.get(fmt, fmt)
```

reader_path 分支中 `fmt in ("10x_mtx", "10x")` 的检查不受影响（在归一化之前执行）。

### 2. `src/scruticell/intelligence/extractors.py`

#### 2a. `READER_BATCH_SYSTEM_PROMPT` (lines 98-131)

更新分组规则和输出 schema 中的 format 值：

| 所在行 | 原文 | 改为 |
|--------|------|------|
| L100 | `format="10x_mtx"` | `format="mtx"` |
| L103 | `format="metadata_csv"` | `format="csv"` |
| L104 | `format="h5"` | `format="h5ad"` |
| L125 | `"10x_mtx\|csv\|tsv\|h5\|metadata_csv"` | `"h5ad\|mtx\|csv"` |

#### 2b. `STEP5B_USER` (lines 451-471)

更新矩阵 format 说明和输出 schema 示例：

| 所在行 | 原文 | 改为 |
|--------|------|------|
| L453 | `"h5" for 10X .h5 files, "10x_mtx" for 10X mtx+barcode+features group, "mtx" for standalone mtx, "csv" for csv/tsv` | `"h5ad" for .h5ad and 10X .h5 files, "mtx" for mtx+barcode+features group or standalone mtx, "csv" for csv/tsv` |
| L471 | `"format": "h5"` | `"format": "h5ad"` |

### 3. `src/scruticell/intelligence/planner.py` (line 18)

更新 `MatrixMapping.format` 的 docstring：

```python
# 原文:
format: str  # "h5", "h5ad", "mtx", "csv", "rds", "loom"

# 改为:
format: str  # canonical: "h5ad", "mtx", "csv", "rds", "loom" — LLM outputs are normalized in executor
```

## Effect

| LLM 原始 format | 归一化后 | 命中分支 |
|----------------|----------|----------|
| `10x_mtx` | `mtx` | `if fmt == "mtx"` → 自动检测 companion 文件 |
| `10x` | `mtx` | 同上 |
| `h5` | `h5ad` | `_read_matrix(filepath, "h5ad")` |
| `tsv` | `csv` | `_read_matrix(filepath, "csv")` |
| `metadata_csv` | `csv` | `_read_matrix(filepath, "csv")` |
