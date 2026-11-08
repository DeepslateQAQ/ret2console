"""MCP 工具层：33 个选手侧工具（schema / annotations / 上下文解析，SPEC §19）。

不含 admin、autoplay、子 shell、账号资料修改与临时身份码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ret2console import service
from ret2console.service import ServiceError
from ret2console.state import App

# ---- 注解 ----

READONLY = {"readOnlyHint": True, "openWorldHint": True}
CONTEXT = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True}
WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True}
DESTRUCTIVE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}

INSTRUCTIONS = (
    "ret2console MCP server. Use r2c_context to inspect the active profile, contest, and challenge. "
    "Contest/challenge tools default to the current context; pass game/challenge to override. "
    "Call r2c_use_contest or r2c_use_challenge to switch context. "
    "Flag submission is asynchronous: r2c_challenge_submit polls until the checker returns or reports pending. "
    "A logged-in profile is required; run r2c auth login first."
)


@dataclass(frozen=True)
class Tool:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[App, dict[str, Any]], Awaitable[Any]]
    annotations: dict[str, Any] = field(default_factory=dict)


# ---- schema 构造 ----


def _str(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _int(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


def _int_arg(args: dict[str, Any], key: str, default: int) -> int:
    value = args.get(key)
    return default if value is None else int(value)


_GAME = _str("比赛 id 或名称（缺省 = 当前比赛上下文）")
_CHALLENGE = _str("题目 id 或名称（缺省 = 当前题目上下文）")
_TEAM_ID = _int("队伍 id（缺省 = 自己所在队伍）")

# ---- 上下文解析 ----


async def _context(
    app: App, args: dict[str, Any], *, require_challenge: bool,
) -> tuple[int, int | None]:
    """解析比赛/题目：缺省取上下文，显式 game 需与当前上下文一致或同时给 challenge。"""
    raw_game = args.get("game")
    raw_challenge = args.get("challenge")
    if raw_game is None:
        gid = app.ctx.game_id
        if gid is None:
            raise ServiceError("未指定比赛（先 r2c_use_contest 或传 game）", code="no_game")
    else:
        gid = await service.resolve_game(app, raw_game)
    same_game = app.ctx.game_id is not None and int(gid) == int(app.ctx.game_id)
    if require_challenge and raw_game is not None and raw_challenge is None and not same_game:
        raise ServiceError("指定 game 时需同时指定 challenge", code="invalid_context")
    if raw_challenge is not None:
        cid = await service.resolve_challenge(app, gid, raw_challenge)
    else:
        cid = app.ctx.challenge_id if same_game else None
        if cid is None and require_challenge:
            raise ServiceError(
                "未指定题目（先 r2c_use_challenge 或传 challenge）", code="no_challenge",
            )
    return int(gid), cid


async def _game_only(app: App, args: dict[str, Any]) -> int:
    raw = args.get("game")
    if raw is None:
        if app.ctx.game_id is None:
            raise ServiceError("未指定比赛（先 r2c_use_contest 或传 game）", code="no_game")
        return app.ctx.game_id
    return await service.resolve_game(app, raw)


# ---- 工具实现 ----


async def _context_tool(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.get_status(app)


async def _use_contest(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await service.resolve_game(app, args["game"])
    await service.get_contest(app, gid)  # 校验存在
    app.ctx.set_game(gid)
    return await service.get_status(app)


async def _use_challenge(app: App, args: dict[str, Any]) -> dict[str, Any]:
    raw_game = args.get("game")
    if raw_game is None:
        if app.ctx.game_id is None:
            raise ServiceError("未指定比赛（先 r2c_use_contest 或传 game）", code="no_game")
        gid = app.ctx.game_id
    else:
        gid = await service.resolve_game(app, raw_game)
    cid = await service.resolve_challenge(app, gid, args["challenge"])
    await service.get_challenge(app, gid, cid)  # 校验存在
    app.ctx.set_challenge(gid, cid)
    return await service.get_status(app)


async def _ls(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.ls(app, args.get("path"))


async def _read(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.read(app, args["path"])


async def _status(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.get_status(app)


async def _account(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.get_account(app)


async def _ping(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.ping(app)


async def _contest_list(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.list_contests(
        app,
        page=_int_arg(args, "page", 1),
        page_size=_int_arg(args, "page_size", 50),
        host_type=args.get("host_type"),
    )


async def _contest_show(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.get_contest(app, args.get("game"))


async def _contest_doc(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"doc": args["doc"], "content": await service.get_contest_doc(app, gid, args["doc"])}


async def _contest_notice(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"items": await service.get_contest_notices(app, gid)}


async def _scoreboard(app: App, args: dict[str, Any]) -> dict[str, Any]:
    return await service.get_scoreboard(
        app,
        args.get("game"),
        page=_int_arg(args, "page", 1),
        page_size=_int_arg(args, "page_size", 20),
    )


async def _challenge_list(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"items": await service.list_challenges(app, gid)}


async def _challenge_show(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.get_challenge(app, gid, int(cid))


async def _challenge_hints(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return {"items": await service.list_hints(app, gid, int(cid))}


async def _challenge_unlock_hint(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.unlock_hint(app, gid, int(cid), int(args["hint_id"]))


async def _challenge_files(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return {"items": await service.list_files(app, gid, int(cid))}


async def _challenge_download(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.download_files(
        app, gid, int(cid),
        file=args.get("file"),
        output_dir=args.get("output_dir"),
    )


async def _challenge_submit(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.submit_flag(app, gid, int(cid), str(args["flag"]))


async def _instance_status(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=False)
    return {"items": await service.list_instances(app, gid, cid)}


async def _instance_start(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.start_instance(app, gid, int(cid))


async def _instance_stop(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.stop_instance(app, gid, int(cid))


async def _instance_renew(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid, cid = await _context(app, args, require_challenge=True)
    return await service.renew_instance(app, gid, int(cid))


async def _team_info(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"team": await service.team_info(app, gid)}


async def _team_rank(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return await service.team_rank(app, gid, args.get("team_id"))


async def _team_members(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"items": await service.team_members(app, gid, args.get("team_id"))}


async def _team_solves(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"items": await service.team_solves(app, gid, args.get("team_id"))}


async def _team_create(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return await service.team_create(app, gid, str(args["name"]), args.get("tag"))


async def _team_join(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return await service.team_join(app, gid, str(args["token"]))


async def _team_update(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    tag = args["tag"] if "tag" in args else service._KEEP
    return await service.team_update(app, gid, str(args["name"]), tag)


async def _team_leave(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return await service.team_leave(app, gid)


async def _submission_list(app: App, args: dict[str, Any]) -> dict[str, Any]:
    gid = await _game_only(app, args)
    return {"items": await service.list_submissions(app, gid)}


# ---- 工具表（按名称排序） ----

TOOLS: list[Tool] = [
    Tool(
        "r2c_account_show",
        "账号资料",
        "读取当前登录账号的资料（昵称、邮箱、头像 hash、权限、简介），不含 token。",
        _schema({}),
        _account,
        READONLY,
    ),
    Tool(
        "r2c_auth_status",
        "登录状态",
        "查看 profile、endpoint、登录态与当前比赛/题目上下文，不联网。",
        _schema({}),
        _status,
        READONLY,
    ),
    Tool(
        "r2c_challenge_download",
        "下载附件",
        "下载题目附件到本地。省略 file 时下载全部附件到 <output_dir 或配置目录>/<题目id>-<题目名>/，"
        "保留后端 folder 结构；只给 file 时按文件名匹配（同名附件可用 folder/file 消歧）。",
        _schema(
            {
                "file": _str("附件名，或 folder/file；省略 = 下载全部"),
                "output_dir": _str("输出目录（批量时为父目录），省略 = 配置的下载目录"),
                "challenge": _CHALLENGE,
                "game": _GAME,
            },
        ),
        _challenge_download,
        WRITE,
    ),
    Tool(
        "r2c_challenge_files",
        "附件列表",
        "列出题目附件的 folder/file，下载前确认文件名。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _challenge_files,
        READONLY,
    ),
    Tool(
        "r2c_challenge_hints",
        "提示列表",
        "列出题目提示及解锁状态，未解锁的提示需要 r2c_challenge_unlock_hint。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _challenge_hints,
        READONLY,
    ),
    Tool(
        "r2c_challenge_list",
        "题目列表",
        "列出比赛的全部题目（id/标题/分类/分值）。",
        _schema({"game": _GAME}),
        _challenge_list,
        READONLY,
    ),
    Tool(
        "r2c_challenge_show",
        "题面",
        "读取题面 Markdown 与题目元数据。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _challenge_show,
        READONLY,
    ),
    Tool(
        "r2c_challenge_submit",
        "提交 flag",
        "提交 flag 并轮询评测队列（最多约 8 秒）；pending=true 表示仍未出结果。",
        _schema(
            {"flag": _str("待提交的 flag"), "challenge": _CHALLENGE, "game": _GAME},
            ["flag"],
        ),
        _challenge_submit,
        WRITE,
    ),
    Tool(
        "r2c_challenge_unlock_hint",
        "解锁提示",
        "消耗提示额度解锁指定提示（写操作）。",
        _schema(
            {"hint_id": _int("提示 id"), "challenge": _CHALLENGE, "game": _GAME},
            ["hint_id"],
        ),
        _challenge_unlock_hint,
        WRITE,
    ),
    Tool(
        "r2c_context",
        "当前上下文",
        "查看 profile、endpoint、登录账号、权限与当前比赛/题目/VFS 路径。",
        _schema({}),
        _context_tool,
        READONLY,
    ),
    Tool(
        "r2c_contest_doc",
        "比赛文档",
        "读取比赛文档：readme / training / rules（参赛前先读 rules）。",
        _schema(
            {
                "doc": {
                    "type": "string",
                    "enum": ["readme", "training", "rules"],
                    "description": "文档类型",
                },
                "game": _GAME,
            },
            ["doc"],
        ),
        _contest_doc,
        READONLY,
    ),
    Tool(
        "r2c_contest_list",
        "比赛列表",
        "分页列出比赛；host_type 过滤 training（练习赛）/ game（正式赛）。",
        _schema(
            {
                "page": _int("页码，从 1 开始"),
                "page_size": _int("每页条数"),
                "host_type": {
                    "type": "string",
                    "enum": ["training", "game"],
                    "description": "比赛类型过滤",
                },
            },
        ),
        _contest_list,
        READONLY,
    ),
    Tool(
        "r2c_contest_notice",
        "比赛通知",
        "列出比赛通知/公告。",
        _schema({"game": _GAME}),
        _contest_notice,
        READONLY,
    ),
    Tool(
        "r2c_contest_show",
        "比赛详情",
        "读取比赛详情（标题、简介、状态、开始/结束时间）。",
        _schema({"game": _GAME}),
        _contest_show,
        READONLY,
    ),
    Tool(
        "r2c_instance_renew",
        "续期实例",
        "续期当前题目实例（无实例时返回 not_running=true，不报错）。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _instance_renew,
        WRITE,
    ),
    Tool(
        "r2c_instance_start",
        "启动实例",
        "启动题目实例；已存在实例时直接返回 already_started=true（不重复创建）。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _instance_start,
        WRITE,
    ),
    Tool(
        "r2c_instance_status",
        "实例状态",
        "列出实例（state/traffic/ports/到期与剩余秒数）；省略 challenge 时列出整个比赛的实例。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _instance_status,
        READONLY,
    ),
    Tool(
        "r2c_instance_stop",
        "停止实例",
        "停止题目实例（破坏性操作，实例内数据会丢失）；无实例时返回 already_stopped=true。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}),
        _instance_stop,
        DESTRUCTIVE,
    ),
    Tool(
        "r2c_ls",
        "虚拟目录",
        "列出虚拟文件系统目录（相对路径基于当前 VFS 路径）。",
        _schema({"path": _str("目录路径，省略 = 当前目录")}),
        _ls,
        READONLY,
    ),
    Tool(
        "r2c_ping",
        "会话检测",
        "请求一次账号接口，验证登录态并返回往返延迟（毫秒）。",
        _schema({}),
        _ping,
        READONLY,
    ),
    Tool(
        "r2c_read",
        "读取虚拟文件",
        "读取虚拟文件（相对路径基于当前 VFS 路径），如 /contests/3/info。",
        _schema({"path": _str("文件路径")}, ["path"]),
        _read,
        READONLY,
    ),
    Tool(
        "r2c_scoreboard",
        "排行榜",
        "分页读取比赛排行榜（min_state=3，按分数降序），返回原始 institute_id。",
        _schema({"game": _GAME, "page": _int("页码"), "page_size": _int("每页条数")}),
        _scoreboard,
        READONLY,
    ),
    Tool(
        "r2c_submission_list",
        "提交记录",
        "列出当前账号在该比赛的提交记录（时间、题目、结果、分数）。",
        _schema({"game": _GAME}),
        _submission_list,
        READONLY,
    ),
    Tool(
        "r2c_team_create",
        "创建队伍",
        "创建队伍并返回邀请 token。MCP 不弹确认；调用前先 r2c_contest_doc 读取 rules。",
        _schema(
            {"name": _str("队伍名"), "tag": _str("队伍 tag（可选）"), "game": _GAME},
            ["name"],
        ),
        _team_create,
        WRITE,
    ),
    Tool(
        "r2c_team_info",
        "我的队伍",
        "读取本人队伍信息（state/score/tag/邀请 token）。",
        _schema({"game": _GAME}),
        _team_info,
        READONLY,
    ),
    Tool(
        "r2c_team_join",
        "加入队伍",
        "用邀请 token 加入队伍。MCP 不弹确认；调用前先 r2c_contest_doc 读取 rules。",
        _schema({"token": _str("邀请 token"), "game": _GAME}, ["token"]),
        _team_join,
        WRITE,
    ),
    Tool(
        "r2c_team_leave",
        "退出队伍",
        "退出当前队伍（破坏性操作，需重新邀请才能加入）。",
        _schema({"game": _GAME}),
        _team_leave,
        DESTRUCTIVE,
    ),
    Tool(
        "r2c_team_members",
        "队伍成员",
        "列出队伍成员。",
        _schema({"team_id": _TEAM_ID, "game": _GAME}),
        _team_members,
        READONLY,
    ),
    Tool(
        "r2c_team_rank",
        "队伍排名",
        "读取队伍排名。",
        _schema({"team_id": _TEAM_ID, "game": _GAME}),
        _team_rank,
        READONLY,
    ),
    Tool(
        "r2c_team_solves",
        "队伍解题",
        "列出队伍已解出/提交的题目。",
        _schema({"team_id": _TEAM_ID, "game": _GAME}),
        _team_solves,
        READONLY,
    ),
    Tool(
        "r2c_team_update",
        "修改队伍",
        "修改本队名称与 tag：省略 tag 保留原值，tag 传 null 清空。",
        _schema(
            {
                "name": _str("新队伍名"),
                "tag": {
                    "type": ["string", "null"],
                    "description": "新 tag；null = 清空",
                },
                "game": _GAME,
            },
            ["name"],
        ),
        _team_update,
        WRITE,
    ),
    Tool(
        "r2c_use_challenge",
        "切换题目",
        "切换当前题目上下文（challenge 可为 id/名称），并返回新的上下文。",
        _schema({"challenge": _CHALLENGE, "game": _GAME}, ["challenge"]),
        _use_challenge,
        CONTEXT,
    ),
    Tool(
        "r2c_use_contest",
        "切换比赛",
        "切换当前比赛上下文（game 可为 id/名称），并返回新的上下文。",
        _schema({"game": _str("比赛 id 或名称")}, ["game"]),
        _use_contest,
        CONTEXT,
    ),
]
