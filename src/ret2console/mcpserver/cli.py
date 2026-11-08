"""`r2c mcp` 子命令：serve / tools / setup / status（SPEC §19）。

`tools` / `setup` 不构建 App（不需要凭据）；`serve` / `status` 才加载配置。
配置片段只打印，绝不写入任何 Agent 配置文件。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any

from ret2console import __version__
from ret2console.config import Config, valid_profile_name
from ret2console.mcpserver import server
from ret2console.mcpserver.protocol import LEGACY_VERSIONS, MODERN_VERSIONS
from ret2console.mcpserver.tools import TOOLS
from ret2console.output import console, is_tty
from ret2console.state import App
from ret2console.vfs_builder import build_vfs

_SETUP_AGENTS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
    "omp": "omp",
}

_SHARED_VALUES = {"--profile": "profile", "--url": "url", "--token": "token"}
_SHARED_FLAGS = {"--insecure": "insecure", "--verbose": "verbose"}


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


def _strip_shared(argv: list[str]) -> tuple[dict[str, Any], list[str]]:
    """提前剥离全局选项，使 `r2c mcp serve --profile work` 也可解析。"""
    options: dict[str, Any] = {
        "profile": None, "url": None, "token": None, "insecure": False, "verbose": False,
    }
    rest: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        name, _, inline = arg.partition("=")
        if name in _SHARED_FLAGS:
            options[_SHARED_FLAGS[name]] = True
        elif name in _SHARED_VALUES:
            if inline:
                options[_SHARED_VALUES[name]] = inline
            elif i + 1 < len(argv):
                i += 1
                options[_SHARED_VALUES[name]] = argv[i]
            else:
                raise ValueError(f"{name} 需要参数")
        else:
            rest.append(arg)
        i += 1
    return options, rest


def build_app(
    *, profile: str | None = None, url: str | None = None,
    token: str | None = None, insecure: bool = False,
) -> App:
    """按覆盖规则构建 App：--url 覆盖时不带 profile token。"""
    if profile is not None and not valid_profile_name(profile):
        raise ValueError(f"无效的 profile 名称: {profile}")
    if url is not None and not re.match(r"^https?://", url):
        raise ValueError("无效的 URL（需以 http:// 或 https:// 开头）")
    cfg = Config.load(profile=profile)
    app = App(config=cfg)
    persist = url is None and token is None

    def _on_token(new_token: str | None) -> None:
        app.session.token = new_token
        if persist:
            app.session.save()

    app.api.on_token = _on_token
    effective_insecure = insecure or cfg.insecure
    if url is not None:
        app.api.configure(base_url=url.rstrip("/"), token=token, insecure=effective_insecure)
        app.session.token = token
    elif token is not None:
        app.api.configure(base_url=cfg.api_base, token=token, insecure=effective_insecure)
        app.session.token = token  # 仅覆盖运行时登录态，不落盘（persist=False）
    app.api.reset_client()
    build_vfs(app)
    return app


def print_tools() -> None:
    from ret2console.output import make_table
    table = make_table(title="MCP 工具")
    table.add_column("工具", style="bold")
    table.add_column("说明")
    for tool in sorted(TOOLS, key=lambda t: t.name):
        table.add_row(tool.name, tool.description)
    console.print(table)
    console.print(
        "[dim]在 shell 中运行 r2c mcp serve 启动 stdio 服务；"
        "r2c mcp setup 打印 Agent 配置片段[/dim]"
    )


def print_status(app: App) -> None:
    from ret2console.output import make_table
    table = make_table(show_header=False)
    table.add_column("字段", style="dim", width=10)
    table.add_column("值")
    table.add_row("版本", __version__)
    table.add_row("profile", app.profile_name)
    table.add_row("endpoint", app.config.api_base)
    table.add_row(
        "登录", app.session.display_name if app.session.logged_in else "未登录"
    )
    table.add_row("账号", app.session.account or "—")
    table.add_row("协议版本", f"{LEGACY_VERSIONS[-1]} / {MODERN_VERSIONS[-1]}")
    table.add_row("工具数", str(len(TOOLS)))
    console.print(table)


def _stdio(command: str, args: list[str]) -> dict[str, Any]:
    return {"type": "stdio", "command": command, "args": args}


def _print_json(data: Any) -> None:
    console.print(json.dumps(data, ensure_ascii=False), markup=False, soft_wrap=True)


def _print_setup(agent: str, profile: str | None) -> None:
    serve_args = ["mcp", "serve"] + (["--profile", profile] if profile else [])
    if agent == "claude":
        console.print("[bold]Claude Code[/bold]")
        command = " ".join([
            "claude", "mcp", "add", "--transport", "stdio", "--scope", "user",
            "r2c", "--", "r2c", *serve_args,
        ])
        console.print(f"$ {command}", markup=False, soft_wrap=True)
        console.print("[dim].mcp.json[/dim]")
        _print_json({"mcpServers": {"r2c": _stdio("r2c", serve_args)}})
        return
    if agent == "codex":
        console.print("[bold]Codex[/bold]")
        command = " ".join(["codex", "mcp", "add", "r2c", "--", "r2c", *serve_args])
        console.print(f"$ {command}", markup=False, soft_wrap=True)
        console.print("[dim]~/.codex/config.toml[/dim]")
        console.print("[mcp_servers.r2c]", markup=False)
        console.print('command = "r2c"', markup=False)
        console.print(f"args = {json.dumps(serve_args)}", markup=False)
        return
    if agent == "opencode":
        console.print("[bold]OpenCode[/bold]")
        console.print("[dim]~/.config/opencode/opencode.json[/dim]")
        _print_json({
            "mcp": {
                "r2c": {
                    "type": "local",
                    "command": ["r2c", *serve_args],
                    "enabled": True,
                }
            }
        })
        return
    console.print("[bold]omp[/bold]")
    console.print("[dim]~/.omp/agent/mcp.json[/dim]")
    _print_json({"mcpServers": {"r2c": _stdio("r2c", serve_args)}})


async def run_setup(*, agent: str | None, profile: str | None) -> int:
    if profile is not None and not valid_profile_name(profile):
        console.print(f"[red]✗ 无效的 profile 名称: {profile}[/red]")
        return 1
    if agent is None:
        if not is_tty():
            console.print("[red]✗ 非交互模式需要 --agent claude|codex|opencode|omp[/red]")
            return 1
        from ret2console.interact import select
        try:
            index = await select("选择要接入的 Agent", list(_SETUP_AGENTS.values()))
        except (KeyboardInterrupt, EOFError):
            console.print("\n已取消")
            return 1
        agent = list(_SETUP_AGENTS)[index]
    if agent not in _SETUP_AGENTS:
        console.print(
            f"[red]✗ 未知 Agent: {agent}（可用: claude/codex/opencode/omp）[/red]"
        )
        return 1
    _print_setup(agent, profile)
    console.print("[green]✓[/green] 已生成配置片段（未写入任何文件）")
    return 0


def _parse_args(rest: list[str]) -> tuple[argparse.Namespace | None, int | None]:
    parser = argparse.ArgumentParser(
        prog="r2c mcp", description="ret2console MCP stdio 服务（供外部 Agent 接入）"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="启动 MCP stdio 服务")
    sub.add_parser("tools", help="列出 MCP 工具")
    setup = sub.add_parser("setup", help="打印 Agent 配置片段")
    setup.add_argument("--agent", choices=sorted(_SETUP_AGENTS))
    sub.add_parser("status", help="显示 MCP 服务配置与登录态")
    try:
        return parser.parse_args(rest), None
    except SystemExit as e:
        return None, int(e.code or 0)


async def main(argv: list[str]) -> int:
    try:
        options, rest = _strip_shared(list(argv))
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    profile = options["profile"] or _env("R2C_PROFILE")
    url = options["url"] or _env("R2C_API_BASE")
    token = options["token"] or _env("R2C_TOKEN")
    if profile is not None and not valid_profile_name(profile):
        console.print(f"[red]✗ 无效的 profile 名称: {profile}[/red]")
        return 1

    args, code = _parse_args(rest)
    if code is not None:
        return code
    if args is None or not args.command:
        console.print("用法: r2c mcp <serve|tools|setup|status>")
        return 1
    if args.command == "tools":
        print_tools()
        return 0
    if args.command == "setup":
        return await run_setup(agent=args.agent, profile=profile)

    try:
        app = build_app(profile=profile, url=url, token=token, insecure=options["insecure"])
    except ValueError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    if args.command == "status":
        print_status(app)
        await app.api.close()
        return 0
    return await server.serve(app, verbose=options["verbose"])


if __name__ == "__main__":  # pragma: no cover - 直接 python -m 调试
    raise SystemExit(__import__("asyncio").run(main(sys.argv[1:])))
