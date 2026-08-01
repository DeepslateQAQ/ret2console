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
