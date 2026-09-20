"""Local MCP server for coding agents (stdio).

Exposes: search_datasets / dataset_report (remote, via user API key),
plan_run (remote plan -> local executor -> feedback), plan_status.
Register this in the coding agent's MCP config; the user's key is read from the
``SCRUTICELL_API_KEY`` environment variable (like other agent API keys).
"""

from __future__ import annotations

from scruticell.client import api, run


def _build_mcp():
    from mcp.server.mcpserver import MCPServer

    from scruticell import __version__

    mcp = MCPServer(name="scruticell-client", version=__version__)

    @mcp.tool()
    def search_datasets(question: str = "", task: str = "", top_n: int = 10) -> dict:
        """检索公开组学数据集（远程，用户 API key 鉴权）。"""
        return api.search_datasets(question=question, task=task, top_n=top_n)

    @mcp.tool()
    def dataset_report(accession: str, deep: bool = False) -> dict:
        """取某数据集核验明细（远程）。"""
        return api.dataset_report(accession, deep=deep)

    @mcp.tool()
    def plan_run(accession: str, source: str = "", workdir: str = "") -> dict:
        """取远程处理方案并在本机执行（下载/解压/解析/转格式），逐步反馈服务端。"""
        return run.plan_run(accession, source=source, workdir=workdir or None)

    @mcp.tool()
    def plan_status(plan_id: str) -> dict:
        """查询服务端 plan 进度。"""
        return api.plan_status(plan_id)

    @mcp.tool()
    def research(question: str, session_id: str = "") -> dict:
        """与研究助手对话：提出科学问题 → 服务端嗅探数据并给出候选/澄清/处理方案。

        无 session_id 时开启新会话；续聊时把上次返回的 session_id 传回。返回
        {session_id, reply, status, done, result}；status 为 clarify（需补充
        信息）/ ready（result 含候选）／ timeout（本轮超预算，可重试）。数据实际
        下载/解析用 plan_run 在客户机执行。
        """
        sid = session_id.strip()
        out = api.research(question, sid)
        if not sid and out.get("session_id"):
            sid = out["session_id"]
        return {"session_id": sid, **{k: v for k, v in out.items() if k != "session_id"}}

    return mcp


def main() -> None:
    mcp = _build_mcp()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
