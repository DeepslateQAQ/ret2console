"""无输出数据层：CLI 命令与 MCP 工具共用（SPEC §18）。

约定：所有函数只返回 JSON 可序列化数据或抛 `ServiceError`，绝不调用
`console.print`；展示层负责把数据渲染成表格/文本。
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Awaitable, TypeVar

from ret2console.api.client import ApiError
from ret2console.state import App
from ret2console.vfs import VfsError, normalize_path

T = TypeVar("T")

_AVATAR_LIMIT = 10 * 1024 * 1024  # 头像上限 10 MiB
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]")
_HOST_TYPES = {"training": 0, "game": 1}
_KEEP = object()


class ServiceError(Exception):
    """数据层错误：携带 MCP 可序列化的错误码与附加数据。"""

    def __init__(
        self, message: str, *, code: str = "error",
        status: int | None = None, data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.data = data


async def _api(coro: Awaitable[T]) -> T:
    """API 调用包装：ApiError → ServiceError（401 统一为 unauthenticated）。"""
    try:
        return await coro
    except ApiError as e:
        code = "unauthenticated" if e.status == 401 else str(e.code or "api_error")
        raise ServiceError(e.message, code=code, status=e.status) from e


def _require_login(app: App) -> None:
    if not app.session.logged_in:
        raise ServiceError("未登录，请先执行 auth login", code="unauthenticated", status=401)


# ---- 名称解析 ----


def _title(item: dict[str, Any]) -> str:
    return str(item.get("title") or item.get("name") or item.get("id") or "")


def _pick(items: list[dict[str, Any]], ref: str, *, label: str) -> int:
    """精确 → 唯一前缀匹配；无匹配 not_found，多匹配 ambiguous（附候选）。"""
    wanted = ref.strip()
    low = wanted.casefold()
    candidates = [
        {"id": int(i["id"]), "name": _title(i)}
        for i in items
        if i.get("id") is not None
    ]
    exact = [c for c in candidates if c["name"].casefold() == low]
    matches = exact or [c for c in candidates if c["name"].casefold().startswith(low)]
    if not matches:
        raise ServiceError(f"找不到{label}: {wanted}", code="not_found", status=404)
    if len(matches) > 1:
        raise ServiceError(
            f"{label}名称不唯一: {wanted}", code="ambiguous", data={"candidates": matches},
        )
    return matches[0]["id"]


async def resolve_game(app: App, ref: str | int | None) -> int:
    """比赛引用解析：上下文默认 / 纯数字 id / 标题（精确或唯一前缀）。"""
    if ref is None or str(ref).strip() == "":
        if app.ctx.game_id is not None:
            return app.ctx.game_id
        raise ServiceError("未指定比赛（先 contest use <比赛>）", code="no_game")
    text = str(ref).strip()
    if text.isdigit():
        return int(text)
    items = await _api(app.api.list_games())
    app.games_cache = {
        int(g["id"]): _title(g) for g in items if isinstance(g, dict) and g.get("id") is not None
    }
    return _pick([g for g in items if isinstance(g, dict)], text, label="比赛")


async def resolve_challenge(app: App, game_id: int, ref: str | int | None) -> int:
    """题目引用解析：上下文默认 / 纯数字 id / 标题（精确或唯一前缀）。"""
    if ref is None or str(ref).strip() == "":
        if app.ctx.challenge_id is not None:
            return app.ctx.challenge_id
        raise ServiceError("未指定题目（先 cd 到题目目录）", code="no_challenge")
    text = str(ref).strip()
    if text.isdigit():
        return int(text)
    items = await _api(app.api.list_challenges(game_id))
    app.challenges_cache[game_id] = {
        int(c["id"]): _title(c) for c in items if isinstance(c, dict) and c.get("id") is not None
    }
    return _pick([c for c in items if isinstance(c, dict)], text, label="题目")


async def resolve_team(app: App, game_id: int, team_id: int | None) -> int:
    """队伍引用解析：显式 id，否则取本人队伍（无队伍 → no_team）。"""
    if team_id is not None:
        return int(team_id)
    team = await team_info(app, game_id)
    if team is None:
        raise ServiceError("尚未加入队伍", code="no_team")
    tid = team.get("id")
    if tid is None:
        raise ServiceError("尚未加入队伍", code="no_team")
    return int(tid)


# ---- 状态与账号 ----


async def get_status(app: App) -> dict[str, Any]:
    return {
        "profile": app.profile_name,
        "api_base": app.config.api_base,
        "logged_in": app.session.logged_in,
        "account": app.session.account,
        "nickname": app.session.nickname,
        "permissions": list(app.session.permissions),
        "game_id": app.ctx.game_id,
        "challenge_id": app.ctx.challenge_id,
        "vfs_path": app.ctx.vfs_path,
    }


async def get_account(app: App) -> dict[str, Any]:
    _require_login(app)
    return await _api(app.api.profile())


async def ping(app: App) -> dict[str, Any]:
    _require_login(app)
    started = time.monotonic()
    await _api(app.api.profile())
    return {"alive": True, "latency_ms": int((time.monotonic() - started) * 1000)}


async def update_account(
    app: App, *, description: str | None, avatar_path: str | None, remove_avatar: bool,
) -> dict[str, Any]:
    """修改资料：仅 description / avatar 两个字段，其余原样回传。"""
    _require_login(app)
    if description is None and avatar_path is None and not remove_avatar:
        raise ServiceError("没有需要提交的修改", code="no_changes")
    profile = await _api(app.api.profile())
    payload: dict[str, Any] = dict(profile)
    if description is not None:
        payload["description"] = description or None
    if remove_avatar:
        payload["avatar"] = None
    elif avatar_path is not None:
        path = Path(avatar_path)
        try:
            data = path.read_bytes()
        except OSError as e:
            raise ServiceError(f"读取头像失败: {e}", code="invalid_avatar") from e
        if len(data) > _AVATAR_LIMIT:
            raise ServiceError("头像文件超过 10 MiB", code="invalid_avatar")
        uploaded = await _api(app.api.upload_media(data, path.name))
        avatar = uploaded.get("hash") or uploaded.get("path")
        if not avatar:
            raise ServiceError("上传头像失败：响应缺少 hash", code="upload_failed")
        payload["avatar"] = avatar
    updated = await _api(app.api.update_profile(payload))
    if not updated:
        updated = await _api(app.api.profile())
    return updated


async def make_code(app: App) -> dict[str, Any]:
    """生成临时身份验证码（5 分钟有效）。"""
    _require_login(app)
    data = await _api(app.api.temporary_code())
    raw = data.get("code")
    if isinstance(raw, int):
        code = f"{raw:06X}"
    elif raw is None:
        code = ""
    else:
        code = str(raw).upper()
    return {
        "code": code,
        "generate_at": data.get("generate_at"),
        "expires_at": data.get("expires_at"),
    }


# ---- 比赛 ----


async def list_contests(
    app: App, *, page: int = 1, page_size: int = 50, host_type: str | None = None,
) -> dict[str, Any]:
    host = _HOST_TYPES.get(host_type) if host_type else None
    items, total = await _api(app.api.list_games_page(page, page_size, host))
    games = [g for g in items if isinstance(g, dict)]
    app.games_cache = {
        int(g["id"]): _title(g) for g in games if g.get("id") is not None
    }
    return {"items": games, "total": total, "page": page, "page_size": page_size}


async def get_contest(app: App, ref: str | int | None) -> dict[str, Any]:
    gid = await resolve_game(app, ref)
    game = await _api(app.api.get_game(gid))
    return game if isinstance(game, dict) else {"id": gid}


async def get_contest_doc(app: App, ref: str | int | None, doc: str) -> str:
    gid = await resolve_game(app, ref)
    return await _api(app.api.get_game_doc(gid, doc))


async def get_contest_notices(app: App, ref: str | int | None) -> list[dict[str, Any]]:
    gid = await resolve_game(app, ref)
    return [n for n in await _api(app.api.get_notifications(gid)) if isinstance(n, dict)]


async def get_scoreboard(
    app: App, ref: str | int | None, *, page: int = 1, page_size: int = 20,
) -> dict[str, Any]:
    gid = await resolve_game(app, ref)
    data = await _api(app.api.get_json(
        f"/game/{gid}/team",
        params={"min_state": 3, "order": "score", "asc": False, "page": page, "page_size": page_size},
    ))
    from ret2console.api.client import split_page
    items, total = split_page(data)
    return {"game_id": gid, "items": [t for t in items if isinstance(t, dict)], "total": total}


# ---- 题目 ----


async def list_challenges(app: App, game_id: int) -> list[dict[str, Any]]:
    items = await _api(app.api.list_challenges(game_id))
    challenges = [c for c in items if isinstance(c, dict)]
    app.challenges_cache[game_id] = {
        int(c["id"]): _title(c) for c in challenges if c.get("id") is not None
    }
    return challenges


async def get_challenge(app: App, game_id: int, challenge_id: int) -> dict[str, Any]:
    data = await _api(app.api.get_challenge(game_id, challenge_id))
    return data if isinstance(data, dict) else {"id": challenge_id}


async def list_hints(app: App, game_id: int, challenge_id: int) -> list[dict[str, Any]]:
    data = await _api(app.api.get_json(f"/game/{game_id}/challenge/{challenge_id}/hint"))
    items = data if isinstance(data, list) else (data or {}).get("hints", [])
    return [h for h in items if isinstance(h, dict)] if isinstance(items, list) else []


async def unlock_hint(app: App, game_id: int, challenge_id: int, hint_id: int) -> dict[str, Any]:
    await _api(app.api.post_json(
        f"/game/{game_id}/challenge/{challenge_id}/hint/unlock", json={"id": int(hint_id)},
    ))
    return {"unlocked": int(hint_id)}


# ---- 附件 ----


async def list_files(app: App, game_id: int, challenge_id: int) -> list[dict[str, Any]]:
    data = await _api(app.api.get_json(f"/game/{game_id}/challenge/{challenge_id}/file"))
    if isinstance(data, dict):
        items = data.get("files", [])
    else:
        items = data
    out: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        out.append({"folder": str(item.get("folder") or ""), "file": str(item.get("file") or "")})
    return out


def _safe_name(value: Any) -> str:
    """取路径末段，剔除目录穿越与空名。"""
    name = Path(str(value)).name
    if name in ("", ".", ".."):
        return "_"
    return name


def _slug(text: str) -> str:
    """题目名 → 目录名：保留 [A-Za-z0-9._-]，其余替换并折叠 '-'，截断 40。"""
    slug = _UNSAFE_RE.sub("-", text)
    slug = re.sub(r"-{2,}", "-", slug)[:40]
    return slug or "challenge"


def _free_path(target: Path) -> Path:
    """目标已存在时追加 ` (1)`、` (2)`…（保留后缀）。"""
    if not target.exists():
        return target
    name = target.name
    stem, suffix = name.rsplit(".", 1) if "." in name else (name, "")
    n = 1
    while True:
        candidate = target.with_name(f"{stem} ({n}){'.' + suffix if suffix else ''}")
        if not candidate.exists():
            return candidate
        n += 1


async def download_files(
    app: App, game_id: int, challenge_id: int, *,
    file: str | None, output_dir: str | None,
) -> dict[str, Any]:
    """下载单个附件或全部附件（批量保留后端 folder 结构）。"""
    items = await list_files(app, game_id, challenge_id)
    path = f"/game/{game_id}/challenge/{challenge_id}/file"

    if file is None:
        if not items:
            root = Path(output_dir or app.config.download_dir) / str(challenge_id)
            return {"root": str(root), "files": []}
        try:
            challenge = await get_challenge(app, game_id, challenge_id)
            title = _title(challenge)
        except ServiceError:
            title = str(challenge_id)
        root = Path(output_dir or app.config.download_dir) / f"{challenge_id}-{_slug(title)}"
        saved: list[dict[str, Any]] = []
        for item in items:
            folder = item["folder"]
            target = root / _safe_name(folder) if folder else root
            target = _free_path(target / _safe_name(item["file"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            data = await _api(app.api.download_bytes(
                path, params={"folder": item["folder"], "file": item["file"]},
            ))
            target.write_bytes(data)
            saved.append({
                "folder": folder, "file": _safe_name(item["file"]),
                "path": str(target), "bytes": len(data),
            })
        return {"root": str(root), "files": saved}

    text = str(file).strip()
    available = [{"folder": it["folder"], "file": it["file"]} for it in items]
    if "/" in text:
        folder_part, _, name_part = text.partition("/")
        wanted_folder = folder_part.strip("/")
        wanted_name = _safe_name(name_part)
        matches = [
            it for it in items
            if _safe_name(it["file"]) == wanted_name
            and it["folder"].strip("/") == wanted_folder
        ]
    else:
        wanted_name = _safe_name(text)
        matches = [it for it in items if _safe_name(it["file"]) == wanted_name]
    if not matches:
        raise ServiceError(
            f"附件不存在: {text}", code="not_found", status=404, data={"available": available},
        )
    if len(matches) > 1:
        raise ServiceError(
            f"附件名不唯一: {text}（可写成 folder/file）", code="ambiguous",
            data={"candidates": [{"folder": m["folder"], "file": m["file"]} for m in matches]},
        )
    item = matches[0]
    base = Path(output_dir or ".")
    target = _free_path(base / _safe_name(item["file"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    data = await _api(app.api.download_bytes(
        path, params={"folder": item["folder"], "file": item["file"]},
    ))
    target.write_bytes(data)
    return {
        "root": str(base),
        "files": [{
            "folder": item["folder"], "file": _safe_name(item["file"]),
            "path": str(target), "bytes": len(data),
        }],
    }


# ---- 提交 ----


async def submit_flag(
    app: App, game_id: int, challenge_id: int, flag: str, *, timeout: float = 8.0,
) -> dict[str, Any]:
    """提交 flag：入队后轮询评测结果（最多 10 次 × 0.8s）。"""
    _require_login(app)
    result = await _api(app.api.submit_flag(game_id, challenge_id, flag))
    result = result if isinstance(result, dict) else {}
    submission_id = result.get("id")
    if submission_id is None:
        return {
            "submission_id": None,
            "solved": result.get("solved"),
            "result": result.get("result"),
            "pending": False,
        }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    status: dict[str, Any] | None = None
    checks = 0
    while checks < 10:
        await asyncio.sleep(0.8)
        checks += 1
        try:
            fetched = await app.api.get_submission(game_id, challenge_id, int(submission_id))
        except ApiError:
            break
        if isinstance(fetched, dict):
            status = fetched
            if fetched.get("solved") is not None:
                break
        if loop.time() >= deadline:
            break
    solved = status.get("solved") if status else None
    return {
        "submission_id": int(submission_id),
        "solved": solved,
        "result": (status or {}).get("result"),
        "pending": solved is None,
    }


# ---- 实例 ----


def _instance_expiry(instance: dict[str, Any]) -> tuple[int | None, int | None]:
    """实例到期时间：created_at + (renew_count + 1) 小时（created_at 非数字则未知）。"""
    raw = instance.get("created_at")
    if raw is None:
        return None, None
    try:
        created = int(raw)
    except (TypeError, ValueError):
        return None, None
    try:
        renew = int(instance.get("renew_count") or 0)
    except (TypeError, ValueError):
        renew = 0
    expires = created + (renew + 1) * 3600
    return expires, max(0, expires - int(time.time()))


async def list_instances(
    app: App, game_id: int, challenge_id: int | None = None,
) -> list[dict[str, Any]]:
    data = await _api(app.api.list_instances(game_id))
    out: list[dict[str, Any]] = []
    for inst in data if isinstance(data, list) else []:
        if not isinstance(inst, dict) or not inst:
            continue
        if challenge_id is not None and str(inst.get("challenge_id")) != str(challenge_id):
            continue
        expires_at, remaining = _instance_expiry(inst)
        out.append({**inst, "expires_at": expires_at, "remaining_seconds": remaining})
    return out


async def start_instance(app: App, game_id: int, challenge_id: int) -> dict[str, Any]:
    try:
        instances = await list_instances(app, game_id, challenge_id)
    except ServiceError:
        instances = []
    if instances:
        return {"started": False, "already_started": True, "instance": instances[0]}
    try:
        await _api(app.api.post_json(f"/game/{game_id}/challenge/{challenge_id}/instance"))
    except ServiceError as e:
        message = (e.message or "").lower()
        if "one instance" in message or "already launched" in message:
            return {"started": False, "already_started": True, "instance": None}
        raise
    return {"started": True, "already_started": False, "instance": None}


async def stop_instance(app: App, game_id: int, challenge_id: int) -> dict[str, Any]:
    try:
        instances = await list_instances(app, game_id, challenge_id)
    except ServiceError:
        instances = []
    if not instances:
        return {"stopped": False, "already_stopped": True}
    await _api(app.api.delete_json(f"/game/{game_id}/challenge/{challenge_id}/instance"))
    return {"stopped": True, "already_stopped": False}


async def renew_instance(app: App, game_id: int, challenge_id: int) -> dict[str, Any]:
    try:
        instances = await list_instances(app, game_id, challenge_id)
    except ServiceError:
        instances = []
    if not instances:
        return {"renewed": False, "not_running": True}
    await _api(app.api.patch_json(f"/game/{game_id}/challenge/{challenge_id}/instance", json={}))
    return {"renewed": True, "not_running": False}


# ---- 队伍 ----


async def team_info(app: App, game_id: int) -> dict[str, Any] | None:
    try:
        team = await app.api.get_json(f"/game/{game_id}/team/self")
    except ApiError as e:
        if e.status == 404 or "not found" in (e.message or "").lower():
            return None
        raise ServiceError(e.message, code=str(e.code or "api_error"), status=e.status) from e
    return team if isinstance(team, dict) and team else None


async def team_rank(app: App, game_id: int, team_id: int | None = None) -> dict[str, Any]:
    tid = await resolve_team(app, game_id, team_id)
    data = await _api(app.api.get_json(f"/game/{game_id}/team/{tid}/rank"))
    if isinstance(data, dict):
        return {"rank": data.get("rank", data)}
    return {"rank": data}


async def team_members(
    app: App, game_id: int, team_id: int | None = None,
) -> list[dict[str, Any]]:
    tid = await resolve_team(app, game_id, team_id)
    data = await _api(app.api.get_json(f"/game/{game_id}/team/{tid}/member"))
    if isinstance(data, dict):
        data = data.get("members") or data.get("data") or []
    return [m for m in data if isinstance(m, dict)] if isinstance(data, list) else []


async def team_solves(
    app: App, game_id: int, team_id: int | None = None,
) -> list[dict[str, Any]]:
    tid = await resolve_team(app, game_id, team_id)
    from ret2console.api.client import split_page
    data = await _api(app.api.get_json(f"/game/{game_id}/team/{tid}/solve"))
    items, _ = split_page(data)
    return [s for s in items if isinstance(s, dict)]


async def team_create(
    app: App, game_id: int, name: str, tag: str | None = None,
) -> dict[str, Any]:
    _require_login(app)
    team = await _api(app.api.post_json(
        f"/game/{game_id}/team", json={"name": name, "tag": tag},
    ))
    return {"team": team if isinstance(team, dict) else {"name": name}}


async def team_join(app: App, game_id: int, token: str) -> dict[str, Any]:
    _require_login(app)
    team = await _api(app.api.patch_json(
        f"/game/{game_id}/team", json={"token": token},
    ))
    return {"team": team if isinstance(team, dict) else {}}


async def team_update(
    app: App, game_id: int, name: str, tag: str | None | object = _KEEP,
) -> dict[str, Any]:
    """修改本队：tag 为 _KEEP 时保留原值，None 清空，字符串覆盖。"""
    team = await team_info(app, game_id)
    if team is None:
        raise ServiceError("尚未加入队伍，无法修改", code="no_team")
    payload = {
        "name": name,
        "tag": team.get("tag") if tag is _KEEP else tag,
        "institute_id": team.get("institute_id"),
    }
    updated = await _api(app.api.patch_json(f"/game/{game_id}/team/self", json=payload))
    return {"team": updated if isinstance(updated, dict) else {"name": name}}


async def team_leave(app: App, game_id: int) -> dict[str, Any]:
    _require_login(app)
    try:
        await _api(app.api.delete_json(f"/game/{game_id}/team/self"))
    except ServiceError as e:
        if e.status == 404:
            raise ServiceError("尚未加入队伍", code="no_team") from e
        raise
    return {"left": True}


# ---- 提交记录 ----


async def list_submissions(app: App, game_id: int) -> list[dict[str, Any]]:
    items = await _api(app.api.list_submissions(game_id))
    return [s for s in items if isinstance(s, dict)]


# ---- 虚拟文件系统 ----


def _join_path(current: str, path: str) -> str:
    if path.startswith("/"):
        return normalize_path(path)
    return normalize_path((current or "/") + "/" + path)


async def ls(app: App, path: str | None = None) -> dict[str, Any]:
    target = _join_path(app.ctx.vfs_path, path or ".")
    try:
        nodes = await app.vfs.list_dir(target)
    except VfsError as e:
        raise ServiceError(str(e), code="vfs", status=404) from e
    return {
        "path": target,
        "entries": [
            {
                "name": n.name,
                "kind": n.kind,
                "game_id": n.game_id,
                "challenge_id": n.challenge_id,
                "meta": n.meta,
            }
            for n in nodes
        ],
    }


async def read(app: App, path: str) -> dict[str, Any]:
    target = _join_path(app.ctx.vfs_path, path)
    try:
        text = await app.vfs.read(target)
    except VfsError as e:
        raise ServiceError(str(e), code="vfs", status=404) from e
    return {"path": target, "text": text}
