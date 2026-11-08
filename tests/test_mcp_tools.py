"""MCP 工具层测试：上下文解析、名称匹配、提交轮询、实例预检（MockTransport）。"""

import json
import time

import httpx
import pytest

from ret2console import service
from ret2console.cli import _build_app
from ret2console.mcpserver.tools import TOOLS
from ret2console.service import ServiceError

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


class Backend:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.games = [{"id": 3, "title": "新生赛"}]
        self.challenges = [{"id": 5, "title": "签到题"}]
        self.instances: list[dict] = []
        self.posts = 0
        self.team = {"id": 7, "name": "星尘", "state": 3, "score": 100, "token": "invite-tok"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.requests.append({
            "method": method,
            "path": path,
            "params": dict(request.url.params),
        })
        if method == "GET" and path.endswith("/game"):
            return httpx.Response(200, json=[self.games, len(self.games)])
        if method == "GET" and path.endswith("/game/3"):
            return httpx.Response(200, json={"id": 3, "title": "新生赛", "brief": "简介"})
        if method == "GET" and path.endswith("/game/3/challenge"):
            return httpx.Response(200, json=[self.challenges, len(self.challenges)])
        if method == "GET" and path.endswith("/game/3/challenge/5"):
            return httpx.Response(200, json={"id": 5, "title": "签到题", "content": "输出 hello"})
        if path.endswith("/game/3/challenge/5/submit"):
            if method == "POST":
                return httpx.Response(200, json={"id": 11, "solved": None, "result": None})
            return httpx.Response(200, json={"solved": True, "result": "accepted"})
        if method == "GET" and path.endswith("/game/3/instance"):
            return httpx.Response(200, json=self.instances)
        if method == "POST" and path.endswith("/game/3/challenge/5/instance"):
            self.posts += 1
            return httpx.Response(200)
        if method == "GET" and path.endswith("/game/3/team/self"):
            return httpx.Response(200, json=self.team)
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})


def _app(backend: Backend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="secret-jwt")
    app.session.token = "secret-jwt"
    return app


async def _call(name: str, app, args: dict):
    return await TOOLS_BY_NAME[name].handler(app, args)


@pytest.mark.asyncio
async def test_context_has_no_token():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_challenge(3, 5)
    data = await _call("r2c_context", app, {})
    assert data["logged_in"] is True
    assert data["game_id"] == 3 and data["challenge_id"] == 5
    assert "secret-jwt" not in json.dumps(data)


@pytest.mark.asyncio
async def test_use_contest_resolves_name():
    backend = Backend()
    app = _app(backend)
    data = await _call("r2c_use_contest", app, {"game": "新生"})
    assert app.ctx.game_id == 3
    assert app.ctx.challenge_id is None
    assert data["game_id"] == 3


@pytest.mark.asyncio
async def test_use_contest_ambiguous_name():
    backend = Backend()
    backend.games = [{"id": 3, "title": "新生赛"}, {"id": 4, "title": "新生杯"}]
    app = _app(backend)
    with pytest.raises(ServiceError) as excinfo:
        await _call("r2c_use_contest", app, {"game": "新生"})
    assert excinfo.value.code == "ambiguous"
    assert len(excinfo.value.data["candidates"]) == 2


@pytest.mark.asyncio
async def test_use_challenge_then_show():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_game(3)
    data = await _call("r2c_use_challenge", app, {"challenge": "签到"})
    assert data["challenge_id"] == 5
    challenge = await _call("r2c_challenge_show", app, {})
    assert challenge["title"] == "签到题"
    assert challenge["content"] == "输出 hello"


@pytest.mark.asyncio
async def test_game_without_challenge_is_invalid_context():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_game(3)
    with pytest.raises(ServiceError) as excinfo:
        await _call("r2c_challenge_show", app, {"game": 4})
    assert excinfo.value.code == "invalid_context"


@pytest.mark.asyncio
async def test_submit_flag_polls_result(monkeypatch):
    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(service.asyncio, "sleep", _no_sleep)
    backend = Backend()
    app = _app(backend)
    app.ctx.set_challenge(3, 5)
    data = await _call("r2c_challenge_submit", app, {"flag": "flag{test}"})
    assert data == {
        "submission_id": 11, "solved": True, "result": "accepted", "pending": False,
    }
    submits = [r for r in backend.requests if r["path"].endswith("/submit")]
    assert [r["method"] for r in submits] == ["POST", "GET"]
    assert submits[1]["params"]["id"] == "11"


@pytest.mark.asyncio
async def test_instance_start_precheck_skips_post():
    backend = Backend()
    backend.instances = [{
        "challenge_id": 5, "state": "running",
        "created_at": int(time.time()) - 600, "renew_count": 0,
    }]
    app = _app(backend)
    app.ctx.set_challenge(3, 5)
    data = await _call("r2c_instance_start", app, {})
    assert data["already_started"] is True
    assert data["started"] is False
    assert data["instance"]["remaining_seconds"] > 0
    assert data["instance"]["expires_at"] == data["instance"]["created_at"] + 3600
    assert backend.posts == 0


@pytest.mark.asyncio
async def test_instance_start_creates_when_idle():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_challenge(3, 5)
    data = await _call("r2c_instance_start", app, {})
    assert data["started"] is True and data["already_started"] is False
    assert backend.posts == 1


@pytest.mark.asyncio
async def test_team_info_and_rank():
    backend = Backend()
    app = _app(backend)
    app.ctx.set_game(3)
    data = await _call("r2c_team_info", app, {})
    assert data["team"]["name"] == "星尘"
    rendered = json.dumps(data)
    assert "secret-jwt" not in rendered


@pytest.mark.asyncio
async def test_all_tools_declare_object_schema():
    for tool in TOOLS:
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["required"], list)
        for key in schema["required"]:
            assert key in schema["properties"], tool.name
        assert tool.description, tool.name
