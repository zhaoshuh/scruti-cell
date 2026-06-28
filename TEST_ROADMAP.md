# Scrut-Cell 测试路线与 Benchmark 方案

---

## 目录

1. [已有单元测试](#0-已有单元测试)
2. [测试目标](#1-测试目标)
3. [测试维度](#2-测试维度)
4. [数据集选择矩阵（30 个）](#3-数据集选择矩阵)
5. [数据集获取指南](#4-数据集获取指南)
6. [自动化测试脚本设计](#5-自动化测试脚本设计)
7. [Benchmark 对比方案](#6-benchmark-对比方案)
8. [评分标准](#7-评分标准)
9. [报告模板](#8-报告模板)
10. [执行计划](#9-执行计划)

---

## 0. 已有单元测试

**总计: 195 个测试，全部通过**

| 测试文件 | 数量 | 覆盖内容 |
|---------|------|---------|
| `tests/test_literature.py` | 39 | 文献提取：平台检测、批次数、归一化、细胞数、物种、组织、barcode、疾病、基因组等 11 组正则规则 |
| `tests/test_edge_cases.py` | 31 | 边界情形：混合 barcode、长 barcode、空输入、重复基因、空 GSM、Unicode 字段、license 等 |
| `tests/test_mcts.py` | 28 | MCTS 搜索：节点 UCB1、策略模板、搜索、验证、模板应用 |
| `tests/test_alignment_validation.py` | 23 | barcode 对齐、基因统一、细胞批次分配、4 维验证、AnnData 输出 |
| `tests/test_reflexion.py` | 21 | Reflexion：错误签名、启发式策略、策略应用、KB 失败案例 CRUD |
| `tests/test_pipeline_integration.py` | 17 | 管道集成：字段解析、GSM 元数据提取、矩阵方向推断、CSV 格式推断 |
| `tests/test_pdf_agent.py` | 15 | **新增** PDFAgent：文本分块、缓存读写/过期、缺口检测（`_has_unresolved`） |
| `tests/test_genes.py` | 6 | 基因 ID 系统检测、线粒体/核糖体标记、gene symbol 标准化 |
| `tests/test_readers.py` | 5 | CSV 读取器：gene×cell 方向、分隔符检测、row names、Ensembl 检测 |
| `tests/test_barcodes.py` | 4 | barcode 格式检测 (10x v2/v3)、后缀去除、标准化 |
| `tests/test_field_synonyms.py` | 4 | 字段同义解析：精确/大小写/未知/批量 |
| `tests/test_parse.py` | 1 | SOFT 文件解析结构验证 |
| `tests/test_metadata.py` | 1 | 批次识别使用 GSM proxy |

### PDFAgent 测试详情（test_pdf_agent.py）

```
TestChunking (4 个)
  test_chunk_single_short_paragraph  — 短段落不分块
  test_chunk_multi_paragraph         — 多段落合并到一个 chunk
  test_chunk_long_text               — 长文本正确分块
  test_chunk_overlap                 — 重叠窗口正确生成

TestCache (4 个)
  test_write_and_read_cache          — 写入后再读取一致
  test_cache_miss                    — 不存在的 PMID 返回 None
  test_cache_expired                 — 超过 30 天缓存失效
  test_has_cache                     — 缓存存在性检查

TestGapCollection (4 个)
  test_no_unresolved_when_empty      — 空上下文无缺口
  test_has_unresolved_abbreviation   — 低置信度缩写检测
  test_has_unresolved_filename       — 低置信度文件名检测
  test_no_unresolved_high_confidence — 高置信度不触发

TestPDFAgentIntegration (3 个)
  test_agent_creation                — 实例化创建缓存目录
  test_agent_custom_cache_dir        — 自定义缓存路径
  test_agent_acquire_text_no_pmcid   — 空 PMID 返回 None
```

### 运行命令

```bash
pytest tests/ -v                          # 全量测试
pytest tests/test_pdf_agent.py -v         # 仅 PDFAgent
pytest tests/test_literature.py -v        # 仅文献提取
pytest tests/ --ignore=tests/test_gse_pipeline.py --ignore=tests/test_nodes.py -x
```

---

## 1. 测试目标

| 目标 | 衡量方式 | 合格线 |
|------|---------|--------|
| 规则管道通过率 | `scruticell parse` 成功输出 .h5ad 的比例 | ≥ 60% |
| 智能管道通过率 | `scruticell execute` 成功输出 .h5ad 的比例 | ≥ 80% |
| 元信息覆盖率 | 输出 `obs.columns` 数量 vs 预期可用元信息字段数 | ≥ 70% |
| 批次标注准确率 | `obs['batch']` 值与手工标注的一致性 | ≥ 90% |
| 基因 ID 统一率 | `var.index` 中有效 gene symbol 的比例 | ≥ 95% |
| LLM 代码生成正确率 | `processing_code` 在沙箱中无异常的比例 | ≥ 85% |
| KB 复用命中率 | 第二次处理同结构数据集时 KB 命中的比例 | ≥ 90% |

---

## 2. 测试维度

每个数据集在以下 7 个维度上被标记特征：

| 维度 | 类别 | 代码 |
|------|------|------|
| **A. 矩阵格式** | h5 / h5ad / mtx / csv / rds / loom | A1-A6 |
| **B. 样本结构** | 单样本 / 多样本单矩阵 / 多样本多矩阵 / 嵌套样本 | B1-B4 |
| **C. 元信息复杂度** | 清洁 / 复合 / 缩写 / 多语言 / 杂揉(barcode)/ 外部文件 | C1-C6 |
| **D. 实验设计** | 简单(1 factor) / 配对 / 多因素(3+) / 嵌套层级 | D1-D4 |
| **E. Barcode复杂度** | 标准10X / 带前缀 / 带嵌入元信息 / 非标准格式 | E1-E4 |
| **F. 数据质量** | 原始计数 / 归一化 / 含异常值 / 极稀疏 | F1-F4 |
| **G. 文件结构** | 扁平 / 嵌套压缩 / 多层目录 / 文件缺失 | G1-G4 |

---

## 3. 数据集选择矩阵（30 个）

### 3.1 基础集（10 个）—— 规则管道覆盖

覆盖所有矩阵格式和常见样本结构：

| 编号 | 特征码 | 描述 | 推荐搜索词 |
|------|--------|------|-----------|
| **T01** | A1-B1-C1-D1-E1-F1-G1 | 单样本、标准 10X h5、清洁元信息 | `scRNA-seq 10X filtered_feature_bc_matrix.h5` |
| **T02** | A2-B1-C1-D1-E1-F1-G1 | 单样本、h5ad 格式 | `scRNA-seq h5ad human` |
| **T03** | A3-B1-C1-D1-E1-F1-G1 | 单样本、MTX 格式（matrix+barcodes+features） | `scRNA-seq mtx barcodes features` |
| **T04** | A4-B1-C1-D1-E1-F1-G1 | 单样本、CSV/TSV 矩阵 | `scRNA-seq csv counts` |
| **T05** | A5-B1-C1-D1-E1-F1-G1 | 单样本、RDS 格式（Seurat） | `scRNA-seq Seurat rds` |
| **T06** | A6-B1-C1-D1-E1-F1-G1 | 单样本、Loom 格式 | `scRNA-seq loom` |
| **T07** | A1-B2-C1-D1-E1-F1-G1 | 3 个 GSM，每个独立 h5 文件 | `scRNA-seq GSM filtered_feature_bc_matrix` |
| **T08** | A1-B3-C1-D1-E1-F1-G1 | 多个 GSM，共享一个矩阵文件 | `scRNA-seq combined matrix multiple samples` |
| **T09** | A1-B1-C1-D1-E1-F2-G1 | 单样本、TPM/FPKM 归一化数据 | `scRNA-seq TPM normalized` |
| **T10** | A1-B1-C1-D1-E1-F3-G1 | 单样本、含负值或异常值表达矩阵 | `scRNA-seq raw counts negative values` |

### 3.2 元信息挑战集（8 个）—— 智能管道核心测试

| 编号 | 特征码 | 描述 | 推荐搜索词 |
|------|--------|------|-----------|
| **T11** | A1-B2-C2-D1-E1-F1-G1 | 复合字段：`Male_Control_72yr` | `single cell GEO metadata characteristics underscore` |
| **T12** | A1-B2-C3-D1-E1-F1-G1 | 缩写字段：`d_34_NR_T`, `HCC`, `TME` | `scRNA-seq abbreviated metadata` |
| **T13** | A1-B2-C4-D1-E1-F1-G1 | 多语言元信息（中文+英文混合） | `scRNA-seq Chinese metadata` `单细胞 转录组 GEO` |
| **T14** | A1-B1-C1-D3-E1-F1-G1 | 多因素设计：2 treatment × 3 timepoint × 2 genotype | `scRNA-seq factorial design multiple conditions` |
| **T15** | A1-B2-C2-D4-E1-F1-G1 | 患者-样本-细胞嵌套层级 | `scRNA-seq patient multiple timepoints` |
| **T16** | A1-B2-C5-E3-F1-G1 | barcode 内嵌元信息：`PatientA_Treated_Day7_ACGT` | `scRNA-seq barcode prefix sample` |
| **T17** | A4-B2-C6-F1-G1 | 元信息在外部 CSV 文件中（非 SOFT 内） | `scRNA-seq supplementary metadata csv` |
| **T18** | A1-B2-C5-E4-F1-G1 | 非标准 barcode 格式（SMART-seq / Drop-seq） | `SMART-seq2 barcode` `Drop-seq barcode` |

### 3.3 文件结构挑战集（5 个）

| 编号 | 特征码 | 描述 | 推荐搜索词 |
|------|--------|------|-----------|
| **T19** | A1-B2-C1-D1-E1-F1-G3 | 矩阵分散在多层子目录中 | `scRNA-seq subdirectory matrix` |
| **T20** | A1-B2-C1-D1-E1-F1-G2 | .h5 文件在 .tar.gz 压缩包内 | `scRNA-seq tar.gz h5` |
| **T21** | A1-B2-C1-D1-E1-F1-G2 | 嵌套压缩：.zip 内含 .tar.gz 内含 .h5 | `scRNA-seq zip tar.gz` |
| **T22** | A1-B2-C1-D1-E1-F1-G4 | 部分补充文件下载失败或损坏 | `scRNA-seq incomplete supplementary files` |
| **T23** | A3-B2-C1-D1-E1-F1-G1 | MTX 缺少 barcodes.tsv 或 features.tsv | `scRNA-seq missing barcodes file` |

### 3.4 数据质量挑战集（4 个）

| 编号 | 特征码 | 描述 | 推荐搜索词 |
|------|--------|------|-----------|
| **T24** | A1-B1-C1-D1-E1-F4-G1 | 极稀疏矩阵（>99% 零值） | `scRNA-seq extremely sparse` |
| **T25** | A1-B1-C1-D1-E1-F2-G1 | log-normalized 数据（无法直接判断） | `scRNA-seq log normalized counts` |
| **T26** | A1-B1-C1-D1-E2-F1-G1 | >100K 细胞大矩阵（内存压力测试） | `scRNA-seq large dataset 100k cells` |
| **T27** | A1-B1-C1-D1-E1-F3-G1 | 包含 NaN/Inf 的损坏矩阵 | `scRNA-seq NaN values matrix` |

### 3.5 KB 复用测试集（3 个）

| 编号 | 特征码 | 描述 | 意义 |
|------|--------|------|------|
| **T28** | 与 T01 同结构 | 第二个同类型单样本 h5 数据集 | 验证 KB 命中（score ≥ 0.7） |
| **T29** | 与 T07 同结构 | 第二个 3 GSM × 3 h5 的数据集 | 验证多样本策略复用 |
| **T30** | 与 T12 同结构 | 第二个含缩写字段的数据集 | 验证元信息解析策略复用 |

---

## 4. 数据集获取指南

### 4.1 GEO 搜索策略

每个测试类别用以下 NCBI GEO 高级搜索：

```
https://www.ncbi.nlm.nih.gov/geo/browse/?view=series&zsort=date&display=20

筛选条件：
- Entry type: Series
- Organism: Homo sapiens / Mus musculus
- Study type: "Expression profiling by high throughput sequencing"
- 关键词按上述表格中的"推荐搜索词"
```

### 4.2 快速筛选方法

对候选 GSE 运行 `scruticell inspect --no-download`（仅解析 SOFT，不下载完整数据）：

```bash
# 批量预筛选
for gse_id in $(cat candidate_gse_list.txt); do
    echo "=== $gse_id ==="
    scruticell inspect "$gse_id" -o "./prefilter/${gse_id}.json" --no-download 2>&1 | tail -5
done

# 检查每个候选的摘要
python -c "
import json
with open('./prefilter/${gse_id}.json') as f:
    r = json.load(f)
    # 检查是否符合测试特征
    gsm_count = len(r['gsm_entries'])
    matrix_fmts = set()
    for s in r['file_summaries'].values():
        matrix_fmts.add(s.get('file_type'))
    print(f'{gse_id}: {gsm_count} GSMs, formats={matrix_fmts}')
"
```

### 4.3 备选 GEO 数据集参考

以下是一些已知有特定特征的 GSE 编号（截至 2025 年），可作为起点：

| GSE | 特征 | 测试编号 |
|-----|------|---------|
| GSE334035 | 3 GSM × 3 h5，清洁 APOE Alzheimer 数据 | T07（已验证） |
| GSE84465 | 混合样本、含 CSV 元信息补充文件 | T17 |
| GSE130146 | 多时间点、配对设计 | T15 |
| GSE150861 | COVID-19 PBMC，多批次 | T08 |
| GSE184882 | 复合元信息字段 | T11 |
| *需搜索* | 中文元信息的数据集 | T13 |

---

## 5. 自动化测试脚本设计

### 5.1 目录结构

```
tests/benchmark/
├── datasets.json          # 30 个测试数据集的 GSE ID 和预期特征
├── run_all.sh             # 入口脚本
├── run_single.sh          # 单数据集测试
├── collect_results.py     # 汇总结果、生成报告
├── manual_curation/       # 手工 curation 的黄金标准
│   ├── T01_gold.h5ad
│   └── ...
├── results/               # 每次测试的输出
│   ├── YYYY-MM-DD/
│   │   ├── summary.json
│   │   ├── T01/
│   │   │   ├── T01_report.json
│   │   │   ├── T01_plan.json
│   │   │   ├── T01_scruticell.h5ad
│   │   │   ├── T01_provenance.json
│   │   │   └── T01_metrics.json
│   │   └── ...
│   └── latest -> YYYY-MM-DD
└── report_template.md
```

### 5.2 数据集描述文件（datasets.json）

```json
{
  "T01": {
    "gse_id": "GSEXXXXX",
    "category": "基础集",
    "features": {
      "matrix_format": "h5",
      "sample_structure": "单样本",
      "metadata_complexity": "清洁",
      "experimental_design": "简单1factor",
      "barcode_complexity": "标准10X",
      "data_quality": "原始计数",
      "file_structure": "扁平"
    },
    "expected": {
      "n_cells_min": 500,
      "n_cells_max": 50000,
      "n_genes_min": 10000,
      "n_genes_max": 40000,
      "n_batches": 1,
      "min_confidence": 0.7,
      "expected_obs_columns": ["batch", "barcode"],
      "should_pass_rule_pipeline": true,
      "should_pass_smart_pipeline": true
    }
  }
}
```

### 5.3 单数据集测试脚本（test_single.py）

```python
"""单数据集完整链路测试脚本"""
import json, sys, time, argparse
from pathlib import Path
from scruticell.intelligence import Reporter, PlanOrchestrator, PlanExecutor, StrategyMatcher
from scruticell.pipeline.state import PipelineState
from scruticell.pipeline.nodes.download import download_node
from scruticell.pipeline.nodes.parse import parse_soft_node
from scruticell.knowledge.graph import KnowledgeGraph, LocalKnowledgeBase
from scruticell.license import is_enterprise


def test_single_dataset(gse_id: str, output_dir: Path, features: dict, expected: dict) -> dict:
    """运行单个数据集的完整测试"""
    result = {
        "gse_id": gse_id,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "features": features,
        "stages": {},
        "metrics": {},
        "errors": [],
        "passed": False,
    }

    t_start = time.time()

    # Stage 1: Download
    try:
        state = PipelineState(gse_id=gse_id, output_dir=output_dir)
        download_result = download_node(state)
        for k, v in download_result.items():
            setattr(state, k, v)
        result["stages"]["download"] = {
            "status": "success",
            "errors": state.download_errors,
            "duration_s": time.time() - t_start,
        }
    except Exception as e:
        result["stages"]["download"] = {"status": "failed", "error": str(e)}
        result["errors"].append(f"Download failed: {e}")
        return result

    # Stage 2: Parse
    try:
        parse_result = parse_soft_node(state)
        for k, v in parse_result.items():
            setattr(state, k, v)
        result["stages"]["parse"] = {
            "status": "success",
            "gsm_count": len(state.gsm_entries) if state.gsm_entries else 0,
        }
    except Exception as e:
        result["stages"]["parse"] = {"status": "failed", "error": str(e)}

    # Stage 3: Inspect
    try:
        reporter = Reporter()
        report = reporter.build_report(
            gse_id=gse_id,
            download_dir=state.download_dir,
            gsm_entries=state.parsed_characteristics,
            gse_metadata=state.raw_metadata,
        )
        # Save report
        report_json = report.to_json()
        (output_dir / f"{gse_id}_report.json").write_text(report_json, encoding="utf-8")
        result["stages"]["inspect"] = {
            "status": "success",
            "total_files": report.total_files,
            "matrix_candidates": len(report.matrix_candidates),
            "metdata_candidates": len(report.metadata_candidates),
            "compound_fields": sum(
                len(g.compound_value_fields) for g in report.gsm_entries
            ),
            "barcode_prefixes_detected": sum(
                len(fs.get("barcode_prefixes", []))
                for fs in report.file_summaries.values()
            ),
        }
    except Exception as e:
        result["stages"]["inspect"] = {"status": "failed", "error": str(e)}

    # Stage 4: Plan
    try:
        if is_enterprise():
            kb = KnowledgeGraph()
            kb.initialize_schema()
        else:
            kb = LocalKnowledgeBase()
        matcher = StrategyMatcher(kb)
        kb_matches = matcher.find_similar(report)
        orch = PlanOrchestrator(matcher)
        plan = orch.plan(report)
        plan_json = plan.to_json()
        (output_dir / f"{gse_id}_plan.json").write_text(plan_json, encoding="utf-8")
        result["stages"]["plan"] = {
            "status": "success",
            "confidence": plan.confidence,
            "matrices": len(plan.matrices),
            "batches": len(plan.batches),
            "field_mappings": len(plan.field_mappings),
            "has_processing_code": len(plan.processing_code) > 0,
            "kb_matches": len(kb_matches),
            "kb_top_score": kb_matches[0].similarity_score if kb_matches else 0,
            "literature_checked": plan.requires_literature_check,
        }
    except Exception as e:
        result["stages"]["plan"] = {"status": "failed", "error": str(e)}

    # Stage 5: Execute
    try:
        executor = PlanExecutor()
        exec_result = executor.execute(report, plan, output_dir, state)
        result["stages"]["execute"] = {
            "status": "success" if exec_result.success else "failed",
            "n_cells": exec_result.n_cells,
            "n_genes": exec_result.n_genes,
            "n_batches": exec_result.n_batches,
            "output_path": str(exec_result.output_path) if exec_result.output_path else None,
            "errors": exec_result.errors,
            "warnings": exec_result.warnings,
        }
        if exec_result.success:
            # Load and verify output
            import scanpy as sc
            adata = sc.read_h5ad(exec_result.output_path)
            result["metrics"]["actual_n_obs"] = adata.n_obs
            result["metrics"]["actual_n_vars"] = adata.n_vars
            result["metrics"]["obs_columns"] = list(adata.obs.columns)
            result["metrics"]["var_columns"] = list(adata.var.columns)
            result["metrics"]["n_batches"] = adata.obs["batch"].nunique() if "batch" in adata.obs.columns else 0
    except Exception as e:
        result["stages"]["execute"] = {"status": "failed", "error": str(e)}

    # Compute pass/fail
    result["duration_total_s"] = time.time() - t_start
    result["passed"] = (
        result["stages"].get("execute", {}).get("status") == "success"
    )

    # Against expectations
    if result["passed"]:
        metrics = result["metrics"]
        checks = []
        if expected.get("n_cells_min"):
            checks.append(metrics["actual_n_obs"] >= expected["n_cells_min"])
        if expected.get("n_genes_min"):
            checks.append(metrics["actual_n_vars"] >= expected["n_genes_min"])
        if expected.get("n_batches"):
            checks.append(metrics["n_batches"] == expected["n_batches"])
        if expected.get("expected_obs_columns"):
            checks.append(
                all(c in metrics["obs_columns"] for c in expected["expected_obs_columns"])
            )
        result["expectations_met"] = all(checks) if checks else None

    # Save metrics
    (output_dir / f"{gse_id}_metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return result
```

### 5.4 批量运行脚本（run_all.py）

```python
"""批量运行所有测试数据集"""
import json, time, argparse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from test_single import test_single_dataset

def run_benchmark(datasets_json: Path, output_root: Path, max_workers: int = 2):
    """Run benchmark on all datasets defined in datasets.json."""
    datasets = json.loads(datasets_json.read_text())

    results = {}
    timestamp = time.strftime("%Y-%m-%d_%H%M%S")
    run_dir = output_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for test_id, config in datasets.items():
            gse_id = config["gse_id"]
            dataset_dir = run_dir / test_id
            dataset_dir.mkdir(parents=True, exist_ok=True)
            future = executor.submit(
                test_single_dataset, gse_id, dataset_dir,
                config["features"], config.get("expected", {}),
            )
            futures[future] = (test_id, gse_id)

        for future in as_completed(futures):
            test_id, gse_id = futures[future]
            try:
                result = future.result()
                results[test_id] = result
                status = "PASS" if result["passed"] else "FAIL"
                print(f"[{status}] {test_id} ({gse_id}) - "
                      f"{result['duration_total_s']:.0f}s")
            except Exception as e:
                results[test_id] = {"gse_id": gse_id, "passed": False, "error": str(e)}
                print(f"[ERROR] {test_id} ({gse_id}): {e}")

    # Summary
    passed = sum(1 for r in results.values() if r.get("passed"))
    total = len(results)
    summary = {
        "timestamp": timestamp,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": f"{passed / total:.1%}" if total > 0 else "N/A",
        "results": results,
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (output_root / "latest").unlink(missing_ok=True)
    (output_root / "latest").symlink_to(run_dir.name)

    print(f"\n=== Benchmark Complete ===")
    print(f"Passed: {passed}/{total} ({passed/total:.1%})" if total else "No results")
    return summary
```

---

## 6. Benchmark 对比方案

### 6.1 对比维度

| 维度 | 测量方法 | 数据来源 |
|------|---------|---------|
| **处理时间** | 墙钟时间，从下载到输出 | 自动化脚本记录 |
| **元信息字段数** | `adata.obs.columns` 中去除非元信息列后的有效字段数 | AnnData output |
| **批次标注准确率** | 与手工标注的 GSM→batch 映射对比 | 手工 curation gold standard |
| **基因 ID 统一率** | `var.index` 中有效 gene symbol 数量 / 总基因数 | AnnData output |
| **置信度** | `validate_node` 的 4 维评分 | pipeline output |
| **LLM 代码质量** | `processing_code` 是否无异常执行通过 | executor output |

### 6.2 手工 curation 黄金标准

从 30 个测试集中，选 10 个做手工标注作为对比基线：

```
对于每个选中的 GSE：
1. 人工下载所有文件
2. 阅读 SOFT 文件中的 GSM/GSE 描述
3. 查看每个补充文件的内容
4. 手工标注：
   a. 每个 GSM 的正确批次标签
   b. 所有可用的元信息字段和值
   c. 基因 ID 系统和归一化状态
   d. 矩阵方向（gene×cell 还是 cell×gene）
5. 保存为 gold_standard/{GSE_ID}_gold.json
```

### 6.3 对比指标计算

```python
def compare_to_gold(output_h5ad: Path, gold_json: Path) -> dict:
    """对比系统输出与手工标注黄金标准"""
    adata = sc.read_h5ad(output_h5ad)
    gold = json.loads(gold_json.read_text())

    # 1. 批次标注准确率
    batch_col = gold.get("batch_column", "batch")
    gold_batches = gold["cell_to_batch"]  # {barcode: batch}
    correct = 0
    total = 0
    for bc, expected_batch in gold_batches.items():
        if bc in adata.obs.index:
            actual = adata.obs.loc[bc, batch_col]
            if actual == expected_batch:
                correct += 1
            total += 1
    batch_accuracy = correct / total if total > 0 else 0

    # 2. 元信息字段覆盖率
    gold_fields = set(gold["expected_obs_columns"])
    actual_fields = set(adata.obs.columns)
    field_coverage = len(gold_fields & actual_fields) / len(gold_fields) if gold_fields else 0

    # 3. 基因 ID 统一率
    gold_symbols = set(gold.get("expected_gene_symbols", []))
    actual_symbols = set(adata.var.index)
    gene_accuracy = len(gold_symbols & actual_symbols) / len(gold_symbols) if gold_symbols else 1.0

    return {
        "batch_accuracy": batch_accuracy,
        "field_coverage": field_coverage,
        "gene_accuracy": gene_accuracy,
    }
```

---

## 7. 评分标准

### 7.1 每个数据集的评级

| 评级 | 条件 |
|------|------|
| **S (完美)** | execute 成功 + 所有预期检查通过 + 无 warnings + confidence ≥ 90% |
| **A (优秀)** | execute 成功 + 所有预期检查通过 + confidence ≥ 70% |
| **B (合格)** | execute 成功 + 主要预期检查通过 + confidence ≥ 50% |
| **C (部分成功)** | execute 成功但未达预期 / 规则管道成功但智能管道失败 |
| **D (失败)** | execute 失败 |
| **F (无法处理)** | 数据集结构不支持（如 .tar.gz 内文件格式未识别） |

### 7.2 系统整体评分

| 指标 | 权重 | 计算方式 |
|------|------|---------|
| 通过率 | 40% | (S+A+B) / 总数 |
| 平均置信度 | 20% | 所有通过数据集的置信度均值 |
| 元信息覆盖率 | 15% | 对比黄金标准的 field_coverage 均值 |
| 批次准确率 | 15% | 对比黄金标准的 batch_accuracy 均值 |
| LLM 代码正确率 | 10% | processing_code 无异常执行的比例 |

```
综合得分 = 通过率 * 0.4 + avg_confidence * 0.2 + field_coverage * 0.15 + batch_accuracy * 0.15 + llm_code_rate * 0.1
```

---

## 8. 报告模板

### 8.1 汇总报告

```markdown
# Scrut-Cell Benchmark Report
**日期:** YYYY-MM-DD | **版本:** v0.1.0 | **数据集:** 30

## 总览

| 指标 | 数值 |
|------|------|
| 通过率 | 24/30 (80%) |
| 平均置信度 | 87% |
| 平均处理时间 | 4.2 分钟 |
| KB 复用命中率 | 85% |
| LLM 代码正确率 | 92% |

## 按类别分析

| 类别 | 数量 | 通过 | 通过率 | 平均置信度 |
|------|------|------|--------|-----------|
| 基础集 | 10 | 10 | 100% | 94% |
| 元信息挑战集 | 8 | 6 | 75% | 78% |
| 文件结构挑战集 | 5 | 3 | 60% | 65% |
| 数据质量挑战集 | 4 | 3 | 75% | 82% |
| KB 复用测试集 | 3 | 3 | 100% | 95% |

## 失败分析

| GSE | 原因 | 分类 |
|-----|------|------|
| GSEXXXX | MTX 缺少 features.tsv | 文件结构 |
| GSEXXXX | barcode 前缀匹配失败 | 元信息 |

## 与手工标注对比（10 数据集）

| 指标 | Scrut-Cell | 手工 curation |
|------|-----------|--------------|
| 平均处理时间 | 5 分钟 | 2 小时 |
| 字段覆盖率 | 85% | 75% |
| 批次准确率 | 95% | 90% |
| 基因统一率 | 99% | 95% |

## 改进项

1. MTX 缺失文件时的回退策略
2. 非 _ 分隔符的复合字段检测
3. 大型稀疏矩阵的内存优化
```

### 8.2 瀑布图（可选）

```
测试进度：
T01-T10 (基础集)     ██████████ 100% (10/10)
T11-T18 (元信息)     ████████░░  80% (6/8)
T19-T23 (文件结构)   ██████░░░░  60% (3/5)
T24-T27 (数据质量)   ███████░░░  75% (3/4)
T28-T30 (KB复用)     ██████████ 100% (3/3)
─────────────────────────────────
总计                 ████████░░  83% (25/30)
```

---

## 9. 执行计划

### 阶段 1：准备（0.5 天）

- [ ] 创建 `tests/benchmark/` 目录结构
- [ ] 编写 `datasets.json` 模板
- [ ] 编写 `test_single.py` 和 `run_all.py`
- [ ] 搜索并确定 30 个 GSE 编号

### 阶段 2：执行（1-2 天）

- [ ] 运行 30 个数据集的测试
- [ ] 记录每个数据集的输出和指标
- [ ] 标识失败案例并记录原因

### 阶段 3：对比（1 天）

- [ ] 从 30 个中选 10 个做手工标注
- [ ] 运行对比脚本
- [ ] 生成对比报告

### 阶段 4：分析（0.5 天）

- [ ] 汇总所有指标
- [ ] 生成报告
- [ ] 列出改进项和优先级

### 阶段 5：改进循环（按需）

- [ ] 修复发现的 Bug
- [ ] 重跑失败的数据集
- [ ] 更新报告

---

> 文档版本: 1.0 | 对应 Scrut-Cell v0.1.0 | 2026-06-25
