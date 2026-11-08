"""入口：REPL / -c 一次性命令 / shebang 脚本（SPEC §3）。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ret2console.commands import EXIT_CODE
from ret2console.commands.admin import register_admin
from ret2console.commands.auth import register_auth
from ret2console.commands.autoplay import register_autoplay
from ret2console.commands.basic import register_basic
from ret2console.commands.challenge import register_challenge
from ret2console.commands.completion import register_completion
from ret2console.commands.contest import register_contest
from ret2console.commands.files import register_files
from ret2console.commands.man import register_man
from ret2console.commands.mcp import register_mcp
from ret2console.commands.scoreboard import register_scoreboard
from ret2console.commands.shell import register_shell
from ret2console.commands.submission import register_submission
from ret2console.commands.submit import register_submit
from ret2console.commands.team import register_team
from ret2console.output import console, is_tty
from ret2console.repl import dispatch, run_repl
from ret2console.state import App
from ret2console.termimg import detect_protocol
from ret2console.vfs_builder import build_vfs

USAGE = """ret2console — Ret2Shell 终端接口

用法:
  r2c                 进入交互式终端（REPL）
  r2c -c '<命令>'     执行一条命令后退出（; 分隔多条）
  r2c <脚本文件>      执行脚本文件（支持 shebang）
  r2c mcp serve       启动 MCP stdio 服务（供外部 Agent 接入）
  r2c completion <bash|zsh|fish>  生成 shell 补全脚本

选项:
  -c <命令>   执行命令后退出
  -h, --help  显示本帮助

交互式终端内输入 help 查看全部命令，Tab 补全。
"""


def _register_all() -> None:
    register_admin()
    register_basic()
    register_auth()
    register_contest()
    register_challenge()
    register_submit()
    register_scoreboard()
    register_files()
    register_man()
    register_team()
    register_shell()
    register_autoplay()
    register_submission()
    register_completion()
    register_mcp()


def _build_app() -> App:
    app = App()
    def _on_token(token: str | None) -> None:
        app.session.token = token
        app.session.save()

    app.api.on_token = _on_token
    build_vfs(app)
    return app


async def _run_script(app: App, lines: list[str]) -> int:
    code = 0
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        code = await dispatch(app, line)
        if code == EXIT_CODE:
            return 0
        if code != 0:
            return code
    return code


def _split_commands(text: str) -> list[str]:
    return text.split(";")


async def _run(mode: str, payload: list[str]) -> int:
    app = _build_app()
    try:
        if mode == "repl":
            # 终端图片协议检测（REPL 接管终端前做一次，cat 图片/附件用）
            app.graphics = detect_protocol()
        if mode == "script":
            return await _run_script(app, payload)
        return await run_repl(app)
    finally:
        await app.wsrx.close()
        await app.api.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _register_all()

    if argv and argv[0] == "mcp":
        # `r2c mcp serve` 需要在 shell 中直接拉起，先于任何 App/wsrx 初始化
        from ret2console.mcpserver import cli as mcp_cli
        return asyncio.run(mcp_cli.main(argv[1:]))

    if argv and argv[0] in ("-h", "--help"):
        console.print(USAGE)
        return 0

    if argv and argv[0] in ("-v", "-V", "--version", "version"):
        from ret2console.repl import print_version_banner
        print_version_banner()
        return 0

    if argv and argv[0] == "-c":
        script = " ".join(argv[1:]) if len(argv) > 1 else ""
        return asyncio.run(_run("script", _split_commands(script)))

    if argv and not argv[0].startswith("-"):
        # shebang / 脚本文件模式
        script_path = Path(argv[0])
        if not script_path.is_file():
            console.print(f"[red]✗ 找不到脚本文件: {script_path}[/red]")
            return 127
        lines = script_path.read_text(encoding="utf-8").splitlines()
        return asyncio.run(_run("script", lines))

    # REPL
    return asyncio.run(_run("repl", []))


if __name__ == "__main__":
    raise SystemExit(main())
