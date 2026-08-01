"""REPL 主循环：提示符、历史过滤、flag 粘贴即提交、命令分发。"""

from __future__ import annotations

import asyncio
import re

from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import ThreadedCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.styles import Style

from ret2console.api.client import ApiError
from ret2console.commands import REGISTRY, EXIT_CODE, command_names, get_command, split_line
from ret2console.completer import Ret2ConsoleCompleter
from ret2console.flag import extract_flag, should_exclude_from_history
from ret2console.lexer import Ret2ConsoleLexer, build_style
from ret2console.output import console
from ret2console.state import App
from ret2console import shells

HISTORY_FILE = "history"

# 字形按列着色：R 列 0-2（红）、2 列 9-11（灰）、C 列 12-14（蓝），其余默认色。
# art 内无字母 R/2/C 字符（纯框线字形），按位置而非字符匹配。
SPLASH_RANGES = (("R", 0, 3, "bold red"), ("2", 9, 12, "bold bright_black"), ("C", 12, 15, "bold blue"))
SPLASH_ART = """┏━┓┏━╸╺┳╸┏━┓┏━╸┏━┓┏┓╻┏━┓┏━┓╻  ┏━╸
┣┳┛┣╸  ┃ ┏━┛┃  ┃ ┃┃┗┫┗━┓┃ ┃┃  ┣╸ 
╹┗╸┗━╸ ╹ ┗━╸┗━╸┗━┛╹ ╹┗━┛┗━┛┗━╸┗━╸"""


def print_splash() -> None:
    """启动 ASCII art：R 红色、2 灰色、C 蓝色（Ret2Console 品牌色），其余默认。"""
    from rich.text import Text
    t = Text()
    lines = SPLASH_ART.splitlines()
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            style = None
            for _, start, end, s in SPLASH_RANGES:
                if start <= ci < end:
                    style = s
                    break
            t.append(ch, style=style)
        if li < len(lines) - 1:
            t.append("\n")
    console.print(t)


def is_valid_command(word: str) -> bool:
    """命令是否有效（用于高亮）：注册表命令；shell 额外要求本机已安装。"""
    if get_command(word) is None:
        return False
    if word in shells.SHELLS:
        return shells.installed(word) is not None
    return True


class FilteredFileHistory(FileHistory):
    """拒绝 flag 行进入历史（SPEC §8 防泄露）。"""

    def store_string(self, string: str) -> None:
        if should_exclude_from_history(string):
            return
        super().store_string(string)


def build_prompt(app: App) -> str:
    """三态提示符（SPEC §5）：guest/已登录绿色，管理员红色 #。"""
    path = app.ctx.vfs_path or "/"
    if len(path) > 60:
        path = "…" + path[-59:]
    if app.session.logged_in:
        user = app.session.display_name
        if app.session.is_admin:
            return f"<ansired><b>[{user}@ret2console:{path}]#</b></ansired> "
        return f"<ansigreen><b>[{user}@ret2console:{path}]$</b></ansigreen> "
    return f"<ansigreen><b>[guest@ret2console:{path}]$</b></ansigreen> "


async def dispatch(app: App, line: str) -> int:
    """分发一行输入：flag 粘贴即提交 → `;`/`&&` 命令列表 → 命令表。返回退出码。"""
    line = line.strip()
    if not line:
        return 0
    code = 0
    for chunk, mode in split_commands(line):
        if mode == "if_ok" and code != 0:
            continue
        # flag 粘贴即提交（SPEC §8）
        flag = extract_flag(chunk)
        if flag is not None:
            from ret2console.commands.submit import _do_submit
            code = await _do_submit(app, flag)
        else:
            code = await dispatch_single(app, chunk)
        if code == EXIT_CODE:
            return code
    return code


def split_commands(line: str) -> list[tuple[str, str]]:
    """拆分命令列表：`;` 无条件执行，`&&` 仅在前一条成功时执行。

    返回 [(命令文本, 模式)]，模式为 "always" 或 "if_ok"。
    """
    import re
    out: list[tuple[str, str]] = []
    mode = "always"
    for part in re.split(r"(&&|;)", line):
        part = part.strip()
        if part == "&&":
            mode = "if_ok"
            continue
        if part == ";":
            mode = "always"
            continue
        if part:
            out.append((part, mode))
            mode = "always"
    return out


async def dispatch_single(app: App, line: str) -> int:
    if line.lstrip().startswith("#"):
        return 0  # 注释（脚本模式语义同样适用于 REPL）
    args = split_line(line)
    if not args:
        return 0
    cmd = get_command(args[0])
    if cmd is None:
        console.print(f"[red]r2c: command not found: {args[0]}[/red]")
        return 127
    return await cmd.handler(app, args[1:])


