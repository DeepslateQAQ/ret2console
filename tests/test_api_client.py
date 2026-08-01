"""ApiClient 基础行为测试（平台信息等）。"""

import httpx
import pytest

from ret2console.api.client import ApiClient


def _fake_async_client(monkeypatch, resp_factory):
    """替换 httpx.AsyncClient：platform_name 的独立短超时请求。"""
    captured = {}

    class FakeClient:
        def __init__(self, *a, **kw):
            captured["kwargs"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, path):
            captured["path"] = path
            return resp_factory()

    monkeypatch.setattr("ret2console.api.client.httpx.AsyncClient", FakeClient)
    return captured


@pytest.mark.asyncio
async def test_platform_name_uses_server_name(monkeypatch):
    captured = _fake_async_client(
        monkeypatch,
        lambda: type("R", (), {
            "status_code": 200,
            "json": lambda self: {"name": "西电 CTF 终端", "external_domain": "ctf.xidian.edu.cn"},
        })(),
    )
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    assert await client.platform_name() == "西电 CTF 终端"
    assert captured["path"] == "/platform/info"
    assert captured["kwargs"]["timeout"] == 4.0  # 短超时，避免平台不可达阻塞启动


@pytest.mark.asyncio
async def test_platform_name_none_when_unconfigured(monkeypatch):
    _fake_async_client(monkeypatch, lambda: type("R", (), {"status_code": 200, "json": lambda self: {"name": None}})())
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    assert await client.platform_name() is None


@pytest.mark.asyncio
async def test_platform_name_none_on_http_error(monkeypatch):
    _fake_async_client(monkeypatch, lambda: type("R", (), {"status_code": 404, "json": lambda self: {"message": "no"}})())
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    assert await client.platform_name() is None


@pytest.mark.asyncio
async def test_platform_name_none_on_network_error(monkeypatch):
    def boom():
        raise httpx.ConnectError("unreachable")

    _fake_async_client(monkeypatch, boom)
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    assert await client.platform_name() is None

