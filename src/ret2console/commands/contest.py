"""比赛命令：contest list/show/use/doc/notice。"""

from __future__ import annotations

from rich.table import Table
from rich.text import Text

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.output import console, render_markdown
from ret2console.state import App


def _fmt_game(game: dict) -> tuple[str, str, str]:
    gid = game.get("id")
    title = game.get("title") or game.get("name") or f"比赛 {gid}"
    status = str(game.get("state") or game.get("status") or "")
    return str(gid), str(title), status


async def cmd_contest_list(app: App, args: list[str]) -> int:
    try:
        games = await app.api.list_games()
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    app.games_cache = {int(g["id"]): str(g.get("title") or g.get("name") or g["id"]) for g in games if g.get("id") is not None}
    if not games:
        console.print("（暂无比赛）")
        return 0
    table = Table(title="比赛列表", box=None)
    table.add_column("ID", style="bold #5f87d7")
    table.add_column("标题")
    table.add_column("状态", style="dim")
    for g in games:
        gid, title, status = _fmt_game(g)
        table.add_row(gid, title, status)
    console.print(table)
    return 0


async def cmd_contest_show(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: contest show <id>[/red]")
        return 1
    try:
        game = await app.api.get_game(int(args[0]))
    except (ValueError, ApiError) as e:
        console.print(f"[red]✗ {getattr(e, 'message', e)}[/red]")
        return 1
    gid = game.get("id")
    table = Table(box=None, show_header=False)
    table.add_column("字段", style="dim", width=10)
    table.add_column("值")
    for key, label in (("id", "ID"), ("title", "标题"), ("description", "简介"), ("state", "状态")):
        if game.get(key) is not None:
            table.add_row(label, str(game[key]))
    console.print(table)
    if gid is not None:
        console.print(f"[dim]提示: cd /contests/{gid} 进入该比赛上下文[/dim]")
    return 0


async def cmd_contest_use(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: contest use <id>[/red]")
        return 1
    try:
        gid = int(args[0])
    except ValueError:
        console.print(f"[red]✗ 无效的比赛 id: {args[0]}[/red]")
        return 1
    app.ctx.set_game(gid)
    console.print(f"已进入比赛 [bold]{gid}[/bold]，当前路径 [bold]{app.ctx.vfs_path}[/bold]")
    return 0


async def cmd_contest_doc(app: App, args: list[str]) -> int:
    if len(args) < 1:
        console.print("[red]用法: contest doc <id> [readme|training|rules][/red]")
        return 1
    gid = int(args[0])
    doc_type = args[1] if len(args) > 1 else "readme"
    try:
        text = await app.api.get_game_doc(gid, doc_type)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    console.print(render_markdown(text))
    return 0


async def cmd_contest_notice(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id if not args else int(args[0])
    if gid is None:
        console.print("[yellow]! 未指定比赛（当前不在任何比赛上下文），用法: contest notice <id>[/yellow]")
        if not args:
            return 1
        return 1
    try:
        notices = await app.api.get_notifications(gid)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    if not notices:
        console.print("（暂无比赛通知）")
        return 0
    for n in notices:
        title = n.get("title") or "通知"
        content = n.get("content") or n.get("text") or ""
        console.print(f"[bold]{title}[/bold] [dim]({n.get('created_at', '')})[/dim]")
        if content:
            console.print(render_markdown(content))
        console.print()
    return 0


def register_contest() -> None:
    register(Command("contest", _contest_dispatch, usage="contest <list|show|use|doc|notice> ...", description="比赛浏览与上下文管理", category="比赛"))


async def _contest_dispatch(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": cmd_contest_list,
        "show": cmd_contest_show,
        "use": cmd_contest_use,
        "doc": cmd_contest_doc,
        "notice": cmd_contest_notice,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知 contest 子命令: {sub}（可用: list/show/use/doc/notice）[/red]")
        return 1
    return await handler(app, args[1:])
