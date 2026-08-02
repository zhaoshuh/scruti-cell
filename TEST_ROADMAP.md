# Scrut-Cell 测试路线与 Benchmark 方案

---

## 目录

1. [已有单元测试](#0-已有单元测试)
2. [多组学测试目标](#1-多组学测试目标)
3. [测试维度](#2-测试维度)
4. [多组学测试集（7 个核心）](#3-多组学测试集)
5. [评测标准](#4-评测标准)
6. [Benchmark 对比方案](#5-benchmark-对比方案)
7. [执行计划](#6-执行计划)

---

## 0. 已有单元测试

**总计: 195 个测试，全部通过**

| 测试文件 | 数量 | 覆盖内容 |
|---------|------|---------|
| `test_literature.py` | 39 | 文献提取：平台检测、批次数、归一化、细胞数、物种、组织等 |
| `test_edge_cases.py` | 31 | 边界情形：混合 barcode、长 barcode、空输入、重复基因、license 等 |
| `test_mcts.py` | 28 | MCTS 搜索：节点 UCB1、策略模板、搜索、验证 |
| `test_alignment_validation.py` | 23 | barcode 对齐、基因统一、细胞批次分配、4 维验证、AnnData 输出 |
| `test_reflexion.py` | 21 | Reflexion：错误签名、启发式策略、策略应用、KB 失败案例 CRUD |
| `test_pipeline_integration.py` | 17 | 管道集成：字段解析、GSM 元数据提取、矩阵方向推断 |
| `test_pdf_agent.py` | 15 | PDFAgent：文本分块、缓存读写/过期、缺口检测 |
| `test_genes.py` | 6 | 基因 ID 系统检测、线粒体/核糖体标记、gene symbol 标准化 |
| `test_readers.py` | 5 | CSV 读取器：方向、分隔符检测、row names、Ensembl 检测 |
| `test_barcodes.py` | 4 | barcode 格式检测 (10x v2/v3)、后缀去除、标准化 |
| `test_field_synonyms.py` | 4 | 字段同义解析 |
| `test_parse.py` | 1 | SOFT 文件解析结构验证 |
| `test_metadata.py` | 1 | 批次识别使用 GSM proxy |

```bash
pytest tests/ -v                    # 全量测试
pytest tests/ --ignore=tests/test_gse_pipeline.py --ignore=tests/test_nodes.py -x
```

---

## 1. 多组学测试目标

Scrut-Cell 的核心能力是**多组学数据预处理**。测试需覆盖以下场景：

| 目标 | 衡量方式 | 合格线 |
|------|---------|--------|
| 组学识别准确率 | 识别出的模态 vs GEO metadata 标注 | ≥ 90% |
| **文件→样本 1:1 归属正确率** | `per_file_gsm_id` 提取 + classifier key 推断 vs ground truth | ≥ 95% |
| **共享文件检测率** | 多个 GSM 共用文件被正确识别为 shared | ≥ 90% |
| **Demux 文件检测率** | 合并文件被标注 has_demux + 拆分规则正确 | ≥ 80% |
| 文件分离完整性 | 无文件被错误归类或遗漏 | ≥ 95% |
| 多组学 GSE 正确拆分 | 包含多模态的 GSE 被正确分离 | ≥ 85% |
| 证据搜集可用性 | 不确定时 PubMed 证据正确辅助分类 | ≥ 80% |
| Token 效率 | 紧凑报告 vs 原始报告 token 比 | ≤ 5% |
| **模态 Skill 加载** | `SKILL.md` 成功注入 classifier prompt | 100% |
| **Rubric 自评估通过率** | 首次分类就 `satisfied` 的 GSE 比例 | ≥ 70% |
| **Code-Gen 沙箱成功率** | `extract_metadata()` / `split_demux_file()` 执行无错误 | ≥ 85% |
| **元数据提取覆盖率** | LLM 提取的字段占可提取字段的比例 | ≥ 60% |
| **Code Cache 命中率** | 相同 GSE 重跑时跳过 LLM 调用 | 100% (hit) |

---

## 2. 测试维度

每个测试 GSE 在以下维度上标记：

| 维度 | 说明 |
|------|------|
| **组学类型** | scRNA, snRNA, ATAC, Spatial, Methylation, CITE-seq, Multiome, TCR/BCR |
| **模态数量** | 单一 / 多模态（2+） |
| **样本数** | GSM 数量 |
| **物种** | 人类 / 小鼠 / 多物种 |
| **suspension** | cell / nucleus / na / 混合 |
| **assay 多样性** | 单一 assay / 多 assay 混合 |
| **文件结构** | 扁平 / 嵌套压缩 / 10x 三件套 / 混合 |
| **归属复杂度** | 标准 (GSM 子目录) / 共享文件 (common features) / 合并文件 (demux) |
| **key 推断需求** | 直接 (GSM 路径) / 需要推断复合 key |

---

## 3. 多组学测试集（7 个核心 GSE）

从 174 个 GEO 数据集中筛选的 20 个多组学 GSE 里选出 7 个代表性测试集：

### Core 5: 覆盖所有组学家族

| # | GSE | 组学 | 数据集 | 细胞数 | 为什么 |
|---|-----|------|--------|--------|--------|
| 1 | **GSE196235** | Multiome | 1 | 51,645 | 极简：单数据集 Multiome (RNA+ATAC 同细胞) |
| 2 | **GSE151302** | ATAC + snRNA | 2 | 47,019 | 最简单的 ATAC 多模态，nucleus suspension |
| 3 | **GSE166895** | CITE-seq + scRNA | 4 | 156,947 | **唯一**的蛋白组学模态 (ADT 抗体) |
| 4 | **GSE192742** | Spatial + RNA + snRNA | 21 | 416,444 | **人+鼠双物种**、三种 suspension 混合 |
| 5 | **GSE271304** | Spatial + scRNA | 39 | 1,142,463 | 最大 GSM 数，压力测试去重优化 |

### Extended 2: 深度边界覆盖

| # | GSE | 组学 | 数据集 | 细胞数 | 为什么 |
|---|-----|------|--------|--------|--------|
| 6 | **GSE176078** | Spatial + scRNA | 13 | 225,725 | 四种 assay 混合 (Visium + 10x 3'/5') |
| 7 | **GSE321762** | ATAC + scRNA | 3 | 286,276 | **鼠源** ATAC，cell+nucleus 混合 |

### 覆盖矩阵

| 能力 | GSE196235 | GSE151302 | GSE166895 | GSE192742 | GSE271304 | GSE176078 | GSE321762 |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| ATAC 检测 | — | ✓ | — | — | — | — | ✓ |
| Spatial 检测 | — | — | — | ✓ | ✓ | ✓ | — |
| CITE-seq | — | — | ✓ | — | — | — | — |
| Multiome | ✓ | — | — | — | — | — | — |
| Nucleus suspension | ✓ | ✓ | — | ✓ | — | — | ✓ |
| 多物种 | — | — | — | ✓ | — | — | — |
| 多 assay 混合 | — | — | ✓ | ✓ | ✓ | ✓ | — |
| 极简 (1 数据集) | ✓ | — | — | — | — | — | — |
| 极大 (39 数据集) | — | — | — | — | ✓ | — | — |
| Token 去重压力 | 低 | 低 | 低 | 高 | **最高** | 中 | 低 |

### 推荐测试顺序

1. **GSE196235** — smoke test（1 数据集，最简单）
2. **GSE151302** — ATAC 检测验证
3. **GSE166895** — CITE-seq / 蛋白模态验证
4. **GSE321762** — 鼠源背景验证
5. **GSE176078** — 中等规模空间检测
6. **GSE192742** — 复杂多物种多 suspension
7. **GSE271304** — 压力测试（39 数据集，最大去重）

---

## 4. 评测标准

### 每个 GSE 的评级

| 评级 | 条件 |
|------|------|
| **S (完美)** | 所有组学正确识别 + 所有文件正确归类 + 0 错误 |
| **A (优秀)** | 组学正确识别 + ≤2 个文件归类错误 |
| **B (合格)** | 主要组学正确 + 次要组学有混淆 |
| **C (部分成功)** | 部分组学识别正确 |
| **D (失败)** | 核心组学识别错误 |

### 系统整体评分

| 指标 | 权重 | 说明 |
|------|------|------|
| 组学识别准确率 | 30% | 每个 GSE 检测到的模态 vs 实际模态 |
| **文件→样本 1:1 归属正确率** | 25% | `per_file_gsm_id` + key 推断 vs ground truth |
| Token 效率 | 10% | 紧凑报告 vs 原始报告 token 比 |
| 共享/Demux 检测 | 10% | shared 文件 + demux 拆分规则正确 |
| **Rubric 自评估通过率** | 10% | 首次分类即 `satisfied` 的比例 |
| 证据搜集可用性 | 10% | 不确定时 PubMed 证据辅助效果 |
| **Code-Gen 沙箱成功率** | 5% | 代码生成/沙箱执行无错误 |

---

## 5. Benchmark 对比方案

### 对比对象

| 对比项 | Scrut-Cell | 手工流程 | CELLxGENE |
|--------|-----------|---------|-----------|
| 预处理时间/数据集 | ~5 分钟 | 2-4 小时 | N/A（不提供自动化） |
| 组学识别 | 自动 (12 种) | 人工查阅 SOFT + 论文 | 人工 curate |
| 文件分离 | 自动 | 人工下载+解压+归类 | 人工 |
| 覆盖范围 | 全部 GEO | 取决于预算 | 885 个精选数据集 |

### 手工标注基线

对 7 个测试 GSE，手工完成以下标注作为 ground truth：
1. 该 GSE 包含哪些组学模态
2. 每个 GSM 对应的组学是什么
3. 每个文件属于哪个 GSM 和组学
4. 文件角色（matrix/barcodes/features/peaks/metadata）

---

## 6. 执行计划

### 阶段 1：准备（0.5 天）

- [ ] 确认 7 个 GSE 的 GEO 数据可正常下载
- [ ] 对每个 GSE 做手工标注（ground truth）
- [ ] 准备自动化测试框架 `tests/benchmark/test_preprocessing.py`

### 阶段 2：执行（1 天）

- [ ] 对每个 GSE 运行 `scruticell preprocess`
- [ ] 记录分类结果 vs ground truth
- [ ] 记录 token 消耗数据
- [ ] 标识失败案例

### 阶段 3：分析（0.5 天）

- [ ] 汇总所有指标
- [ ] 生成 benchmark 报告
- [ ] 列出改进项和优先级

---

> 文档版本: 2.0 | 对应 omics-scruti-cell 分支 | 2026-07-09
