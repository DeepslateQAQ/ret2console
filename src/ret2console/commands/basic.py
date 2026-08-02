"""系统命令：help / exit / clear / version / echo / pwd / ls / cd / status。"""

from __future__ import annotations

from ret2console.output import make_table

from ret2console.commands import EXIT_CODE, Command, REGISTRY, commands_by_category, register
from ret2console.output import console
from ret2console.state import App
from ret2console.vfs import VfsError


async def cmd_help(app: App, args: list[str]) -> int:
    if args:
        cmd = REGISTRY.get(args[0])
        if cmd is None:
            console.print(f"[red]✗ 未知命令: {args[0]}[/red]")
            return 1
        print_cmd_help(cmd)
        return 0
    for category, cmds in commands_by_category().items():
        table = make_table(title=category, show_header=False, padding=(0, 2))
        for cmd in sorted(cmds, key=lambda c: c.name):
            table.add_row(f"[bold]{cmd.name}[/bold]", f"[dim]{cmd.usage}[/dim]", cmd.description)
        console.print(table)
    console.print("\n输入命令名 + --help 查看单个命令详情，man <命令> 查看手册；tab 补全可用。")
    return 0


def print_cmd_help(cmd: Command) -> None:
    """单个命令帮助：名称 / 用法 / 说明 / 别名。"""
    console.print(f"[bold]{cmd.name}[/bold] [dim]{cmd.usage}[/dim]")
    console.print(cmd.description)
    if cmd.aliases:
        console.print(f"[dim]别名: {'/'.join(cmd.aliases)}[/dim]")


async def cmd_exit(app: App, args: list[str]) -> int:
    return EXIT_CODE


async def cmd_clear(app: App, args: list[str]) -> int:
    from prompt_toolkit.shortcuts import clear
    clear()
    return 0


async def cmd_version(app: App, args: list[str]) -> int:
    from ret2console import __version__
    console.print(f"ret2console [bold]{__version__}[/bold] — Ret2Shell (回归终端) 终端接口")
    console.print(f"API: {app.config.api_base}")
    return 0


async def cmd_echo(app: App, args: list[str]) -> int:
    console.print(" ".join(args))
    return 0


async def cmd_pwd(app: App, args: list[str]) -> int:
    console.print(app.ctx.vfs_path)
    return 0


async def cmd_ls(app: App, args: list[str]) -> int:
    long_format = False
    recursive = False
    path_args: list[str] = []
    for a in args:
        if a.startswith("-") and len(a) > 1:
            long_format = long_format or "l" in a
            recursive = recursive or "R" in a
            # "a" 在虚拟文件系统中无隐藏节点，接受但不改变行为
        else:
            path_args.append(a)
    target = path_args[0] if path_args else "."
    path = _resolve_path(app, target)
    try:
        nodes = await app.vfs.list_dir(path)
    except VfsError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    solved, running = set(), set()
    if any(n.challenge_id is not None for n in nodes):
        from ret2console.vfs_builder import _load_challenge_status
        gid = next((n.game_id for n in nodes if n.challenge_id is not None), None)
        if gid is not None:
            solved, running = await _load_challenge_status(app, gid)
    if long_format:
        return _render_long(app, nodes, solved=solved, running=running)
    if recursive:
        return await _render_recursive(app, path, nodes, solved=solved, running=running)
    if not nodes:
        return 0
    console.print(_render_short(nodes, solved=solved, running=running))
    return 0


def _file_style(n) -> str | None:
    """特殊文件高亮：flag/instance 金色（对齐 flag 高亮），压缩包红色。"""
    if n.name in ("flag", "instance"):
        return "bold bright_yellow"
    if n.name.endswith((".zip", ".tar", ".gz", ".7z", ".rar", ".xz")):
        return "bold bright_red"
    return None


def _challenge_style(n, solved: set[int], running: set[int]) -> str:
    """题目目录样式：已通过绿色，运行中金色，一般蓝色。"""
    if n.challenge_id in solved:
        return "bold bright_green"
    if n.challenge_id in running:
        return "bold bright_yellow"
    return "bold bright_blue"


