# GEO 单细胞转录组数据问题全面总结

---

## 一、SOFT 文件格式结构性问题

### 1.1 整体格式不标准
- 不同课题组、不同期刊、不同年代的提交规范千差万别，无强制执行的结构化 Schema
- SOFT 文件本质上是一个半结构化纯文本格式，字段定义松散，允许无限自定义
- 同一 GSE 内的各 GSM 条目字段数量、种类、顺序都可能不一致

### 1.2 固定字段的滥用与缺失
- `!Sample_title` 常被填入编号、日期、实验者姓名或任意自由文本
- `!Sample_source_name_ch1` 与 `!Sample_characteristics_ch1` 的职责边界模糊，同一个信息可能出现在任一位置
- `!Sample_description` 常为空或只写 "N/A"、"."、"-"
- `!Sample_type` 在单细胞数据中几乎从不被正确标注
- `!Sample_organism_ch1`、`!Sample_molecule_ch1` 等关键字段时有时无

### 1.3 自定义字段爆炸
- 提交者可定义任意 `!Sample_xxx` 字段，命名规范无从约束
- 常见自创字段如 `!Sample_disease_state`、`!Sample_cell_type`、`!Sample_patient_id`，但拼写、大小写、下划线/空格使用千差万别
- 自定义字段的取值格式极其混乱：可能是一个值、逗号分隔列表、分号分隔、JSON 字符串、或内嵌表格

### 1.4 平台注释（GPL）的高频问题
- `!Platform_table_begin` 到 `!Platform_table_end` 之间的探针注释表往往为空、截断或不完整
- 探针 ID 与基因名的映射关系已过时，参考基因组版本不明
- 同一探针集在多个 GPL 中被重复定义，版本关系不可追溯
- GPL 记录可能被多个 GSE 共享但被分别修改，导致分歧

### 1.5 矩阵存储结构混乱
- 表达矩阵可能存在于三个位置：`!series_matrix_table_begin`（系列级）、各 GSM 的 `!Sample_table_begin`（样本级）、或 supplementary files 中的独立文件
- 系列级矩阵可能为空，实际数据全在 supplementary 文件中
- GSM 级矩阵可能只有 metadata 表格而无表达值
- 矩阵的行/列标签格式不一致：有无引号、有无物种前缀、有无注释编号

### 1.6 文件编码问题
- 非英文字符（中文、日文、德文等临床注释）常因编码错误而损坏
- GB2312/Shift_JIS/Latin-1 等编码混入 UTF-8 声明的文件
- 全角/半角字符混用（全角逗号、全角括号、全角空格）
- BOM 头有无不一致
- 控制字符、零宽字符、不可见空白字符以各种方式侵入

---

## 二、元信息（Metadata）问题

### 2.1 元信息存储位置的不可预测性
- 可能存在于：SOFT 文件内各 GSM 的 characteristics 字段
- 可能存在于：系列描述的自由文本中（`!Series_summary`、`!Series_overall_design`）
- 可能存在于：supplementary files 内的独立 CSV/Excel/txt 文件
- 可能存在于：文件名本身（如 `GSMxxx_disease_treated_rep2.csv.gz`）
- 可能存在于：非标准格式的压缩包内注释文件
- 可能存在于：论文正文或补充材料的表格中（需手动从 PDF 提取）

### 2.2 字段语义模糊与命名混乱
- 同一临床概念（如"疾病状态"）的字段名变体示例：
  - `disease` / `disease_state` / `disease_status` / `condition` / `group` / `phenotype` / `clinical_status` / `diagnosis` / `health_status` / `source_disease`
- 同一治疗信息可能标注为：
  - `treatment` / `treated_with` / `drug` / `therapy` / `stimulation` / `intervention` / `compound` / `response`
- 同一组织信息可能标注为：
  - `tissue` / `tissue_type` / `organ` / `source_tissue` / `biopsy_site` / `anatomical_location` / `body_site` / `sample_origin`

