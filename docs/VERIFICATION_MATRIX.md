# 验证计划：跨组学 × 多源 验证 scruti-cell（真实场景、全数据集 ≤100MB）

> 工具：`scruticell client verify`（实现于 `src/scruticell/client/verify.py`），
> 用例表即 `verify.CASES`（本文件与之一致，`--matrix` 可打印）。
>
> 原则：在**沙箱真实运行环境**内，从**可达源**中挑「**全数据集总大小 ≤100MB**」的真用例，
> 走**真实用户流程**（真服务端 `/api/plan/create` → 真客户端下载/解压/解析/对齐 → 断言）。

## 1. 沙箱可达性（实测）
| 源/门户 | 可达 | 备注 |
|---|---|---|
| GEO FTP | ✅ 稳定 | ~10KB/s，小文件可用 |
| EBI / ArrayExpress (BioStudies) | ✅ 稳定 | 元数据/文件 |
| cellxgene API | ✅ 稳定 | 门户元数据 |
| figshare（走 mihomo :7890） | ⚠️ 时断时续 | 公开文章按 ID 可取；断时如实报 `BLOCKED` |
| Zenodo / HuggingFace / OSF / Dryad / GDC / ENCODE / PRIDE / DataCite | ❌ 阻断 | B 层，有网机器可跑 |

## 2. A 层矩阵（沙箱内真跑；全数据集总大小已测）
| # | id | 组学·设计 | 源 | accession | 总大小 | row_type | 断言 |
|---|---|---|---|---|---|---|---|
| 1 | bulk_fpkm | bulk RNA · 队列 FPKM | GEO | GSE135624 | 5.1MB | sample | obs=样本列；var=基因；align 按样本 |
| 2 | cite_seq | CITE-seq · RNA+ADT | GEO | GSE100866 | 28.7MB | barcode | 多模态；蛋白(ADT)侧车 |
| 3 | methylation | 甲基化 450k · EWAS β | GEO | GSE207605 | ~29MB | sample | β 值矩阵；var=CpG |
| 4 | scrna_h5ad | scRNA · h5ad（仓库源） | figshare | figshare-22577536 | 99.5MB | barcode | h5ad 直接采用（保持 obs/var/uns） |
| 5 | atac_bulk | ATAC-seq · bulk peaks | GEO | GSE150589 | 1.7MB | sample | peaks 侧车（下载+侧车） |
| 6 | ae_metadata | ArrayExpress · 元数据 | EBI | E-MTAB-12595 | 0.02MB | sample | idf+sdrf 元数据（scATAC 研究） |
| 7 | portal_cellxgene | 门户 · cellxgene | cellxgene | cxg-verify | — | — | 元数据参考入口 |

## 3. B 层矩阵（阻断宿主 / 超限 / 异形格式；沙箱内如实报 BLOCKED/OVERSIZE/FAIL）
| id | 组学 | 源 | accession | 总大小 | 原因 |
|---|---|---|---|---|---|
| bulk_de | bulk RNA（Cufflinks） | GEO | GSE52778 | 3.5MB | 空格分隔 Cufflinks，reader 不支持 |
| scrna_10x | scRNA（RAW 归档） | GEO | GSE163121 | 96.5MB | RAW.tar of TARs，无干净矩阵 |
| scatac | scATAC-seq | GEO | GSE129785 | 87GB | RAW.tar 72GB → OVERSIZE |
| spatial | 空间 Visium | GEO | GSE240715 | 443MB | cloupe 409MB → OVERSIZE |
| multiome | 多组学 RNA+ATAC | GEO | GSE194122 | 3.5GB | cite 615MB + multiome 2.9GB → OVERSIZE |
| perturb_zenodo | 扰动筛选 | Zenodo | zenodo-10044268 | 24.7MB | 宿主阻断 → BLOCKED |
| scrna_hf | scRNA h5ad | HuggingFace | scverse/pbmc3k | ~40MB | 宿主阻断 → BLOCKED |

## 4. 覆盖保证
- 每种可达源 ≥1 个 A 层用例：GEO、arrayexpress、figshare、cellxgene（`matrix_complete()` 强制校验）。
- 组学覆盖：bulk RNA、scRNA（CITE/10x/h5ad）、CITE-seq、甲基化、ATAC-seq、ArrayExpress、门户。
- 注：scATAC/空间/多组学/扰动/蛋白（质谱）**在可达源上不存在 ≤100MB 的整数据集**（整数据集都含 RAW.tar/cloupe/fragments 等大文件）——其 barcode/多模态管线由 `cite_seq`（多模态 barcode）+ 单测覆盖，真实大文件用例列于 B 层（有网机器可跑）。

## 5. verify CLI 流程（真实场景）
每用例：可达性预检 → 真 `plan/create` → **运行时整数据集大小过滤**（HEAD 求和 ≤100MB，超限 `OVERSIZE`）→ 真客户端循环（download→decompress→parse→align→assemble）→ 断言（row_type/storage/obs/obsm/最终 h5ad）。结果 `PASS/FAIL/OVERSIZE/BLOCKED`。

## 6. 运行结果（沙箱实测，已完成）
| id | 结果 | 说明 |
|---|---|---|
| bulk_fpkm | ✅ PASS | GSE135624 两个 FPKM 矩阵 → obs=样本(20/46)、var=基因 |
| cite_seq | ✅ PASS | GSE100866 RNA+ADT（9 文件 28.7MB）→ barcode 多模态 |
| methylation | ✅ PASS | GSE207605 β 值 csv（~29MB）→ sample |
| atac_bulk | ✅ PASS | GSE150589 peaks 侧车（下载+侧车） |
| ae_metadata | ✅ PASS | E-MTAB-12595 idf+sdrf 元数据 |
| portal_cellxgene | ✅ PASS | cellxgene 门户元数据参考 |
| scrna_h5ad | ⛔ BLOCKED | figshare 代理时断时续（源不可达时如实报 BLOCKED） |

> 结论：沙箱内可达的 GEO / EBI / cellxgene 用例全部真跑通过（bulk / scRNA-CITE / 甲基化 / ATAC / AE / 门户，覆盖多种组学与源）；figshare 源当前网络不通、如实报 BLOCKED，网络恢复后同一命令即可真跑。

## 7. 交付物
- `src/scruticell/client/verify.py` + `cli.py::verify` 命令（三件套之一）
- `tests/verification/test_verify_cli.py`（单测：过滤/状态/矩阵完整性）
- `tests/verification/test_e2e_live.py`（`@pytest.mark.live`，`--run-live` 真跑）
- `docs/VERIFICATION_MATRIX.md`（本文件）
- 顺带修复：`readers/csv_matrix.py` 现在可解析 GEO 常见 bulk 表达矩阵（含注释列 `UniqueID,GeneID,...` 的 genes×samples）；服务端 `_fetch_repo_entries` 改用带重试的仓库文件抓取。