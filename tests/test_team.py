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


class RulesBackend(TeamBackend):
    """在 TeamBackend 基础上让 doc/rules 可用（触发参赛规则确认）。"""

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/doc/rules"):
            self.requests.append({"method": request.method, "path": request.url.path, "body": ""})
            return httpx.Response(200, json="# 参赛规则\n\n禁止作弊")
        return super().handler(request)


def _team_writes(backend: TeamBackend) -> list[dict]:
    return [r for r in backend.requests if r["method"] in ("POST", "PATCH", "DELETE")]


@pytest.mark.asyncio
async def test_team_create_shows_rules_and_requires_yes(capsys):
    backend = RulesBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team create 星尘") == 1
    out = capsys.readouterr().out
    assert "禁止作弊" in out
    assert "需要 --yes" in out
    assert _team_writes(backend) == []


@pytest.mark.asyncio
async def test_team_create_with_yes_skips_confirm(capsys):
    backend = RulesBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team create 星尘 --yes") == 0
    assert [r["method"] for r in _team_writes(backend)] == ["POST"]
    assert "禁止作弊" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_team_join_with_yes():
    backend = RulesBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "team join tok --yes") == 0
    assert [r["method"] for r in _team_writes(backend)] == ["PATCH"]


@pytest.mark.asyncio
async def test_team_create_tty_confirm_cancel(monkeypatch):
    from ret2console.commands import team as team_mod

    backend = RulesBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    monkeypatch.setattr(team_mod, "is_tty", lambda: True)

    async def _cancel(_question: str) -> bool:
        return False

    monkeypatch.setattr(team_mod, "confirm", _cancel)
    assert await dispatch(app, "team create 星尘") == 0
    assert _team_writes(backend) == []


@pytest.mark.asyncio
async def test_team_create_tty_confirm_accept(monkeypatch):
    from ret2console.commands import team as team_mod

    backend = RulesBackend()
    app = _app_with_backend(backend)
    app.ctx.set_game(3)
    monkeypatch.setattr(team_mod, "is_tty", lambda: True)
    asked: list[str] = []

    async def _accept(question: str) -> bool:
        asked.append(question)
        return True

    monkeypatch.setattr(team_mod, "confirm", _accept)
    assert await dispatch(app, "team create 星尘") == 0
    assert asked and "创建队伍" in asked[0]
    assert [r["method"] for r in _team_writes(backend)] == ["POST"]
