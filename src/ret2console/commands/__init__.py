"""命令注册与分发。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from ret2console.state import App

EXIT_CODE = -99  # 特殊退出信号


@dataclass(frozen=True)
class Command:
    name: str
    handler: Callable[[App, list[str]], Awaitable[int]]
    usage: str = ""
    description: str = ""
    category: str = "系统"
    aliases: tuple[str, ...] = ()
    # 需要登录
    requires_auth: bool = False
    # 需要当前比赛/题目上下文
    requires_game: bool = False
    requires_challenge: bool = False


REGISTRY: dict[str, Command] = {}


def register(cmd: Command) -> None:
    REGISTRY[cmd.name] = cmd
    for alias in cmd.aliases:
        REGISTRY.setdefault(alias, cmd)


def get_command(name: str) -> Command | None:
    return REGISTRY.get(name)


def command_names() -> list[str]:
    return sorted(REGISTRY.keys())


def commands_by_category() -> dict[str, list[Command]]:
    out: dict[str, list[Command]] = {}
    seen: set[str] = set()
    for cmd in REGISTRY.values():
        if cmd.name in seen:
            continue
        seen.add(cmd.name)
        out.setdefault(cmd.category, []).append(cmd)
    return out


def split_line(line: str) -> list[str]:
    """命令行分词：按空白切分，去掉首尾空白（flag 不含空格，无需引号解析）。"""
    return line.strip().split()


def report_service_error(error) -> int:
    """统一 ServiceError 输出（SPEC §9.1）：ambiguous 追加候选表，缺上下文黄色提示。"""
    from ret2console.output import console, make_table
    code = getattr(error, "code", "")
    message = getattr(error, "message", str(error))
    if code == "ambiguous":
        console.print(f"[red]✗ {message}[/red]")
        candidates = (getattr(error, "data", None) or {}).get("candidates") or []
        if candidates:
            table = make_table()
            keys = list(candidates[0].keys())
            for key in keys:
                table.add_column(
                    key.upper() if key == "id" else key,
                    style="bold" if key == "id" else "",
                )
            for cand in candidates:
                table.add_row(*(str(cand.get(key) or "—") for key in keys))
            console.print(table)
        return 1
    if code in ("no_game", "no_challenge", "no_team", "invalid_context"):
        console.print(f"[yellow]! {message}[/yellow]")
        return 1
    console.print(f"[red]✗ {message}[/red]")
    return 1
