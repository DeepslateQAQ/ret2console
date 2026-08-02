"""题目命令：challenge list/show/hint/unlock/start/instance/connect/disconnect/stop/renew。"""

from __future__ import annotations

from ret2console.output import make_table

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.output import console, format_ts, render_markdown
from ret2console.state import App
from ret2console.wsrx import (
    load_tunnels,
    pick_free_port,
    pool_instances,
    remove_tunnel,
    save_tunnel,
    tunnel_add,
    tunnel_remove,
    tunnel_status,
    wsrx_link,
)


async def _get_instances(app: App, gid: int, cid: int | None) -> list[dict]:
    """选手视角实例列表：GET /game/{gid}/instance，可选按题目过滤。

    challenge 级的 GET /instance 是管理员接口（选手会 403），不要用。
    """
    instances = await app.api.list_instances(gid)
    items = [i for i in instances if isinstance(i, dict)]
    if cid is not None:
        items = [i for i in items if str(i.get("challenge_id")) == str(cid)]
    return items


def _game_id(app: App, args: list[str]) -> int | None:
    if args and args[0].isdigit():
        return int(args[0])
    return app.ctx.game_id


async def cmd_challenge_list(app: App, args: list[str]) -> int:
    gid = _game_id(app, args)
    if gid is None:
        console.print("[yellow]! 未指定比赛：请先 cd /contests/<id> 或 contest use <id>[/yellow]")
        return 1
    try:
        challenges = await app.api.list_challenges(gid)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    app.challenges_cache[gid] = {
        int(c["id"]): str(c.get("title") or c.get("name") or c["id"]) for c in challenges if c.get("id") is not None
    }
    if not challenges:
        console.print("（暂无题目）")
        return 0
    from ret2console.vfs_builder import _load_challenge_status
    solved, running = await _load_challenge_status(app, gid)
    table = make_table(title=f"比赛 {gid} 题目列表")
    table.add_column("ID", style="bold #5f87d7")
    table.add_column("标题")
    table.add_column("分类", style="dim")
    table.add_column("分值", style="dim")
    for c in challenges:
        cid = c.get("id")
        title = str(c.get("title") or c.get("name") or cid)
        if isinstance(cid, int) and cid in solved:
            title = f"[bold bright_green]{title}[/bold bright_green]"
        if isinstance(cid, int) and cid in running:
            from ret2console.output import RUNNING_STAR
            title = f"{title} {RUNNING_STAR}"
        table.add_row(
            str(cid),
            title,
            _challenge_category(c),
            str(c.get("score") or c.get("value") or ""),
        )
    console.print(table)
    return 0


