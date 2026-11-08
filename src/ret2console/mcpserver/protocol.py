"""MCP 协议层：dual-era JSON-RPC（传统 initialize 握手 + 现代 `_meta` 无状态，SPEC §19）。

不依赖第三方 SDK：仅标准库，`handle()` 负责版本协商与工具调用，`server.py`
负责 stdio 帧读写。
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Sequence

from ret2console import __version__
from ret2console.api.client import ApiError
from ret2console.service import ServiceError
from ret2console.vfs import VfsError

LEGACY_VERSIONS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")
MODERN_VERSIONS = ("2026-07-28",)
LATEST_LEGACY = "2025-11-25"
META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

CAPABILITIES: dict[str, Any] = {"tools": {"listChanged": False}}


class RpcError(Exception):
    """JSON-RPC 错误：code/message/data 直接进响应体。"""

    def __init__(self, code: int, message: str, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


class McpProtocol:
    """协议状态机：工具表 + 服务器信息 + 当前 App。"""

    def __init__(
        self, tools: Sequence[Any], app: Any, *,
        server_name: str = "ret2console", server_version: str = __version__,
        instructions: str = "",
    ) -> None:
        self.tools = {tool.name: tool for tool in tools}
        self.app = app
        self.server_name = server_name
        self.server_version = server_version
        self.instructions = instructions

    # ---- 响应骨架 ----

    @property
    def _server_info(self) -> dict[str, str]:
        return {"name": self.server_name, "version": self.server_version}

    def tool_specs(self) -> list[dict[str, Any]]:
        """按名称排序的工具描述（不输出内部 handler）。"""
        return [
            {
                "name": tool.name,
                "title": tool.title,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "annotations": tool.annotations,
            }
            for tool in sorted(self.tools.values(), key=lambda t: t.name)
        ]

    async def handle(self, msg: Any) -> dict[str, Any] | None:
        """处理一条 JSON-RPC 消息；通知返回 None，请求返回完整响应体。"""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
            raise RpcError(-32600, "Invalid Request")
        if "id" not in msg:
            # 通知（initialized/cancelled 及未知通知）一律静默
            return None
        request_id = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(params, dict):
            raise RpcError(-32602, "Invalid params")

        meta = params.get("_meta")
        version = meta.get(META_VERSION) if isinstance(meta, dict) else None
        if version is not None:
            if version not in MODERN_VERSIONS:
                raise RpcError(
                    -32022, "Unsupported protocol version",
                    {
                        "supported": [*MODERN_VERSIONS, *LEGACY_VERSIONS],
                        "requested": version,
                    },
                )
            result = await self._modern(str(method), params)
            result = {
                **result,
                "resultType": "complete",
                "_meta": {META_SERVER_INFO: self._server_info},
            }
        else:
            result = await self._legacy(str(method), params)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    # ---- 传统分支（initialize 握手） ----

    async def _legacy(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            requested = params.get("protocolVersion")
            chosen = requested if requested in LEGACY_VERSIONS else LATEST_LEGACY
            return {
                "protocolVersion": chosen,
                "capabilities": CAPABILITIES,
                "serverInfo": self._server_info,
                "instructions": self.instructions,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tool_specs()}
        if method == "tools/call":
            return await self._call_tool(params)
        raise RpcError(-32601, f"Method not found: {method}")

    # ---- 现代分支（无状态 `_meta`） ----

    async def _modern(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "server/discover":
            return {
                "supportedVersions": [*MODERN_VERSIONS, *LEGACY_VERSIONS],
                "capabilities": CAPABILITIES,
                "instructions": self.instructions,
                "ttlMs": 60000,
                "cacheScope": "private",
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.tool_specs(), "ttlMs": 60000, "cacheScope": "private"}
        if method == "tools/call":
            return await self._call_tool(params)
        raise RpcError(-32601, f"Method not found: {method}")

    # ---- 工具调用 ----

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = self.tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise RpcError(-32602, f"Unknown tool: {name}")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise RpcError(-32602, "Invalid arguments: 需要 JSON 对象")
        for key in tool.input_schema.get("required", []):
            if arguments.get(key) is None:
                raise RpcError(-32602, f"Missing required argument: {key}")
        try:
            data = await tool.handler(self.app, arguments)
        except ServiceError as e:
            return _tool_error(e.message, e.code, e.status, e.data)
        except ApiError as e:
            code = "unauthenticated" if e.status == 401 else str(e.code or "api_error")
            return _tool_error(e.message, code, e.status, None)
        except VfsError as e:
            return _tool_error(str(e), "vfs", 404, None)
        except Exception as e:  # 工具异常不向 stdout 写堆栈
            return _tool_error(f"{type(e).__name__}: {e}", "internal_error", None, None)
        return {
            "content": [{"type": "text", "text": _dump(data)}],
            "structuredContent": data,
            "isError": False,
        }


Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]


def _dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _tool_error(
    message: str, code: str, status: int | None, data: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = {"error": {"code": code, "message": message, "status": status, "data": data}}
    return {"content": [{"type": "text", "text": _dump(payload)}], "isError": True}
