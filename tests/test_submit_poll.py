"""submit 评测队列轮询测试（POST 入队 → GET /submit?id= 查状态）。"""

import asyncio
import io
import sys

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class QueueBackend:
    """提交进入评测队列：POST 返回 id，GET 若干次后才有结果。"""

    def __init__(self, pending_gets: int = 2, final_solved: bool = True) -> None:
        self.pending_gets = pending_gets
        self.final_solved = final_solved
        self.get_calls = 0
        self.post_calls = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/submit") and request.method == "POST":
            self.post_calls += 1
            return httpx.Response(200, json={"id": 42, "solved": None, "result": None})
        if path.endswith("/submit") and request.method == "GET":
            self.get_calls += 1
            assert "id=42" in str(request.url)
            if self.get_calls <= self.pending_gets:
                return httpx.Response(200, json={"id": 42, "solved": None, "result": None})
            return httpx.Response(200, json={
                "id": 42, "solved": self.final_solved,
                "result": "accepted" if self.final_solved else "wrong flag",
            })
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


@pytest.fixture
def fast_sleep(monkeypatch):
    async def noop(*a, **kw):
        pass
    monkeypatch.setattr(asyncio, "sleep", noop)


def _app(backend: QueueBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"
    app.session.account = "alice"
    app.session.nickname = "爱丽丝"
    app.session.permissions = ["basic"]
    app.ctx.set_challenge(3, 5)
    return app


@pytest.mark.asyncio
async def test_submit_polls_until_solved(fast_sleep):
    backend = QueueBackend(pending_gets=2, final_solved=True)
    assert await dispatch(_app(backend), "submit flag{hello}") == 0
    assert backend.post_calls == 1
    assert backend.get_calls == 3  # 2 次排队中 + 1 次出结果


@pytest.mark.asyncio
async def test_submit_polls_wrong_flag(fast_sleep):
    backend = QueueBackend(pending_gets=0, final_solved=False)
    assert await dispatch(_app(backend), "submit flag{no}") == 0
    assert backend.get_calls == 1


@pytest.mark.asyncio
async def test_submit_poll_timeout_warns(fast_sleep):
    """评测超时（始终 solved=None）→ 提示仍在评测，不报失败。"""
    backend = QueueBackend(pending_gets=999, final_solved=True)
    assert await dispatch(_app(backend), "submit flag{slow}") == 0
    assert backend.get_calls == 10  # 轮询次数上限（8s / 0.8s）


@pytest.mark.asyncio
async def test_submit_stdin_uses_env_context(monkeypatch):
    """子 shell 通道：无 REPL 上下文时从 R2C_GAME_ID/R2C_CHALLENGE_ID 恢复题目。"""
    backend = QueueBackend(pending_gets=0, final_solved=True)
    app = _app(backend)
    app.ctx = type(app.ctx)()  # 清空上下文（模拟 `ret2console -c 'submit -'` 新进程）
    monkeypatch.setenv("R2C_GAME_ID", "3")
    monkeypatch.setenv("R2C_CHALLENGE_ID", "5")
    monkeypatch.setattr(sys, "stdin", io.StringIO("flag{from-shell}"))
    assert await dispatch(app, "submit -") == 0
    assert backend.post_calls == 1
    assert app.ctx.game_id == 3 and app.ctx.challenge_id == 5


@pytest.mark.asyncio
async def test_submit_stdin_without_context(monkeypatch):
    """无 REPL 上下文且无环境变量 → 提示未指定题目。"""
    backend = QueueBackend(pending_gets=0, final_solved=True)
    app = _app(backend)
    app.ctx = type(app.ctx)()
    monkeypatch.setattr(sys, "stdin", io.StringIO("flag{anon}"))
    assert await dispatch(app, "submit -") == 1
    assert backend.post_calls == 0