async def run_repl(app: App, history_path: str | None = None) -> int:
    if history_path is None:
        history_path = str(Path.home() / ".ret2console" / HISTORY_FILE)
        Path(history_path).parent.mkdir(parents=True, exist_ok=True)
    style = Style(list(build_style().items()))
    lexer = Ret2ConsoleLexer(is_command=is_valid_command)
    completer = ThreadedCompleter(Ret2ConsoleCompleter(
        commands=lambda: _command_table(),
        vfs=app.vfs,
        current_path=lambda: app.ctx.vfs_path,
        loop=asyncio.get_running_loop(),
        dynamic_candidates=_dynamic_candidates(app),
    ))
    session = PromptSession(
        history=FilteredFileHistory(history_path),
        style=style,
        lexer=lexer,
        completer=completer,
        complete_in_thread=False,
    )
    print_splash()
    await _welcome(app)
    while True:
        try:
            line = await session.prompt_async(HTML(build_prompt(app)))
        except KeyboardInterrupt:
            console.print()
            continue
        except EOFError:
            console.print()
            return 0
        try:
            code = await dispatch(app, line)
        except ApiError as e:
            console.print(f"[red]✗ {e.message}[/red]")
        except Exception as e:  # 命令异常不杀死 REPL
            console.print(f"[red]✗ 命令执行出错: {type(e).__name__}: {e}[/red]")
            continue
        if code == EXIT_CODE:
            return 0


def _command_table() -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for name in command_names():
        cmd = get_command(name)
        if cmd is None or cmd.name != name:
            continue
        table[name] = _subcommands(name)
        for sub in _SUBCOMMANDS.get(name, []):  # 两级命令：<name> <sub> 作为补全 key
            two = f"{name} {sub}"
            if two in _SUBCOMMANDS:
                table[two] = _SUBCOMMANDS[two]
    return table


_SUBCOMMANDS: dict[str, list[str]] = {
    "auth": ["login", "logout", "register", "status", "endpoint"],
    "contest": ["list", "show", "use", "doc", "notice"],
    "challenge": ["list", "show", "hint", "unlock", "start", "instance", "connect", "disconnect", "stop", "renew"],
    "team": ["create", "join", "info", "rank", "member", "solve", "leave"],
    "admin": ["bulletin", "user", "game", "challenge", "platform", "cluster"],
    "admin bulletin": ["list", "show", "create", "update", "delete"],
    "admin user": ["list", "show", "update", "delete", "ip"],
    "admin game": ["list", "show", "create", "update", "delete", "token", "admins", "submissions", "audit", "stats"],
    "admin challenge": ["list", "show", "create", "update", "delete", "publish", "unpublish", "answer", "history", "submissions", "instances", "attach"],
    "admin platform": ["stats", "logs", "config"],
    "admin cluster": ["nodes", "config", "calmdown"],
}


def _subcommands(name: str) -> list[str]:
    return _SUBCOMMANDS.get(name, [])


def _dynamic_candidates(app: App):
    """第三层补全：比赛/题目 id（缓存已由列表命令/fs 加载填充）。"""

    def candidates(cmd: str, word: str) -> list[tuple[str, str | None]]:
        if cmd == "contest" and word.lstrip("-").isdigit() is False:
            return [(str(gid), title) for gid, title in app.games_cache.items() if str(gid).startswith(word)]
        if cmd == "challenge":
            gid = app.ctx.game_id
            if gid is not None and gid in app.challenges_cache:
                return [(str(cid), title) for cid, title in app.challenges_cache[gid].items() if str(cid).startswith(word)]
        return []

    return candidates


async def _welcome(app: App) -> None:
    """启动欢迎：已登录显示会话缓存并验证 token；过期则回到 guest。"""
    if not app.session.logged_in:
        console.print("[dim]未登录（guest）— 输入 auth login 登录[/dim]")
        return
    console.print(f"[bold green]✓[/bold green] 欢迎回来，[bold]{app.session.display_name}[/bold]")
    try:
        profile = await app.api.profile()
        app.session.account = profile.get("account") or app.session.account
        app.session.nickname = profile.get("nickname") or app.session.nickname
        perms = profile.get("permissions")
        if isinstance(perms, list):
            app.session.permissions = perms
        app.session.save()
    except ApiError as e:
        if e.status == 401:
            app.session.clear()
            console.print("[yellow]! 会话已过期，请重新 auth login[/yellow]")
        else:
            console.print(f"[yellow]! 会话验证失败（{e.message}），可继续使用本地功能[/yellow]")