### 2.3 元信息值的不规范
- 二元变量：`True` / `TRUE` / `true` / `Yes` / `yes` / `Y` / `1` / `positive` / `+` / `T` / `present`
- 缺失值：`NA` / `N/A` / `na` / `--` / `.` / ``（空串）/ `null` / `None` / `unknown` / `not available` / `not applicable` / `missing`
- 数值变量中混入文本：`"<5"`、`">10"`、`"approx 20"`、`"20-30"`、`"normal"`
- 同一字段存储多维度信息：`"Male_Control_72yr"`（性别+分组+年龄混于一个字段）
- 日期格式：`2023-01-15` / `01/15/2023` / `15-Jan-2023` / `20230115` / `Day 0` / `Week 3`

### 2.4 层级信息缺失
- 患者-样本-细胞的三级嵌套关系几乎从不被显式记录
- 同一患者的多个时间点、多个组织、技术重复/生物学重复的关系不可追踪
- 对同一患者的多次采样之间的关联（配对信息）隐含在难以解析的命名约定中
- 长期队列研究的访视编号、诊疗分期信息通常缺失

### 2.5 元信息与数据对象的对齐断裂
- obs 表（细胞级注释）中的细胞 barcode 前缀/后缀可能与表达矩阵列名不一致
- 细胞 barcode 可能带有来源标识后缀（如 `-1` 表示第一个 10X lane），而 obs 中无此后缀
- var 表（基因级注释）中的基因标识可能与矩阵行名使用不同的 ID 体系
- 批次信息可能只存在于样本级，未传播到细胞级

---

## 三、表达数据问题

### 3.1 归一化状态不明确
- 原始 counts 还是 TPM/FPKM/RPKM/CPM，标注方式随意甚至缺失
- log-normalized 数据（log2、ln、log10）与线性数据无法自动区分
- 文库大小归一化（library-size normalization）是否已执行不可知
- SCTransform、scran、Seurat NormalizeData 等工具的处理残差难以识别
- 同一 GSE 内不同 GSM 可能使用不同的归一化策略

### 3.2 基因标识符的全面混乱
- **ID 体系混用**：Gene Symbol、Ensembl ID（含/不含版本号）、Entrez ID、RefSeq、UniProt 同时出现
- **版本不一致**：`ENSG00000139618.17` vs `ENSG00000139618`、`BRCA1` vs `BRCA1-202`
- **Symbol 冲突**：`MARCH1`（基因名 Marchf1 / 月份 March 1 难以区分）、`SEPT1`（Septin 1 / 9 月 1 日）
- **一对多映射**：一个探针对应多个基因（多靶点探针），一个基因对应多个探针（冗余探针）
- **重复 Symbol**：同一 gene symbol 在矩阵中出现多次（对应不同探针/转录本），未经过唯一化处理
- **历史命名**：基因名使用已废弃的旧版本命名（如 `C10orf10` 现已改名为 `DEPP1`）
- **物种混入**：人类基因标记在鼠类数据中，或反之（常见于异种移植模型）

### 3.3 矩阵规模与稀疏性
- 单细胞数据可能以密集矩阵（dense matrix）形式存储，占据巨大空间
- 也可能以稀疏格式（MTX/HDF5）存储，但读取方式不统一
- 部分数据集在存储时进行了细胞或基因子采样，子采样策略未被记录
- 过滤前后的细胞/基因数量不可知，只能依赖提交者的自由描述

### 3.4 表达值异常
- 负数表达值（因归一化方法或软件 bug 导致）
- 全零基因/全零细胞（空滴未被正确过滤）
- 数值极端异常值（软件 pipeline 错误导致）
- 不同类型的 NaN/Inf（计算过程中产生的非数值）
- 浮点数精度问题（不同平台存储精度不同）

### 3.5 矩阵的转置方向不明确
- 行=基因 × 列=细胞 vs 行=细胞 × 列=基因，没有强制标准
- 提交者可能在 matrix 文件中记录了方向信息，但格式不统一
- 通过形状猜测容易误判（相同数目的基因和细胞难以判断）

---

## 四、实验设计与批次问题

### 4.1 批次的隐藏与歧义
- **显式 vs 隐式批次**：GSM 编号代表的是"数据提交单元"而非"实验批次单元"
  - 一个 GSM 可能包含多个技术批次的合并数据
  - 多个 GSM 可能属于同一实验批次（同一次测序 run）
- **真实的实验批次极少被记录**：测序 lane、测序日期、芯片编号、操作者、试剂批号等 QC 关键信息缺失
- **批次与生物变量的混淆**：若所有疾病样本在同一天测序、所有对照在另一天测序，批次与条件完全共线，无法校正

