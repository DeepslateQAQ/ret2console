"""排行榜命令：scoreboard [比赛] [--page N] [--page-size N]（SPEC §14）。"""

from __future__ import annotations

from ret2console import service
from ret2console.commands import Command, register, report_service_error
from ret2console.output import console, make_table
from ret2console.service import ServiceError
from ret2console.state import App
from ret2console.vfs_builder import _load_institutes, team_org_name


def _parse(args: list[str]) -> tuple[list[str], int, int, str | None]:
    page, page_size = 1, 20
    rest: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--page", "--page-size"):
            if i + 1 >= len(args):
                return rest, page, page_size, f"{arg} 需要参数"
            i += 1
            value = args[i]
        elif arg.startswith(("--page=", "--page-size=")):
            arg, _, value = arg.partition("=")
        else:
            rest.append(arg)
            i += 1
            continue
        if not value.isdigit() or int(value) < 1:
            return rest, page, page_size, f"{arg} 需要正整数"
        if arg == "--page":
            page = int(value)
        else:
            page_size = int(value)
        i += 1
    return rest, page, page_size, None


async def cmd_scoreboard(app: App, args: list[str]) -> int:
    rest, page, page_size, error = _parse(args)
    if error is not None:
        console.print(f"[red]✗ {error}[/red]")
        return 1
    ref = " ".join(rest).strip() or None
    try:
        data = await service.get_scoreboard(app, ref, page=page, page_size=page_size)
    except ServiceError as e:
        return report_service_error(e)
    teams = data["items"]
    count = data.get("total") or 0
    gid = data.get("game_id")
    if not teams:
        console.print("（暂无排行数据）")
        return 0
    institutes = await _load_institutes(app)
    table = make_table(title=f"比赛 {gid} 排行榜（共 {count} 队）")
    table.add_column("#", justify="right", style="bold", no_wrap=True)
    table.add_column("队名")
    table.add_column("组织", style="dim")
    table.add_column("分数", justify="right", no_wrap=True)
    offset = (page - 1) * page_size
    for i, team in enumerate(teams, 1):
        table.add_row(
            str(offset + i),
            str(team.get("name") or team.get("title") or team.get("id") or "?"),
            team_org_name(team, institutes) or "—",
            str(team.get("score") or 0),
        )
    console.print(table)
    return 0


def register_scoreboard() -> None:
    register(Command(
        "scoreboard", cmd_scoreboard, usage="scoreboard [比赛] [--page N] [--page-size N]",
        description="查看排行榜", category="比赛",
    ))
