"""认证命令：auth login / auth logout / auth register / auth status。"""

from __future__ import annotations

import re

from ret2console.api.client import ApiError
from ret2console.commands import Command, register
from ret2console.config import DEFAULT_API_BASE
from ret2console.interact import ask
from ret2console.output import console
from ret2console.state import App


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


def register_auth() -> None:
    from ret2console.commands import REGISTRY
    if "auth" in REGISTRY:
        return
    register(
        Command(
            "auth", _auth_dispatch, usage="auth <login|logout|register|status|endpoint> ...",
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
    }
    handler = table.get(sub)
    if handler is None:
        console.print(f"[red]✗ 未知 auth 子命令: {sub}（可用: login/logout/register/status/endpoint）[/red]")
        return 1
    return await handler(app, args[1:])