### 4.2 GSM 粒度不一致
- 一个 GSM = 一个样本（理想情况）
- 一个 GSM = 一个患者的所有样本（多时间点、多组织合并）
- 一个 GSM = 多个患者的 pooled 样本
- 一个 GSM = 一个技术重复
- 一个样本被拆分成多个 GSM（按细胞类型、按处理条件、按测序批次）
- 没有任何标准化规则约束提交者的拆分/合并决策

### 4.3 实验设计的不可追溯
- 自身前后对照（paired design）关系不显式标注
- 多因素实验设计（如：2 种处理 × 3 个时间点 × 2 种基因型）被平铺为一维的分组标签
- 随机区组、分层抽样、巢式设计等统计设计信息完全缺失
- 实验的随机化方案、盲法方案几乎从不被记录

### 4.4 生物重复与技术重复的混淆
- 技术重复（同一样品多次测序）与生物重复（不同个体的同条件样品）未被明确区分
- 10X 单样本多 lane 技术（lane-level technical replicates）与多采样生物学重复可能被标注为同一标签
- 合并样本（如 3 只小鼠的细胞混合后标记为一个样本）的重复策略不明

---

## 五、补充文件（Supplementary Files）的问题

### 5.1 文件格式的多重矩阵
| 格式 | 典型用途 | 常见问题 |
|------|----------|----------|
| `.mtx` / `.mtx.gz` | 稀疏表达矩阵 | 需配合 barcodes.tsv 和 features.tsv，三个文件缺一不可，但提交时常遗漏 |
| `.h5` / `.h5ad` | AnnData/HDF5 | 版本兼容性问题，anndata 0.7 vs 0.8 vs 0.10 不互相兼容 |
| `.rds` / `.RData` | Seurat / SingleCellExperiment | R 对象依赖 R 环境和特定包版本，跨语言读取困难 |
| `.csv` / `.csv.gz` / `.tsv` / `.tsv.gz` | 表格式 | 压缩/未压缩混用，分隔符不一致（逗号、制表符、分号） |
| `.xlsx` / `.xls` | 元信息/小规模表达 | Excel 的自动格式化破坏基因名（如 `MARCH1`→日期、`SEPT1`→日期） |
| `.loom` | loom 格式 | 已逐渐被弃用，生态萎缩 |
| `.txt` | 混杂 | 无格式约定，需人工推断结构 |
| `.zip` / `.tar.gz` | 多层打包 | 压缩包内嵌套压缩包，文件结构不可预知 |
| 自定义二进制 | 极少数 | 完全无兼容性 |

### 5.2 文件命名问题
- 无命名规范：`data.csv`、`matrix.txt`、`counts.tsv.gz` 等过于通用，多个文件间不可区分
- 文件名中的空格、特殊字符、非 ASCII 字符导致跨平台问题
- 文件名长度超限
- 文件名大小写不一致（Windows 上冲突，Linux 上歧义）

### 5.3 文件完整性问题
- 补充文件声明存在但实际无法下载（链接失效、权限错误）
- 文件截断（上传中断，大小不完整）
- 文件损坏（gz 文件无法解压，checksum 不匹配）
- 静默替换：同一文件名被覆盖为新版本而旧版本已不可回溯
- 补充文件的 `!Sample_supplementary_file_1` 等字段与实际文件数量和文件名不对应

### 5.4 数据规模与实际下载的矛盾
- 百万级细胞的 h5ad 文件可超过 100GB，GEO 的 HTTP 下载基础设施无法稳定支持
- 下载超时、断点续传不可靠
- 某些数据集需通过 GEO 外的第三方托管（如 SRA、dbGaP、EGA）获取，但缺失索引链接
- Aspera/FTP 等高速协议的支持不稳定

---

## 六、质量控制（QC）信息的缺失

### 6.1 报告欠缺的 QC 信息
- 空滴（empty droplets）过滤方法及阈值
- 双细胞（doublet）检测方法（Scrublet、DoubletFinder、solo 等）及去除标准
- 线粒体基因比例阈值
- 核糖体蛋白基因过滤策略
- 最小/最大基因数阈值
- 最小/最大 UMI 计数阈值
- 细胞周期校正是否执行、使用何种方法

