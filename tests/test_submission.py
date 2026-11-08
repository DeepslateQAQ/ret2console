"""submissions 命令测试（提交记录渲染与名称解析）。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.output import format_ts
from ret2console.repl import dispatch


class SubmissionBackend:
    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path.endswith("/game") and request.method == "GET":
            return httpx.Response(200, json=[[{"id": 3, "title": "新生赛"}], 1])
        if path.endswith("/game/3/solve"):
            return httpx.Response(200, json=[self.items, len(self.items)])
        return httpx.Response(404, json={"message": f"unexpected {path}"})


def _app(backend: SubmissionBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"
    return app


@pytest.mark.asyncio
async def test_submissions_table(capsys):
    created = 1700000000
    backend = SubmissionBackend([
        {"challenge_id": 5, "challenge_name": "签到题", "result": "accepted", "solved": True,
         "score": 100, "created_at": created},
        {"challenge_id": 6, "result": "wrong", "solved": False, "created_at": created},
    ])
    app = _app(backend)
    assert await dispatch(app, "submissions 3") == 0
    out = capsys.readouterr().out
    assert "签到题" in out
    assert "accepted" in out
    assert "100" in out
    assert format_ts(created) in out
    assert "—" in out


@pytest.mark.asyncio
async def test_submissions_empty(capsys):
    backend = SubmissionBackend([])
    app = _app(backend)
    assert await dispatch(app, "submissions 3") == 0
    assert "（暂无提交记录）" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_submissions_resolves_contest_name(capsys):
    backend = SubmissionBackend([
        {"challenge_id": 5, "challenge_name": "签到题", "solved": True, "created_at": 1700000000},
    ])
    app = _app(backend)
    assert await dispatch(app, "submissions 新生赛") == 0
    assert "/api/game" in backend.requests
    assert "签到题" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_submissions_without_context(capsys):
    backend = SubmissionBackend([])
    app = _app(backend)
    assert await dispatch(app, "submissions") == 1
    assert "未指定比赛" in capsys.readouterr().out
