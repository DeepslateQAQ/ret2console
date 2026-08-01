"""子 shell 命令：REPL 内输入 bash/zsh/fish 交互式启动并注入 flag handler（SPEC §11）。"""

from __future__ import annotations

from ret2console.commands import Command, register
from ret2console.output import console
from ret2console.shells import launch_shell


def _make_handler(name: str):
    async def handler(app, args):
        if args:
            console.print(f"[red]✗ {name}: 不支持参数[/red]")
            return 1
        return await launch_shell(app, name)
    return handler


def register_shell() -> None:
    for name in ("bash", "zsh", "fish"):
        register(Command(
            name, _make_handler(name), usage=name,
            description=f"交互式启动 {name}（已注入 flag 自动提交 handler）",
            category="系统",
        ))