### 6.2 数据阶段的模糊性
- 提交的数据是 raw counts（未经任何过滤）还是 filtered data（已去空滴和 doublet）？
- 若是 filtered，过滤策略的代码/脚本几乎从来不附
- 数据是 aligned+quantified 后的原始计数，还是经过了批次校正/归一化/插补？

### 6.3 参考基因组信息不透明
- 比对的参考基因组版本（hg19/hg38/mm10/mm39/dm6 等）
- 比对工具及版本（STAR、Cell Ranger、Kallisto、Salmon 等）
- 基因注释 GTF 文件来源及版本（Ensembl v?? / GENCODE v?? / RefSeq）
- 内含子区域是否包含在计数中（pre-mRNA vs mature mRNA）

---

## 七、跨模式 / 多模态数据问题

### 7.1 多模态数据的分离存储
- CITE-seq（RNA + ADT表面蛋白）数据可能拆分为两个独立文件，无跨模态细胞级对齐信息
- scATAC-seq + scRNA-seq 配对数据（如 10X Multiome）模态间关联记录缺失
- V(D)J 免疫组库数据（TCR/BCR）与 RNA 表达数据的关联路径难寻
- 空间转录组数据（Visium、MERFISH、Slide-seq）的组织位置坐标文件的格式多样

### 7.2 数据模态标识不明确
- 同一 GSE 包含多个 assay 类型但未在 GSM 级别明确标识
- 基于文库制备方法也无法准确推断数据模态（SMART-seq2 可能是全长转录组也可能是 targeted panel）
- library strategy 字段（`RNA-Seq` / `ssRNA-seq` / `scRNA-Seq`）取值混乱

---

## 八、跨 GSE / 跨平台整合问题

### 8.1 语义对齐问题
同一临床概念在不同研究中的标签集完全不同：
- "Control" / "Healthy" / "Normal" / "WT" / "Wild-type" / "Untreated" / "Baseline" / "Non-disease" / "Reference"
- "Tumor" / "Cancer" / "Tumour" / "Malignant" / "Neoplastic" / "Carcinoma"
- "Peripheral blood" / "PBMC" / "whole blood" / "venous blood"

### 8.2 单细胞实验平台异质性
- 不同平台的关键参数差异：
  - 10X Genomics v2 vs v3 vs v4（双端读取长度不同、UMI 长度不同）
  - SMART-seq2 / SMART-seq3（全长 vs UMI）
  - Drop-seq / inDrop / Seq-Well（微滴 vs 微孔 vs 微流控）
  - Microwell-seq / DNBelab C4（华大自主平台）
  - Parse Biosciences / SPLiT-seq（组合条形码分裂池策略）
- 平台的文库制备方向（3' vs 5' vs 全长）决定了基因覆盖区域，GEO 中极少记录

### 8.3 基因模型的不可比性
- 不同研究使用不同的基因注释文件，即使是同一物种
- 某些研究包含 lncRNA、pseudogene、非编码 RNA，另一些只包含 protein-coding genes
- 同一种细胞类型在不同研究中因基因集合差异而导致后续分析不兼容

---

## 九、语义和语言学问题

### 9.1 多语言元信息
- 中文临床注释（常见于国内课题组）对非中文 NLP 工具完全不友好
- 混合语言：英文缩写 + 中文描述混写于同一字段
- 翻译歧义：英文译名不统一，同一中文病名对应多个英文表达

### 9.2 缩写泛滥
- 组内约定缩写无定义：`d_34_NR_T` = "day 34, non-responder, treatment T"
- 领域术语缩写：`HCC`（肝细胞癌）、`TME`（肿瘤微环境）、`TIL`（肿瘤浸润淋巴细胞）
- 无对应全称，仅靠读者领域知识推测

### 9.3 Typo 与拼写错误
- `tretment`、`disease`、`Control`、`cancer`、`metastatis` 等拼写错误大量存在
- 难以通过简单的 fuzzy matching 区分是拼写错误还是不同的合法类别

### 9.4 自由文本的结构化幻觉
- 研究者可能在自由文本字段中写入结构化数据：“Age:45, Sex:M, BMI:23.5”
- 但也可能只是自然语言描述：“The patient is a 45-year-old male with BMI around 23.5”
- 同一字段在不同 GSMs 间格式互不统一，甚至同一 GSM 的同一字段在语句间格式变化

