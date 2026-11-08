"""比赛命令：contest list/show/use/doc/notice（支持筛选与名称解析）。"""

from __future__ import annotations

import time

from ret2console.output import make_table

from ret2console import service
from ret2console.api.client import ApiError
from ret2console.commands import Command, register, report_service_error
from ret2console.output import console, format_ts, render_markdown
from ret2console.service import ServiceError
from ret2console.state import App
from ret2console.termimg import show_image


def _game_status(game: dict) -> str:
    """r2s 无 state 字段，按报名/开赛/结束时间计算状态。"""
    now = int(time.time())
    try:
        start = int(game.get("start_at") or 0)
        end = int(game.get("end_at") or 0)
        register = int(game.get("register_at") or 0)
    except (TypeError, ValueError):
        return ""
    if start and end and start <= now < end:
        return "进行中"
    if end and now >= end:
        return "已结束"
    if start and now < start:
        return "未开始" if (not register or now >= register) else "未开放报名"
    return ""


def _fmt_game(game: dict) -> tuple[str, str, str]:
    gid = game.get("id")
    title = game.get("title") or game.get("name") or f"比赛 {gid}"
    status = _game_status(game)
    return str(gid), str(title), status


def _host_label(game: dict) -> str:
    host = game.get("host_type")
    if host in (0, "0", "Training", "training"):
        return "练习赛"
    if host in (1, "1", "Game", "game"):
        return "正式赛"
    return "—"


def _cell_time(ts) -> str:
    text = format_ts(ts)
    return "—" if text in ("None", "") else text


def _parse_list_args(args: list[str]) -> tuple[int, int, str | None, str | None]:
    """解析 --page / --page-size / --type；返回 (page, page_size, host_type, error)。"""
    page, page_size = 1, 50
    host_type: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--page", "--page-size", "--type"):
            if i + 1 >= len(args):
                return page, page_size, host_type, f"{arg} 需要参数"
            i += 1
            value = args[i]
        elif arg.startswith(("--page=", "--page-size=", "--type=")):
            arg, _, value = arg.partition("=")
        else:
            return page, page_size, host_type, f"未知参数: {arg}"
        if arg == "--type":
            if value not in ("training", "game"):
                return page, page_size, host_type, "--type 只支持 training / game"
            host_type = value
        elif not value.isdigit() or int(value) < 1:
            return page, page_size, host_type, f"{arg} 需要正整数"
        elif arg == "--page":
            page = int(value)
        else:
            page_size = int(value)
        i += 1
    return page, page_size, host_type, None


async def cmd_contest_list(app: App, args: list[str]) -> int:
    page, page_size, host_type, error = _parse_list_args(args)
    if error is not None:
        console.print(f"[red]✗ {error}[/red]")
        return 1
    if not args:
        # 无 flag：保持全量行为（自动翻页）
        try:
            games = await app.api.list_games()
        except ApiError as e:
            console.print(f"[red]✗ {e.message}[/red]")
            return 1
        app.games_cache = {
            int(g["id"]): str(g.get("title") or g.get("name") or g["id"])
            for g in games if g.get("id") is not None
        }
        if not games:
            console.print("（暂无比赛）")
            return 0
        table = make_table(title="比赛列表")
        table.add_column("ID", style="bold #5f87d7")
        table.add_column("标题")
        table.add_column("状态", style="dim")
        for g in games:
            gid, title, status = _fmt_game(g)
            table.add_row(gid, title, status)
        console.print(table)
        return 0
    try:
        data = await service.list_contests(
            app, page=page, page_size=page_size, host_type=host_type,
        )
    except ServiceError as e:
        return report_service_error(e)
    games = data["items"]
    if not games:
        console.print("（暂无比赛）")
        return 0
    total = data.get("total")
    title = "比赛列表"
    if total is not None:
        title = f"比赛列表（第 {data['page']} 页 / 共 {total} 场）"
    table = make_table(title=title)
    table.add_column("ID", style="bold #5f87d7")
    table.add_column("标题")
    table.add_column("类型", style="dim")
    table.add_column("开始", style="dim")
    table.add_column("结束", style="dim")
    table.add_column("状态", style="dim")
    for g in games:
        gid, name, status = _fmt_game(g)
        table.add_row(
            gid, name, _host_label(g),
            _cell_time(g.get("start_at")), _cell_time(g.get("end_at")), status,
        )
    console.print(table)
    return 0


