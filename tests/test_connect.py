"""Challenge connection ownership and lifecycle tests."""

import httpx
import pytest

from ret2console import wsrx as wsrx_mod
from ret2console.cli import _build_app
from ret2console.repl import dispatch
from ret2console.wsrx import Wsrx

NANOID = "abc123def456ghi789jkl"


def _instance(traffic=NANOID, cid=5, ports=None):
    return {
        "state": "running",
        "traffic": traffic,
        "ports": ports or [8000],
        "challenge_id": cid,
        "challenge_name": "test",
        "game_id": 3,
        "game_name": "g",
    }


class InstanceBackend:
    def __init__(self, ports=None):
        self.ports = ports
        self.deleted = False

    def handler(self, request):
        path = request.url.path
        if request.method == "GET" and path == "/api/game/3/instance":
            return httpx.Response(200, json=[
                _instance(ports=self.ports),
                _instance("othernanoid123456789012", 6),
            ])
        if request.method == "DELETE" and path == "/api/game/3/challenge/5/instance":
            self.deleted = True
            return httpx.Response(200, json=None)
        if request.method == "POST" and path == "/api/game/3/challenge/5/instance":
            return httpx.Response(200, content=b"")
        return httpx.Response(404, json={"message": f"unexpected {request.method} {path}"})


class FakeEndpoint:
    def __init__(self, local, remote):
        self.local_addr = local
        self.websocket_url = remote
        self.shutdown_count = 0

    async def shutdown(self):
        self.shutdown_count += 1


def _app(backend):
    app = _build_app()
    app.api._client = httpx.AsyncClient(
        base_url="http://mock/api", transport=httpx.MockTransport(backend.handler)
    )
    app.api.configure(base_url="http://mock/api", token="jwt")
    app.wsrx = Wsrx()
    app.ctx.set_challenge(3, 5)
    return app


def fake_binder(monkeypatch, fail_at=None):
    endpoints = []
    calls = []

    async def bind(local, remote):
        calls.append((local, remote))
        if fail_at is not None and len(calls) == fail_at:
            raise RuntimeError("bind failed")
        endpoint = FakeEndpoint(f"127.0.0.1:{41000 + len(calls)}", remote)
        endpoints.append(endpoint)
        return endpoint

    monkeypatch.setattr(wsrx_mod, "_bind_client_endpoint", bind)
    return calls, endpoints


@pytest.mark.asyncio
async def test_connect_filters_player_instance_and_prints_actual_local(monkeypatch, capsys):
    calls, _ = fake_binder(monkeypatch)
    app = _app(InstanceBackend())
    assert await dispatch(app, "challenge connect") == 0
    assert calls == [("127.0.0.1:0", f"ws://mock/api/traffic/{NANOID}?port=8000")]
    assert "127.0.0.1:41001" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_connect_skips_exact_existing_remote(monkeypatch):
    calls, _ = fake_binder(monkeypatch)
    app = _app(InstanceBackend())
    remote = f"ws://mock/api/traffic/{NANOID}?port=8000"
    await app.wsrx.add(remote, game_id=3, challenge_id=5)
    assert await dispatch(app, "challenge connect") == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_disconnect_and_stop_shutdown_owned_endpoints(monkeypatch):
    _, endpoints = fake_binder(monkeypatch)
    backend = InstanceBackend()
    app = _app(backend)
    assert await dispatch(app, "challenge connect") == 0
    assert await dispatch(app, "challenge disconnect") == 0
    assert endpoints[0].shutdown_count == 1
    assert await dispatch(app, "challenge connect") == 0
    assert await dispatch(app, "challenge stop") == 0
    assert endpoints[1].shutdown_count == 1
    assert backend.deleted


@pytest.mark.asyncio
async def test_multi_port_failure_rolls_back_new_endpoints(monkeypatch):
    _, endpoints = fake_binder(monkeypatch, fail_at=2)
    app = _app(InstanceBackend(ports=[8000, 9000]))
    assert await dispatch(app, "challenge connect") == 1
    assert endpoints[0].shutdown_count == 1
    assert app.wsrx.pool_instances() == []


@pytest.mark.asyncio
async def test_start_ok_with_empty_body():
    app = _app(InstanceBackend())
    assert await dispatch(app, "challenge start") == 0
