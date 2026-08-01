"""admin 命令测试：bulletin/user/game/challenge/platform/cluster（SPEC §16）。"""

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch


class AdminBackend:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.users = [
            {"id": 1, "account": "alice", "nickname": "爱丽丝", "email": "alice@x.edu.cn",
             "permissions": ["game"], "hidden": False, "banned": False},
            {"id": 2, "account": "bob", "nickname": "鲍勃", "email": "bob@x.edu.cn",
             "permissions": ["verified"], "hidden": False, "banned": True},
        ]
        self.bulletins = [{"id": 9, "title": "赛前通知", "content": "注意防作弊", "draft": False, "updated_at": 1700000000}]
        self.game = {"id": 3, "name": "测试赛", "host_type": "Game", "hidden": True, "team_size": 1}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        self.requests.append({"method": method, "path": path, "query": str(request.url.params), "body": request.read().decode()})
        if path == "/api/bulletin" and method == "POST":
            return httpx.Response(200, json={"id": 10, "title": "新公告"})
        if path.startswith("/api/bulletin/"):
            return httpx.Response(200, json=self.bulletins[0])
        if path == "/api/bulletin":
            return httpx.Response(200, json=[self.bulletins, 1])
        if path == "/api/user" and method == "GET":
            return httpx.Response(200, json=[self.users, 2])
        if path == "/api/user/1" and method == "GET":
            return httpx.Response(200, json=self.users[0])
        if path == "/api/user/1" and method == "PATCH":
            body = httpx.Response(200, json=self.users[0])
            return body
        if path == "/api/user/1/ip":
            return httpx.Response(200, json=[{"ip": "1.2.3.4", "updated_at": 1700000000}])
        if path == "/api/game" and method == "POST":
            return httpx.Response(200, json={"id": 4, "name": "新赛"})
        if path == "/api/game/3" and method == "GET":
            return httpx.Response(200, json=self.game)
        if path == "/api/game/3" and method == "PATCH":
            return httpx.Response(200, json=self.game)
        if path == "/api/game/3/token":
            return httpx.Response(200, json={"token": "new-token"})
        if path == "/api/game/3/administrator" and method == "GET":
            return httpx.Response(200, json=self.users[:1])
        if path == "/api/game/3/administrator" and method == "PATCH":
            return httpx.Response(200, json=self.game)
        if path == "/api/game/3/submission":
            return httpx.Response(200, json=[[], 0])
        if path == "/api/game/3/audit" and method == "GET":
            return httpx.Response(200, json=[[{
                "id": 11, "created_at": 1700000000, "reason": "提交异常", "challenge_id": 5,
                "user_id": 1, "team_id": 7, "game_id": 3, "state": 0,
            }], 1])
        if path == "/api/game/3/audit/11" and method == "PATCH":
            return httpx.Response(200, json={})
        if path == "/api/game/3/statistics":
            return httpx.Response(200, json={"users": {"total": 10}})
        if path == "/api/game/3/challenge" and method == "POST":
            return httpx.Response(200, json={"id": 5, "name": "新题"})
        if path == "/api/game/3/challenge":
            return httpx.Response(200, json=[[], 0])
        if path == "/api/game/3/challenge/5" and method == "GET":
            return httpx.Response(200, json={"id": 5, "name": "题一", "score": 500, "content": "题面"})
        if path == "/api/game/3/challenge/5" and method == "PATCH":
            return httpx.Response(200, json={"id": 5, "name": "题一"})
        if path == "/api/game/3/challenge/5/publish":
            return httpx.Response(200, json={})
        if path == "/api/game/3/challenge/5/answer" and method == "GET":
            return httpx.Response(200, json="flag{old}")
        if path == "/api/game/3/challenge/5/answer" and method == "PATCH":
            return httpx.Response(200, json="flag{new}")
        if path == "/api/game/3/challenge/5/submission":
            return httpx.Response(200, json=[[], 0])
        if path == "/api/game/3/challenge/5/instance":
            return httpx.Response(200, json=[])
        if path == "/api/platform/statistics":
            return httpx.Response(200, json={"games": {"total": 1}})
        if path == "/api/cluster/node":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": f"unexpected {method} {path}"})


