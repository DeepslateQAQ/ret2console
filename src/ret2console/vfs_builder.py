"""VFS 动态节点构造：比赛/题目目录树（数据来自 API，SPEC §7）。"""

from __future__ import annotations

from typing import Awaitable, Callable

from ret2console.state import App
from ret2console.vfs import Node, dir_node, file_node


async def _load_games(app: App) -> list[Node]:
    games = await app.api.list_games()
    cache: dict[int, str] = {}
    nodes: list[Node] = []
    for g in games:
        gid = g.get("id")
        if gid is None:
            continue
        title = str(g.get("title") or g.get("name") or gid)
        cache[int(gid)] = title
        nodes.append(dir_node(str(gid), game_id=int(gid), meta=title, loader=_game_loader(app, int(gid))))
    app.games_cache = cache
    return nodes


def _game_loader(app: App, gid: int) -> Callable[[], Awaitable[list[Node]]]:
    async def load() -> list[Node]:
        return [
            file_node("info", game_id=gid, reader=lambda: _game_info(app, gid)),
            file_node("notice", game_id=gid, reader=lambda: _game_notice(app, gid)),
            file_node("scoreboard", game_id=gid, reader=lambda: _game_scoreboard(app, gid)),
            dir_node("docs", game_id=gid, loader=_docs_loader(app, gid)),
            file_node("team", game_id=gid, reader=lambda: _team_info(app, gid)),
            dir_node("challenges", game_id=gid, loader=_challenges_loader_wrapper(app, gid)),
        ]
    return load


def _challenges_loader_wrapper(app: App, gid: int) -> Callable[[], Awaitable[list[Node]]]:
    async def load() -> list[Node]:
        return await _challenges_loader(app, gid)
    return load


def _docs_loader(app: App, gid: int) -> Callable[[], Awaitable[list[Node]]]:
    async def load() -> list[Node]:
        return [
            file_node("readme", game_id=gid, reader=lambda: app.api.get_game_doc(gid, "readme")),
            file_node("training", game_id=gid, reader=lambda: app.api.get_game_doc(gid, "training")),
            file_node("rules", game_id=gid, reader=lambda: app.api.get_game_doc(gid, "rules")),
        ]
    return load


async def _challenges_loader(app: App, gid: int) -> list[Node]:
    challenges = await app.api.list_challenges(gid)
    cache: dict[int, str] = {}
    nodes: list[Node] = []
    for c in challenges:
        cid = c.get("id")
        if cid is None:
            continue
        title = str(c.get("title") or c.get("name") or cid)
        cache[int(cid)] = title
        nodes.append(dir_node(str(cid), game_id=gid, challenge_id=int(cid), meta=title, loader=_challenge_loader(app, gid, int(cid))))
    app.challenges_cache[gid] = cache
    return nodes


def _challenge_loader(app: App, gid: int, cid: int) -> Callable[[], Awaitable[list[Node]]]:
    async def load() -> list[Node]:
        return [
            file_node("README", game_id=gid, challenge_id=cid, reader=lambda: _challenge_readme(app, gid, cid)),
            file_node("hint", game_id=gid, challenge_id=cid, reader=lambda: _challenge_hint(app, gid, cid)),
            dir_node("files", game_id=gid, challenge_id=cid, loader=_files_loader(app, gid, cid)),
            file_node("instance", game_id=gid, challenge_id=cid, reader=lambda: _instance_info(app, gid, cid)),
            file_node("flag", game_id=gid, challenge_id=cid, reader=lambda: "（提交 flag：请粘贴 flag 或使用 submit 命令）"),
        ]
    return load


def _files_loader(app: App, gid: int, cid: int) -> Callable[[], Awaitable[list[Node]]]:
    async def load() -> list[Node]:
        data = await app.api.get_json(f"/game/{gid}/challenge/{cid}/file")
        items = data if isinstance(data, list) else data.get("files", [])
        return [
            file_node(str(f.get("file") or f.get("name") or i), game_id=gid, challenge_id=cid, reader=lambda f=f: _file_reader(app, gid, cid, f))
            for i, f in enumerate(items)
        ]
    return load


async def _file_reader(app: App, gid: int, cid: int, f: dict) -> str:
    name = str(f.get("file") or f.get("name") or "?")
    folder = str(f.get("folder") or "?")
    if name.endswith((".zip", ".tar", ".gz", ".7z", ".rar", ".xz")):
        return f"压缩包附件 {name}（{folder}）：请使用 download 命令下载（cat 不直接输出二进制）"
    return f"附件 {name}（{folder}）：请使用 download 命令下载"


