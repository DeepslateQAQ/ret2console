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
from ret2console.commands.contest import register_contest
from ret2console.commands.files import register_files
from ret2console.commands.scoreboard import register_scoreboard
from ret2console.commands.shell import register_shell
from ret2console.commands.submit import register_submit
from ret2console.commands.team import register_team
from ret2console.output import console, is_tty
from ret2console.repl import dispatch, run_repl
from ret2console.state import App
from ret2console.vfs_builder import build_vfs
from ret2console.wsrx import ensure_wsrx, register_control_source, stop_daemon, wsrx_origin

USAGE = """ret2console — Ret2Shell 终端接口

用法:
  r2c                 进入交互式终端（REPL）
  r2c -c '<命令>'     执行一条命令后退出（; 分隔多条）
  r2c <脚本文件>      执行脚本文件（支持 shebang）

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
    register_team()
    register_shell()
    register_autoplay()


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
        app.wsrx = ensure_wsrx(origin=wsrx_origin(app.config.api_base))
        if app.wsrx.mode == "none":
            console.print("[yellow]! wsrx 不可用（未检测到 wsrx-desktop，且 daemon 启动失败），challenge connect 无法使用[/yellow]")
        elif app.wsrx.mode == "desktop" and mode == "repl":
            # 控制源显示名对齐 r2s 平台配置（如「西电 CTF 终端」），拿不到就兜底
            source_name = await app.api.platform_name() or "ret2console"
            status = register_control_source(app.wsrx, name=source_name)
            if status == "approved":
                console.print(f"[dim]✓ wsrx-desktop（127.0.0.1:{app.wsrx.api_port}）已授权控制源「{source_name}」[/dim]")
            elif status == "pending":
                console.print(f"[yellow]! wsrx-desktop 授权待确认：请在弹窗中允许「{source_name}」管理隧道[/yellow]")
            else:
                console.print(f"[dim]wsrx-desktop（127.0.0.1:{app.wsrx.api_port}）已检测到（控制源未授权，隧道仍可建立）[/dim]")
        elif app.wsrx.mode == "daemon" and mode == "repl":
            console.print(f"[dim]✓ wsrx 后台 daemon 已就绪（127.0.0.1:{app.wsrx.api_port}，{app.wsrx.source}）[/dim]")
        if mode == "script":
            return await _run_script(app, payload)
        return await run_repl(app)
    finally:
        # REPL 退出即清理 daemon（`-c`/脚本模式保留：后台任务语义）
        if mode == "repl" and app.wsrx.mode == "daemon":
            stop_daemon()
        await app.api.close()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _register_all()

    if argv and argv[0] in ("-h", "--help"):
        console.print(USAGE)
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
