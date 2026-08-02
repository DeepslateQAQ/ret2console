"""排行榜命令：scoreboard（GET /api/game/{id}/team?min_state=3&order=score&asc=false）。"""

from __future__ import annotations

from ret2console.output import make_table

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.output import console
from ret2console.state import App
from ret2console.vfs_builder import _load_institutes, team_org_name


async def cmd_scoreboard(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id if not args else (int(args[0]) if args[0].isdigit() else None)
    if gid is None:
        console.print("[yellow]! 未指定比赛：请先 cd /contests/<id> 或 scoreboard <id>[/yellow]")
        return 1
    try:
        data = await app.api.get_json(
            f"/game/{gid}/team",
            params={"min_state": 3, "order": "score", "asc": False, "page": 1, "page_size": 20},
        )
    except ApiError as e:
        console.print(f"[red]✗ {e.message}[/red]")
        return 1
    teams, count = data if isinstance(data, list) and len(data) == 2 else (data if isinstance(data, list) else [], 0)
    if not teams:
        console.print("（暂无排行数据）")
        return 0
    institutes = await _load_institutes(app)
    table = make_table(title=f"比赛 {gid} 排行榜（共 {count} 队）")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("队名")
    table.add_column("组织", style="dim")
    table.add_column("分数", justify="right", no_wrap=True)
    for i, team in enumerate(teams, 1):
        table.add_row(
            str(i),
            str(team.get("name") or team.get("title") or team.get("id") or "?"),
            team_org_name(team, institutes) or "—",
            str(team.get("score") or 0),
        )
    console.print(table)
    return 0


def register_scoreboard() -> None:
    register(Command("scoreboard", cmd_scoreboard, usage="scoreboard [比赛id]", description="查看排行榜", category="比赛"))
