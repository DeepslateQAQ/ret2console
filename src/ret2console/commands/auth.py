"""认证命令：auth login / logout / register / status / endpoint / profile。"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ret2console.output import make_table

from ret2console import service
from ret2console.api.client import ApiError
from ret2console.commands import Command, register, report_service_error
from ret2console.config import (
    DEFAULT_API_BASE,
    DEFAULT_PROFILE,
    list_profiles,
    profile_config_path,
    profile_dir,
    profile_exists,
    profile_session_path,
    valid_profile_name,
)
from ret2console.interact import ask, confirm
from ret2console.output import console, format_ts, is_tty, render_markdown
from ret2console.service import ServiceError
from ret2console.state import App
from ret2console.termimg import show_image


async def cmd_auth_login(app: App, args: list[str]) -> int:
    if app.session.logged_in:
        console.print("[yellow]! 已经登录，先 auth logout 再登录其他账号[/yellow]")
        return 1
    try:
        account = args[0] if args else await ask("账号")
        password = args[1] if len(args) > 1 else await ask("密码", password=True)
    except (KeyboardInterrupt, EOFError):
        console.print("\n已取消")
        return 1
    console.print("[dim]正在求解 PoW 验证码...[/dim]")
    try:
        result = await app.api.login(account, password)
        token = result.get("token") if result else None
        if token:
            app.set_token(token)
    except ApiError as e:
        console.print(f"[red]✗ 登录失败: {e.message}[/red]")
        return 1
    # 拉取 profile 补充昵称与权限
    try:
        profile = await app.api.profile()
        app.session.account = profile.get("account") or account
        app.session.nickname = profile.get("nickname") or profile.get("account") or account
        perms = profile.get("permissions")
        app.session.permissions = perms if isinstance(perms, list) else []
    except ApiError:
        app.session.account = account
        app.session.nickname = account
        app.session.permissions = []
    app.session.save()
    console.print(f"[bold green]✓[/bold green] 登录成功，欢迎 [bold]{app.session.display_name}[/bold]")
    # 登录即检测：已加入且进行中的比赛 → 询问快速切换
    from ret2console.contest_switch import offer_switch
    try:
        await offer_switch(app)
    except Exception:
        pass
    return 0


async def cmd_auth_logout(app: App, args: list[str]) -> int:
    if app.session.token:
        try:
            await app.api.logout()
        except ApiError as e:
            console.print(f"[yellow]! 登出请求未完成: {e.message}[/yellow]")
    app.api.token = None
    app.session.clear()
    # guest 不再持有任何比赛上下文，虚拟路径回到根目录（SPEC §7）
    app.ctx.set_path("/", None, None)
    console.print("[bold yellow]![/bold yellow] 已登出，回到 guest 状态")
    return 0


async def cmd_auth_register(app: App, args: list[str]) -> int:
    if app.session.logged_in:
        console.print("[yellow]! 请先 auth logout 再注册新账号[/yellow]")
        return 1
    try:
        account = args[0] if args else await ask("账号")
        nickname = args[1] if len(args) > 1 else await ask("昵称")
        email = args[2] if len(args) > 2 else await ask("邮箱")
        password = args[3] if len(args) > 3 else await ask("密码", password=True)
        if len(args) < 4:
            again = await ask("确认密码", password=True)
            if again != password:
                console.print("[red]✗ 两次密码不一致[/red]")
                return 1
    except (KeyboardInterrupt, EOFError):
        console.print("\n已取消")
        return 1
    console.print("[dim]正在求解 PoW 验证码...[/dim]")
    try:
        await app.api.register(account, nickname, email, password)
    except ApiError as e:
        console.print(f"[red]✗ 注册失败: {e.message}[/red]")
        return 1
    console.print("[bold green]✓[/bold green] 注册成功，请 auth login")
    return 0


async def cmd_auth_status(app: App, args: list[str]) -> int:
    from ret2console.commands.basic import cmd_status
    return await cmd_status(app, args)


async def cmd_auth_endpoint(app: App, args: list[str]) -> int:
    """切换 API endpoint：无参显示当前地址；带参切换并清除旧登录态。"""
    if not args or args[0] in ("show", "-h", "--help"):
        console.print(f"当前 API: [bold]{app.config.api_base}[/bold]"
                      + ("（[yellow]insecure[/yellow]）" if app.config.insecure else ""))
        return 0
    if args[0] in ("reset", "default"):
        url, insecure = DEFAULT_API_BASE, False
    else:
        url = args[0].rstrip("/")
        if not re.match(r"^https?://", url):
            console.print("[red]✗ 无效的 API 地址（需以 http:// 或 https:// 开头）[/red]")
            return 1
        rest = args[1:]
        insecure = "--insecure" in rest or "-k" in rest
        if "--no-insecure" in rest:
            insecure = False
    app.config.api_base = url
    app.config.insecure = insecure
    app.config.save()
    app.api.configure(base_url=url, token=None, insecure=insecure)
    app.api.reset_client()
    app.session.clear()
    console.print(f"[green]✓[/green] API 已切换: [bold]{url}[/bold]"
                  + ("（[yellow]insecure[/yellow]）" if insecure else ""))
    console.print("[yellow]! 登录态已清除，请重新 auth login[/yellow]")
    return 0


def _profile_login_state(name: str) -> str:
    """profile 的登录态摘要（用于列表展示）。"""
    path = profile_session_path(name)
    if not path.exists():
        return "[dim]未登录[/dim]"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("token"):
            return f"{data.get('nickname') or data.get('account') or '已登录'}"
        return "[dim]未登录[/dim]"
    except (json.JSONDecodeError, OSError):
        return "[dim]未登录[/dim]"


async def cmd_auth_profile(app: App, args: list[str]) -> int:
    """多 profile 管理：每个 profile 独立保存凭证与 API endpoint。"""
    sub = args[0] if args else "list"
    if sub in ("list", "ls"):
        table = make_table()
        table.add_column("profile", style="bold")
        table.add_column("endpoint")
        table.add_column("登录", style="dim")
        names = [app.profile_name] + [n for n in list_profiles() if n != app.profile_name]
        for name in names:
            ep = profile_config_path(name)
            try:
                data = json.loads(ep.read_text(encoding="utf-8"))
                endpoint = data.get("api_base") or DEFAULT_API_BASE
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                endpoint = DEFAULT_API_BASE
            marker = "* " if name == app.profile_name else "  "
            table.add_row(f"{marker}{name}", endpoint, _profile_login_state(name))
        console.print(table)
        console.print("[dim]* 当前 profile；auth profile use <name> 切换，每个 profile 独立凭证与 endpoint[/dim]")
        return 0
    if sub in ("create", "new"):
        if len(args) < 2:
            console.print("[red]用法: auth profile create <名称>[/red]")
            return 1
        name = args[1]
        if not valid_profile_name(name):
            console.print("[red]✗ 无效的 profile 名称（字母/数字开头，仅含 [A-Za-z0-9._-]，≤32 字符）[/red]")
            return 1
        if profile_exists(name):
            console.print(f"[yellow]! profile 「{name}」已存在[/yellow]")
            return 1
        profile_dir(name).mkdir(parents=True, exist_ok=True)
        app.switch_profile(name)
        console.print(f"[green]✓[/green] 已创建并切换到 profile [bold]{name}[/bold]（endpoint 为默认值，请 auth login）")
        return 0
    if sub in ("use", "switch"):
        if len(args) < 2:
            console.print("[red]用法: auth profile use <名称>[/red]")
            return 1
        name = args[1]
        if not profile_exists(name):
            console.print(f"[red]✗ profile 「{name}」不存在（auth profile create <名称>）[/red]")
            return 1
        if name == app.profile_name:
            console.print(f"[dim]已在 profile 「{name}」[/dim]")
            return 0
        was_logged_in = app.session.logged_in
        app.switch_profile(name)
        if app.session.logged_in:
            console.print(f"[green]✓[/green] 已切换到 profile [bold]{name}[/bold]，欢迎回来 [bold]{app.session.display_name}[/bold]")
        else:
            console.print(f"[green]✓[/green] 已切换到 profile [bold]{name}[/bold]（guest，auth login 登录）")
        if was_logged_in or app.session.logged_in:
            from ret2console.contest_switch import offer_switch
            try:
                await offer_switch(app)
            except Exception:
                pass
        return 0
    if sub == "delete":
        if len(args) < 2:
            console.print("[red]用法: auth profile delete <名称>[/red]")
            return 1
        name = args[1]
        if not profile_exists(name):
            console.print(f"[red]✗ profile 「{name}」不存在[/red]")
            return 1
        if name == app.profile_name:
            console.print(f"[red]✗ 不能删除当前 profile，请先 auth profile use <其他>[/red]")
            return 1
        remaining = [n for n in list_profiles() if n != name]
        if not remaining:
            console.print("[red]✗ 至少需要保留一个 profile（请先 auth profile create <名称>）[/red]")
            return 1
        if profile_dir(name).exists():
            shutil.rmtree(profile_dir(name))
        console.print(f"[green]✓[/green] 已删除 profile [bold]{name}[/bold]")
        return 0
    if sub == "status":
        console.print(f"当前 profile: [bold]{app.profile_name}[/bold]")
        console.print(f"endpoint: {app.config.api_base}")
        if app.session.logged_in:
            console.print(f"登录: {app.session.display_name}")
        else:
            console.print("[dim]登录: 未登录（guest）[/dim]")
        return 0
    console.print(f"[red]✗ 未知 auth profile 子命令: {sub}（可用: list/create/use/delete/status）[/red]")
    return 1


def _render_account_table(profile: dict) -> None:
    table = make_table(show_header=False)
    table.add_column("字段", style="dim", width=10)
    table.add_column("值")
    table.add_row("账号", str(profile.get("account") or "—"))
    table.add_row("昵称", str(profile.get("nickname") or "—"))
    table.add_row("邮箱", str(profile.get("email") or "—"))
    table.add_row("头像", str(profile.get("avatar") or "—"))
    table.add_row(
        "机构 ID",
        str(profile.get("institute_id")) if profile.get("institute_id") is not None else "—",
    )
    registered = profile.get("registered_at")
    table.add_row("注册时间", format_ts(registered) if registered else "—")
    description = profile.get("description") or ""
    table.add_row("简介", description.splitlines()[0] if description else "—")
    console.print(table)


async def cmd_auth_show(app: App, args: list[str]) -> int:
    """显示账号资料（头像在支持终端图片时附带渲染）。"""
    try:
        profile = await service.get_account(app)
    except ServiceError as e:
        return report_service_error(e)
    _render_account_table(profile)
    description = profile.get("description") or ""
    if description:
        console.print()
        console.print(render_markdown(description))
    avatar = profile.get("avatar")
    if avatar and app.graphics:
        try:
            data = await app.api.download_bytes("/media", params={"hash": avatar})
            if data:
                show_image(data, app.graphics, console.width)
        except Exception:
            pass  # 头像渲染失败静默跳过（保留 hash 文本）
    return 0


async def _edit_description(app: App) -> str | None:
    """TTY 无参：用 $VISUAL/$EDITOR 编辑当前简介，返回新内容（取消/失败返回 None）。"""
    try:
        profile = await service.get_account(app)
    except ServiceError as e:
        report_service_error(e)
        return None
    fd, path = tempfile.mkstemp(suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(profile.get("description") or "")
        editor = (
            os.environ.get("VISUAL")
            or os.environ.get("EDITOR")
            or ("notepad" if sys.platform == "win32" else "vi")
        )
        code = subprocess.run([*shlex.split(editor), path]).returncode
        if code != 0:
            console.print(f"[red]✗ 编辑器退出码 {code}[/red]")
            return None
        text = Path(path).read_text(encoding="utf-8")
    finally:
        Path(path).unlink(missing_ok=True)
    console.print(render_markdown(text) if text else "[dim]（简介为空）[/dim]")
    return text


def _parse_edit_args(args: list[str]) -> tuple[dict, str | None]:
    options: dict = {
        "description": None, "file": None, "avatar": None,
        "remove_avatar": False, "yes": False,
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--description", "--file", "--avatar"):
            if i + 1 >= len(args):
                return options, f"{arg} 需要参数"
            i += 1
            options[arg.lstrip("-")] = args[i]
        elif arg.startswith("--description="):
            options["description"] = arg.split("=", 1)[1]
        elif arg.startswith("--file="):
            options["file"] = arg.split("=", 1)[1]
        elif arg.startswith("--avatar="):
            options["avatar"] = arg.split("=", 1)[1]
        elif arg in ("--remove-avatar",):
            options["remove_avatar"] = True
        elif arg in ("--yes", "-y"):
            options["yes"] = True
        else:
            return options, f"未知参数: {arg}"
        i += 1
    return options, None


async def cmd_auth_edit(app: App, args: list[str]) -> int:
    """修改资料：简介（--description/--file/编辑器）与头像（--avatar/--remove-avatar）。"""
    options, error = _parse_edit_args(args)
    if error is not None:
        console.print(f"[red]✗ {error}[/red]")
        return 1
    if options["description"] is not None and options["file"] is not None:
        console.print("[red]✗ --description 与 --file 不能同时使用[/red]")
        return 1
    if options["avatar"] is not None and options["remove_avatar"]:
        console.print("[red]✗ --avatar 与 --remove-avatar 不能同时使用[/red]")
        return 1

    description = options["description"]
    if options["file"] is not None:
        if options["file"] == "-":
            description = sys.stdin.read()
        else:
            try:
                description = Path(options["file"]).read_text(encoding="utf-8")
            except OSError as e:
                console.print(f"[red]✗ 读取文件失败: {e}[/red]")
                return 1

    explicit = any((
        options["description"] is not None, options["file"] is not None,
        options["avatar"] is not None, options["remove_avatar"],
    ))
    if not explicit:
        if not is_tty():
            console.print(
                "[red]✗ auth edit 需要 --description/--file 或 --avatar；非交互模式还需 --yes[/red]"
            )
            return 1
        edited = await _edit_description(app)
        if edited is None:
            return 1
        description = edited
        explicit = True

    if not is_tty() and not options["yes"]:
        console.print("[red]✗ 非交互模式需要 --yes[/red]")
        return 1
    if is_tty() and not options["yes"]:
        try:
            ok = await confirm("提交这些资料修改？")
        except (KeyboardInterrupt, EOFError):
            console.print("\n已取消")
            return 1
        if not ok:
            console.print("[yellow]! 已取消[/yellow]")
            return 0
    try:
        await service.update_account(
            app,
            description=description,
            avatar_path=options["avatar"],
            remove_avatar=options["remove_avatar"],
        )
    except ServiceError as e:
        return report_service_error(e)
    console.print("[green]✓[/green] 资料已更新")
    return 0


async def cmd_auth_code(app: App, args: list[str]) -> int:
    """生成临时身份验证码（5 分钟有效，任何持有者可访问你的身份）。"""
    yes = "--yes" in args or "-y" in args
    if not is_tty() and not yes:
        console.print("[red]✗ 非交互模式需要 --yes[/red]")
        return 1
    if is_tty() and not yes:
        try:
            ok = await confirm("生成临时身份验证码？任何持有者可在 5 分钟内访问你的身份。")
        except (KeyboardInterrupt, EOFError):
            console.print("\n已取消")
            return 1
        if not ok:
            console.print("[yellow]! 已取消[/yellow]")
            return 0
    try:
        data = await service.make_code(app)
    except ServiceError as e:
        return report_service_error(e)
    table = make_table(show_header=False)
    table.add_column("字段", style="dim", width=10)
    table.add_column("值")
    table.add_row("Code", data.get("code") or "—")
    expires = data.get("expires_at")
    table.add_row("Expires", format_ts(expires) if expires else "—")
    console.print(table)
    return 0


async def cmd_auth_ping(app: App, args: list[str]) -> int:
    """验证会话是否仍有效并输出往返延迟。"""
    try:
        result = await service.ping(app)
    except ServiceError as e:
        return report_service_error(e)
    console.print("[bold green]✓[/bold green] 会话有效")
    console.print(f"延迟: {result['latency_ms']} ms")
    return 0


def register_auth() -> None:
    from ret2console.commands import REGISTRY
    if "auth" in REGISTRY:
        return
    register(
        Command(
            "auth", _auth_dispatch,
            usage="auth <login|logout|register|status|endpoint|profile|show|edit|code|ping> ...",
            description="认证管理（PoW 验证码自动求解）", category="认证",
        )
    )


async def _auth_dispatch(app: App, args: list[str]) -> int:
    sub = args[0] if args else "status"
    table = {
        "login": cmd_auth_login,
        "logout": cmd_auth_logout,
        "register": cmd_auth_register,
        "status": cmd_auth_status,
        "endpoint": cmd_auth_endpoint,
        "profile": cmd_auth_profile,
        "show": cmd_auth_show,
        "edit": cmd_auth_edit,
        "code": cmd_auth_code,
        "ping": cmd_auth_ping,
    }
    handler = table.get(sub)
    if handler is None:
        console.print(
            f"[red]✗ 未知 auth 子命令: {sub}"
            "（可用: login/logout/register/status/endpoint/profile/show/edit/code/ping）[/red]"
        )
        return 1
    return await handler(app, args[1:])
