"""端到端冒烟：mock 后端验证 auth 登录 + flag 提交链路。"""

import hashlib

import httpx
import pytest

from ret2console.cli import _register_all, _build_app
from ret2console.repl import dispatch
from ret2console.state import App

_register_all()


def _pow_answer(seed: str) -> str:
    i = 0
    while True:
        ans = f"{seed}{i}"
        if hashlib.sha256(ans.encode()).hexdigest().startswith("0000"):
            return ans
        i += 1


class FakeBackend:
    """最小可用的 ret2shell mock：PoW + 登录 + profile + 提交。"""

    def __init__(self) -> None:
        self.token_counter = 0
        self.submitted: list[dict] = []
        self.current_token: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/account/captcha/cli"):
            return httpx.Response(200, json={
                "id": "captcha-1",
                "validator": "pow",
                "challenge": "4#mockseed12345678",
            })
        if path.endswith("/account/login"):
            body = request.read().decode()
            assert "mockseed12345678" in body  # PoW 答案必须带 seed
            self.token_counter += 1
            self.current_token = f"jwt-{self.token_counter}"
            return httpx.Response(200, headers={"Set-Token": self.current_token})
        if path.endswith("/account/profile"):
            return httpx.Response(200, json={
                "account": "alice", "nickname": "爱丽丝", "permissions": ["basic"],
            })
        if path.endswith("/submit") and request.method == "POST":
            self.submitted.append({"path": path, "body": request.read().decode()})
            return httpx.Response(200, json={"id": len(self.submitted), "solved": None, "result": None})
        if path.endswith("/submit") and request.method == "GET":
            # 评测队列：GET /submit?id=... 返回最终结果
            return httpx.Response(200, json={"id": 1, "solved": True, "result": "accepted"})
        if "/challenge" in path and request.method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


@pytest.mark.asyncio
async def test_login_and_submit_flow():
    backend = FakeBackend()
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api",
        transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token=None)

    # 登录（走 PoW）
    code = await dispatch(app, "auth login alice pw123")
    assert code == 0
    assert app.session.logged_in
    assert app.session.nickname == "爱丽丝"
    assert app.api.token == "jwt-1"

    # 进入题目上下文
    app.ctx.set_challenge(3, 5)

    # 显式提交
    code = await dispatch(app, "submit flag{hello}")
    assert code == 0
    assert backend.submitted, "提交未到达后端"
    assert "flag{hello}" in backend.submitted[-1]["body"]

    # 粘贴即提交
    code = await dispatch(app, "moectf{world}")
    assert code == 0
    assert "moectf{world}" in backend.submitted[-1]["body"]

    # 未登录提交 → 报错
    app.session.clear()
    app.api.token = None
    code = await dispatch(app, "flag{anon}")
    assert code == 1


@pytest.mark.asyncio
async def test_session_persists_across_restart():
    """登录态缓存：登录保存后，新建 App 应恢复登录态（无需重新登录）。"""
    backend = FakeBackend()
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api",
        transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token=None)

    code = await dispatch(app, "auth login alice pw123")
    assert code == 0
    assert app.session.logged_in
    await app.api.close()

    # 模拟重启：全新 App 从磁盘加载 session
    app2 = _build_app()
    assert app2.session.logged_in, "重启后登录态未恢复"
    assert app2.session.nickname == "爱丽丝"
    assert app2.api.token == "jwt-1"
    await app2.api.close()


@pytest.mark.asyncio
async def test_expired_session_falls_back_to_guest():
    """token 过期：启动验证返回 401 → 清除会话回 guest。"""
    app = _build_app()
    app.session.token = "expired-jwt"
    app.session.account = "alice"
    app.session.nickname = "爱丽丝"
    app.api.configure(base_url="http://mock/api", token="expired-jwt")

    class ExpiredBackend:
        def handler(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"message": "token expired"})

    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(ExpiredBackend().handler)
    )
    from ret2console.repl import _welcome
    await _welcome(app)
    assert not app.session.logged_in
    await app.api.close()
