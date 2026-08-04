"""子 shell 命令：REPL 内交互式启动本机 shell（SPEC §11）。

平台相关：非 Windows 注册 bash/zsh/fish（注入 flag 自动提交 handler）；
Windows 下隐藏三者，换为 cmd/pwsh/powershell（cmd 无注入，仅提示；
pwsh/powershell 注入 flag 自动提交 handler）。
"""

from __future__ import annotations

from ret2console.commands import Command, register
from ret2console.output import console
from ret2console.shells import launch_shell, shell_names


def _make_handler(name: str):
    async def handler(app, args):
        if args:
            console.print(f"[red]✗ {name}: 不支持参数[/red]")
            return 1
        return await launch_shell(app, name)
    return handler


def register_shell() -> None:
    for name in shell_names():
        if name == "cmd":
            description = "交互式启动 cmd（不支持 flag 自动提交）"
        else:
            description = f"交互式启动 {name}（已注入 flag 自动提交 handler）"
        register(Command(
            name, _make_handler(name), usage=name,
            description=description,
            category="系统",
        ))
