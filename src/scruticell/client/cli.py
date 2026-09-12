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


def client_help() -> str:
    return ("scruti-cell client（coding-agent 模式）\n"
            "  scruticell client mcp            # 本地 stdio MCP\n"
            "  scruticell client run --accession GSE1\n"
            "  scruticell client keygen         # 打印环境变量配置")
