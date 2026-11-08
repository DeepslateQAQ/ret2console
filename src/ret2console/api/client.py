"""Ret2Shell HTTP 客户端。

关键机制（SPEC §4.2）：
- token 通过响应头 `Set-Token` 下发/续期，客户端必须监听每个响应的该头并更新本地 token。
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx

from ret2console.api.pow import PowChallenge, solve


def _unwrap_list(data: Any) -> list[Any]:
    """兼容 ret2shell 分页格式 `[items, total]` 与 `{data: [...]}` 等包装。"""
    if isinstance(data, list):
        if len(data) == 2 and isinstance(data[0], list):
            return data[0]
        return data
    if isinstance(data, dict):
        for key in ("data", "games", "challenges", "notifications"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def split_page(data: Any) -> tuple[list[Any], int | None]:
    """分页响应 → (items, total)：兼容 `[items, total]`、`{data: [...]}` 与裸数组。"""
    items = _unwrap_list(data)
    if (
        isinstance(data, list)
        and len(data) == 2
        and isinstance(data[0], list)
        and isinstance(data[1], int)
    ):
        return items, data[1]
    if isinstance(data, dict) and isinstance(data.get("total"), int):
        return items, data["total"]
    return items, None


class ApiError(Exception):
    """携带后端语义的 API 错误。"""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotLoggedInError(ApiError):
    def __init__(self) -> None:
        super().__init__("未登录，请先执行 auth login")


@dataclass
class ApiClient:
    base_url: str = "https://ctf.xidian.edu.cn/api"
    token: str | None = None
    insecure: bool = False
    # token 变化回调：参数为新的 JWT（或 None 表示登出）
    on_token: Callable[[str | None], None] | None = None
    _client: httpx.AsyncClient = field(init=False, repr=False, default=None)  # type: ignore[assignment]

    def configure(
        self, base_url: str, token: str | None, *, insecure: bool | None = None,
    ) -> None:
        if insecure is not None and insecure != self.insecure:
            self.insecure = insecure
        self.base_url = base_url.rstrip("/")
        self.token = token

    def reset_client(self) -> None:
        """丢弃当前 httpx client：下次请求按最新 base_url/insecure 重建。

        endpoint 切换后必须调用：懒创建的 client 已绑定旧 base_url 与 verify，
        只改属性不会生效（请求仍发往旧平台，导致 JSONDecodeError 之类的错乱）。
        """
        self._client = None

    def _headers(self, *, auth: bool = True) -> dict[str, str]:
        headers = {"User-Agent": "ret2console/0.1"}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json: Any = None, content: bytes | None = None, files: Any = None,
        auth: bool = True,
    ) -> httpx.Response:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=30.0, verify=not self.insecure
            )
            if self.insecure:
                try:
                    warnings.filterwarnings("ignore", category=httpx.InsecureRequestWarning)
                except AttributeError:
                    # 部分 httpx 版本未导出该警告类（urllib3 侧发出）
                    warnings.filterwarnings("ignore", message="Unverified HTTPS request")
        resp = await self._client.request(
            method, path, params=params, json=json, content=content,
            files=files, headers=self._headers(auth=auth),
        )
        # 监听 Set-Token 续期（SPEC §4.2 关键坑）
        new_token = resp.headers.get("set-token")
        if new_token and new_token != self.token:
            self.token = new_token
            if self.on_token:
                self.on_token(new_token)
        if resp.status_code >= 400:
            if (
                resp.status_code == 401
                and self.token
                and path not in ("/account/login", "/account/register")
            ):
                # 服务端已吊销/过期当前 token：本地立即失效，避免各处报
                # 裸 `please login first` 且 auth login 被「已登录」挡住。
                self.token = None
                if self.on_token:
                    self.on_token(None)
                raise ApiError("登录态已失效（已在其他设备登出或已过期），请重新 auth login", 401)
            raise self._error(resp)
        return resp

    def _error(self, resp: httpx.Response) -> ApiError:
        try:
            body = resp.json()
            message = body.get("message") or body.get("error") or str(body)
            code = body.get("code")
        except Exception:
            message = resp.text[:200] or resp.reason_phrase
            code = None
        if resp.status_code == 401:
            return NotLoggedInError() if not self.token else ApiError(message, resp.status_code, code)
        return ApiError(message, resp.status_code, code)

    async def get_json(self, path: str, **kw: Any) -> Any:
        return (await self._request("GET", path, **kw)).json()

    async def post_json(self, path: str, json: Any = None, **kw: Any) -> Any:
        resp = await self._request("POST", path, json=json, **kw)
        if not resp.content:
            return None  # 部分接口成功时返回空 body（如 start instance 的 Ok(())）
        return resp.json()

    async def patch_json(self, path: str, json: Any = None, **kw: Any) -> Any:
        resp = await self._request("PATCH", path, json=json, **kw)
        if not resp.content:
            return None
        return resp.json()

    async def delete_json(self, path: str, **kw: Any) -> Any:
        resp = await self._request("DELETE", path, **kw)
        if not resp.content:
            return None
        return resp.json()

    async def upload(
        self, path: str, files: Any, params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """multipart 上传（管理端附件：admin challenge attach）。"""
        return await self._request("POST", path, params=params, files=files)

    # ---- 认证 ----

    async def get_pow_captcha(self) -> PowChallenge:
        data = await self.get_json("/account/captcha/cli")
        return PowChallenge.parse(data["id"], data["challenge"])

    async def platform_name(self, timeout: float = 4.0) -> str | None:
        """平台显示名（如「西电 CTF 终端」）：GET /platform/info 的 server.name。

        供 wsrx 控制源注册等场景使用；请求失败/未配置名字时返回 None（调用方兜底），
        短超时避免平台不可达时阻塞启动。
        """
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url, timeout=timeout, headers=self._headers(),
                verify=not self.insecure,
            ) as client:
                resp = await client.get("/platform/info")
                if resp.status_code >= 400:
                    return None
                data = resp.json()
        except Exception:
            return None
        name = data.get("name") if isinstance(data, dict) else None
        return name or None

    async def solve_captcha(self) -> tuple[str, str]:
        challenge = await self.get_pow_captcha()
        return challenge.captcha_id, solve(challenge)

    async def login(self, account: str, password: str) -> dict[str, Any]:
        captcha_id, captcha_answer = await self.solve_captcha()
        resp = await self._request(
            "POST", "/account/login",
            json={
                "account": account,
                "password": password,
                "captcha_id": captcha_id,
                "captcha_answer": captcha_answer,
            },
            auth=False,
        )
        return {"token": self.token}  # token 已通过 Set-Token 头回调

    async def register(
        self, account: str, nickname: str, email: str, password: str
    ) -> None:
        captcha_id, captcha_answer = await self.solve_captcha()
        await self._request(
            "POST", "/account/register",
            json={
                "account": account,
                "nickname": nickname,
                "email": email,
                "password": password,
                "captcha_id": captcha_id,
                "captcha_answer": captcha_answer,
            },
            auth=False,
        )

    async def logout(self) -> None:
        await self._request("POST", "/account/logout")

    async def profile(self) -> dict[str, Any]:
        return await self.get_json("/account/profile")

    async def update_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        """修改资料：PATCH /account/profile（后端成功时可能返回空 body）。"""
        data = await self.patch_json("/account/profile", json=payload)
        return data if isinstance(data, dict) else {}

    async def upload_media(self, data: bytes, filename: str) -> dict[str, Any]:
        """上传媒体（头像等）：POST /media，multipart 字段名 file，返回 {hash, ...}。"""
        resp = await self._request("POST", "/media", files={"file": (filename, data)})
        if not resp.content:
            return {}
        body = resp.json()
        return body if isinstance(body, dict) else {}

    async def temporary_code(self) -> dict[str, Any]:
        """生成临时身份验证码：POST /account/code（5 分钟有效）。"""
        data = await self.post_json("/account/code", json={})
        return data if isinstance(data, dict) else {}

    async def list_institutes(self) -> list[dict[str, Any]]:
        """机构（归属单位）列表：GET /account/institute，返回 [{id, name, ...}]。

        排行榜接口的 team 对象只有 institute_id（institute_name 仅 /team/self
        等 get_ex 路径 JOIN 填充），需要这里映射成可读名称。
        """
        return _unwrap_list(await self.get_json("/account/institute"))

    # ---- 比赛 ----

    async def list_games(self, page: int = 1, page_size: int = 50) -> list[dict[str, Any]]:
        return await self._paginate("/game", page_size)

    async def list_games_page(
        self, page: int = 1, page_size: int = 50, host_type: int | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """单页比赛列表：GET /game?page=&page_size=[&host_type=]，返回 (items, total)。"""
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if host_type is not None:
            params["host_type"] = host_type
        return split_page(await self.get_json("/game", params=params))

    async def get_game(self, game_id: int) -> dict[str, Any]:
        return await self.get_json(f"/game/{game_id}")

    async def download_bytes(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        """下载原始字节（附件/封面等）：走带 Bearer 的 _request，返回响应体。"""
        return (await self._request("GET", path, params=params)).content

    async def get_game_doc(self, game_id: int, doc_type: str) -> str:
        resp = await self._request("GET", f"/game/{game_id}/doc/{doc_type}")
        # 后端返回 Json(content)：响应体是 JSON 编码的字符串（\n 为转义字面量）
        try:
            data = resp.json()
        except Exception:
            return resp.text
        return data if isinstance(data, str) else resp.text

    async def get_notifications(self, game_id: int) -> list[dict[str, Any]]:
        data = await self.get_json(f"/game/{game_id}/notification")
        return _unwrap_list(data)

    # ---- 题目 ----

    async def list_challenges(self, game_id: int, page: int = 1, page_size: int = 100) -> list[dict[str, Any]]:
        return await self._paginate(f"/game/{game_id}/challenge", page_size)

    async def _paginate(self, path: str, page_size: int) -> list[dict[str, Any]]:
        """自动翻页拉取全量列表（r2s 分页响应为 `[items, total]`）。

        题目/比赛多于一页时仅取第一页会丢数据（如 MoeCTF 2025 题量 > 100），
        这里按 total 或「页不满」判定结束；page_size=100 时上限 100 页兜底。
        """
        out: list[dict[str, Any]] = []
        page = 1
        while page <= 100:
            data = await self.get_json(path, params={"page": page, "page_size": page_size})
            items = _unwrap_list(data)
            out.extend(items)
            total = (
                data[1]
                if isinstance(data, list) and len(data) == 2 and isinstance(data[0], list) and isinstance(data[1], int)
                else None
            )
            if total is not None:
                if len(out) >= total or not items:
                    break
            elif len(items) < page_size:
                break
            page += 1
        return out

    async def get_challenge(self, game_id: int, challenge_id: int) -> dict[str, Any]:
        return await self.get_json(f"/game/{game_id}/challenge/{challenge_id}")

    async def list_instances(self, game_id: int) -> list[dict[str, Any]]:
        """当前用户在该比赛运行中的实例（选手侧接口 GET /game/{gid}/instance）。

        注意：`GET /game/{gid}/challenge/{cid}/instance` 是管理员接口（403），
        选手只能 POST/PATCH/DELETE challenge 级路径，查询一律走 game 级。
        """
        data = await self.get_json(f"/game/{game_id}/instance")
        if isinstance(data, list):
            return data
        return [data] if data else []

    async def list_solves(self, game_id: int) -> list[dict[str, Any]]:
        """当前队伍已提交记录（GET /game/{gid}/solve），含 solved/challenge_id。"""
        return _unwrap_list(await self.get_json(f"/game/{game_id}/solve"))

    async def list_submissions(self, game_id: int) -> list[dict[str, Any]]:
        """当前账号在该比赛的提交记录（GET /game/{gid}/solve，分页兼容）。"""
        return split_page(await self.get_json(f"/game/{game_id}/solve"))[0]

    # ---- 提交 ----

    async def submit_flag(self, game_id: int, challenge_id: int, flag: str) -> dict[str, Any]:
        return await self.post_json(
            f"/game/{game_id}/challenge/{challenge_id}/submit", json={"content": flag}
        )

    async def get_submission(
        self, game_id: int, challenge_id: int, submission_id: int
    ) -> dict[str, Any]:
        """查询评测结果：GET /submit?id=<POST 返回的 id>（提交后进入评测队列）。"""
        return await self.get_json(
            f"/game/{game_id}/challenge/{challenge_id}/submit",
            params={"id": submission_id},
        )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
