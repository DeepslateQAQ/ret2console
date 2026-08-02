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


@pytest.mark.asyncio
async def test_get_game_doc_decodes_json_string():
    """后端返回 Json(content)：响应体是 JSON 编码字符串，需解码为真实文本（\n 转义还原）。"""
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    client._client = httpx.AsyncClient(
        base_url="https://ctf.xidian.edu.cn/api",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json="# 比赛介绍\n\n欢迎参赛"))
    )
    assert await client.get_game_doc(3, "readme") == "# 比赛介绍\n\n欢迎参赛"


@pytest.mark.asyncio
async def test_get_game_doc_plain_text_fallback():
    """非 JSON 响应（纯文本）时原样返回，不做解码。"""
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    client._client = httpx.AsyncClient(
        base_url="https://ctf.xidian.edu.cn/api",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text="# 纯文本"))
    )
    assert await client.get_game_doc(3, "readme") == "# 纯文本"


class _FakeResp:
    def __init__(self, data):
        self._data = data
        self.status_code = 200
        self.headers = {}

    def json(self):
        return self._data


class _FakePagedClient:
    """记录请求路径与 page 参数，按页返回 `[items, total]`。"""

    def __init__(self, pages: list, total: int):
        self.pages = pages
        self.total = total
        self.requests: list[tuple[str, dict]] = []

    async def request(self, method, path, *, params=None, **kw):
        self.requests.append((path, dict(params or {})))
        page = int((params or {}).get("page", 1))
        items = self.pages[page - 1] if page <= len(self.pages) else []
        return _FakeResp([items, self.total])


def _install_paged_client(monkeypatch, pages, total):
    fake = _FakePagedClient(pages, total)
    monkeypatch.setattr("ret2console.api.client.httpx.AsyncClient", lambda *a, **kw: fake)
    return fake


@pytest.mark.asyncio
async def test_paginate_fetches_all_pages(monkeypatch):
    pages = [[{"id": i} for i in range(1, 101)], [{"id": i} for i in range(101, 201)], [{"id": i} for i in range(201, 251)]]
    fake = _install_paged_client(monkeypatch, pages, 250)
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    items = await client.list_challenges(22)
    assert len(items) == 250
    assert [p for _, p in fake.requests] == [
        {"page": 1, "page_size": 100}, {"page": 2, "page_size": 100}, {"page": 3, "page_size": 100},
    ]


@pytest.mark.asyncio
async def test_paginate_stops_at_exact_total(monkeypatch):
    pages = [[{"id": i} for i in range(101, 201)]]
    fake = _install_paged_client(monkeypatch, pages, 100)
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    items = await client.list_challenges(22)
    assert len(items) == 100
    assert len(fake.requests) == 1  # total=100 恰好一页，不再请求第二页


@pytest.mark.asyncio
async def test_paginate_falls_back_to_short_page(monkeypatch):
    """后端不返回 total（普通 list 响应）时，以「页不满」判定结束。"""
    pages = [[{"id": i} for i in range(1, 61)]]
    monkeypatch.setattr(
        "ret2console.api.client.httpx.AsyncClient",
        lambda *a, **kw: _FakePagedClient(pages, total=None),
    )
    client = ApiClient(base_url="https://ctf.xidian.edu.cn/api")
    items = await client.list_challenges(22)
    assert len(items) == 60
