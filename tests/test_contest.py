"""contest / scoreboard 新行为：筛选、名称解析、文档与封面。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class ContestBackend:
    def __init__(self, cover: str | None = "abc123") -> None:
        self.cover = cover
        self.requests: list[dict] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append({
            "method": request.method,
            "path": path,
            "params": dict(request.url.params),
        })
        if path.endswith("/api/game") and request.method == "GET":
            return httpx.Response(200, json=[[
                {"id": 3, "title": "新生赛", "host_type": 0,
                 "start_at": 1700000000, "end_at": 1700003600},
            ], 1])
        if path.endswith("/api/game/3") and request.method == "GET":
            return httpx.Response(200, json={
                "id": 3, "title": "新生赛", "brief": "简介",
                "description": "说明", "cover": self.cover,
                "start_at": 1700000000, "end_at": 1700003600,
            })
        if path.endswith("/api/game/3/doc/readme"):
            return httpx.Response(200, json="# 新生赛简介\n\n欢迎")
        if path.endswith("/api/game/3/doc/rules"):
            return httpx.Response(200, json="# 规则\n\n禁止作弊")
        if path.endswith("/api/media"):
            return httpx.Response(200, content=b"PNG-BYTES")
        if path.endswith("/api/game/3/team"):
            return httpx.Response(200, json=[[
                {"id": 7, "name": "星尘", "score": 100, "institute_id": 2},
            ], 1])
        if path.endswith("/api/account/institute"):
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unexpected {path}"})


def _app(backend: ContestBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"
    return app


@pytest.mark.asyncio
async def test_contest_list_filters(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest list --type training --page 2 --page-size 10") == 0
    params = backend.requests[-1]["params"]
    assert params["host_type"] == "0"
    assert params["page"] == "2"
    assert params["page_size"] == "10"
    out = capsys.readouterr().out
    assert "练习赛" in out
    assert "共 1 场" in out
    assert app.games_cache == {3: "新生赛"}


@pytest.mark.asyncio
async def test_contest_list_rejects_bad_type(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest list --type league") == 1
    assert "--type" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_list_without_flags_stays_full(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest list") == 0
    assert "新生赛" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_show_intro_by_name(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest show 新生 --intro") == 0
    assert backend.requests[-1]["path"].endswith("/doc/readme")
    assert "欢迎" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_show_conflicting_flags(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest show --intro --cover") == 1
    assert "只能选一个" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_show_cover_prints_url_without_graphics(capsys):
    backend = ContestBackend()
    app = _app(backend)
    app.graphics = None
    assert await dispatch(app, "contest show 3 --cover") == 0
    out = capsys.readouterr().out
    assert "/api/media?hash=abc123" in out
    assert any(r["path"].endswith("/api/media") for r in backend.requests)


@pytest.mark.asyncio
async def test_contest_show_cover_missing(capsys):
    backend = ContestBackend(cover=None)
    app = _app(backend)
    assert await dispatch(app, "contest show 3 --cover") == 1
    assert "没有封面" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_use_by_name(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest use 新生") == 0
    assert app.ctx.game_id == 3
    assert app.ctx.vfs_path == "/contests/3"
    assert "已进入比赛" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_doc_with_name_and_type(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest doc 新生赛 rules") == 0
    assert backend.requests[-1]["path"].endswith("/doc/rules")
    assert "禁止作弊" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_scoreboard_resolves_name(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "scoreboard 新生 --page 2 --page-size 5") == 0
    board = next(r for r in backend.requests if r["path"].endswith("/game/3/team"))
    params = board["params"]
    assert params["min_state"] == "3"
    assert params["page"] == "2"
    assert params["page_size"] == "5"
    out = capsys.readouterr().out
    assert "星尘" in out
    assert "共1队" in "".join(out.split())


@pytest.mark.asyncio
async def test_scoreboard_requires_context(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "scoreboard") == 1
    assert "未指定比赛" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_contest_use_unknown_name(capsys):
    backend = ContestBackend()
    app = _app(backend)
    assert await dispatch(app, "contest use 不存在") == 1
    assert "找不到比赛" in capsys.readouterr().out
