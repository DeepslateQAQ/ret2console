"""队伍命令：team create/join/modify/info/rank/member/solve/leave（SPEC §9，队伍与比赛绑定）。"""

from __future__ import annotations

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.interact import confirm
from ret2console.output import console, is_tty, render_markdown
from ret2console.state import App

_TEAM_STATE = {0: "封禁", 1: "待审核", 2: "隐藏", 3: "已通过"}


async def _confirm_rules(app: App, gid: int, action: str, *, yes: bool) -> int | None:
    """展示 doc/rules 并确认（--yes 跳过确认）；返回 None 表示继续执行。"""
    try:
        rules = await app.api.get_game_doc(gid, "rules")
    except ApiError:
        return None  # 规则文档不可用：跳过展示与确认
    if not isinstance(rules, str) or not rules.strip():
        return None
    console.print(render_markdown(rules))
    if yes:
        return None
    if not is_tty():
        console.print("[red]✗ 非交互模式需要 --yes[/red]")
        return 1
    try:
        ok = await confirm(f"已阅读参赛规则，{action}？")
    except (KeyboardInterrupt, EOFError):
        console.print("\n已取消")
        return 1
    if not ok:
        console.print("[yellow]! 已取消[/yellow]")
        return 0
    return None


async def _self_team(app: App, gid: int) -> dict | None:
    """当前用户在该比赛的队伍（未加入返回 None）。"""
    try:
        team = await app.api.get_json(f"/game/{gid}/team/self")
    except ApiError:
        return None
    return team if isinstance(team, dict) and team else None


def _require_game(app: App) -> int | None:
    if not app.session.logged_in:
        console.print("[yellow]! 请先登录：auth login[/yellow]")
        return 1
    if app.ctx.game_id is None:
        console.print("[yellow]! 请先进入比赛上下文：cd /contests/<id> 或 contest use <id>[/yellow]")
        return 1
    return None


def _team_id(app: App, args: list[str], team: dict | None) -> int | None:
    if args and args[0].isdigit():
        return int(args[0])
    if team:
        return int(team.get("id") or 0) or None
    return None


