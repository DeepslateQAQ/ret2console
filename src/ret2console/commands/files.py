"""文件命令：cat（读取虚拟文件）/ download（下载附件, wget 别名）。"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.commands.basic import _resolve_path
from ret2console.output import console, render_markdown
from ret2console.state import App
from ret2console.vfs import VfsError


async def cmd_cat(app: App, args: list[str]) -> int:
    if not args:
        # 无参：若在题目上下文内，等价 challenge show（SPEC §9 彩蛋语义）
        if app.ctx.challenge_id is not None:
            from ret2console.commands.challenge import cmd_challenge_show
            return await cmd_challenge_show(app, [])
        console.print("[red]用法: cat <虚拟文件路径>[/red]")
        return 1
    path = _resolve_path(app, args[0])
    try:
        text = await app.vfs.read(path)
    except VfsError as e:
        console.print(f"[red]✗ cat: {e}[/red]")
        return 1
    console.print(render_markdown(text))
    return 0


async def cmd_download(app: App, args: list[str]) -> int:
    if not args:
        console.print("[red]用法: download [题目id] <文件名>[/red]")
        return 1
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    fname = args[0]
    if len(args) >= 2 and args[0].isdigit():
        cid, fname = int(args[0]), args[1]
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文：cd /contests/<比赛>/challenges/<题目>[/yellow]")
        return 1
    safe_name = Path(fname).name
    try:
        items = await app.api.get_json(f"/game/{gid}/challenge/{cid}/file")
        matches = [it for it in items if str(it.get("file")) == safe_name]
        if not matches:
            available = "、".join(str(it.get("file")) for it in items) or "（无附件）"
            console.print(f"[red]✗ 附件不存在: {safe_name}（可用: {available}）[/red]")
            return 1
        item = matches[0]
        resp = await app.api._request(
            "GET", f"/game/{gid}/challenge/{cid}/file",
            params={"folder": item.get("folder"), "file": safe_name},
        )
    except ApiError as e:
        console.print(f"[red]✗ 下载失败: {e.message}[/red]")
        return 1
    content = resp.content
    target = Path.cwd() / safe_name
    if target.exists():
        stem, suffix = safe_name.rsplit(".", 1) if "." in safe_name else (safe_name, "")
        n = 1
        while target.exists():
            target = Path.cwd() / (f"{stem} ({n}){'.' + suffix if suffix else ''}")
            n += 1
    target.write_bytes(content)
    console.print(f"[green]已下载: {target}[/green]（{len(content)} 字节）")
    return 0


def register_files() -> None:
    register(Command("cat", cmd_cat, usage="cat <文件路径>", description="读取虚拟文件（题目目录内无参 = 显示题面）", category="文件系统"))
    register(Command("download", cmd_download, usage="download [题目id] <文件名>", description="下载附件到当前目录（zip 等压缩包请用此命令）", category="附件", aliases=("wget",)))
