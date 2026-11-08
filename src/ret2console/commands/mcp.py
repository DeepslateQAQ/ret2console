"""mcp 命令：REPL 内查看工具/状态/配置片段；serve 需在 shell 中运行（SPEC §19）。"""

from __future__ import annotations

from ret2console.commands import Command, register
from ret2console.output import console
from ret2console.state import App


def _parse_setup(args: list[str]) -> tuple[str | None, str | None]:
    agent: str | None = None
    profile: str | None = None
    i = 0
    while i < len(args):
        if args[i] == "--agent" and i + 1 < len(args):
            agent = args[i + 1]
            i += 2
        elif args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]
            i += 2
        else:
            i += 1
    return agent, profile


async def _mcp_dispatch(app: App, args: list[str]) -> int:
    sub = args[0] if args else "status"
    from ret2console.mcpserver import cli as mcp_cli
    if sub == "serve":
        console.print("[yellow]! mcp serve 需在 shell 中运行：r2c mcp serve[/yellow]")
        return 1
    if sub == "tools":
        mcp_cli.print_tools()
        return 0
    if sub == "status":
        mcp_cli.print_status(app)
        return 0
    if sub == "setup":
        agent, profile = _parse_setup(args[1:])
        return await mcp_cli.run_setup(agent=agent, profile=profile)
    console.print(f"[red]✗ 未知 mcp 子命令: {sub}（可用: serve/tools/setup/status）[/red]")
    return 1


def register_mcp() -> None:
    from ret2console.commands import REGISTRY
    if "mcp" in REGISTRY:
        return
    register(Command(
        "mcp", _mcp_dispatch, usage="mcp <serve|tools|setup|status> ...",
        description="MCP 服务（供外部 Agent 接入；serve 需在 shell 运行）", category="系统",
    ))
