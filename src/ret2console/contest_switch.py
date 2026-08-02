"""进入账号后的活跃赛事检测：已加入队伍且在比赛时间内 → 询问快速切换。"""

from __future__ import annotations

import time

from ret2console.api.client import ApiError
from ret2console.interact import confirm
from ret2console.output import console, is_tty
from ret2console.state import App


async def find_joined_active_games(app: App) -> list[tuple[dict, dict]]:
    """返回 [(比赛, 队伍)]：已登录账号加入过队伍、且当前处于比赛时间内的比赛。"""
    if not app.session.logged_in:
        return []
    try:
        games = await app.api.list_games(page=1, page_size=100)
    except ApiError:
        return []
    now = int(time.time())
    active: list[dict] = []
    for g in games:
        gid = g.get("id")
        if gid is None:
            continue
        try:
            start, end = int(g.get("start_at") or 0), int(g.get("end_at") or 0)
        except (TypeError, ValueError):
            continue
        if start and end and start <= now < end:
            active.append(g)
    joined: list[tuple[dict, dict]] = []
    for g in active:
        try:
            team = await app.api.get_json(f"/game/{g['id']}/team/self")
        except ApiError:
            continue
        if isinstance(team, dict) and team:
            joined.append((g, team))
    return joined


async def offer_switch(app: App) -> bool:
    """检测并询问是否快速切换到已加入的进行中比赛；非交互环境直接跳过。"""
    if not is_tty() or not app.session.logged_in:
        return False
    try:
        joined = await find_joined_active_games(app)
    except Exception:
        return False
    if not joined:
        return False
    from ret2console.commands.contest import _game_status
    for game, team in joined:
        gid = game.get("id")
        title = game.get("title") or game.get("name") or f"比赛 {gid}"
        team_name = team.get("name") or "?"
        status = _game_status(game) or "进行中"
        ok = await confirm(
            f"检测到已加入的比赛「{title}」（{status}，队伍：{team_name}），是否快速切换？"
        )
        if ok:
            app.ctx.set_game(int(gid))
            console.print(f"[green]✓[/green] 已切换到 [bold]/contests/{gid}[/bold]")
            return True
    return False