async def _game_info(app: App, gid: int) -> str:
    from ret2console.commands.contest import _fmt_game
    game = await app.api.get_game(gid)
    gid_s, title, status = _fmt_game(game)
    desc = game.get("description") or game.get("introduction") or "（无简介）"
    return f"比赛 #{gid_s}  {title}\n状态: {status}\n\n{desc}"


async def _game_notice(app: App, gid: int) -> str:
    notices = await app.api.get_notifications(gid)
    if not notices:
        return "（暂无比赛通知）"
    lines = []
    for n in notices:
        lines.append(f"**{n.get('title') or '通知'}**  {n.get('created_at', '')}\n{n.get('content') or n.get('text') or ''}")
    return "\n\n".join(lines)


async def _game_scoreboard(app: App, gid: int) -> str:
    data = await app.api.get_json(
        f"/game/{gid}/team",
        params={"min_state": "passed", "order": "score", "asc": False, "page": 1, "page_size": 20},
    )
    teams, count = data if isinstance(data, list) and len(data) == 2 else (data if isinstance(data, list) else [], 0)
    if not teams:
        return "（暂无排行数据）"
    lines = [f"比赛 {gid} 排行榜（共 {count} 队）", ""]
    for i, team in enumerate(teams, 1):
        lines.append(f"{i:>3}. {team.get('name') or team.get('title') or team.get('id') or '?'}  {team.get('score') or 0} 分")
    return "\n".join(lines)


async def _team_info(app: App, gid: int) -> str:
    try:
        team = await app.api.get_json(f"/game/{gid}/team/self")
    except Exception:
        return "（未加入队伍，或比赛未开放队伍查询）"
    if not team:
        return "（未加入队伍）"
    return f"队伍: {team.get('name') or team.get('id')}\n队长: {team.get('leader') or team.get('captain') or '?'}\n简介: {team.get('description') or '—'}"


async def _challenge_readme(app: App, gid: int, cid: int) -> str:
    challenge = await app.api.get_challenge(gid, cid)
    title = challenge.get("title") or f"题目 {cid}"
    content = challenge.get("content") or challenge.get("description") or "（无题面内容）"
    return f"# {title}\n\n{content}"


async def _challenge_hint(app: App, gid: int, cid: int) -> str:
    data = await app.api.get_json(f"/game/{gid}/challenge/{cid}/hint")
    items = data if isinstance(data, list) else data.get("hints", [])
    if not items:
        return "（暂无提示）"
    lines = []
    for i, h in enumerate(items, 1):
        title = h.get("title") or f"提示 {i}"
        if h.get("unlocked"):
            lines.append(f"**{title}**: {h.get('content') or ''}")
        else:
            lines.append(f"*{title}*: （未解锁）")
    return "\n\n".join(lines)


async def _instance_info(app: App, gid: int, cid: int) -> str:
    try:
        instances = await app.api.list_instances(gid)
    except Exception as e:
        return f"（实例状态获取失败: {getattr(e, 'message', e)}）"
    items = [
        i for i in instances
        if isinstance(i, dict) and str(i.get("challenge_id")) == str(cid)
    ]
    if not items or (len(items) == 1 and not items[0]):
        return "（无运行中的实例，challenge start 启动）"
    lines = []
    for inst in items:
        lines.append(f"state: {inst.get('state', '?')}")
        if inst.get("traffic"):
            from ret2console.wsrx import tunnel_status
            lines.append(f"tunnel: {tunnel_status(app.wsrx, str(inst['traffic']))}")
        if inst.get("ports"):
            lines.append(f"ports: {', '.join(map(str, inst['ports']))}")
        lines.append(f"renew: {inst.get('renew_count', 0)}  created: {inst.get('created_at', '')}")
    return "\n".join(lines)


async def _bulletin(app: App) -> str:
    """根 /notice：全局公告（GET /api/bulletin，分页 [items, total]）。"""
    try:
        data = await app.api.get_json("/bulletin")
    except Exception as e:
        return f"（全局公告获取失败: {getattr(e, 'message', e)}）"
    if isinstance(data, list) and data and isinstance(data[0], list):
        items = data[0]  # 分页格式 [items, total]
    elif isinstance(data, dict):
        items = data.get("data") or data.get("items") or []
    else:
        items = data or []
    if not items:
        return "（暂无全局公告）"
    lines = []
    for a in items:
        if not isinstance(a, dict):
            continue
        title = a.get("title") or "公告"
        created = a.get("created_at", "")
        content = a.get("content") or ""
        lines.append(f"**{title}**  {created}\n{content}")
    return "\n\n".join(lines)


def build_vfs(app: App) -> None:
    from ret2console.vfs import Vfs
    vfs = Vfs()
    vfs.build(contests_loader=lambda: _load_games(app), bulletin_reader=lambda: _bulletin(app))
    app.vfs = vfs
