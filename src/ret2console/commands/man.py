"""man 手册命令：man [命令] [子命令]（含子命令说明表）。"""

from __future__ import annotations

from ret2console.commands import Command, REGISTRY, register
from ret2console.output import console
from ret2console.repl import _SUBCOMMANDS
from ret2console.state import App


_SUB_HELP: dict[str, str] = {
    "auth login": "登录（PoW 验证码自动求解）",
    "auth logout": "退出登录，回到 guest 状态",
    "auth register": "注册新账号",
    "auth status": "查看登录状态",
    "auth endpoint": "查看或切换 API 平台地址（reset 恢复默认）",
    "auth profile": "多 profile 管理（list/create/use/delete，凭证与 endpoint 独立）",
    "auth show": "查看账号资料（昵称/邮箱/头像/机构/简介）",
    "auth edit": "修改简介与头像（--description/--file/--avatar/--remove-avatar）",
    "auth code": "生成临时身份验证码（5 分钟有效）",
    "auth ping": "验证会话有效性并显示延迟",
    "contest list": "列出比赛（--page/--page-size/--type training|game）",
    "contest show": "显示比赛详情（--intro/--rules/--cover）",
    "contest use": "切换当前比赛上下文（支持名称/唯一前缀）",
    "contest doc": "查看比赛文档（readme / training / rules）",
    "contest notice": "查看比赛通知",
    "challenge list": "列出当前比赛的题目",
    "challenge show": "显示题面",
    "challenge hint": "查看可用提示",
    "challenge unlock": "解锁指定提示",
    "challenge start": "启动靶机实例",
    "challenge instance": "查看实例状态与端口",
    "challenge connect": "经 wsrx 将靶机端口映射到本地",
    "challenge disconnect": "关闭本地隧道",
    "challenge stop": "停止实例",
    "challenge renew": "重建/续期实例",
    "team create": "创建队伍（与当前比赛绑定）",
    "team join": "使用邀请码加入队伍",
    "team modify": "修改队伍信息",
    "team info": "查看我的队伍信息",
    "team rank": "查看队伍排行",
    "team member": "查看成员列表",
    "team solve": "查看已解题列表",
    "team leave": "离开队伍",
    "admin bulletin": "公告管理（list/show/create/update/delete）",
    "admin user": "用户管理（list/show/update/delete/ip）",
    "admin game": "比赛管理（list/show/create/update/delete/token/admins/submissions/audit/stats）",
    "admin challenge": "题目管理（list/show/create/update/delete/publish/unpublish/answer/history/submissions/instances/attach）",
    "admin platform": "平台管理（stats/logs/config）",
    "admin cluster": "集群管理（nodes/config/calmdown）",
}


async def cmd_man(app: App, args: list[str]) -> int:
    if not args:
        from ret2console.commands.basic import cmd_help
        await cmd_help(app, [])
        console.print("\n输入 [bold]man <命令>[/bold] 查看手册，[bold]man <命令> <子命令>[/bold] 查看子命令说明。")
        return 0
    cmd = REGISTRY.get(args[0])
    if cmd is None:
        console.print(f"[red]✗ 没有手册页: {args[0]}（help 查看命令列表）[/red]")
        return 1
    sub = args[1] if len(args) > 1 else None
    if sub is not None and sub not in _SUBCOMMANDS.get(cmd.name, []):
        console.print(f"[red]✗ 未知子命令: {cmd.name} {sub}（man {cmd.name} 查看子命令列表）[/red]")
        return 1
    _print_page(cmd, sub)
    return 0


def _print_page(cmd: Command, sub: str | None) -> None:
    console.print(f"[bold]{cmd.name}[/bold](1)  —  {cmd.description}")
    console.print()
    if cmd.usage:
        console.print("[bold]用法[/bold]")
        console.print(f"    {cmd.usage}")
        console.print()
    subs = _SUBCOMMANDS.get(cmd.name, [])
    if subs:
        console.print("[bold]子命令[/bold]")
        for s in subs:
            desc = _SUB_HELP.get(f"{cmd.name} {s}", "")
            console.print(f"    [bold]{s:<14}[/bold]{desc}")
        console.print()
    if sub:
        desc = _SUB_HELP.get(f"{cmd.name} {sub}")
        if desc:
            console.print(f"[bold]{sub} 说明[/bold]")
            console.print(f"    {desc}")
            console.print()
    if cmd.aliases:
        console.print(f"[bold]别名[/bold]")
        console.print(f"    {', '.join(cmd.aliases)}")
        console.print()
    console.print(f"[bold]分类[/bold]")
    console.print(f"    {cmd.category}")


def register_man() -> None:
    register(Command(
        "man", cmd_man, usage="man [命令] [子命令]",
        description="查看命令手册（man auth login 查看子命令说明）",
        category="系统", aliases=("manual",),
    ))
