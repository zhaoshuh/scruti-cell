# Omics Storage Specification (v1.0)

Scrut-Cell 预处理输出的统一存储规范。定义每种组学模态的目录结构、文件格式、元数据 schema、和 Python 加载接口。

---

## 设计原则

| # | 原则 | 说明 |
|---|------|------|
| 1 | **每种模态独立** | 转录组 AnnData，TCR CSV，空间图像 PNG。各自原生格式，不互相转换 |
| 2 | **一个模态一个子目录** | 互不干扰 |
| 3 | **metadata.json 统一层** | 所有模态共享同一套元数据 schema |
| 4 | **不存在的文件不创建** | 一个 CITE-seq 样本就没有 atac 子目录 |
| 5 | **`scruticell.load()` 一行加载** | 隐藏目录结构，返回 Python 对象 |
| 6 | **复用现有 pipeline** | AnnData 输出来自 Layer 1/2，preprocess 负责文件分离 |

---

## 目录结构

```
output/{GSE}/preprocessed/{group_id}/
│
├── metadata.json                   # ← 统一元数据
│
├── scrna_seq/                      # 单细胞转录组
│   ├── matrix.h5ad
│   └── files.json
│
├── snrna_seq/                      # 单核转录组
│   └── matrix.h5ad
│
├── scatac_seq/                     # 单细胞 ATAC
│   ├── matrix.h5ad
│   ├── peaks.bed                   # (如有)
│   ├── fragments.tsv.gz            # (如有)
│   └── files.json
│
├── spatial/                        # 空间转录组
│   ├── matrix.h5ad
│   ├── tissue_positions_list.csv
│   ├── tissue_hires_image.png
│   ├── tissue_lowres_image.png
│   ├── scalefactors_json.json
│   └── files.json
│
├── dna_methylation/                # DNA 甲基化
│   └── matrix.h5ad
│
├── cite_seq/                       # CITE-seq (抗体蛋白 + 转录组)
│   ├── rna_matrix.h5ad
│   ├── adta_matrix.h5ad
│   └── files.json
│
├── tcr_seq/                        # T 细胞受体免疫组库
│   ├── filtered_contig_annotations.csv
│   ├── clonotypes.csv              # (如有)
│   └── files.json
│
├── bcr_seq/                        # B 细胞受体免疫组库
│   ├── filtered_contig_annotations.csv
│   ├── clonotypes.csv              # (如有)
│   └── files.json
│
├── perturb_seq/                    # CRISPR 筛选
│   ├── matrix.h5ad
│   ├── guide_info.csv
│   └── files.json
│
├── proteomics/                     # 蛋白质组学
│   └── matrix.h5ad
│
├── multiome/                       # 10x Multiome (RNA+ATAC 同细胞)
│   ├── rna_matrix.h5ad
│   ├── atac_matrix.h5ad
│   └── files.json
│
└── other/                          # 未知/未分类（兜底）
    └── files.json                  # （原始文件不放这里，只放清单）
```

---

## 各模态文件清单

### 转录组 (scrna_seq / snrna_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | gene×cell 矩阵 |
| `files.json` | JSON | `json.load()` | 原始来源文件清单 |

两者区别：`obs['suspension_type']` = `"cell"` vs `"nucleus"`。

### 单细胞 ATAC (scatac_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | peak×cell 稀疏矩阵 |
| `peaks.bed` | TSV | `pd.read_csv(sep='\t')` | peak 注释 |
| `fragments.tsv.gz` | TSV.gz | `pd.read_csv(compression='gzip')` | fragment 文件 (10x 输出) |

`matrix.h5ad` 的 `var` 包含 `chrom`, `start`, `end` 三列。

### 空间转录组 (spatial)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | gene×spot 矩阵 |
| `tissue_positions_list.csv` | CSV | `pd.read_csv()` | barcode + 坐标 |
| `tissue_hires_image.png` | PNG | `PIL.Image.open()` | 高分辨率组织图像 |
| `tissue_lowres_image.png` | PNG | `PIL.Image.open()` | 低分辨率组织图像 |
| `scalefactors_json.json` | JSON | `json.load()` | 像素→微米转换参数 |

### DNA 甲基化 (dna_methylation)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | CpG×cell 稀疏矩阵 |

