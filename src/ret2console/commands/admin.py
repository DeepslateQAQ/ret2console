"""管理员命令：admin bulletin/user/game/challenge/platform/cluster（SPEC §16，权限由 r2s 服务端把关）。

参数风格：``key=value`` 直接映射 r2s model 字段；时间字段接受 unix 秒、ISO8601
或 ``YYYY-MM-DD HH:MM``（本地时区）；bool 接受 true/false/1/0。
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime

from rich.table import Table

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.output import console
from ret2console.state import App

# r2s Permission 枚举：Basic/Verified 为普通权限，其余均为管理面
_ADMIN_PERM_NAMES = {"calendar", "wiki", "bulletin", "game", "host", "user", "statistics", "devops"}
_TS_FIELDS = {"created_at", "updated_at", "start_at", "end_at", "register_at", "archive_at", "release_at"}
_BOOL_FIELDS = {
    "hidden", "offline", "frozen", "enable_audit", "can_register_after_started",
    "draft", "published", "enable_comment", "banned", "restrict",
}
_USAGE = "admin <bulletin|user|game|challenge|platform|cluster> ..."


def _now() -> int:
    return int(time.time())


def _ts(value: object) -> int:
    """时间参数 → unix 秒（r2s ts_seconds 序列化）。"""
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    s = s.replace("Z", "+00:00")
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    if not re.search(r"[+-]\d\d:\d\d$", s):
        s += "+08:00"  # 无时区按本地
    return int(datetime.fromisoformat(s).timestamp())


def _cast(key: str, value: str):
    s = value.strip()
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if s.startswith(("{", "[")):
        try:
            return json.loads(s)
        except ValueError:
            pass
    return s


def _kv(args: list[str]) -> tuple[dict, list[str]]:
    """拆 key=value 与裸参数。key 值再按字段类型转换。"""
    kv: dict = {}
    pos: list[str] = []
    for a in args:
        if "=" in a:
            k, _, v = a.partition("=")
            kv[k] = _cast(k, v)
        else:
            pos.append(a)
    return kv, pos


def _fmt_time(ts: object) -> str:
    if isinstance(ts, (int, float)) and ts:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    return str(ts or "")


def _dump(data) -> None:
    if isinstance(data, (dict, list)):
        console.print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        console.print(data)


def _page(args: list[str]) -> dict:
    """page / page_size 参数。"""
    params = {}
    for a in args:
        if a.startswith("page=") or a.startswith("page_size="):
            k, _, v = a.partition("=")
            params[k] = int(v)
    return params


def _items(data) -> list:
    """兼容 r2s 分页 `(items, count)` 与裸数组两种返回结构。"""
    if isinstance(data, (list, tuple)):
        if data and isinstance(data[0], list):
            return data[0]
        return list(data)
    return []


def _table(title: str, headers: list[str], rows: list[tuple]) -> None:
    t = Table(title=title, box=None)
    for i, h in enumerate(headers):
        t.add_column(h, style="bold" if i == 0 else None)
    for row in rows:
        t.add_row(*[str(c) if c is not None else "" for c in row])
    console.print(t)


def _require_admin(app: App) -> int | None:
    if not app.session.logged_in:
        console.print("[yellow]! 请先登录：auth login[/yellow]")
        return 1
    perms = app.session.permissions
    is_admin = any(
        (isinstance(p, str) and p.lower() in _ADMIN_PERM_NAMES)
        or (isinstance(p, int) and p >= 2)  # 0=Basic 1=Verified
        for p in perms
    )
    if not is_admin:
        console.print("[yellow]! 当前账号没有管理员权限（需要 bulletin/user/game/host/devops 等）[/yellow]")
        return 1
    return None


# ---- bulletin ----

async def _bulletin_list(app: App, args: list[str]) -> int:
    data = await app.api.get_json("/bulletin", params=_page(args))
    items = _items(data)
    count = data[1] if isinstance(data, (list, tuple)) and len(data) > 1 else len(items)
    _table(f"公告（共 {count} 条）", ["ID", "标题", "状态", "更新时间"], [
        (a.get("id"), a.get("title"), "草稿" if a.get("draft") else "已发布", _fmt_time(a.get("updated_at")))
        for a in items
    ])
    return 0


async def _bulletin_show(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin bulletin show <id>[/red]")
        return 1
    try:
        article = await app.api.get_json(f"/bulletin/{int(args[0])}")
    except ApiError as e:
        console.print(f"[red]✗ 获取公告失败: {e.message}[/red]")
        return 1
    console.print(f"[bold]{article.get('title')}[/bold] [dim](#{article.get('id')})[/dim]")
    console.print((article.get("content") or "（空）").strip())
    return 0


def _bulletin_body(args: list[str]) -> dict | None:
    kv, pos = _kv(args)
    title = pos[0] if pos else kv.pop("title", None)
    if not title:
        console.print("[red]用法: admin bulletin create <标题> [正文] [k=v...][/red]")
        return None
    content = pos[1] if len(pos) > 1 else kv.pop("content", None)
    body = {
        "id": 0, "created_at": _now(), "updated_at": _now(),
        "title": title, "path": [], "content": content, "publisher_id": 0,
        "access_policy": 0,  # r2s ActiveEnum repr(i32)：Bulletin=0（服务端再强制覆盖）
        "enable_comment": False, "weight": 0, "draft": False, "published": True,
    }
    body.update(kv)
    return body


async def _bulletin_create(app: App, args: list[str]) -> int:
    body = _bulletin_body(args)
    if body is None:
        return 1
    try:
        article = await app.api.post_json("/bulletin", json=body)
    except ApiError as e:
        console.print(f"[red]✗ 发布失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 公告已发布 [bold]#{article.get('id') if isinstance(article, dict) else '?'}[/bold]")
    return 0


async def _bulletin_update(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin bulletin update <id> <标题> [正文] [k=v...][/red]")
        return 1
    body = _bulletin_body(args[1:])
    if body is None:
        return 1
    try:
        await app.api.patch_json(f"/bulletin/{int(args[0])}", json=body)
    except ApiError as e:
        console.print(f"[red]✗ 更新失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 公告 #{args[0]} 已更新")
    return 0


async def _bulletin_delete(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin bulletin delete <id>[/red]")
        return 1
    try:
        await app.api.delete_json(f"/bulletin/{int(args[0])}")
    except ApiError as e:
        console.print(f"[red]✗ 删除失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 公告 #{args[0]} 已删除")
    return 0


async def _bulletin(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": _bulletin_list, "show": _bulletin_show, "create": _bulletin_create,
        "update": _bulletin_update, "delete": _bulletin_delete,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知子命令: admin bulletin {sub}（可用: list/show/create/update/delete）[/red]")
        return 1
    return await handler(app, args[1:])


# ---- user ----

async def _user_list(app: App, args: list[str]) -> int:
    kv, pos = _kv(args)
    params = _page(args)
    if pos:
        params["filter"] = pos[0]
    data = await app.api.get_json("/user", params=params)
    items = _items(data)
    _table("用户列表", ["ID", "账号", "昵称", "权限", "状态"], [
        (u.get("id"), u.get("account"), u.get("nickname"),
         ",".join(str(p) for p in (u.get("permissions") or [])),
         "封禁" if u.get("banned") else ("隐藏" if u.get("hidden") else "正常"))
        for u in items
    ])
    return 0


async def _user_show(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin user show <id>[/red]")
        return 1
    user = await app.api.get_json(f"/user/{int(args[0])}")
    _table(f"用户 #{user.get('id')}", ["字段", "值"], [
        (k, user.get(k)) for k in ("account", "nickname", "email", "description", "institute_id", "permissions", "hidden", "banned")
        if user.get(k) is not None
    ])
    return 0


async def _user_update(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin user update <id> key=value...[/red]")
        return 1
    uid = int(args[0])
    kv, pos = _kv(args[1:])
    if pos:
        console.print("[red]✗ 多余的位置参数（请用 key=value 形式）[/red]")
        return 1
    if "permissions" in kv:
        v = kv["permissions"]
        kv["permissions"] = v if isinstance(v, list) else [p.strip() for p in str(v).split(",")]
    try:
        current = await app.api.get_json(f"/user/{uid}")
        body = {
            "id": uid, "account": kv.get("account", current.get("account")),
            "nickname": kv.get("nickname", current.get("nickname")),
            "email": kv.get("email", current.get("email")),
            "registered_at": current.get("registered_at"),  # 无 default，必须回传
            "password": None,  # skip_serializing 字段反序列化仍必需
            "description": kv.get("description", current.get("description")),
            "avatar": kv.get("avatar", current.get("avatar")),
            "institute_id": kv.get("institute_id", current.get("institute_id")),
            "permissions": kv.get("permissions", current.get("permissions") or []),
            "hidden": kv.get("hidden", current.get("hidden", False)),
            "banned": kv.get("banned", current.get("banned", False)),
        }
        user = await app.api.patch_json(f"/user/{uid}", json=body)
    except ApiError as e:
        console.print(f"[red]✗ 更新失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 用户 [bold]{user.get('account') if isinstance(user, dict) else uid}[/bold] 已更新")
    return 0


async def _user_delete(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin user delete <id>[/red]")
        return 1
    await app.api.delete_json(f"/user/{int(args[0])}")
    console.print(f"[green]✓[/green] 用户 #{args[0]} 已删除")
    return 0


async def _user_ip(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin user ip <id>[/red]")
        return 1
    data = await app.api.get_json(f"/user/{int(args[0])}/ip")
    items = data if isinstance(data, list) else []
    _table(f"用户 #{args[0]} IP 记录", ["IP", "更新时间"], [
        (i.get("ip") or i.get("address"), _fmt_time(i.get("updated_at"))) for i in items
    ])
    return 0


async def _user(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": _user_list, "show": _user_show, "update": _user_update,
        "delete": _user_delete, "ip": _user_ip,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知子命令: admin user {sub}（可用: list/show/update/delete/ip）[/red]")
        return 1
    return await handler(app, args[1:])


# ---- game ----

_GAME_DEFAULTS = {
    "id": 0, "brief": "", "introduction_id": None, "hidden": True, "offline": False,
    "frozen": False, "host_type": 1, "team_size": 1, "env_limit": None,  # HostType: 0=Training 1=Game
    "access_policy": {"restrict": False, "institutes": [], "sync": 2},
    "admins": [],  # 服务端强制为 [当前用户 id]，此处仅满足反序列化
    "cover": None, "logo": None, "enable_audit": False, "can_register_after_started": False,
    "award_rate": 0, "award_rates": None, "weight": 0, "bucket": None, "token": None,
    "timeline_presets": None, "node_selector": None, "traffic": None, "lifecycle": None,
    "archive_policy": {"challenge": {"show_answer": False, "show_hints": False}},
    "hammer_policy": {"enabled": True, "outer_label": None, "outer_url": None},
}


def _game_body(args: list[str], current: dict | None = None) -> tuple[dict, dict]:
    """构造 game model body：默认值 + k=v 覆盖；有 current 时以 current 为基底。"""
    kv, pos = _kv(args)
    base = dict(current) if current else dict(_GAME_DEFAULTS)
    if not current:
        base.update({
            "start_at": _now(), "end_at": _now(), "register_at": _now(),
            "archive_at": _now(), "updated_at": _now(),
        })
    base.update(kv)
    return base, kv


async def _game_list(app: App, args: list[str]) -> int:
    data = await app.api.get_json("/game", params=_page(args))
    items = _items(data)
    _table("比赛列表", ["ID", "名称", "类型", "状态"], [
        (g.get("id"), g.get("name"), g.get("host_type"),
         "归档" if g.get("archive_at") else ("冻结" if g.get("frozen") else ("隐藏" if g.get("hidden") else "进行中")))
        for g in items
    ])
    return 0


async def _game_show(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game show <id>[/red]")
        return 1
    game = await app.api.get_json(f"/game/{int(args[0])}")
    _table(f"比赛 #{game.get('id')} {game.get('name')}", ["字段", "值"], [
        (k, game.get(k)) for k in (
            "brief", "host_type", "start_at", "end_at", "register_at", "archive_at",
            "team_size", "hidden", "offline", "frozen", "enable_audit", "weight",
        ) if game.get(k) is not None
    ])
    return 0


async def _game_create(app: App, args: list[str]) -> int:
    body, kv = _game_body(args)
    name = kv.pop("name", None)
    if not name:
        console.print("[red]用法: admin game create name=<名称> [brief=...] [start_at=...] [k=v...][/red]")
        return 1
    body["name"] = name
    game = await app.api.post_json("/game", json=body)
    gid = game.get("id") if isinstance(game, dict) else None
    console.print(f"[green]✓[/green] 比赛 [bold]{name}[/bold] 已创建" + (f"（#{gid}）" if gid else ""))
    return 0


async def _game_update(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game update <id> key=value...[/red]")
        return 1
    gid = int(args[0])
    current = await app.api.get_json(f"/game/{gid}")
    body, kv = _game_body(args[1:], current=current)
    await app.api.patch_json(f"/game/{gid}", json=body)
    console.print(f"[green]✓[/green] 比赛 #{gid} 已更新")
    return 0


async def _game_delete(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game delete <id> [--force][/red]")
        return 1
    force = "--force" in args[1:]
    params = {"force": force} if force else None
    await app.api.delete_json(f"/game/{int(args[0])}", params=params)
    console.print(f"[green]✓[/green] 比赛 #{args[0]} 已删除")
    return 0


async def _game_token(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game token <id>[/red]")
        return 1
    game = await app.api.post_json(f"/game/{int(args[0])}/token")
    console.print(f"[green]✓[/green] 新 game token: [bold]{game.get('token') if isinstance(game, dict) else ''}[/bold]")
    return 0


async def _game_admins(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game admins <id> [user_id...][/red]")
        return 1
    gid = int(args[0])
    ids = [int(a) for a in args[1:] if a.isdigit()]
    if ids:
        await app.api.patch_json(f"/game/{gid}/administrator", json=ids)
        console.print(f"[green]✓[/green] 比赛 #{gid} 管理员已更新: {ids}")
    else:
        admins = await app.api.get_json(f"/game/{gid}/administrator")
        _table(f"比赛 #{gid} 管理员", ["ID", "账号", "昵称"], [
            (a.get("id"), a.get("account"), a.get("nickname")) for a in admins if isinstance(a, dict)
        ])
    return 0


async def _game_submissions(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game submissions <id> [page=...][/red]")
        return 1
    data = await app.api.get_json(f"/game/{int(args[0])}/submission", params=_page(args[1:]))
    items = _items(data)
    _table(f"比赛 #{args[0]} 提交记录", ["ID", "题目", "队伍", "结果", "时间"], [
        (s.get("id"), s.get("challenge_name") or s.get("challenge_id"),
         s.get("team_name") or s.get("team_id"), s.get("result") or ("✓" if s.get("solved") else "✗"),
         _fmt_time(s.get("created_at")))
        for s in items if isinstance(s, dict)
    ])
    return 0


async def _game_audit(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game audit <id> [page] | admin game audit <id> <audit_id> <通过|驳回> [原因][/red]")
        return 1
    gid = int(args[0])
    if len(args) >= 3:
        audit_id = int(args[1])
        action = args[2]
        state = {"通过": 2, "确认": 2, "accept": 2, "confirmed": 2,
                 "驳回": 1, "misjudged": 1, "reject": 1}.get(action.lower() if action.isascii() else action)
        if state is None:
            console.print("[red]✗ 操作应为「通过」或「驳回」[/red]")
            return 1
        reason = args[3] if len(args) > 3 else ("审核通过" if state == 2 else "审核驳回")
        # audit::Model 全字段无 serde default：先查列表取现状再合并（created_at 等必须回传）
        data = await app.api.get_json(f"/game/{gid}/audit", params={"page_size": 100})
        records = _items(data)
        prev = next((a for a in records if isinstance(a, dict) and a.get("id") == audit_id), None)
        if prev is None:
            console.print(f"[red]✗ 未找到审核 #{audit_id}（前 100 条内），可先 admin game audit {gid} 查看[/red]")
            return 1
        body = {
            "id": audit_id, "created_at": prev.get("created_at"), "reason": reason,
            "challenge_id": prev.get("challenge_id"), "user_id": prev.get("user_id"),
            "team_id": prev.get("team_id"), "game_id": prev.get("game_id"), "state": state,
        }
        await app.api.patch_json(f"/game/{gid}/audit/{audit_id}", json=body)
        console.print(f"[green]✓[/green] 审核 #{audit_id} 已{'确认' if state == 2 else '驳回'}")
        return 0
    params = {"page": int(args[1])} if len(args) > 1 and args[1].isdigit() else {}
    data = await app.api.get_json(f"/game/{gid}/audit", params=params)
    items = _items(data)
    state_names = {0: "待审核", 1: "已驳回", 2: "已确认"}
    _table(f"比赛 #{gid} 审核队列", ["ID", "题目", "队伍", "用户", "状态", "原因"], [
        (a.get("id"), a.get("challenge_name") or a.get("challenge_id"),
         a.get("team_name") or a.get("team_id"), a.get("user_name") or a.get("user_id"),
         state_names.get(a.get("state"), a.get("state")), a.get("reason"))
        for a in items if isinstance(a, dict)
    ])
    return 0


async def _game_stats(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: admin game stats <id>[/red]")
        return 1
    data = await app.api.get_json(f"/game/{int(args[0])}/statistics")
    _dump(data)
    return 0


async def _game(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": _game_list, "show": _game_show, "create": _game_create,
        "update": _game_update, "delete": _game_delete, "token": _game_token,
        "admins": _game_admins, "submissions": _game_submissions,
        "audit": _game_audit, "stats": _game_stats,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知子命令: admin game {sub}（可用: list/show/create/update/delete/token/admins/submissions/audit/stats）[/red]")
        return 1
    return await handler(app, args[1:])


# ---- challenge ----

_CHALLENGE_DEFAULTS = {
    "id": 0, "content": None, "hidden": True, "tag": [], "score": 500,
    "score_rule": {"initial": 500, "minimum": 0, "decay": 0},
    "bucket": None, "ref_id": None, "release_at": None, "archive_at": None,
}


def _resolve_gid(app: App, args: list[str]) -> tuple[int | None, int | None, list[str]]:
    """解析 gid/cid：k=v 或位置参数（cid 为首个数字，剩余参数中最后一个数字为 gid）；
    gid 缺省用当前比赛上下文。"""
    kv, pos = _kv(args)
    gid = kv.pop("gid", None)
    if gid is not None:
        gid = int(gid)
    cid = None
    if pos and pos[0].isdigit():
        cid = int(pos.pop(0))
        for a in reversed(pos):
            if a.isdigit():
                gid = int(a)
                break
    if gid is None:
        gid = app.ctx.game_id
    return gid, cid, pos


async def _challenge_list(app: App, args: list[str]) -> int:
    gid = None
    if args and args[0].isdigit():
        gid = int(args.pop(0))
    if gid is None:
        gid = app.ctx.game_id
    if gid is None:
        console.print("[red]用法: admin challenge list <gid>（或先 cd /contests/<gid>）[/red]")
        return 1
    data = await app.api.get_json(f"/game/{gid}/challenge")
    items = _items(data)
    _table(f"比赛 #{gid} 题目", ["ID", "名称", "分值", "状态"], [
        (c.get("id"), c.get("name"), c.get("score"),
         "隐藏" if c.get("hidden") else ("已归档" if c.get("archive_at") else "已发布"))
        for c in items if isinstance(c, dict)
    ])
    return 0


async def _challenge_show(app: App, args: list[str]) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge show <cid> [gid][/red]")
        return 1
    ch = await app.api.get_json(f"/game/{gid}/challenge/{cid}")
    _table(f"题目 #{cid}", ["字段", "值"], [
        (k, ch.get(k)) for k in ("name", "score", "hidden", "release_at", "archive_at", "tag")
        if ch.get(k) is not None
    ])
    if ch.get("content"):
        console.print(ch["content"].strip())
    return 0


async def _challenge_create(app: App, args: list[str]) -> int:
    kv, pos = _kv(args)
    gid = kv.pop("gid", None)
    if gid is None and pos and pos[0].isdigit():
        gid = int(pos.pop(0))
    if gid is None:
        gid = app.ctx.game_id
    if gid is None:
        console.print("[red]用法: admin challenge create <gid> name=<名称> [content=...] [score=...] [k=v...][/red]")
        return 1
    name = kv.pop("name", None) or (pos[0] if pos else None)
    if not name:
        console.print("[red]用法: admin challenge create <gid> name=<名称> [content=...][/red]")
        return 1
    body = dict(_CHALLENGE_DEFAULTS)
    body["game_id"] = int(gid)
    body["name"] = name
    body["updated_at"] = _now()
    body.update(kv)
    if body.get("score") == 500 and "score" not in kv:
        body["score"] = body["score_rule"].get("initial", 500)
    try:
        ch = await app.api.post_json(f"/game/{int(gid)}/challenge", json=body)
    except ApiError as e:
        console.print(f"[red]✗ 创建失败: {e.message}[/red]")
        return 1
    cid = ch.get("id") if isinstance(ch, dict) else None
    console.print(f"[green]✓[/green] 题目 [bold]{name}[/bold] 已创建" + (f"（#{cid}，隐藏，发布用 publish）" if cid else ""))
    return 0


async def _challenge_update(app: App, args: list[str]) -> int:
    gid, cid, rest = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge update <cid> [gid] key=value...[/red]")
        return 1
    try:
        current = await app.api.get_json(f"/game/{gid}/challenge/{cid}")
        kv, pos = _kv(rest)
        body = dict(current)
        body.update(kv)
        await app.api.patch_json(f"/game/{gid}/challenge/{cid}", json=body)
    except ApiError as e:
        console.print(f"[red]✗ 更新失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 题目 #{cid} 已更新")
    return 0


async def _challenge_delete(app: App, args: list[str]) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge delete <cid> [gid][/red]")
        return 1
    try:
        await app.api.delete_json(f"/game/{gid}/challenge/{cid}")
    except ApiError as e:
        console.print(f"[red]✗ 删除失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 题目 #{cid} 已删除")
    return 0


async def _challenge_publish(app: App, args: list[str], up: bool) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge publish|unpublish <cid> [gid][/red]")
        return 1
    try:
        if up:
            await app.api.post_json(f"/game/{gid}/challenge/{cid}/publish")
        else:
            await app.api.delete_json(f"/game/{gid}/challenge/{cid}/publish")
    except ApiError as e:
        console.print(f"[red]✗ {'发布' if up else '下架'}失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 题目 #{cid} 已{'发布' if up else '下架'}")
    return 0


async def _challenge_answer(app: App, args: list[str]) -> int:
    gid, cid, rest = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge answer <cid> [gid] [flag][/red]")
        return 1
    kv, pos = _kv(rest)
    flag = kv.pop("flag", None) or (pos[0] if pos else None)
    if flag is None:
        answer = await app.api.get_json(f"/game/{gid}/challenge/{cid}/answer")
        text = answer if isinstance(answer, str) else (answer.get("answer") if isinstance(answer, dict) else str(answer))
        console.print(f"当前答案: [bold]{text}[/bold]")
        return 0
    try:
        await app.api.patch_json(f"/game/{gid}/challenge/{cid}/answer", json=flag)  # body 为裸 JSON 字符串
    except ApiError as e:
        console.print(f"[red]✗ 设置答案失败: {e.message}[/red]")
        return 1
    console.print("[green]✓[/green] 答案已更新")
    return 0


async def _challenge_history(app: App, args: list[str]) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge history <cid> [gid][/red]")
        return 1
    data = await app.api.get_json(f"/game/{gid}/challenge/{cid}/history")
    _dump(data)
    return 0


async def _challenge_submissions(app: App, args: list[str]) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge submissions <cid> [gid][/red]")
        return 1
    data = await app.api.get_json(f"/game/{gid}/challenge/{cid}/submission")
    items = _items(data)
    _table(f"题目 #{cid} 提交记录", ["ID", "队伍", "用户", "结果", "时间"], [
        (s.get("id"), s.get("team_name") or s.get("team_id"), s.get("user_name") or s.get("user_id"),
         s.get("result") or ("✓" if s.get("solved") else "✗"), _fmt_time(s.get("created_at")))
        for s in items if isinstance(s, dict)
    ])
    return 0


async def _challenge_instances(app: App, args: list[str]) -> int:
    gid, cid, _ = _resolve_gid(app, args)
    if gid is None or cid is None:
        console.print("[red]用法: admin challenge instances <cid> [gid][/red]")
        return 1
    data = await app.api.get_json(f"/game/{gid}/challenge/{cid}/instance")
    items = _items(data)
    _table(f"题目 #{cid} 运行实例", ["状态", "用户", "队伍", "流量", "端口"], [
        (i.get("state"), i.get("user_name") or i.get("user_id"), i.get("team_name") or i.get("team_id"),
         i.get("traffic"), ",".join(str(p) for p in (i.get("ports") or [])))
        for i in items if isinstance(i, dict)
    ])
    return 0


async def _challenge_attach(app: App, args: list[str]) -> int:
    from pathlib import Path
    gid, cid, rest = _resolve_gid(app, args)
    if gid is None or cid is None or not rest:
        console.print("[red]用法: admin challenge attach <cid> <本地文件> [gid] [folder=static|mapped|checker][/red]")
        return 1
    kv, pos = _kv(rest)
    file_arg = pos[0] if pos else None
    if not file_arg:
        console.print("[red]用法: admin challenge attach <cid> <本地文件> [folder=static|mapped|checker][/red]")
        return 1
    folder = kv.pop("folder", "static")
    if folder not in ("static", "mapped", "checker"):
        console.print("[red]✗ folder 应为 static/mapped/checker[/red]")
        return 1
    path = Path(file_arg)
    if not path.is_file():
        console.print(f"[red]✗ 文件不存在: {path}[/red]")
        return 1
    try:
        with path.open("rb") as fh:
            await app.api.upload(
                f"/game/{gid}/challenge/{cid}/file", params={"folder": folder},
                files={"file": (path.name, fh.read(), "application/octet-stream")},
            )
    except ApiError as e:
        console.print(f"[red]✗ 上传失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 已上传 [bold]{path.name}[/bold] 到题目 #{cid}（{folder}）")
    return 0


async def _challenge(app: App, args: list[str]) -> int:
    sub = args[0] if args else "list"
    table = {
        "list": _challenge_list, "show": _challenge_show, "create": _challenge_create,
        "update": _challenge_update, "delete": _challenge_delete,
        "publish": lambda a, x: _challenge_publish(a, x, True),
        "unpublish": lambda a, x: _challenge_publish(a, x, False),
        "answer": _challenge_answer, "history": _challenge_history,
        "submissions": _challenge_submissions, "instances": _challenge_instances,
        "attach": _challenge_attach,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知子命令: admin challenge {sub}（可用: list/show/create/update/delete/publish/unpublish/answer/history/submissions/instances/attach）[/red]")
        return 1
    return await handler(app, args[1:])


# ---- platform / cluster ----

async def _platform(app: App, args: list[str]) -> int:
    sub = args[0] if args else "stats"
    if sub == "stats":
        _dump(await app.api.get_json("/platform/statistics"))
        return 0
    if sub == "logs":
        kv, _ = _kv(args[1:])
        params = {k: v for k, v in kv.items() if k in ("query", "level", "limit", "from", "account", "trace")}
        _dump(await app.api.get_json("/platform/logs", params=params))
        return 0
    if sub == "config":
        _dump(await app.api.get_json("/platform/config"))
        return 0
    console.print(f"[red]✗ 未知子命令: admin platform {sub}（可用: stats/logs/config）[/red]")
    return 1


async def _cluster(app: App, args: list[str]) -> int:
    sub = args[0] if args else "nodes"
    if sub == "nodes":
        _dump(await app.api.get_json("/cluster/node"))
        return 0
    if sub == "config":
        _dump(await app.api.get_json("/cluster/config"))
        return 0
    if sub == "calmdown":
        _dump(await app.api.get_json("/cluster/calmdown"))
        return 0
    console.print(f"[red]✗ 未知子命令: admin cluster {sub}（可用: nodes/config/calmdown）[/red]")
    return 1


# ---- dispatch ----

async def _admin_dispatch(app: App, args: list[str]) -> int:
    blocked = _require_admin(app)
    if blocked is not None:
        return blocked
    group = args[0] if args else None
    groups = {
        "bulletin": _bulletin, "user": _user, "game": _game,
        "challenge": _challenge, "platform": _platform, "cluster": _cluster,
    }
    handler = groups.get(group)
    if handler is None:
        console.print(f"[red]用法: {_USAGE}[/red]")
        if group:
            console.print(f"[red]✗ 未知分组: {group}（可用: {'/'.join(groups)}）[/red]")
        return 1
    return await handler(app, args[1:])


def register_admin() -> None:
    register(Command(
        "admin", _admin_dispatch, usage=_USAGE,
        description="管理员操作（公告/用户/比赛/题目/平台/集群，需要对应权限）",
        category="管理",
    ))