---

## 十、数据版本化与可重复性问题

### 10.1 GSE 的静默更新
- 同一 GSE 编号在提交后可以增、删、替换 GSM 和补充文件
- 提交者可能修正数据错误、添加新样本、重新分析，但极少留下 changelog
- 用户无法判断当前看到的版本与论文发表时的版本是否一致
- GEO 的 "Update date" 字段仅表示最近操作时间，不表示数据版本或稳定性

### 10.2 无数据溯源
- 数据从 raw fastq → aligned BAM → quantified counts → filtered+normalized matrix 的完整 pipeline 极少被以可执行形式提供
- 每个处理步骤的工具名、版本号、参数设置几乎从不被记录
- 结果的可复现性依赖于提交者后续配合度（通常为零）

### 10.3 撤回与修正的滞后
- 数据质量问题被发现后，GSE 记录被撤回或标记为 "suppressed" 的时效性差
- 已撤回数据集的下游衍生数据（被引用、被整合）无原子化追踪

---

## 十一、细胞注释（Cell Annotation）问题

### 11.1 细胞类型标签的王国混乱
- 同一细胞类型在文献中可能有 10+ 种标签变体
- 不同精度的层级标注共存于同一字段：`T cell` / `CD4+ T cell` / `CD4+ naive T cell` / `CD4+CCR7+SELL+ T cell`
- 自动注释（如 SingleR、CellTypist、Azimuth）与手工注释未区分记录
- 注释置信度分数几乎从不保存

### 11.2 细胞 barcode 问题
- barcode 格式：`AAACCCAAGAAACTCA-1`（10X 标准）、`AAACCCAAGAAACTCA`（无 suffix）
- 多来源数据的 barcode 前缀/后缀约定不一致
- demultiplexing hash-tag barcode 与 RNA barcode 无直接对应关系在记录中

### 11.3 无监督聚类与人工标注的断裂
- GEO 数据集中通常包含细胞类型终标签，但缺少 clustering resolution、marker gene 列表等中间产物
- 无法验证标注质量或重新生成不同的聚类方案

---

## 十二、数据访问与基础设施问题

### 12.1 GEO 查询检索的局限
- GEO 的 Web 检索界面功能原始，无法按单细胞属性精确过滤
- NCBI Entrez API 对单细胞数据的特殊字段（细胞数、技术平台、过滤状态）无支持
- 搜索结果无法区分 scRNA-seq 和 bulk RNA-seq
- 同一疾病的大量数据集分散在无关联的 GSE 编号下，缺乏 dataset-level 的聚合

### 12.2 下载基础设施瓶颈
- 大文件的 HTTP 单线程下载不稳定
- 无官方 torrent/magnet/checksum 分发
- 中国的国际带宽限制使得大规模 GEO 数据下载动辄数天
- GEO 未提供 rsync/aspera/globus 等科研常用高性能传输

### 12.3 人类受试者数据的准入复杂性
- 部分数据集需要 dbGaP 申请（dbGaP accession link 不在 GEO 中直接提供）
- EGA（European Genome-phenome Archive）托管的数据需要申请流程与 GEO 脱钩
- 同一项目的数据分散在 GEO + dbGaP + EGA，用户需要多次提交申请，审核流程独立且漫长

---

## 十三、解析自动化面临的核心挑战总结

### 13.1 启发式解析的脆弱性
- 任何基于 pattern matching 的解析方案都需要维护一个庞大的特例规则库
- 新增 GSE 数据的 pattern 可能打破之前的解析逻辑
- 字段推断高度依赖上下文，上下文的完整性无法保证

### 13.2 信息熵的不可逆损失
- 原始信息在上传时已经过大量主观压缩和筛选
- 研究者认为"不重要的信息"（而恰是整合分析的核心需求）已被丢弃
- 这种损失是不可恢复的，只能接受"数据是不完整的"

### 13.3 人工审核的成本陷阱
- 每个 GSE 的人工审核（curation）可能需要数小时到数天
- 手动提取的元信息精确度与个人经验高度相关
- 人工 curation 的可扩展性与 GEO 数据增长速度存在根本矛盾

---

*本文档旨在为 Scrut-Cell 项目的 GEO 数据获取与统一化模块提供需求参考和技术背景分析。*