`var` 包含 `chrom`, `start`, `end`, `context` (CpG/CHG/CHH) 四列。`X` 为甲基化比率 (0-1) 或覆盖度计数值。极稀疏，必须 CSR 格式。

### CITE-seq (cite_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `rna_matrix.h5ad` | AnnData | `sc.read_h5ad()` | gene×cell 矩阵 |
| `adta_matrix.h5ad` | AnnData | `sc.read_h5ad()` | antibody×cell 矩阵 |

RNA 和 ADT 来自同一批细胞（barcode 完全对应）。`adta_matrix.h5ad` 的 `var.index` 是抗体名 (CD3, CD4 等)。两个文件各自带完整的 `obs`。

### TCR 免疫组库 (tcr_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `filtered_contig_annotations.csv` | CSV | `pd.read_csv()` | 10x VDJ 的 contig 注释 |
| `clonotypes.csv` | CSV | `pd.read_csv()` | clonotype 聚类结果 |

CSV 列符合 AIRR 标准：`barcode`, `v_gene`, `d_gene`, `j_gene`, `cdr3`, `cdr3_nt`, `chain`。`clonotypes.csv` 含有 `clonotype_id`, `frequency`。

### BCR 免疫组库 (bcr_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `filtered_contig_annotations.csv` | CSV | `pd.read_csv()` | 10x VDJ 的 contig 注释 |
| `clonotypes.csv` | CSV | `pd.read_csv()` | clonotype 聚类结果 |

同 TCR，增加 `isotype` 列。链类型为 IGH/IGK/IGL。

### CRISPR 筛选 (perturb_seq)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | gene×cell 矩阵 |
| `guide_info.csv` | CSV | `pd.read_csv()` | sgRNA→基因映射表 |

`obs['guide_assignment']` 存储每个细胞的 sgRNA 标签。`guide_info.csv` 列：`sgRNA_id`, `target_gene`, `sequence`。

### 蛋白质组学 (proteomics)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `matrix.h5ad` | AnnData | `sc.read_h5ad()` | protein×cell 矩阵 |

`var.index` 是蛋白 ID (UniProt)。`var` 列包含 `protein_id`, `gene_name`。

### 10x Multiome (multiome)

| 文件 | 格式 | Python 读取 | 说明 |
|------|------|-----------|------|
| `rna_matrix.h5ad` | AnnData | `sc.read_h5ad()` | gene×cell 矩阵 |
| `atac_matrix.h5ad` | AnnData | `sc.read_h5ad()` | peak×cell 矩阵 |

同一批细胞 (barcode 完全对应)。两个 AnnData 各自独立，`obs` 完全相同。

---

## metadata.json schema

```json
{
  "gse_id": "GSE256646",
  "group_label": "GSM8112031",
  "gsm_ids": ["GSM8112031"],

  "sample_metadata": {
    "organism": "Mus musculus",
    "donor_id": "M1",
    "tissue": "kidney",
    "disease": "normal",
    "sex": "male",
    "development_stage": "8-week-old stage",
    "cell_type": null
  },

  "modalities": {
    "scrna_seq": {
      "assay": "10x 3' v3",
      "suspension": "cell",
      "n_cells": 5150,
      "n_features": 26686,
      "files": [
        {"path": "scrna_seq/matrix.h5ad", "role": "matrix", "format": "h5ad"}
      ],
      "source_files": [
        {"role": "barcodes", "path": "GSM8112031/barcodes.tsv.gz"},
        {"role": "features", "path": "GSM8112031/features.tsv.gz"},
        {"role": "matrix",   "path": "GSM8112031/matrix.mtx.gz"}
      ]
    },

    "scatac_seq": {
      "assay": "10x scATAC-seq",
      "suspension": "nucleus",
      "n_cells": 3840,
      "n_features": 104522,
      "files": [
        {"path": "scatac_seq/matrix.h5ad",     "role": "matrix",    "format": "h5ad"},
        {"path": "scatac_seq/peaks.bed",        "role": "peaks",     "format": "bed"},
        {"path": "scatac_seq/fragments.tsv.gz", "role": "fragments", "format": "tsv.gz"}
      ],
      "source_files": [...]
    },

    "tcr_seq": {
      "assay": "10x V(D)J",
      "suspension": "cell",
      "n_cells": null,
      "n_features": null,
      "files": [
        {"path": "tcr_seq/filtered_contig_annotations.csv", "role": "contigs",     "format": "csv"},
        {"path": "tcr_seq/clonotypes.csv",                   "role": "clonotypes",  "format": "csv"}
      ],
      "source_files": [...]
    }
  }
}
```

