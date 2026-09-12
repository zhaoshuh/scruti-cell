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
| `search_datasets(question, task?, top_n?)` | 远程检索（用户 key） |
| `dataset_report(accession, deep?)` | 远程核验明细 |
| `plan_run(accession, source?, workdir?)` | 服务端出方案 → **本机执行** → 逐步反馈 |
| `plan_status(plan_id)` | 查询服务端 plan 进度 |

## 源无关
服务端规划 Agent 用 omics-discovery 的访问知识（GEO/AE/Zenodo/HF/Dryad/OSF/figshare/
DataCite…文件端点）把任何源的 accession 变成通用方案；客户端只按方案执行，不认识源。
仅元数据源（cellxgene/HCA/GDC/SCXA/ENA/PRIDE/NGDC）作参考入口，不预处理。

## 典型用法
1. `search_datasets("2026 Virtual Cell Challenge 的 CRISPRi Perturb-seq", top_n=10)`
2. `plan_run("GSE308682")` → 本机下载/处理，逐步反馈，直到 `done`
3. 查看本机 `output/client/preprocessed/<GSE>.scruticell.h5ad`