async def cmd_challenge_show(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文：cd /contests/<比赛>/challenges/<题目> 或 challenge show <题目id>[/yellow]")
        return 1
    try:
        challenge = await app.api.get_challenge(gid, cid)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    title = challenge.get("title") or f"题目 {cid}"
    console.print(f"[bold]{title}[/bold] [dim](#{cid})[/dim]")
    content = challenge.get("content") or challenge.get("description") or ""
    if content:
        console.print(render_markdown(content))
    else:
        console.print("（无题面内容）")
    return 0


async def cmd_challenge_instance(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None:
        console.print("[yellow]! 请先进入比赛上下文：cd /contests/<id> 或 contest use <id>[/yellow]")
        return 1
    try:
        items = await _get_instances(app, gid, cid)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    if not items or (len(items) == 1 and not items[0]):
        console.print("（无运行中的实例，challenge start <id> 启动）")
        return 0
    for inst in items:
        tag = f"题目 {inst.get('challenge_id')} ({inst.get('challenge_name', '')})" if cid is None else f"题目 {cid}"
        console.print(f"[bold]{tag}[/bold]  state: [bold]{inst.get('state', '?')}[/bold]")
        if inst.get("traffic"):
            console.print(f"tunnel: {tunnel_status(app.wsrx, str(inst['traffic']))}")
        if inst.get("ports"):
            console.print(f"ports: {', '.join(map(str, inst['ports']))}")
        console.print(f"renew: {inst.get('renew_count', 0)}  created: {format_ts(inst.get('created_at', ''))}")
    return 0


async def cmd_challenge_connect(app: App, args: list[str]) -> int:
    """建立到实例的 wsrx 隧道（后台 daemon / GUI HTTP 控制，不前台阻塞）。"""
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None:
        console.print("[yellow]! 请先进入比赛上下文：cd /contests/<id> 或 contest use <id>[/yellow]")
        return 1
    if app.wsrx.mode == "none":
        console.print("[yellow]! wsrx 不可用（未检测到 GUI 且 daemon 启动失败），靶机远程访问不可用[/yellow]")
        return 1
    try:
        items = await _get_instances(app, gid, cid)
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    inst = next((i for i in items if i.get("traffic")), None)
    if inst is None:
        console.print("[yellow]! 无运行中的实例，challenge start <id> 启动后再 connect[/yellow]")
        return 1
    traffic = str(inst["traffic"])
    ports = [int(p) for p in (inst.get("ports") or []) if str(p).isdigit()]
    if not ports:
        console.print("[yellow]! 实例未暴露任何端口，无法建立隧道[/yellow]")
        return 1
    try:
        existing = {str(p.get("remote") or "") for p in pool_instances(app.wsrx)}
    except Exception:
        existing = set()
    created: list[tuple[str, int]] = []  # (traffic:local, local)
    label = f"{inst.get('challenge_name') or cid} ({inst.get('game_name') or gid})"
    for port in ports:
        link = wsrx_link(app.api.base_url, traffic, port)
        if link in existing:
            console.print(f"[dim]隧道已存在（{link}），跳过[/dim]")
            continue
        local = pick_free_port()
        try:
            tunnel_add(app.wsrx, local, link, label=label)
        except Exception as e:
            console.print(f"[red]✗ 隧道建立失败（{link}）: {e}[/red]")
            for key, lp in created:
                tunnel_remove(app.wsrx, lp)
                remove_tunnel(key)
            return 1
        save_tunnel(traffic, local, game_id=gid, challenge_id=cid)
        created.append((f"{traffic}:{local}", local))
        console.print(f"[green]✓[/green] 本地已映射到 [bold]127.0.0.1:{local}[/bold]（{link}）")
    if created:
        who = "wsrx-desktop" if app.wsrx.mode == "desktop" else "后台 wsrx daemon"
        console.print(f"[dim]隧道由 {who} 托管：challenge instance 查看状态，challenge disconnect 关闭[/dim]")
    return 0


async def cmd_challenge_disconnect(app: App, args: list[str]) -> int:
    """关闭本地隧道（不停止实例）：challenge disconnect [题目id]。"""
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None:
        console.print("[yellow]! 请先进入比赛上下文[/yellow]")
        return 1
    records = load_tunnels()
    closed = 0
    for key, rec in list(records.items()):
        if int(rec.get("game_id") or 0) != gid:
            continue
        if cid is not None and int(rec.get("challenge_id") or -1) != cid:
            continue
        local = rec.get("port")
        if local:
            try:
                tunnel_remove(app.wsrx, int(local))
            except Exception:
                pass
        remove_tunnel(key)
        closed += 1
    if closed:
        console.print(f"[green]✓[/green] 已关闭 {closed} 条隧道")
    else:
        console.print("（没有需要关闭的隧道，challenge connect 建立）")
    return 0


async def cmd_challenge_stop(app: App, args: list[str]) -> int:
    """关闭当前实例并清理其本地隧道。"""
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文[/yellow]")
        return 1
    for key, rec in list(load_tunnels().items()):
        if int(rec.get("game_id") or 0) == gid and int(rec.get("challenge_id") or -1) == cid:
            local = rec.get("port")
            if local:
                try:
                    tunnel_remove(app.wsrx, int(local))
                except Exception:
                    pass
            remove_tunnel(key)
    try:
        await app.api.delete_json(f"/game/{gid}/challenge/{cid}/instance")
    except ApiError as e:
        console.print(f"[red]✗ 关闭失败: {e.message}[/red]")
        return 1
    console.print("[green]✓[/green] 实例已关闭")
    return 0


async def cmd_challenge_start(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文[/yellow]")
        return 1
    try:
        await app.api.post_json(f"/game/{gid}/challenge/{cid}/instance")
    except ApiError as e:
        msg = e.message
        console.print(f"[red]✗ {msg}[/red]")
        if "one instance" in msg or "already launched" in msg:
            console.print("[yellow]! 提示：challenge instance 查看运行中的实例；challenge stop <题目id> 可关闭实例后重新启动[/yellow]")
        return 1
    console.print("[yellow]➜ 实例启动请求已提交，稍候 challenge instance 查看状态[/yellow]")
    return 0


async def cmd_challenge_renew(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文[/yellow]")
        return 1
    try:
        await app.api.patch_json(f"/game/{gid}/challenge/{cid}/instance", json={})
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    console.print("[green]实例已续期[/green]")
    return 0


async def cmd_challenge_hint(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if args and args[0].isdigit():
        cid = int(args[0])
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文[/yellow]")
        return 1
    try:
        hints = await app.api.get_json(f"/game/{gid}/challenge/{cid}/hint")
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    items = hints if isinstance(hints, list) else hints.get("hints", [])
    if not items:
        console.print("（暂无提示）")
        return 0
    for i, h in enumerate(items, 1):
        title = h.get("title") or f"提示 {i}"
        content = h.get("content") or ""
        unlocked = h.get("unlocked")
        if unlocked:
            console.print(f"[bold]{title}[/bold]: {content}")
        else:
            console.print(f"[dim]{title}: （未解锁，challenge unlock {cid} {h.get('id', i)}）[/dim]")
    return 0


async def cmd_challenge_unlock(app: App, args: list[str]) -> int:
    """解锁提示：challenge unlock [题目id] <提示id>（当前题目上下文可省略题目id）。"""
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if len(args) >= 2 and args[0].isdigit():
        cid, hint_id = int(args[0]), args[1]
    elif len(args) == 1:
        hint_id = args[0]
    else:
        console.print("[red]用法: challenge unlock [题目id] <提示id>[/red]")
        return 1
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文[/yellow]")
        return 1
    try:
        await app.api.post_json(f"/game/{gid}/challenge/{cid}/hint/unlock", json={"id": int(hint_id)})
    except (ValueError, ApiError) as e:
        console.print(f"[red]✗ 解锁失败: {getattr(e, 'message', e)}[/red]")
        return 1
    console.print(f"[green]✓[/green] 提示 #{hint_id} 已解锁，challenge hint 查看")
    return 0


def register_challenge() -> None:
    register(Command("challenge", _challenge_dispatch, usage="challenge <list|show|hint|unlock|start|instance|connect|disconnect|stop|renew> ...", description="题目操作（需比赛上下文）", category="题目"))


async def _challenge_dispatch(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": cmd_challenge_list,
        "show": cmd_challenge_show,
        "hint": cmd_challenge_hint,
        "unlock": cmd_challenge_unlock,
        "start": cmd_challenge_start,
        "instance": cmd_challenge_instance,
        "connect": cmd_challenge_connect,
        "disconnect": cmd_challenge_disconnect,
        "stop": cmd_challenge_stop,
        "renew": cmd_challenge_renew,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知 challenge 子命令: {sub}（可用: list/show/hint/unlock/start/instance/connect/disconnect/stop/renew）[/red]")
        return 1
    return await handler(app, args[1:])

def _challenge_category(c: dict) -> str:
    """r2s 的分类在 tag[] 里：取 primary 标签，否则取第一个标签。"""
    tags = c.get("tag") or []
    if not isinstance(tags, list):
        return ""
    for t in tags:
        if isinstance(t, dict) and t.get("primary"):
            return str(t.get("name") or "")
    for t in tags:
        if isinstance(t, dict) and t.get("name"):
            return str(t["name"])
    return ""