async def cmd_team_create(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    if gid is None:
        console.print("[yellow]! 请先进入比赛上下文[/yellow]")
        return 1
    yes = "--yes" in args or "-y" in args
    rest = [a for a in args if a not in ("--yes", "-y")]
    if not rest:
        console.print("[red]用法: team create <队伍名> [tag] [--yes][/red]")
        return 1
    name = rest[0]
    tag = rest[1] if len(rest) > 1 else None
    blocked = await _confirm_rules(app, gid, "创建队伍", yes=yes)
    if blocked is not None:
        return blocked
    try:
        team = await app.api.post_json(f"/game/{gid}/team", json={"name": name, "tag": tag})
    except ApiError as e:
        console.print(f"[red]✗ 创建失败: {e.message}[/red]")
        return 1
    tid = team.get("id") if isinstance(team, dict) else None
    token = team.get("token") if isinstance(team, dict) else None
    console.print(f"[green]✓[/green] 队伍 [bold]{name}[/bold] 创建成功" + (f"（#{tid}）" if tid else ""))
    if token:
        console.print("[yellow]! 邀请队友加入：[/yellow]")
        console.print(f"[bold]team join {token}[/bold]")
    else:
        console.print("[dim]（未返回邀请 token，可让队友用你的邀请码加入）[/dim]")
    return 0


async def cmd_team_join(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    if gid is None:
        console.print("[yellow]! 请先进入比赛上下文[/yellow]")
        return 1
    yes = "--yes" in args or "-y" in args
    rest = [a for a in args if a not in ("--yes", "-y")]
    if not rest:
        console.print("[red]用法: team join <邀请token> [--yes][/red]")
        return 1
    blocked = await _confirm_rules(app, gid, "加入队伍", yes=yes)
    if blocked is not None:
        return blocked
    try:
        team = await app.api.patch_json(f"/game/{gid}/team", json={"token": rest[0]})
    except ApiError as e:
        console.print(f"[red]✗ 加入失败: {e.message}[/red]")
        return 1
    name = team.get("name") if isinstance(team, dict) else ""
    console.print(f"[green]✓[/green] 已加入队伍 [bold]{name or '?'}[/bold]")
    return 0


async def cmd_team_modify(app: App, args: list[str]) -> int:
    """修改队伍信息：PATCH /game/{gid}/team/self（名字必填，tag 省略保留原值、传 - 清空）。"""
    gid = app.ctx.game_id
    team = await _self_team(app, gid)
    if team is None:
        console.print("[yellow]! 尚未加入队伍，无法修改[/yellow]")
        return 1
    if not args:
        console.print("[red]用法: team modify <新名字> [新tag]（tag 传 - 清空）[/red]")
        return 1
    name = args[0]
    if len(args) > 1:
        tag = None if args[1] == "-" else args[1]
    else:
        tag = team.get("tag")  # 省略时保留原 tag（r2s 的 tag 为 Option，传 null 会清空）
    institute_id = team.get("institute_id")  # 服务端省略 institute_id 会清空，这里显式回传保留
    try:
        updated = await app.api.patch_json(
            f"/game/{gid}/team/self",
            json={"name": name, "tag": tag, "institute_id": institute_id},
        )
    except ApiError as e:
        console.print(f"[red]✗ 修改失败: {e.message}[/red]")
        return 1
    new_name = updated.get("name") if isinstance(updated, dict) else name
    console.print(f"[green]✓[/green] 队伍已更新: [bold]{new_name}[/bold]")
    return 0


async def cmd_team_info(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    try:
        team = await app.api.get_json(f"/game/{gid}/team/self")
    except ApiError as e:
        if "not found" in (e.message or "").lower():
            team = None
        else:
            console.print(f"[red]✗ {e.message}[/red]")
            return 1
    if not team:
        console.print("[yellow]! 尚未加入队伍（team create <名字> 或 team join <token>）[/yellow]")
        return 1
    state = _TEAM_STATE.get(int(team.get("state") or 3), team.get("state"))
    console.print(f"[bold]{team.get('name') or '?'}[/bold] [dim](#{team.get('id')})[/dim]")
    console.print(f"状态: {state}  积分: {team.get('score', 0)}")
    if team.get("tag"):
        console.print(f"tag: {team['tag']}")
    if team.get("institute_name"):
        console.print(f"单位: {team['institute_name']}")
    token = team.get("token")
    if token:
        console.print(f"邀请 token: {token}")
    return 0


async def cmd_team_rank(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    team = await _self_team(app, gid)
    tid = _team_id(app, args, team)
    if tid is None:
        console.print("[yellow]! 尚未加入队伍，或用法: team rank [队伍id][/yellow]")
        return 1
    try:
        rank = await app.api.get_json(f"/game/{gid}/team/{tid}/rank")
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    console.print(f"队伍 [bold]{team.get('name') if team else tid}[/bold] 当前排名: [bold]#{rank}[/bold]")
    return 0


async def cmd_team_member(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    team = await _self_team(app, gid)
    tid = _team_id(app, args, team)
    if tid is None:
        console.print("[yellow]! 尚未加入队伍，或用法: team member [队伍id][/yellow]")
        return 1
    try:
        members = await app.api.get_json(f"/game/{gid}/team/{tid}/member")
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    if not members:
        console.print("（队伍暂无成员）")
        return 0
    for m in members:
        nick = m.get("nickname") or m.get("account") or "?"
        extra = f"（{m['account']}）" if m.get("account") and m.get("nickname") else ""
        console.print(f"- {nick}{extra}")
    return 0


async def cmd_team_solve(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    team = await _self_team(app, gid)
    tid = _team_id(app, args, team)
    if tid is None:
        console.print("[yellow]! 尚未加入队伍，或用法: team solve [队伍id][/yellow]")
        return 1
    try:
        solves = await app.api.get_json(f"/game/{gid}/team/{tid}/solve")
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    if not solves:
        console.print("（暂未解出任何题目）")
        return 0
    for s in solves:
        mark = "✓" if s.get("solved") else "✗"
        result = s.get("result") or ""
        console.print(f"{mark} 题目 #{s.get('challenge_id', '?')}  {result}")
    return 0


async def cmd_team_leave(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    team = await _self_team(app, gid)
    if team is None:
        console.print("[yellow]! 尚未加入队伍[/yellow]")
        return 1
    try:
        await app.api.delete_json(f"/game/{gid}/team/self")
    except ApiError as e:
        console.print(f"[red]✗ 退出失败: {e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] 已退出队伍 [bold]{team.get('name') or ''}[/bold]")
    return 0


async def _team_dispatch(app: App, args: list[str]) -> int:
    sub = args[0] if args else "info"
    table = {
        "create": cmd_team_create,
        "join": cmd_team_join,
        "modify": cmd_team_modify,
        "info": cmd_team_info,
        "rank": cmd_team_rank,
        "member": cmd_team_member,
        "solve": cmd_team_solve,
        "leave": cmd_team_leave,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知 team 子命令: {sub}（可用: create/join/modify/info/rank/member/solve/leave）[/red]")
        return 1
    blocked = _require_game(app)
    if blocked is not None:
        return blocked
    return await handler(app, args[1:])


def register_team() -> None:
    register(Command("team", _team_dispatch, usage="team <create|join|modify|info|rank|member|solve|leave>", description="队伍管理（与当前比赛绑定）", category="队伍"))
