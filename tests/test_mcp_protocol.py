"""MCP 协议层测试：dual-era 协商、tools/list、错误码与 stdout 纯净性。"""

import io
import json

import pytest

from ret2console.cli import _build_app
from ret2console.mcpserver.protocol import (
    LATEST_LEGACY,
    LEGACY_VERSIONS,
    META_SERVER_INFO,
    META_VERSION,
    MODERN_VERSIONS,
    McpProtocol,
    RpcError,
)
from ret2console.mcpserver.server import serve
from ret2console.mcpserver.tools import TOOLS


def _protocol() -> McpProtocol:
    return McpProtocol(TOOLS, app=_build_app(), instructions="test instructions")


def _request(method: str, params: dict | None = None, request_id: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _modern(method: str, params: dict | None = None) -> dict:
    body = dict(params or {})
    body["_meta"] = {META_VERSION: MODERN_VERSIONS[0]}
    return _request(method, body)


@pytest.mark.asyncio
async def test_initialize_echoes_supported_version():
    response = await _protocol().handle(_request(
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    ))
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {"listChanged": False}}
    assert result["serverInfo"]["name"] == "ret2console"
    assert result["instructions"] == "test instructions"


@pytest.mark.asyncio
async def test_initialize_unknown_version_falls_back():
    response = await _protocol().handle(_request(
        "initialize", {"protocolVersion": "1999-01-01"},
    ))
    assert response["result"]["protocolVersion"] == LATEST_LEGACY


@pytest.mark.asyncio
async def test_notification_is_silent():
    protocol = _protocol()
    assert await protocol.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    ) is None
    assert await protocol.handle(
        {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}
    ) is None


@pytest.mark.asyncio
async def test_modern_discovery():
    response = await _protocol().handle(_modern("server/discover"))
    result = response["result"]
    assert result["resultType"] == "complete"
    supported = result["supportedVersions"]
    assert MODERN_VERSIONS[0] in supported
    assert LEGACY_VERSIONS[-1] in supported
    assert result["ttlMs"] == 60000
    assert result["cacheScope"] == "private"
    assert result["_meta"][META_SERVER_INFO]["name"] == "ret2console"


@pytest.mark.asyncio
async def test_unsupported_modern_version():
    message = _modern("server/discover")
    message["params"]["_meta"][META_VERSION] = "2027-01-01"
    with pytest.raises(RpcError) as excinfo:
        await _protocol().handle(message)
    error = excinfo.value
    assert error.code == -32022
    assert error.data is not None
    assert set(error.data["supported"]) == {*MODERN_VERSIONS, *LEGACY_VERSIONS}
    assert error.data["requested"] == "2027-01-01"


@pytest.mark.asyncio
async def test_tools_list_legacy_and_modern_shapes():
    protocol = _protocol()
    legacy = (await protocol.handle(_request("tools/list")))["result"]
    assert set(legacy) == {"tools"}
    modern = (await protocol.handle(_modern("tools/list")))["result"]
    assert set(modern) == {"tools", "ttlMs", "cacheScope", "resultType", "_meta"}
    names = [tool["name"] for tool in legacy["tools"]]
    assert names == sorted(names)
    assert len(names) == len(set(names)) == len(TOOLS) == 33
    spec = next(t for t in legacy["tools"] if t["name"] == "r2c_context")
    assert set(spec) == {"name", "title", "description", "inputSchema", "annotations"}
    assert spec["inputSchema"] == {
        "type": "object", "properties": {}, "required": [], "additionalProperties": False,
    }


@pytest.mark.asyncio
async def test_unknown_tool_is_invalid_params():
    with pytest.raises(RpcError) as excinfo:
        await _protocol().handle(_request("tools/call", {"name": "nope", "arguments": {}}))
    assert excinfo.value.code == -32602
    assert "Unknown tool" in excinfo.value.message


@pytest.mark.asyncio
async def test_missing_required_argument():
    with pytest.raises(RpcError) as excinfo:
        await _protocol().handle(_request("tools/call", {"name": "r2c_read", "arguments": {}}))
    assert excinfo.value.code == -32602
    assert "path" in excinfo.value.message


@pytest.mark.asyncio
async def test_tool_error_becomes_is_error_result():
    app = _build_app()
    app.session.token = None
    protocol = McpProtocol(TOOLS, app=app)
    response = await protocol.handle(_request(
        "tools/call", {"name": "r2c_account_show", "arguments": {}},
    ))
    result = response["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["error"]["code"] == "unauthenticated"
    assert payload["error"]["status"] == 401
    assert "structuredContent" not in result


@pytest.mark.asyncio
async def test_modern_tools_call_carries_result_type():
    app = _build_app()
    app.ctx.set_challenge(3, 5)
    protocol = McpProtocol(TOOLS, app=app)
    message = _modern("tools/call", {"name": "r2c_context", "arguments": {}})
    result = (await protocol.handle(message))["result"]
    assert result["resultType"] == "complete"
    assert result["_meta"][META_SERVER_INFO]["version"]
    assert result["structuredContent"]["challenge_id"] == 5


@pytest.mark.asyncio
async def test_serve_writes_only_json_lines():
    requests = [
        _request("initialize", {"protocolVersion": "2025-06-18"}),
        _request("tools/list", {}, request_id=2),
    ]
    source = "\n".join(json.dumps(r) for r in requests) + "\n"
    sink = io.StringIO()
    app = _build_app()
    code = await serve(app, stdin=io.StringIO(source), stdout=sink)
    assert code == 0
    lines = sink.getvalue().splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["result"]["protocolVersion"] == "2025-06-18"
    assert any(t["name"] == "r2c_context" for t in second["result"]["tools"])


@pytest.mark.asyncio
async def test_serve_parse_and_batch_errors():
    source = "not json\n[]\n"
    sink = io.StringIO()
    code = await serve(_build_app(), stdin=io.StringIO(source), stdout=sink)
    assert code == 0
    parsed = [json.loads(line) for line in sink.getvalue().splitlines()]
    assert [p["error"]["code"] for p in parsed] == [-32700, -32600]


@pytest.mark.asyncio
async def test_serve_keeps_protocol_stream_pure(monkeypatch):
    """协议流用真实 stdout 时，工具内的杂散打印必须改道 stderr。"""
    import sys
    from dataclasses import replace

    from ret2console.mcpserver import server as server_mod

    sink = io.StringIO()
    errors = io.StringIO()
    monkeypatch.setattr(sys, "stdout", sink)
    monkeypatch.setattr(sys, "stderr", errors)
    noisy_name = TOOLS[0].name
    source = json.dumps(_request(
        "tools/call", {"name": noisy_name, "arguments": {}},
    )) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(source))

    async def noisy(_app, _args):
        print("stray console output")
        return {"ok": True}

    monkeypatch.setattr(server_mod, "TOOLS", [replace(TOOLS[0], handler=noisy)])
    assert await server_mod.serve(_build_app()) == 0

    lines = sink.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["result"]["structuredContent"] == {"ok": True}
    assert "stray console output" not in sink.getvalue()
    assert "stray console output" in errors.getvalue()
    # 协议期结束后 stdout 引用恢复（不残留为 stderr）
    assert sys.stdout is sink
