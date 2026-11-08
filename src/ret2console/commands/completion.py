"""shell 补全命令：completion <bash|zsh|fish>（SPEC §10）。

脚本直接写 stdout（不经 Rich，避免 `[` 被当成标记解析），或 `--output` 落盘。
"""

from __future__ import annotations

import sys
from pathlib import Path

from ret2console.commands import Command, command_names, get_command, register
from ret2console.output import console, is_tty
from ret2console.state import App

_SHELLS = ("bash", "zsh", "fish")


def _description(name: str) -> str:
    cmd = get_command(name)
    return cmd.description if cmd is not None else ""


def _command_table() -> dict[str, list[str]]:
    """二级命令表：仅规范命令名（别名不重复列子命令）。"""
    from ret2console.repl import _SUBCOMMANDS
    table: dict[str, list[str]] = {}
    for name in command_names():
        cmd = get_command(name)
        if cmd is None or cmd.name != name:
            continue
        table[name] = list(_SUBCOMMANDS.get(name, []))
    return table


def _posix_quote(value: str) -> str:
    """bash/zsh 单引号字符串：`'` → `'\\''`，反斜杠转义。"""
    return "'" + value.replace("\\", "\\\\").replace("'", "'\\''") + "'"


def _fish_quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _bash_script(names: list[str], table: dict[str, list[str]]) -> str:
    lines = [
        "# bash completion for ret2console / r2c",
        "_r2c_completions() {",
        "    local cur",
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        '    if [ "${COMP_CWORD}" -eq 1 ]; then',
        f"        COMPREPLY=( $(compgen -W '{' '.join(names)}' -- \"${{cur}}\") )",
        "        return 0",
        "    fi",
        '    case "${COMP_WORDS[1]}" in',
    ]
    for name in sorted(table):
        subs = table[name]
        if not subs:
            continue
        lines += [
            f"        {name})",
            '            if [ "${COMP_CWORD}" -eq 2 ]; then',
            f"                COMPREPLY=( $(compgen -W '{' '.join(subs)}' -- \"${{cur}}\") )",
            "            fi",
            "            ;;",
        ]
    lines += [
        "    esac",
        "    return 0",
        "}",
        "complete -F _r2c_completions r2c ret2console",
    ]
    return "\n".join(lines) + "\n"


def _zsh_script(names: list[str], table: dict[str, list[str]]) -> str:
    lines = [
        "#compdef r2c ret2console",
        "_r2c() {",
        "    local -a _r2c_commands",
        "    _r2c_commands=(",
    ]
    for name in names:
        desc = _description(name).replace(":", "：")
        entry = f"{name}:{desc}" if desc else name
        lines.append(f"        {_posix_quote(entry)}")
    lines += [
        "    )",
        "    if (( CURRENT == 2 )); then",
        "        _describe 'command' _r2c_commands",
        "        return",
        "    fi",
        "    case $words[2] in",
    ]
    for name in sorted(table):
        subs = table[name]
        if not subs:
            continue
        lines += [
            f"        {name})",
            "            if (( CURRENT == 3 )); then",
            "                local -a _r2c_subs",
            "                _r2c_subs=(" + " ".join(_posix_quote(s) for s in subs) + ")",
            "                _values " + _posix_quote(name) + " ${_r2c_subs[@]}",
            "            fi",
            "            ;;",
        ]
    lines += ["    esac", "}", '_r2c "$@"']
    return "\n".join(lines) + "\n"


def _fish_script(names: list[str], table: dict[str, list[str]]) -> str:
    lines = [
        "# fish completion for ret2console / r2c",
        "complete -c r2c -f",
        "complete -c ret2console -f",
    ]
    for name in names:
        desc = _description(name)
        suffix = f" -d {_fish_quote(desc)}" if desc else ""
        for binary in ("r2c", "ret2console"):
            lines.append(
                f"complete -c {binary} -n '__fish_use_subcommand' -a "
                f"{_fish_quote(name)}{suffix}"
            )
    for name in sorted(table):
        subs = table[name]
        if not subs:
            continue
        for binary in ("r2c", "ret2console"):
            lines.append(
                f"complete -c {binary} -n '__fish_seen_subcommand_from {name}' -a "
                f"{_fish_quote(' '.join(subs))}"
            )
    return "\n".join(lines) + "\n"


def _build(shell: str) -> str:
    names = command_names()
    table = _command_table()
    if shell == "bash":
        return _bash_script(names, table)
    if shell == "zsh":
        return _zsh_script(names, table)
    return _fish_script(names, table)


def _parse(args: list[str]) -> tuple[str | None, str | None, bool, str | None]:
    shell: str | None = None
    output: str | None = None
    force = False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--force":
            force = True
        elif arg == "--output":
            if i + 1 >= len(args):
                return None, None, force, "--output 需要参数"
            i += 1
            output = args[i]
        elif arg.startswith("--output="):
            output = arg.split("=", 1)[1]
        elif arg.startswith("-") and arg != "-":
            return None, None, force, f"未知选项: {arg}"
        elif shell is None:
            shell = arg
        else:
            return None, None, force, f"多余的参数: {arg}"
        i += 1
    return shell, output, force, None


async def cmd_completion(app: App, args: list[str]) -> int:
    shell, output, force, error = _parse(args)
    if error is not None:
        console.print(f"[red]✗ {error}[/red]")
        return 1
    if shell is None:
        if not is_tty():
            console.print("[red]✗ 用法: completion <bash|zsh|fish> [--output PATH] [--force][/red]")
            return 1
        from ret2console.interact import select
        try:
            index = await select("选择 shell", list(_SHELLS))
        except (KeyboardInterrupt, EOFError):
            console.print("\n已取消")
            return 1
        shell = _SHELLS[index]
    if shell not in _SHELLS:
        console.print(f"[red]✗ 未知 shell: {shell}（可用: bash/zsh/fish）[/red]")
        return 1
    script = _build(shell)
    if output is not None and output != "-":
        target = Path(output)
        if target.exists() and not force:
            console.print(f"[red]✗ 文件已存在: {target}（--force 覆盖）[/red]")
            return 1
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
        console.print(f"[green]✓[/green] 已写入 [bold]{target}[/bold]")
        return 0
    sys.stdout.write(script)
    sys.stdout.flush()
    return 0


def register_completion() -> None:
    from ret2console.commands import REGISTRY
    if "completion" in REGISTRY:
        return
    register(Command(
        "completion", cmd_completion, usage="completion <bash|zsh|fish> [--output PATH] [--force]",
        description="生成 shell 补全脚本（bash/zsh/fish）", category="系统",
    ))