def _app_with_admin(backend: AdminBackend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.session.token = "jwt"
    app.session.permissions = ["game", "host"]
    return app


@pytest.mark.asyncio
async def test_admin_requires_login():
    backend = AdminBackend()
    app = _build_app()
    assert await dispatch(app, "admin bulletin list") == 1
    assert not backend.requests


@pytest.mark.asyncio
async def test_admin_requires_admin_permission():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    app.session.permissions = ["verified"]
    assert await dispatch(app, "admin user list") == 1
    assert not backend.requests


@pytest.mark.asyncio
async def test_admin_unknown_group():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin bogus") == 1


@pytest.mark.asyncio
async def test_admin_bulletin_create():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin bulletin create 赛前通知 注意防作弊") == 0
    req = backend.requests[-1]
    assert req["method"] == "POST" and req["path"] == "/api/bulletin"
    assert '"title": "赛前通知"' in req["body"] or '"title":"赛前通知"' in req["body"]
    assert '"access_policy": 0' in req["body"] or '"access_policy":0' in req["body"]  # ActiveEnum i32
    assert "published" in req["body"]


@pytest.mark.asyncio
async def test_admin_bulletin_delete():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin bulletin delete 9") == 0
    assert backend.requests[-1]["method"] == "DELETE"
    assert backend.requests[-1]["path"] == "/api/bulletin/9"


@pytest.mark.asyncio
async def test_admin_user_update_keeps_email():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin user update 1 nickname=阿狸 banned=true") == 0
    req = backend.requests[-1]
    assert req["method"] == "PATCH" and req["path"] == "/api/user/1"
    assert "alice@x.edu.cn" in req["body"]
    assert "registered_at" in req["body"]  # 无 default 字段必须回传
    assert '"password": null' in req["body"] or '"password":null' in req["body"]
    assert '"nickname": "阿狸"' in req["body"] or '"nickname":"阿狸"' in req["body"]


@pytest.mark.asyncio
async def test_admin_game_create_defaults():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin game create name=测试赛 start_at=2026-08-02 10:00") == 0
    req = backend.requests[-1]
    assert req["method"] == "POST" and req["path"] == "/api/game"
    assert '"name": "测试赛"' in req["body"] or '"name":"测试赛"' in req["body"]
    assert '"host_type": 1' in req["body"] or '"host_type":1' in req["body"]
    assert '"admins": []' in req["body"] or '"admins":[]' in req["body"]  # 反序列化必需


@pytest.mark.asyncio
async def test_admin_game_token_and_audit():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin game token 3") == 0
    assert backend.requests[-1]["path"] == "/api/game/3/token"
    assert await dispatch(app, "admin game audit 3 11 通过 违规封禁") == 0
    req = backend.requests[-1]
    assert req["method"] == "PATCH" and req["path"] == "/api/game/3/audit/11"
    assert '"state":2' in req["body"]
    assert "created_at" in req["body"]  # 合并现状字段，避免被覆盖为默认值


@pytest.mark.asyncio
async def test_admin_challenge_answer_and_publish():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin challenge answer 5 flag{new} 3") == 0
    req = backend.requests[-1]
    assert req["method"] == "PATCH" and req["path"] == "/api/game/3/challenge/5/answer"
    assert '"flag{new}"' in req["body"]  # 裸 JSON 字符串
    assert await dispatch(app, "admin challenge publish 5 3") == 0
    assert backend.requests[-1]["method"] == "POST"
    assert backend.requests[-1]["path"] == "/api/game/3/challenge/5/publish"


@pytest.mark.asyncio
async def test_admin_challenge_uses_game_context():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    app.ctx.set_game(3)
    assert await dispatch(app, "admin challenge show 5") == 0
    assert backend.requests[-1]["path"] == "/api/game/3/challenge/5"


@pytest.mark.asyncio
async def test_admin_platform_and_cluster():
    backend = AdminBackend()
    app = _app_with_admin(backend)
    assert await dispatch(app, "admin platform stats") == 0
    assert backend.requests[-1]["path"] == "/api/platform/statistics"
    assert await dispatch(app, "admin cluster nodes") == 0
    assert backend.requests[-1]["path"] == "/api/cluster/node"
