"""CLI for the coding-agent client mode.

Usage:
    scruticell client mcp                 # run local stdio MCP server
    scruticell client run --accession GSE1
    scruticell client keygen              # print the env config to use
"""

from __future__ import annotations

import click


@click.group("client", invoke_without_command=True)
@click.pass_context
def client_cmd(ctx):
    if ctx.invoked_subcommand is None:
        click.echo(client_help())


@client_cmd.command("mcp")
def mcp_run():
    """Run the local MCP server (stdio). Register in your coding agent."""
    from scruticell.client import mcp_server

    mcp_server.main()


@client_cmd.command("run")
@click.option("--accession", required=True)
@click.option("--source", default="")
@click.option("--workdir", default="")
def run_cmd(accession, source, workdir):
    """Create a plan on the server, execute it locally, feed results back."""
    from scruticell.client import run

    result = run.plan_run(accession, source=source, workdir=workdir or None)
    run.print_plan_result(result)


@client_cmd.command("keygen")
def keygen_cmd():
    from scruticell.client import api

    click.echo("把以下环境变量配到 coding agent 里（像 OPENAI_API_KEY 一样）：")
    click.echo(f"  SCRUTICELL_SERVER_URL={api.server_url()}")
    click.echo("  SCRUTICELL_API_KEY=<你在服务器 omics-discovery keygen 拿到的 key>")


@client_cmd.command("verify")
@click.option("--tier", default="all", help="A | B | all")
@click.option("--cases", default="", help="逗号分隔的用例 id，如 bulk_de,cite_seq")
@click.option("--out-dir", default="", help="输出目录")
@click.option("--max-mb", default=100, type=int, help="整数据集总大小上限(MB)")
@click.option("--concurrency", default=4, type=int, help="并发用例数")
@click.option("--matrix", is_flag=True, help="只打印用例矩阵")
def verify_cmd(tier, cases, out_dir, max_mb, concurrency, matrix):
    """跨组学 × 多源验证（真实服务端 + 真实客户端 + 真实下载）。"""
    from scruticell.client import verify

    if matrix:
        verify.print_matrix()
        missing = verify.matrix_complete()
        if missing:
            click.echo(f"矩阵缺口（每组学×可达源需 ≥1 A 层用例）：{missing}")
        return
    ids = [c.strip() for c in cases.split(",") if c.strip()] or None
    summary = verify.run_matrix(tier=tier, case_ids=ids, out_dir=out_dir or None,
                                max_mb=max_mb, concurrency=concurrency)
    verify.print_result(summary)


def client_help() -> str:
    return ("scruti-cell client（coding-agent 模式）\n"
            "  scruticell client mcp            # 本地 stdio MCP\n"
            "  scruticell client run --accession GSE1\n"
            "  scruticell client keygen         # 打印环境变量配置")
