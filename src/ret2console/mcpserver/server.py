"""MCP stdio 服务：逐行 JSON-RPC，stdout 只允许 MCP 报文（SPEC §19）。"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from ret2console.mcpserver.protocol import McpProtocol, RpcError
from ret2console.mcpserver.tools import INSTRUCTIONS, TOOLS


def _reconfigure(stream: TextIO) -> None:
    """统一 UTF-8 + LF，避免 Windows cp1252/CRLF 破坏 JSONL。"""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (ValueError, OSError):
        pass


def _log(method: Any, tool: Any) -> None:
    """verbose 日志只记方法名/工具名，绝不含 arguments（flag/token 不进日志）。"""
    suffix = f" [{tool}]" if isinstance(tool, str) and tool else ""
    print(f"[r2c-mcp] {method}{suffix}", file=sys.stderr, flush=True)


async def serve(
    app: Any, *, stdin: TextIO | None = None, stdout: TextIO | None = None, verbose: bool = False,
) -> int:
    """stdio JSON-RPC 循环。

    使用真实 stdout 作协议流时，进程内其余写入（Rich console 动态解析
    `sys.stdout`、`show_image` 的终端转义、意外 `print`）在此重定向到 stderr，
    保证协议流只有 JSON 行；传入显式 `stdout`（测试/嵌入）时不做全局替换。
    """
    real_stdout = sys.stdout
    source = stdin if stdin is not None else sys.stdin
    sink = stdout if stdout is not None else real_stdout
    _reconfigure(source)
    _reconfigure(sink)
    redirect_stdout = stdout is None
    if redirect_stdout:
        sys.stdout = sys.stderr
    protocol = McpProtocol(TOOLS, app=app, instructions=INSTRUCTIONS)
    try:
        while True:
            line = source.readline()
            if not line:
                return 0
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                response: dict[str, Any] | None = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "Parse error"},
                }
            else:
                if isinstance(msg, list):
                    # MCP 不使用 JSON-RPC batch
                    response = {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32600, "message": "Invalid Request: batch not supported"},
                    }
                else:
                    request_id = msg.get("id") if isinstance(msg, dict) else None
                    if verbose and isinstance(msg, dict):
                        params = msg.get("params")
                        name = params.get("name") if isinstance(params, dict) else None
                        _log(msg.get("method"), name)
                    try:
                        response = await protocol.handle(msg)
                    except RpcError as e:
                        error: dict[str, Any] = {"code": e.code, "message": e.message}
                        if e.data is not None:
                            error["data"] = e.data
                        response = {"jsonrpc": "2.0", "id": request_id, "error": error}
            if response is not None:
                sink.write(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                sink.flush()
    except (KeyboardInterrupt, EOFError, BrokenPipeError):
        return 0
    finally:
        if redirect_stdout:
            sys.stdout = real_stdout
        await app.api.close()
