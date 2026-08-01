"""challenge connect/disconnect/stop 隧道测试（SPEC §13.4，HTTP 控制）。"""

import json

import httpx
import pytest

from ret2console.cli import _build_app
from ret2console.repl import dispatch
from ret2console.wsrx import TUNNEL_FILE, Wsrx

NANOID = "abc123def456ghi789jkl"


def _instance(traffic: str = NANOID, cid: int = 5, ports: list[int] | None = None) -> dict:
    return {
        "state": "running",
        "name": "pod-1",
        "traffic": traffic,
        "ports": ports or [8000],
        "renew_count": 0,
        "created_at": 1720000000,
        "user_id": 1,
        "user_name": "alice",
        "team_id": 0,
        "team_name": "",
        "challenge_id": cid,
        "challenge_name": "test",
        "game_id": 3,
        "game_name": "g",
    }


class InstanceBackend:
    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/api/game/3/instance":
            # 选手侧接口：返回全部实例，connect 需要按 challenge_id 过滤
            return httpx.Response(200, json=[
                _instance(),
                _instance(traffic="othernanoid123456789012", cid=6),
            ])
        if request.method == "DELETE" and path == "/api/game/3/challenge/5/instance":
            return httpx.Response(200, json=None)
        if request.method == "POST" and path == "/api/game/3/challenge/5/instance":
            # start_challenge_instance 成功返回 Ok(())：空 body
            return httpx.Response(200, content=b"")
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


class FakePool:
    """模拟 wsrx daemon/desktop 的 /pool HTTP API。"""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.deletes: list[dict] = []
        self.pool: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/pool"):
            body = json.loads(request.read().decode())
            self.posts.append(body)
            self.pool.append({"local": body["local"], "remote": body["remote"]})
            return httpx.Response(200, json=body)
        if request.method == "DELETE" and path.endswith("/pool"):
            body = json.loads(request.read().decode())
            self.deletes.append(body)
            self.pool = [p for p in self.pool if p["local"] != body["key"]]
            return httpx.Response(200, json=None)
        if request.method == "GET" and path.endswith("/pool"):
            return httpx.Response(200, json=self.pool)
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


def _app(backend: InstanceBackend, wsrx: Wsrx):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler),
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.wsrx = wsrx
    app.ctx.set_challenge(3, 5)
    return app


@pytest.mark.asyncio
async def test_connect_requires_wsrx():
    app = _app(InstanceBackend(), Wsrx("none"))
    assert await dispatch(app, "challenge connect") == 1


@pytest.mark.asyncio
async def test_connect_daemon_mode_posts_pool(monkeypatch):
    """daemon 模式：POST /pool 建隧道、写 tunnel.json、立即返回（不前台阻塞）。"""
    pool = FakePool()
    from ret2console import wsrx as wsrx_mod
    monkeypatch.setattr(wsrx_mod, "_pool_client", lambda base, headers: httpx.Client(
        base_url=base, transport=httpx.MockTransport(pool), headers=headers,
    ))
    app = _app(InstanceBackend(), Wsrx("daemon", api_port=8899, secret="s3cret"))
    assert await dispatch(app, "challenge connect") == 0
    assert len(pool.posts) == 1
    body = pool.posts[0]
    assert body["remote"] == f"ws://mock/api/traffic/{NANOID}?port=8000"
    assert body["local"].startswith("127.0.0.1:")
    # 只给当前题目建隧道（过滤掉 cid=6 的实例）
    assert "othernanoid" not in body["remote"]
    records = json.loads(TUNNEL_FILE.read_text())
    assert len(records) == 1
    rec = next(iter(records.values()))
    assert rec["game_id"] == 3 and rec["challenge_id"] == 5


@pytest.mark.asyncio
async def test_connect_desktop_mode_posts_pool_with_origin(monkeypatch):
    """desktop 模式：POST /pool 带 Origin 控制源头 + label。"""
    pool = FakePool()
    from ret2console import wsrx as wsrx_mod
    monkeypatch.setattr(wsrx_mod, "_pool_client", lambda base, headers: httpx.Client(
        base_url=base, transport=httpx.MockTransport(pool), headers=headers,
    ))
    app = _app(InstanceBackend(), Wsrx("desktop", api_port=8899))
    assert await dispatch(app, "challenge connect") == 0
    body = pool.posts[0]
    assert body["remote"] == f"ws://mock/api/traffic/{NANOID}?port=8000"
    assert body["label"]
    assert body["local"].startswith("127.0.0.1:")


@pytest.mark.asyncio
async def test_connect_skips_existing_tunnel(monkeypatch):
    pool = FakePool()
    pool.pool = [{"local": "127.0.0.1:39947", "remote": f"ws://mock/api/traffic/{NANOID}?port=8000"}]
    from ret2console import wsrx as wsrx_mod
    monkeypatch.setattr(wsrx_mod, "_pool_client", lambda base, headers: httpx.Client(
        base_url=base, transport=httpx.MockTransport(pool), headers=headers,
    ))
    app = _app(InstanceBackend(), Wsrx("daemon", api_port=8899))
    assert await dispatch(app, "challenge connect") == 0
    assert pool.posts == []


@pytest.mark.asyncio
async def test_disconnect_closes_tunnels(monkeypatch):
    pool = FakePool()
    records = {
        f"{NANOID}:8000": {"port": 39947, "pid": None, "game_id": 3, "challenge_id": 5},
        "legacynanoid1234567890123:8000": {"port": 39948, "pid": None, "game_id": 3, "challenge_id": 6},
    }
    TUNNEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_FILE.write_text(json.dumps(records))
    from ret2console import wsrx as wsrx_mod
    monkeypatch.setattr(wsrx_mod, "_pool_client", lambda base, headers: httpx.Client(
        base_url=base, transport=httpx.MockTransport(pool), headers=headers,
    ))
    app = _app(InstanceBackend(), Wsrx("daemon", api_port=8899))
    # 只断开当前题目 5
    assert await dispatch(app, "challenge disconnect") == 0
    assert len(pool.deletes) == 1
    assert pool.deletes[0]["key"] == "127.0.0.1:39947"
    remaining = json.loads(TUNNEL_FILE.read_text())
    assert list(remaining) == ["legacynanoid1234567890123:8000"]


@pytest.mark.asyncio
async def test_stop_deletes_instance():
    """challenge stop 走 DELETE /game/{gid}/challenge/{cid}/instance。"""
    app = _app(InstanceBackend(), Wsrx("none"))
    assert await dispatch(app, "challenge stop") == 0


@pytest.mark.asyncio
async def test_start_ok_with_empty_body():
    """challenge start 成功时后端返回空 body（Ok(())），不得 JSONDecodeError。"""
    app = _app(InstanceBackend(), Wsrx("none"))
    assert await dispatch(app, "challenge start") == 0
