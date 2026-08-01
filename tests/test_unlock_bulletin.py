"""challenge unlock 与根 /notice 全局公告测试。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class Backend:
    def __init__(self) -> None:
        self.reqs: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.reqs.append({"method": request.method, "path": path, "body": request.read().decode()})
        if path.endswith("/hint/unlock"):
            return httpx.Response(200, json={"id": 2})
        if path.endswith("/bulletin"):
            return httpx.Response(200, json=[[
                {"title": "维护公告", "created_at": 1720000000, "content": "周六 10:00 维护"},
                {"title": "赛事提醒", "created_at": 1720000001, "content": "记得签到"},
            ], 2])
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _app(backend: Backend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    return app


@pytest.mark.asyncio
async def test_challenge_unlock_in_context():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_challenge(3, 5)
    assert await dispatch(app, "challenge unlock 2") == 0
    req = backend.reqs[-1]
    assert req["method"] == "POST"
    assert req["path"] == "/api/game/3/challenge/5/hint/unlock"
    assert '"id":2' in req["body"]


@pytest.mark.asyncio
async def test_challenge_unlock_explicit_cid():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "challenge unlock 5 2") == 0
    assert backend.reqs[-1]["path"] == "/api/game/3/challenge/5/hint/unlock"


@pytest.mark.asyncio
async def test_challenge_unlock_requires_context():
    backend = Backend()
    app = _app(backend)
    assert await dispatch(app, "challenge unlock 2") == 1


@pytest.mark.asyncio
async def test_cat_root_notice_fetches_bulletin(tmp_path, monkeypatch):
    """根 /notice 拉取全局公告并走 Markdown 渲染。"""
    from ret2console.commands import files as files_mod

    monkeypatch.chdir(tmp_path)
    app = _app(Backend())
    seen = []
    monkeypatch.setattr(files_mod, "render_markdown", lambda t: seen.append(t) or t)
    assert await dispatch(app, "cat /notice") == 0
    assert seen, "公告内容未经过渲染路径"
    assert "维护公告" in seen[0]
    assert "**维护公告**" in seen[0]


@pytest.mark.asyncio
async def test_notice_command_forwards_to_bulletin(tmp_path, monkeypatch):
    """notice 命令无参 = 全局公告（SPEC §9）。"""
    from ret2console.commands import files as files_mod

    monkeypatch.chdir(tmp_path)
    app = _app(Backend())
    seen = []
    monkeypatch.setattr(files_mod, "render_markdown", lambda t: seen.append(t) or t)
    assert await dispatch(app, "notice") == 0
    assert seen and "维护公告" in seen[0]
