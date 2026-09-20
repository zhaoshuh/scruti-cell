# MCP 集成：装进 coding agent 的客户端（重活本地跑）

架构是**「服务端规划 Agent + 客户端执行器」**：

- **服务端（轻量）**：`omics-discovery` 提供检索/核验，以及 scruti-cell **规划 Agent**
  （`/api/plan/*`），把任意源的 accession 消解成**源无关的分步处理方案**（下载 URL 清单、
  文件→样本映射、每样本模态、解析指令、目标布局）。用户用 `omics-discovery keygen`
  拿到 API key。
- **客户端（客户机，重活本地）**：`scruti-cell client` 是**装进 coding agent** 的本地
  MCP + 确定性执行器：按服务端方案在**本机**下载/解压/解析/转 AnnData，逐步把结果
  反馈给服务端 Agent，循环直至完成。**客户端不跑 LLM、不认识数据源**（源适配在服务端）。

## 流程
```
agent: search_datasets("...")  → 远程 /api/search（用户 key）
     → 选 accession
     → plan_run(accession)      → 服务端 /api/plan/create 产出分步方案
     → 客户端本机执行当前步（下载/解压/解析/转格式）
     → /api/plan/step 反馈结果/错误
     → 服务端 Agent 诊断/修复/下一步 → 循环 → done
```

## 安装（自带独立 venv）
```bash
bash /data/scruti-cell/scripts/install_client.sh
```
在 `~/.scruticell/client-venv` 建隔离 venv 并安装。之后把 MCP 注册进 coding agent：

**opencode**（`opencode.json`）
```json
{ "mcp": { "scruticell": {
    "type": "local",
    "command": ["$HOME/.scruticell/client-venv/bin/scruticell-client", "mcp"],
    "enabled": true } } }
```
**Claude Code**
```bash
claude mcp add --transport stdio scruticell $HOME/.scruticell/client-venv/bin/scruticell-client mcp
```
**Codex**（`~/.codex/config.toml`）
```toml
[mcp_servers.scruticell]
command = "$HOME/.scruticell/client-venv/bin/scruticell-client"
args = ["mcp"]
```

## 环境变量（像 API key 一样配给 agent）
```
SCRUTICELL_SERVER_URL=http://<server>:8000
SCRUTICELL_API_KEY=<omics-discovery keygen 生成的 key>
```

## 工具
| 工具 | 作用 |
|---|---|
| `research(question, session_id?)` | **对话式研究**：提出科学问题 → 服务端 agent 嗅探数据（检索/核验/可行性/方案），多轮（传回 session_id）双向指导 |
| `search_datasets(question, task?, top_n?)` | 远程快速检索（用户 key） |
| `dataset_report(accession, deep?)` | 远程核验明细 |
| `plan_run(accession, source?, workdir?)` | 服务端出方案 → **本机执行** → 逐步反馈 |
| `plan_status(plan_id)` | 查询服务端 plan 进度 |

## 对话式「嗅探 → 引导处理」
```txt
用户: 研究XX的单细胞表达
opencode → research("研究XX…")            # 开启会话，服务端 agent 嗅探
服务端: 澄清提问 / 候选数据集+处理方案（调 plan_dataset 评估可行性：整数据集 ≤100MB、含可解析矩阵）
用户: 回答/改选/纠偏 → research(ans, session_id)
opencode: 对 agent 推荐的可处理数据集 → plan_run(accession)  → 本机 h5ad
用户: 后续 → research(...) 继续
```
- **服务端只规划+嗅探**；数据实际下载/解析/对齐全在客户机（opencode 调 `plan_run`）。
- agent 的 `plan_dataset` 工具返回 `row_type / storage / 文件数 / 总大小 / 是否可行 / 步骤`，
  并给出处理命令；不可行会说明原因（超大小上限/无矩阵/无文件清单）。

## 代码不泄露（重要）
- 服务端（omics-discovery）的代码、知识（`knowledge/*.md`）、源适配器、API key **只存在于服务器**；
  新会话唯一入口是远程 HTTP API（Bearer key），只见结构化输入输出，不回显任何内部资产。
- **新 opencode 会话请跑在干净工作区**（不挂载 omics-discovery / scruti-cell 仓库源码），仅通过
  `SCRUTICELL_SERVER_URL`/`SCRUTICELL_API_KEY` 访问服务——否则 agent 有文件访问权时可读源码。
- 客户端（scruti-cell）是通用执行器（下载/解压/解析/对齐），不含知识/适配器/密钥，本机可见可接受。

## 源无关
服务端规划 Agent 用 omics-discovery 的访问知识（GEO/AE/Zenodo/HF/Dryad/OSF/figshare/
DataCite…文件端点）把任何源的 accession 变成通用方案；客户端只按方案执行，不认识源。
仅元数据源（cellxgene/HCA/GDC/SCXA/ENA/PRIDE/NGDC）作参考入口，不预处理。

## 跨源元数据发现（每次 plan 必做）
一个数据集的元数据**不一定在它被找到的那个源**（可能在镜像、其他仓库、门户）。
服务端 `locate_dataset_metadata` 每个 plan 都会做：
- **镜像**：E-GEOD ↔ GSE 互查，取权威文件清单；
- **标题跨源检索**：Zenodo/Dryad/OSF/HF/figshare/DataCite/ArrayExpress（并发 + 全局时限，网络差也能快速降级），找同研究的其它沉积；
- **门户**：已注册门户作为参考。
结果进 plan 的 `metadata_manifest`（`primary` / `mirror` / `related`）；与主清单重复的文件自动去重。

## 观测轴感知对齐 + 经验化存储
每种组学/实验设计**适合的存储表示**保存在服务端经验知识（`knowledge/experience/*.md`，
调研自 Scanpy/Seurat/Signac/ArchR/DESeq2/Squidpy 等科研惯例），不硬编码：
- **row_type**：scrna/snrna/scatac/multiome → `barcode`；bulk/芯片/甲基化 → `sample`；空间 → `spot`；
- **storage 规范**：容器（v1 统一 h5ad + 侧车）、obs 字段、obsm/uns、侧车清单（fragments/peaks/图像/tissue_positions）；
- **plan 步骤**：`download` → `metadata_download`(跨源) → `decompress` → `map_samples`(真实分组+row_type) → `classify` → `parse_matrix`(按行定义文件设 obs_names/var) → **`align`**(样本/GSM 元数据并入 `.obs`：bulk 按行匹配、barcode/spot 上卷为 `sample.*` 常量列；空间坐标入 `obsm["spatial"]`) → `assemble` → `report`。
客户端只执行 plan 里的存储/对齐规范，仍不认识源、不内置经验。

## 典型用法
1. `search_datasets("2026 Virtual Cell Challenge 的 CRISPRi Perturb-seq", top_n=10)`
2. `plan_run("GSE308682")` → 本机下载/处理，逐步反馈，直到 `done`
3. 查看本机 `output/client/preprocessed/<GSE>.scruticell.h5ad`（obs 已带 `sample.*` 元数据；空间数据带 `obsm["spatial"]`）