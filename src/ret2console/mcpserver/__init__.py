"""MCP 接入（SPEC §19）：纯标准库 dual-era 协议实现。"""

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
from ret2console.mcpserver.tools import INSTRUCTIONS, TOOLS, Tool

__all__ = [
    "INSTRUCTIONS",
    "LATEST_LEGACY",
    "LEGACY_VERSIONS",
    "META_SERVER_INFO",
    "META_VERSION",
    "MODERN_VERSIONS",
    "McpProtocol",
    "RpcError",
    "TOOLS",
    "Tool",
    "serve",
]
