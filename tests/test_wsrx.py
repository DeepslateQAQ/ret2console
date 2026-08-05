import asyncio
import socket
import time

import libwsrx
import pytest

from ret2console import wsrx as wsrx_mod
from ret2console.wsrx import Wsrx, wsrx_link


class FakeEndpoint:
    def __init__(self, local_addr, websocket_url, shutdown_error=None):
        self.local_addr = local_addr
        self.websocket_url = websocket_url
        self.shutdown_error = shutdown_error
        self.shutdown_count = 0

    async def shutdown(self):
        self.shutdown_count += 1
        if self.shutdown_error is not None:
            raise self.shutdown_error


@pytest.mark.asyncio
async def test_bind_retains_lists_and_reuses_exact_remote(monkeypatch):
    calls = []
    endpoint = FakeEndpoint("127.0.0.1:41001", "ws://remote/one")

    async def bind(local, remote):
        calls.append((local, remote))
        return endpoint

    monkeypatch.setattr(wsrx_mod, "_bind_client_endpoint", bind)
    manager = Wsrx()
    assert await manager.add("ws://remote/one", game_id=3, challenge_id=5) == "127.0.0.1:41001"
    assert await manager.add("ws://remote/one", game_id=3, challenge_id=5) == "127.0.0.1:41001"
    assert calls == [("127.0.0.1:0", "ws://remote/one")]
    assert manager.pool_instances() == [
        {"local": "127.0.0.1:41001", "remote": "ws://remote/one"}
    ]


@pytest.mark.asyncio
async def test_remove_is_independent_and_removes_state_before_failure(monkeypatch):
    endpoints = [
        FakeEndpoint("127.0.0.1:41001", "ws://remote/one", RuntimeError("shutdown")),
        FakeEndpoint("127.0.0.1:41002", "ws://remote/two"),
    ]

    async def bind(local, remote):
        return endpoints.pop(0)

    monkeypatch.setattr(wsrx_mod, "_bind_client_endpoint", bind)
    manager = Wsrx()
    first = await manager.add("ws://remote/one", game_id=3, challenge_id=5)
    await manager.add("ws://remote/two", game_id=3, challenge_id=6)
    with pytest.raises(RuntimeError, match="shutdown"):
        await manager.remove(first)
    assert manager.pool_instances() == [
        {"local": "127.0.0.1:41002", "remote": "ws://remote/two"}
    ]


@pytest.mark.asyncio
async def test_remove_for_and_close_attempt_every_endpoint(monkeypatch):
    created = []

    async def bind(local, remote):
        endpoint = FakeEndpoint(
            f"127.0.0.1:{41001 + len(created)}",
            remote,
            RuntimeError("ignored") if len(created) == 0 else None,
        )
        created.append(endpoint)
        return endpoint

    monkeypatch.setattr(wsrx_mod, "_bind_client_endpoint", bind)
    manager = Wsrx()
    await manager.add("ws://remote/one", game_id=3, challenge_id=5)
    await manager.add("ws://remote/two", game_id=3, challenge_id=6)
    await manager.add("ws://remote/three", game_id=4, challenge_id=5)
    assert await manager.remove_for(game_id=3, challenge_id=5) == 1
    assert created[0].shutdown_count == 1
    await manager.close()
    await manager.close()
    assert [endpoint.shutdown_count for endpoint in created] == [1, 1, 1]
    assert manager.pool_instances() == []


def test_wsrx_link_is_unchanged():
    assert wsrx_link("https://ctf.example/api", "traffic", 80) == (
        "wss://ctf.example/api/traffic/traffic?port=80"
    )
    assert wsrx_link("http://localhost:8080/api", "traffic", 443) == (
        "ws://localhost:8080/api/traffic/traffic?port=443"
    )


def reserve_loopback_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


async def wait_for_banner(local_addr, banner):
    host, port = local_addr.rsplit(":", 1)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.open_connection(host, int(port))
            if await asyncio.wait_for(reader.readexactly(len(banner)), 0.5) == banner:
                return reader, writer
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.TimeoutError):
            await asyncio.sleep(0.02)
    raise AssertionError("endpoint did not become ready")


@pytest.mark.asyncio
async def test_end_to_end_manager_transport_and_shutdown():
    banner = b"RET2CONSOLE\n"

    async def echo(reader, writer):
        try:
            writer.write(banner)
            await writer.drain()
            while chunk := await reader.read(32 * 1024):
                writer.write(chunk)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    target = await asyncio.start_server(echo, "127.0.0.1", 0)
    target_port = target.sockets[0].getsockname()[1]
    websocket_port = reserve_loopback_port()
    server = asyncio.ensure_future(
        libwsrx.run_server(
            f"127.0.0.1:{websocket_port}", f"127.0.0.1:{target_port}"
        )
    )
    manager = Wsrx()
    writer = None
    try:
        local = await manager.add(
            f"ws://127.0.0.1:{websocket_port}", game_id=1, challenge_id=2
        )
        reader, writer = await wait_for_banner(local, banner)
        payload = bytes(range(256)) * 300 + b"END"
        assert len(payload) > 65_536
        writer.write(payload)
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(payload)), 2) == payload
        await manager.close()
        host, port = local.rsplit(":", 1)
        with pytest.raises((ConnectionError, asyncio.TimeoutError)):
            await asyncio.wait_for(asyncio.open_connection(host, int(port)), 1)
    finally:
        await manager.close()
        server.cancel()
        await asyncio.gather(server, return_exceptions=True)
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        target.close()
        await target.wait_closed()
