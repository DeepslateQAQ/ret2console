"""提交记录命令：submissions [比赛]（SPEC §9）。"""

from __future__ import annotations

from ret2console import service
from ret2console.commands import Command, register, report_service_error
from ret2console.output import console, format_ts, make_table
from ret2console.service import ServiceError
from ret2console.state import App


async def cmd_submissions(app: App, args: list[str]) -> int:
    ref = " ".join(args).strip() or None
    try:
        gid = await service.resolve_game(app, ref)
        items = await service.list_submissions(app, gid)
    except ServiceError as e:
        return report_service_error(e)
    if not items:
        console.print("（暂无提交记录）")
        return 0
    table = make_table(title=f"比赛 {gid} 提交记录")
    table.add_column("时间", style="dim")
    table.add_column("题目")
    table.add_column("结果")
    table.add_column("分数", justify="right")
    for item in items:
        name = item.get("challenge_name") or item.get("challenge_id") or "?"
        result = item.get("result") or ("Solved" if item.get("solved") else "—")
        score = item.get("score")
        table.add_row(
            format_ts(item.get("created_at", "")),
            str(name),
            str(result),
            str(score) if score not in (None, "") else "—",
        )
    console.print(table)
    return 0


def register_submission() -> None:
    from ret2console.commands import REGISTRY
    if "submissions" in REGISTRY:
        return
    register(Command(
        "submissions", cmd_submissions, usage="submissions [比赛]",
        description="查看当前账号在该比赛的提交记录", category="提交",
    ))
