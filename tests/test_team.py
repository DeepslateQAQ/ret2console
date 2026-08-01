"""team 命令测试：create/join/modify/info/rank/member/solve/leave（SPEC §9）。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class TeamBackend:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.team = {"id": 7, "name": "星尘", "game_id": 3, "state": 3, "score": 100, "token": "invite-tok", "tag": "星尘tag"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append({"method": request.method, "path": path, "body": request.read().decode()})
        if request.method == "POST" and path.endswith("/team"):
            return httpx.Response(200, json=self.team)
        if request.method == "PATCH" and path.endswith("/team"):
            return httpx.Response(200, json=self.team)
        if request.method == "PATCH" and path.endswith("/team/self"):
            import json as _json
            body = _json.loads(request.read().decode())
            if self.team:
                self.team = {**self.team, "name": body.get("name", self.team["name"]), "tag": body.get("tag")}
            return httpx.Response(200, json=self.team)
        if request.method == "DELETE" and path.endswith("/team/self"):
            self.team = None
            return httpx.Response(200, json={})
        if path.endswith("/team/self"):
            return httpx.Response(200, json=self.team if self.team else {})
        if path.endswith("/team/7/rank"):
            return httpx.Response(200, json=3)
        if path.endswith("/team/7/member"):
            return httpx.Response(200, json=[
                {"account": "alice", "nickname": "爱丽丝"},
                {"account": "bob", "nickname": "鲍勃"},
            ])
        if path.endswith("/team/7/solve"):
            return httpx.Response(200, json=[
                {"challenge_id": 5, "solved": True, "result": "accepted"},
                {"challenge_id": 6, "solved": False, "result": "wrong"},
            ])
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _app_with_backend(backend: TeamBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"  # team 命令需要登录态
    return app


@pytest.mark.asyncio
async def test_team_requires_game_context():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    code = await dispatch(app, "team info")
    assert code == 1  # 无比赛上下文


@pytest.mark.asyncio
async def test_team_create_and_invite_token():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    code = await dispatch(app, "team create 星尘")
    assert code == 0
    req = backend.requests[-1]
    assert req["method"] == "POST" and req["path"] == "/api/game/3/team"
    assert '"name":"星尘"' in req["body"]
    assert '"tag":null' in req["body"]


@pytest.mark.asyncio
async def test_team_join():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    code = await dispatch(app, "team join some-token")
    assert code == 0
    req = backend.requests[-1]
    assert req["method"] == "PATCH" and req["path"] == "/api/game/3/team"
    assert '"token":"some-token"' in req["body"]


@pytest.mark.asyncio
async def test_team_modify_keeps_tag():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    code = await dispatch(app, "team modify 新星尘")
    assert code == 0
    req = backend.requests[-1]
    assert req["method"] == "PATCH" and req["path"] == "/api/game/3/team/self"
    assert '"name":"新星尘"' in req["body"]
    assert '"tag":"星尘tag"' in req["body"]  # 省略 tag → 保留原值
    assert '"institute_id":' in req["body"]  # 显式回传避免被服务端清空


@pytest.mark.asyncio
async def test_team_modify_new_tag():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team modify 新星尘 喵喵") == 0
    assert '"tag":"喵喵"' in backend.requests[-1]["body"]


@pytest.mark.asyncio
async def test_team_modify_clear_tag():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team modify 新星尘 -") == 0
    assert '"tag":null' in backend.requests[-1]["body"]


@pytest.mark.asyncio
async def test_team_modify_not_joined():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    backend.team = None
    assert await dispatch(app, "team modify 新星尘") == 1
    assert backend.requests[-1]["method"] == "GET"


@pytest.mark.asyncio
async def test_team_info_and_auto_tid():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team info") == 0
    # rank/member/solve 无参时自动使用自己队伍 id
    assert await dispatch(app, "team rank") == 0
    assert await dispatch(app, "team member") == 0
    assert await dispatch(app, "team solve") == 0
    paths = [r["path"] for r in backend.requests]
    assert "/api/game/3/team/self" in paths
    assert "/api/game/3/team/7/rank" in paths
    assert "/api/game/3/team/7/member" in paths
    assert "/api/game/3/team/7/solve" in paths


@pytest.mark.asyncio
async def test_team_rank_explicit_tid():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team rank 7") == 0
    assert backend.requests[-1]["path"] == "/api/game/3/team/7/rank"


@pytest.mark.asyncio
async def test_team_leave():
    backend = TeamBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team leave") == 0
    assert backend.requests[-1]["method"] == "DELETE"
    assert backend.requests[-1]["path"] == "/api/game/3/team/self"
    # 已退出再 leave → 提示未加入
    assert await dispatch(app, "team leave") == 1