def _render_short(nodes: list, solved: set[int] | None = None, running: set[int] | None = None):
    """短格式：目录蓝色、特殊文件金色/红色、一般文件默认色；题目目录带状态。"""
    solved = solved or set()
    running = running or set()
    from ret2console.output import RUNNING_STAR_STYLE
    from rich.text import Text
    dirs = [n for n in nodes if n.kind == "dir"]
    files = [n for n in nodes if n.kind == "file"]
    parts: list[Text] = []
    for n in dirs:
        style = "bold bright_blue"
        if n.challenge_id is not None:
            style = _challenge_style(n, solved, running)
        t = Text(n.name, style=style)
        if n.challenge_id is not None and n.challenge_id in running:
            t.append("*", style=RUNNING_STAR_STYLE)
        t.append("/", style=style)
        parts.append(t)
    for n in files:
        style = _file_style(n)
        parts.append(Text(n.name, style=style))
    return Text("  ").join(parts)


async def _render_recursive(
    app: App, path: str, nodes: list, solved: set[int] | None = None, running: set[int] | None = None
) -> int:
    """ls -R 递归目录树：目录头 + 子项（懒加载逐层展开）。"""
    solved = solved or set()
    running = running or set()
    header = path if path != "/" else "/"
    console.print(f"[bold]{header}:[/bold]")
    if nodes:
        console.print(_render_short(nodes, solved=solved, running=running))
    for n in nodes:
        if n.kind != "dir":
            continue
        sub = path.rstrip("/") + "/" + n.name
        try:
            children = await app.vfs.list_dir(sub)
        except VfsError as e:
            console.print(f"[red]✗ {e}[/red]")
            continue
        sub_solved, sub_running = solved, running
        if any(c.challenge_id is not None for c in children):
            from ret2console.vfs_builder import _load_challenge_status
            gid = next((c.game_id for c in children if c.challenge_id is not None), None)
            if gid is not None:
                sub_solved, sub_running = await _load_challenge_status(app, gid)
        console.print()
        await _render_recursive(app, sub, children, solved=sub_solved, running=sub_running)
    return 0


def _render_long(app: App, nodes: list, solved: set[int] | None = None, running: set[int] | None = None) -> int:
    """ls -l 长格式：类型 + 名称 + 元信息（id/标题，来自缓存）。"""
    solved = solved or set()
    running = running or set()
    from ret2console.output import make_table
    table = make_table(show_header=False, pad_edge=False)
    table.add_column("", width=2)
    table.add_column("名称")
    table.add_column("信息", style="dim")
    for n in nodes:
        kind = "d" if n.kind == "dir" else "f"
        if n.kind == "dir":
            style = _challenge_style(n, solved, running) if n.challenge_id is not None else "bold bright_blue"
            from ret2console.output import RUNNING_STAR
            star = RUNNING_STAR if n.challenge_id is not None and n.challenge_id in running else ""
            name = f"[{style}]{n.name}{star}[/{style}]/"
            kind = f"[{style}]d[/{style}]"
        elif style := _file_style(n):
            name = f"[{style}]{name}[/{style}]"
            kind = f"[{style}]f[/{style}]"
        meta = ""
        if n.game_id is not None:
            title = app.games_cache.get(n.game_id)
            meta = f"比赛 {n.game_id}" + (f" {title}" if title else "")
        if n.challenge_id is not None:
            gid = n.game_id
            title = app.challenges_cache.get(gid, {}).get(n.challenge_id) if gid else None
            meta = f"题目 {n.challenge_id}" + (f" {title}" if title else "")
        if n.name == "contests":
            meta = "比赛列表"
        elif n.name == "notice":
            meta = "全局公告"
        elif n.name == "info":
            meta = "比赛详情"
        elif n.name == "scoreboard":
            meta = "排行榜"
        elif n.name == "team":
            meta = "我的队伍"
        elif n.name == "README":
            meta = "题面"
        elif n.name == "hint":
            meta = "提示"
        elif n.name == "files":
            meta = "附件"
        elif n.name == "instance":
            meta = "靶机实例"
        elif n.name == "flag":
            meta = "提交入口"
        table.add_row(kind, name, meta)
    console.print(table)
    return 0


