"""文件命令：cat（读取虚拟文件）/ download（下载附件, wget 别名）。"""

from __future__ import annotations

from ret2console import service
from ret2console.commands import Command, register, report_service_error
from ret2console.commands.basic import _resolve_path
from ret2console.output import console, render_markdown
from ret2console.service import ServiceError
from ret2console.state import App
from ret2console.termimg import (
    extract_images,
    resolve_media_url,
    show_image,
)
from ret2console.vfs import VfsError


async def cmd_cat(app: App, args: list[str]) -> int:
    raw = "--raw" in args
    paths = [a for a in args if a != "--raw"]
    if not paths:
        # 无参：若在题目上下文内，等价 challenge show（SPEC §9 彩蛋语义）
        if app.ctx.challenge_id is not None:
            from ret2console.commands.challenge import cmd_challenge_show
            return await cmd_challenge_show(app, [])
        console.print("[red]用法: cat <虚拟文件路径>[/red]")
        return 1
    path = _resolve_path(app, paths[0])
    try:
        text = await app.vfs.read(path)
    except VfsError as e:
        console.print(f"[red]✗ cat: {e}[/red]")
        return 1
    if raw:
        console.print(text)
        return 0
    if app.graphics:
        # Markdown 里的图片：取链接下载并渲染，成功后从文本中移除（失败保留 🌆 占位）
        for alt, url in extract_images(text):
            try:
                resp = await app.api._request("GET", resolve_media_url(url, app.api.base_url))
                ok = show_image(resp.content, app.graphics, console.width) if resp.content else False
            except Exception:
                ok = False
            if not ok:
                continue
            text = text.replace(f"![{alt}]({url})", "", 1)
    console.print(render_markdown(text))
    return 0


async def cmd_download(app: App, args: list[str]) -> int:
    """下载附件：省略文件名 = 全部附件（落到 <下载目录>/<题目id>-<题目名>/）。"""
    output_dir: str | None = None
    rest: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--dir":
            if i + 1 >= len(args):
                console.print("[red]✗ --dir 需要参数[/red]")
                return 1
            i += 1
            output_dir = args[i]
        elif arg.startswith("--dir="):
            output_dir = arg.split("=", 1)[1]
        else:
            rest.append(arg)
        i += 1

    gid = app.ctx.game_id
    if gid is None:
        console.print("[yellow]! 请先进入题目上下文：cd /contests/<比赛>/challenges/<题目>[/yellow]")
        return 1
    ref: int | None = None
    if rest and rest[0].isdigit():
        ref = int(rest[0])
        rest = rest[1:]
    fname = " ".join(rest).strip() or None
    try:
        cid = await service.resolve_challenge(app, gid, ref)
        result = await service.download_files(
            app, gid, cid, file=fname, output_dir=output_dir,
        )
    except ServiceError as e:
        if e.code == "not_found":
            console.print(f"[red]✗ {e.message}[/red]")
            available = (e.data or {}).get("available") or []
            if available:
                console.print("[dim]可用附件:[/dim]")
                for item in available:
                    label = f"{item['folder']}/{item['file']}" if item.get("folder") else item["file"]
                    console.print(f"[dim]  {label}[/dim]")
            elif not available:
                console.print("[dim]（该题无附件，challenge files 查看）[/dim]")
            return 1
        return report_service_error(e)

    files = result.get("files") or []
    if fname is None:
        if not files:
            console.print("[yellow]! 无附件[/yellow]")
            return 0
        console.print(
            f"[green]✓[/green] 已下载 {len(files)} 个附件到 [bold]{result['root']}[/bold]"
        )
        for item in files:
            label = f"{item['folder']}/{item['file']}" if item["folder"] else item["file"]
            console.print(f"[dim]{label}[/dim]")
        return 0
    item = files[0]
    console.print(f"[green]✓ 已下载: {item['path']}[/green]（{item['bytes']} 字节）")
    return 0


def register_files() -> None:
    register(Command("cat", cmd_cat, usage="cat [--raw] <文件路径>", description="读取虚拟文件（--raw 输出原始 Markdown；题目目录内无参 = 显示题面）", category="文件系统"))
    register(Command(
        "download", cmd_download, usage="download [--dir PATH] [题目id] [文件名]",
        description="下载附件到当前目录；不带文件名 = 下载全部附件（保留 folder 结构）",
        category="附件", aliases=("wget",),
    ))