### 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `gse_id` | str | GEO accession |
| `group_label` | str | 样本组标签 (文件名安全) |
| `gsm_ids` | list[str] | 包含的 GSM ID |
| `sample_metadata` | dict | 该组统一元数据。所有值可为 null |
| `sample_metadata.*` | str\|null | organism, donor_id, tissue, disease, sex, development_stage, cell_type |
| `modalities.<name>` | dict | 该组包含的模态，key 为 12 种模态标签之一 |
| `modalities.<name>.assay` | str | 测序 assay |
| `modalities.<name>.suspension` | str | cell / nucleus / na |
| `modalities.<name>.n_cells` | int\|null | 细胞 / spot 数量 |
| `modalities.<name>.n_features` | int\|null | 特征数量 |
| `modalities.<name>.files` | list | 该模态目录下的文件清单 |
| `modalities.<name>.files[].path` | str | 相对路径 (从 group 目录起) |
| `modalities.<name>.files[].role` | str | matrix / barcodes / features / peaks / fragments / contigs / clonotypes / spatial_positions / spatial_image / guide_info / other |
| `modalities.<name>.files[].format` | str | h5ad / csv / bed / tsv.gz / png / json |
| `modalities.<name>.source_files` | list | 原始 GEO 下载文件路径 (provenance) |

---

## `scruticell.load()` API

```python
import scruticell as scell

# ── 加载整个样本组 ──
group = scell.load("output/GSE215353/preprocessed/group_GSM6833846/")

# SampleGroup 属性
group.label               # "GSM6833846"
group.gsm_ids             # ["GSM6833846"]
group.sample_metadata     # dict
group.modalities          # ["scrna_seq", "dna_methylation"]

# 按名字访问模态
group.scrna_seq.matrix          # AnnData
group.scrna_seq.n_cells         # 34722

group.scatac_seq.matrix         # AnnData
group.scatac_seq.peaks          # DataFrame | None
group.scatac_seq.fragments      # DataFrame | None

group.spatial.matrix            # AnnData
group.spatial.positions         # DataFrame | None
group.spatial.image_hires       # PIL.Image | None
group.spatial.image_lowres      # PIL.Image | None
group.spatial.scalefactors      # dict | None

group.dna_methylation.matrix    # AnnData

group.cite_seq.rna_matrix       # AnnData
group.cite_seq.adta_matrix      # AnnData

group.tcr_seq.contigs           # DataFrame
group.tcr_seq.clonotypes        # DataFrame | None

group.bcr_seq.contigs           # DataFrame
group.bcr_seq.clonotypes        # DataFrame | None

group.perturb_seq.matrix        # AnnData
group.perturb_seq.guide_info    # DataFrame

group.proteomics.matrix         # AnnData

group.multiome.rna_matrix       # AnnData
group.multiome.atac_matrix      # AnnData

# ── 只加载一个模态 ──
rna = scell.load_modality("output/.../group_GSM001/scrna_seq/")
#> ModalityData(modality="scrna_seq", matrix=AnnData, n_cells=34722)

tcr = scell.load_modality("output/.../group_GSM001/tcr_seq/")
#> ModalityData(modality="tcr_seq", contigs=DataFrame, clonotypes=None)

# ── 批量扫描 ──
groups = scell.list_groups("output/GSE215353/preprocessed/")
#> [SampleGroupInfo(label="GSM001", modalities=["scrna_seq","dna_methylation"]), ...]
```

### 实现要点

