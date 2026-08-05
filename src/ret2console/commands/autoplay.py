"""autoplay 一把梭（SPEC §12）：检测 Agent → 选择 → 警告确认 →
启动实例并建立隧道 → 下载附件生成任务书（含连接信息）→ 启动 Agent TUI。"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.commands.challenge import _get_instances
from ret2console.interact import confirm, select
from ret2console.output import console
from ret2console.state import App
from ret2console.wsrx import wsrx_link

AGENTS = ("codex", "claude", "opencode", "omp", "reasonix", "kimi")
# 不支持 TUI 初始消息的 Agent：打印提示词供选手复制粘贴，确认后裸启动
NO_INITIAL_PROMPT_AGENTS = ("kimi", "reasonix")
WARNING = "警告：使用一把梭功能可能导致您的亲人遭受损失（物理），是否仍要继续？"
INSTANCE_WAIT = 12.0  # 实例构建轮询上限（秒）


def detect_agents() -> list[str]:
    return [a for a in AGENTS if shutil.which(a)]


async def _download_attachments(app: App, gid: int, cid: int, workdir: Path) -> list[Path]:
    """下载题目附件到工作目录，返回本地路径列表。"""
    items = await app.api.get_json(f"/game/{gid}/challenge/{cid}/file")
    items = items if isinstance(items, list) else items.get("files", [])
    saved: list[Path] = []
    for i, f in enumerate(items):
        name = str(f.get("file") or f.get("name") or i)
        safe = Path(name).name
        resp = await app.api._request(
            "GET", f"/game/{gid}/challenge/{cid}/file",
            params={"folder": f.get("folder"), "file": safe},
        )
        target = workdir / safe
        n = 1
        while target.exists():
            stem, suffix = safe.rsplit(".", 1) if "." in safe else (safe, "")
            target = workdir / (f"{stem} ({n}){'.' + suffix if suffix else ''}")
            n += 1
        target.write_bytes(resp.content)
        saved.append(target)
    return saved


async def _ensure_instance(app: App, gid: int, cid: int) -> dict | None:
    """确保当前题目有实例在跑：无实例则启动，并轮询等待就绪（出现 traffic）。"""
    try:
        items = await _get_instances(app, gid, cid)
    except ApiError as e:
        console.print(f"[yellow]! 查询实例失败: {e.message}[/yellow]")
        return None
    inst = next((i for i in items if i.get("traffic")), None)
    if inst is not None:
        return inst
    if not items:
        try:
            await app.api.post_json(f"/game/{gid}/challenge/{cid}/instance")
        except ApiError as e:
            console.print(f"[yellow]! 实例启动失败: {e.message}[/yellow]")
            return None
        console.print("[yellow]➜ 实例启动中，等待就绪…[/yellow]")
    else:
        console.print("[yellow]➜ 实例构建中，等待就绪…[/yellow]")
    deadline = time.monotonic() + INSTANCE_WAIT
    while time.monotonic() < deadline:
        await asyncio.sleep(1.0)
        try:
            items = await _get_instances(app, gid, cid)
        except ApiError:
            continue
        inst = next((i for i in items if i.get("traffic")), None)
        if inst is not None:
            return inst
    console.print("[yellow]! 实例尚未就绪（可能仍在构建），稍后可 challenge connect[/yellow]")
    return None


async def _ensure_tunnel(app: App, gid: int, cid: int, inst: dict) -> list[str]:
    """Retain one in-process libwsrx endpoint per exposed instance port."""
    traffic = str(inst.get("traffic") or "")
    ports = [int(p) for p in (inst.get("ports") or []) if str(p).isdigit()]
    if not traffic or not ports:
        return []
    pool = app.wsrx.pool_instances()
    by_remote = {
        item["remote"]: item["local"]
        for item in pool
        if item.get("remote") and item.get("local")
    }
    locals_: list[str] = []
    for port in ports:
        remote = wsrx_link(app.api.base_url, traffic, port)
        existing = by_remote.get(remote)
        if existing:
            locals_.append(existing)
            continue
        try:
            local = await app.wsrx.add(remote, game_id=gid, challenge_id=cid)
        except Exception as e:
            console.print(f"[yellow]! 隧道建立失败（{remote}）: {e}[/yellow]")
            continue
        locals_.append(local)
    return locals_


def _append_conn(taskbook: Path, conn: list[str], api_base: str) -> None:
    """任务书追加实例连接段落。"""
    lines = ["", "## 实例连接", ""]
    if conn:
        for addr in conn:
            lines.append(f"- 本地地址: {addr}")
        lines.append("")
        lines.append("说明：上述本地地址已通过隧道映射到题目靶机，可直接访问。")
        lines.append(f"- 平台: {api_base}")
    else:
        lines.append("（未建立到靶机的隧道，实例可能仍在构建或未暴露端口）")
    with taskbook.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


async def _build_taskbook(app: App, gid: int, cid: int, workdir: Path, agent: str) -> Path:
    """生成任务书 markdown：题面全文 + 附件绝对路径 + 目标说明。"""
    challenge = await app.api.get_challenge(gid, cid)
    title = challenge.get("title") or f"题目 {cid}"
    content = challenge.get("content") or challenge.get("description") or "（无题面内容）"
    attachments = await _download_attachments(app, gid, cid, workdir)
    lines = [
        f"# 任务书：{title}（#{cid}）",
        "",
        f"你是 {agent}。请尝试解出以下 CTF 题目的 flag（格式如 flag{{...}}），",
        "可以用工作目录中的附件和任意工具。解出后把 flag 告诉用户。",
        "",
        "## 题面",
        "",
        content,
        "",
        "## 附件",
        "",
    ]
    if attachments:
        lines += [f"- {p.resolve()}" for p in attachments]
    else:
        lines.append("（本题无附件）")
    taskbook = workdir / "任务书.md"
    taskbook.write_text("\n".join(lines), encoding="utf-8")
    return taskbook


def _launch_args(agent: str, prompt: str) -> list[str]:
    """Agent 启动方式：codex/claude/omp 支持直接传初始消息（`omp "<prompt>"` 进带初始消息的 TUI）；
    opencode 用 --prompt；kimi（Kimi Code）与 reasonix 不支持 TUI 初始消息，
    由 cmd_autoplay 先提示选手复制提示词，确认后裸启动（reasonix 入口为 `reasonix code`）。"""
    if agent in ("codex", "claude", "omp"):
        return [agent, prompt]
    if agent == "opencode":
        return [agent, "--prompt", prompt]
    if agent == "reasonix":
        return [agent, "code"]
    return [agent]


async def cmd_autoplay(app: App, args: list[str]) -> int:
    gid = app.ctx.game_id
    cid = app.ctx.challenge_id
    if gid is None or cid is None:
        console.print("[yellow]! 请先进入题目上下文：cd /contests/<比赛>/challenges/<题目>[/yellow]")
        return 1
    if not app.session.logged_in:
        console.print("[red]✗ 未登录：下载题面/附件需要认证，请先 auth login[/red]")
        return 1
    agents = detect_agents()
    if not agents:
        console.print("[red]✗ 未检测到可用 Agent（codex / claude / opencode / omp / reasonix / kimi）[/red]")
        return 1
    if len(agents) == 1:
        agent = agents[0]
    else:
        try:
            idx = await select("选择要使用的 Agent", agents)
        except KeyboardInterrupt:
            console.print("\n已取消")
            return 1
        agent = agents[idx]
    try:
        ok = await confirm(WARNING)
    except KeyboardInterrupt:
        console.print("\n已取消")
        return 1
    if not ok:
        return 1
    workdir = Path(app.config.autoplay_dir) / str(cid)
    workdir.mkdir(parents=True, exist_ok=True)
    # 实例启动与任务书生成并行：POST 后靶机在后台构建，附件下载不阻塞
    startup = asyncio.create_task(_ensure_instance(app, gid, cid))
    try:
        taskbook = await _build_taskbook(app, gid, cid, workdir, agent)
    except ApiError as e:
        startup.cancel()
        console.print(f"[red]✗ 任务书生成失败: {e.message}[/red]")
        return 1
    inst = await startup
    conn = await _ensure_tunnel(app, gid, cid, inst) if inst else []
    _append_conn(taskbook, conn, app.config.api_base)
    prompt = taskbook.read_text(encoding="utf-8")
    console.print(f"[green]✓[/green] 任务书已生成: {taskbook}")
    if agent in NO_INITIAL_PROMPT_AGENTS:
        # kimi / reasonix 不支持 TUI 初始消息：打印提示词供复制，确认后启动
        console.print(f"[yellow]! {agent} 不支持 TUI 初始消息：请复制下方提示词，启动后粘贴到首条消息[/yellow]")
        console.print(prompt, markup=False)
        try:
            ok = await confirm("确认已复制提示词，启动该 Agent？")
        except KeyboardInterrupt:
            console.print("\n已取消")
            return 1
        if not ok:
            return 1
    console.print(f"[dim]正在启动 {agent}（退出后返回 ret2console）[/dim]")
    proc = await asyncio.create_subprocess_exec(*_launch_args(agent, prompt))
    await proc.wait()
    return 0


def register_autoplay() -> None:
    register(Command("autoplay", cmd_autoplay, usage="autoplay", description="一把梭：让 Agent 尝试解出当前题目", category="系统"))
