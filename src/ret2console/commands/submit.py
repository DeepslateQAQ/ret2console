"""提交命令：submit [flag]（无参进入安全粘贴模式，flag 从 stdin/交互输入读取）。"""

from __future__ import annotations

import asyncio
import os

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.flag import extract_flag
from ret2console.interact import ask
from ret2console.output import console
from ret2console.state import App


def _apply_shell_context(app: App) -> None:
    """子 shell 注入通道：`ret2console -c 'submit -'` 是独立进程，
    从 R2C_GAME_ID / R2C_CHALLENGE_ID 恢复 REPL 里的题目上下文。"""
    if app.ctx.game_id is not None:
        return
    gid = os.environ.get("R2C_GAME_ID")
    cid = os.environ.get("R2C_CHALLENGE_ID")
    if gid and gid.isdigit() and cid and cid.isdigit():
        app.ctx.set_challenge(int(gid), int(cid))


async def _poll_submission(
    app: App, gid: int, cid: int, submission_id: int, timeout: float = 8.0,
) -> dict | None:
    """轮询评测队列：GET /submit?id=... 直到 solved 有值或超时。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    checks = 0
    while True:
        await asyncio.sleep(0.8)
        checks += 1
        try:
            status = await app.api.get_submission(gid, cid, submission_id)
        except ApiError:
            return None
        if status.get("solved") is not None:
            return status
        if checks >= 10 or loop.time() >= deadline:
            return None


def _report_result(status: dict) -> None:
    solved = status.get("solved")
    result_text = status.get("result") or status.get("status") or ""
    if solved is True:
        console.print("[bold green]✓ 提交成功，flag 正确！[/bold green]")
    elif solved is False:
        console.print("[bold red]✗ flag 错误[/bold red]")
        if result_text:
            console.print(f"[dim]{result_text}[/dim]")
    else:
        console.print(f"提交结果: {result_text or status}")


async def _do_submit(app: App, flag: str) -> int:
    if not app.session.logged_in:
        console.print("[red]✗ 未登录：请先 auth login[/red]")
        return 1
    if app.ctx.game_id is None or app.ctx.challenge_id is None:
        console.print("[yellow]! 未指定题目：请先 cd /contests/<比赛>/challenges/<题目>[/yellow]")
        return 1
    console.print("[dim]正在提交...[/dim]")
    try:
        result = await app.api.submit_flag(app.ctx.game_id, app.ctx.challenge_id, flag)
    except ApiError as e:
        console.print(f"[red]✗ 提交失败: {e.message}[/red]")
        return 1
    submission_id = result.get("id")
    if submission_id is None:
        # 后端同步返回了结果（兼容旧行为）
        _report_result(result)
        return 0
    console.print(f"[yellow]➜ 已进入评测队列（#{submission_id}），等待结果...[/yellow]")
    status = await _poll_submission(app, app.ctx.game_id, app.ctx.challenge_id, int(submission_id))
    if status is None or status.get("solved") is None:
        console.print("[yellow]! 评测仍在进行，稍后重试或重新提交可查询结果[/yellow]")
        return 0
    _report_result(status)
    return 0


async def cmd_submit(app: App, args: list[str]) -> int:
    _apply_shell_context(app)
    if args and args[0] == "-":
        # stdin 通道（子 shell 注入使用，零注入风险）
        import sys
        flag = sys.stdin.read().strip()
        if not flag:
            console.print("[red]✗ stdin 为空[/red]")
            return 1
        return await _do_submit(app, flag)
    if args:
        return await _do_submit(app, args[0])
    # 安全粘贴模式：不回显、不进历史
    try:
        text = await ask("请输入 flag（不回显）", password=True)
    except (KeyboardInterrupt, EOFError):
        console.print("\n已取消")
        return 1
    flag = extract_flag(text)
    if flag is None:
        console.print("[red]✗ 输入内容不符合 flag 格式（如 flag{...}）[/red]")
        return 1
    return await _do_submit(app, flag)


def register_submit() -> None:
    register(Command("submit", cmd_submit, usage="submit [flag | -]", description="提交 flag；无参进入安全粘贴模式；- 从 stdin 读取", category="提交", requires_challenge=True))