```python
class ModalityData:
    """一个模态的数据容器。只加载已存在的文件。"""
    modality: str
    assay: str = ""
    n_cells: int | None = None
    n_features: int | None = None
    # 按需出现的属性（不存在的文件 = None）
    matrix: AnnData | None = None
    peaks: pd.DataFrame | None = None
    fragments: pd.DataFrame | None = None
    positions: pd.DataFrame | None = None
    image_hires: Image.Image | None = None
    image_lowres: Image.Image | None = None
    scalefactors: dict | None = None
    contigs: pd.DataFrame | None = None
    clonotypes: pd.DataFrame | None = None
    guide_info: pd.DataFrame | None = None
    rna_matrix: AnnData | None = None       # CITE-seq / Multiome
    adta_matrix: AnnData | None = None      # CITE-seq
    atac_matrix: AnnData | None = None      # Multiome


class SampleGroup:
    """一个样本组 (一个或多个 GSM)。"""
    label: str
    gsm_ids: list[str]
    sample_metadata: dict
    metadata: dict            # metadata.json 的原始内容

    @property
    def modalities(self) -> list[str]:
        """返回该组包含的模态标签列表。"""


def load(path: str) -> SampleGroup:
    """加载预处理后的样本组。基于 metadata.json 自动发现文件。"""


def load_modality(path: str) -> ModalityData:
    """只加载一个模态目录。"""


def list_groups(path: str) -> list[dict]:
    """列出预处理输出中的所有样本组及其模态。"""
```

---

## 与现有 pipeline 的对接

```
preprocess (Layer 0)
    → 下载+解压 → 按样本×模态分离文件 → 输出 files.json

standardize / run (Layer 1/2)
    → 读取 files.json 中的文件路径
    → 矩阵模态：生成 matrix.h5ad (复用现有 AnnData 输出)
    → CSV 模态：硬链接 raw 文件到目标位置
    → 图像文件：硬链接
    → 生成 metadata.json
    → metadata 从 SOFT 元数据 + LLM 分类结果中提取
```

### 各模态已有覆盖

| 模态 | 状态 |
|------|------|
| scRNA / snRNA | ✅ 现有 pipeline 已输出 AnnData |
| ATAC | ✅ MTX 格式可走现有 reader |
| Spatial | ✅ MTX 格式可走现有 reader，图像需硬链接 |
| Multiome | ✅ 两个 MTX/H5 可走现有 reader |
| CITE-seq | ✅ 两个 MTX/H5 可走现有 reader |
| Methylation | 🆕 需要 cov→AnnData 转换 |
| TCR / BCR | ✅ 无需转换，硬链接 CSV |
| Perturb-seq | ✅ matrix.h5ad 走现有 reader + 硬链接 guide CSV |
| Proteomics | 🆕 需要专门 reader (极少出现) |

---

## 数据模型 (models.py 新增)

```python
class ModalityFileEntry(BaseModel):
    path: str                    # 相对路径 (从模态目录起)
    role: str                    # matrix/barcodes/features/peaks/...
    format: str                  # h5ad/csv/bed/tsv.gz/png/json

class ModalityManifest(BaseModel):
    assay: str = ""
    suspension: str = ""
    n_cells: int | None = None
    n_features: int | None = None
    files: list[ModalityFileEntry] = Field(default_factory=list)
    source_files: list[ModalityFileEntry] = Field(default_factory=list)

class GroupMetadata(BaseModel):
    gse_id: str = ""
    group_label: str = ""
    gsm_ids: list[str] = Field(default_factory=list)
    sample_metadata: dict = Field(default_factory=dict)
    modalities: dict[str, ModalityManifest] = Field(default_factory=dict)
```

---

## 实施顺序

| Phase | 步骤 | 文件 |
|-------|------|------|
| 0 | 数据模型 | `preprocessing/models.py` 新增 `ModalityFileEntry`, `ModalityManifest`, `GroupMetadata` |
| 0 | Python loader | `preprocessing/loader.py` — `load()`, `load_modality()`, `list_groups()` |
| 1 | 更新 `organize_tool.py` | 按新目录结构输出文件 + metadata.json |
| 1 | 验证 scRNA/ATAC | 用现有 pipeline 验证 matrix.h5ad 输出 |
| 2 | Spatial/CITE-seq/Multiome | 图像硬链接、配对文件处理 |
| 3 | Methylation/TCR/BCR/Perturb-seq | 特殊格式处理 + 硬链接 |
| 4 | Python loader 扩展 | CSV/图像/peaks/fragments 读取 |

---

> 版本: 1.0 | 日期: 2026-07-09 | 关联: plan-omics-preprocessing.md, plan-field-value-mapping.md