async def cmd_contest_show(app: App, args: list[str]) -> int:
    intro = "--intro" in args
    rules = "--rules" in args
    cover = "--cover" in args
    if sum((intro, rules, cover)) > 1:
        console.print("[red]✗ --intro/--rules/--cover 只能选一个[/red]")
        return 1
    ref = " ".join(a for a in args if a not in ("--intro", "--rules", "--cover")).strip() or None
    try:
        gid = await service.resolve_game(app, ref)
    except ServiceError as e:
        return report_service_error(e)
    if intro or rules:
        doc = "readme" if intro else "rules"
        try:
            text = await service.get_contest_doc(app, gid, doc)
        except ServiceError as e:
            return report_service_error(e)
        console.print(render_markdown(text) if text else "（文档为空）")
        return 0
    try:
        game = await service.get_contest(app, gid)
    except ServiceError as e:
        return report_service_error(e)
    if cover:
        cover_hash = game.get("cover")
        if not cover_hash:
            console.print("[red]✗ 该比赛没有封面[/red]")
            return 1
        try:
            data = await app.api.download_bytes("/media", params={"hash": cover_hash})
        except ApiError as e:
            console.print(f"[red]✗ {e.message}[/red]")
            return 1
        if app.graphics and data and show_image(data, app.graphics, console.width):
            return 0
        console.print(f"/api/media?hash={cover_hash}")
        return 0
    gid_value = game.get("id") or gid
    app.games_cache[int(gid)] = str(game.get("title") or game.get("name") or gid)
    title = game.get("title") or game.get("name") or f"比赛 {gid_value}"
    brief = game.get("brief") or game.get("description") or "（无简介）"
    table = make_table(show_header=False)
    table.add_column("字段", style="dim", width=10)
    table.add_column("值")
    table.add_row("ID", str(gid_value))
    table.add_row("标题", str(title))
    table.add_row("状态", _game_status(game) or "—")
    table.add_row("简介", str(brief))
    start = format_ts(game.get("start_at"))
    end = format_ts(game.get("end_at"))
    table.add_row("时间", f"{start} ~ {end}" if start != "None" else "—")
    console.print(table)
    if gid_value is not None:
        console.print(f"[dim]提示: cd /contests/{gid_value} 进入该比赛上下文[/dim]")
    return 0


async def cmd_contest_use(app: App, args: list[str]) -> int:
    ref = " ".join(args).strip() or None
    if ref is None:
        console.print("[red]用法: contest use <比赛>[/red]")
        return 1
    try:
        gid = await service.resolve_game(app, ref)
        game = await service.get_contest(app, gid)
    except ServiceError as e:
        return report_service_error(e)
    app.games_cache[int(gid)] = str(game.get("title") or game.get("name") or gid)
    app.ctx.set_game(gid)
    console.print(f"已进入比赛 [bold]{gid}[/bold]，当前路径 [bold]{app.ctx.vfs_path}[/bold]")
    return 0


async def cmd_contest_doc(app: App, args: list[str]) -> int:
    rest = list(args)
    doc_type = "readme"
    if rest and rest[-1] in ("readme", "training", "rules"):
        doc_type = rest.pop()
    ref = " ".join(rest).strip() or None
    try:
        gid = await service.resolve_game(app, ref)
        text = await service.get_contest_doc(app, gid, doc_type)
    except ServiceError as e:
        return report_service_error(e)
    console.print(render_markdown(text) if text else "（文档为空）")
    return 0


async def cmd_contest_notice(app: App, args: list[str]) -> int:
    ref = " ".join(args).strip() or None
    try:
        notices = await service.get_contest_notices(app, ref)
    except ServiceError as e:
        return report_service_error(e)
    if not notices:
        console.print("（暂无比赛通知）")
        return 0
    for n in notices:
        title = n.get("title") or "通知"
        content = n.get("content") or n.get("text") or ""
        console.print(f"[bold]{title}[/bold] [dim]({format_ts(n.get('published_at', n.get('created_at', '')))})[/dim]")
        if content:
            console.print(render_markdown(content))
        console.print()
    return 0


def register_contest() -> None:
    register(Command(
        "contest", _contest_dispatch,
        usage="contest <list|show|use|doc|notice> ...",
        description="比赛浏览与上下文管理（支持名称/id 解析）", category="比赛",
    ))


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