async def cmd_cd(app: App, args: list[str]) -> int:
    if len(args) > 1:
        console.print("[red]用法: cd <路径>[/red]")
        return 1
    target = args[0] if args else "/"
    path = _resolve_path(app, target)
    try:
        node = await app.vfs.resolve(path)
    except VfsError as e:
        console.print(f"[red]✗ {e}[/red]")
        return 1
    if node.kind != "dir":
        console.print(f"[red]✗ cd: {path} 不是目录[/red]")
        return 1
    # cd 即 use：按节点元数据同步上下文（SPEC §7）
    app.ctx.set_path(node.name if path == "/" else path, node.game_id, node.challenge_id)
    if node.name == "/" or path == "/":
        app.ctx.set_path("/", None, None)
    return 0


async def cmd_status(app: App, args: list[str]) -> int:
    session = app.session
    if session.logged_in:
        role = "管理员" if session.is_admin else "已登录"
        console.print(f"[bold]{role}[/bold] 用户: {session.display_name} (account: {session.account})")
        # r2s 的 permissions 是 int 枚举数组（0=Basic…9=DevOps），转名称展示
        _NAMES = {0: "basic", 1: "verified", 2: "calendar", 3: "wiki", 4: "bulletin",
                  5: "game", 6: "host", 7: "user", 8: "statistics", 9: "devops"}
        perms = [
            _NAMES.get(p, str(p)) if isinstance(p, int) else str(p)
            for p in session.permissions
        ] if session.permissions else []
        console.print(f"权限: {', '.join(perms) if perms else '—'}")
    else:
        console.print("[bold]未登录[/bold] (guest)")
    ctx = app.ctx
    parts = [f"路径: [bold]{ctx.vfs_path}[/bold]"]
    if ctx.game_id:
        parts.append(f"当前比赛: [bold]{ctx.game_id}[/bold]")
    if ctx.challenge_id:
        parts.append(f"当前题目: [bold]{ctx.challenge_id}[/bold]")
    console.print("  ".join(parts))
    console.print(f"API: {app.config.api_base}")
    console.print(f"profile: {app.profile_name}")
    return 0


def _resolve_path(app: App, target: str) -> str:
    if target in (".", ""):
        return app.ctx.vfs_path
    if target == "..":
        return app.vfs.parent_path(app.ctx.vfs_path)
    if target.startswith("/"):
        return target
    base = app.ctx.vfs_path
    if base.endswith("/"):
        return base + target
    return base + "/" + target


async def cmd_notice(app: App, args: list[str]) -> int:
    """公告：无参 = 全局公告（/notice）；带比赛 id = 比赛通知（SPEC §9）。"""
    if args and args[0].isdigit():
        from ret2console.commands.contest import cmd_contest_notice
        return await cmd_contest_notice(app, [args[0]])
    from ret2console.commands.files import cmd_cat
    return await cmd_cat(app, ["/notice"])


def register_basic() -> None:
    register(Command("help", cmd_help, usage="help [命令]", description="显示命令列表或单个命令帮助"))
    register(Command("exit", cmd_exit, usage="exit", description="退出 ret2console", aliases=("quit",)))
    register(Command("clear", cmd_clear, usage="clear", description="清屏"))
    register(Command("version", cmd_version, usage="version", description="显示版本与 API 地址"))
    register(Command("echo", cmd_echo, usage="echo <文本...>", description="回显文本"))
    register(Command("pwd", cmd_pwd, usage="pwd", description="显示当前虚拟路径"))
    register(Command("ls", cmd_ls, usage="ls [路径]", description="列出虚拟目录内容", category="文件系统"))
    register(Command("cd", cmd_cd, usage="cd <路径>", description="进入虚拟目录（cd 即 use，同步当前比赛/题目上下文）", category="文件系统"))
    register(Command("status", cmd_status, usage="status", description="显示登录状态与当前上下文"))
    register(Command("notice", cmd_notice, usage="notice [比赛id]", description="查看公告：无参为全局公告，带 id 为比赛通知", category="比赛"))
