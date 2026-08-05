"""In-process libwsrx endpoint ownership for challenge tunnels."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import libwsrx


async def _bind_client_endpoint(local_addr: str, remote: str) -> Any:
    """Bind through a Python seam so tests can substitute native endpoints."""
    return await libwsrx.ClientEndpoint.bind(local_addr, remote)


@dataclass(frozen=True)
class _Tunnel:
    endpoint: Any
    remote: str
    local: str
    game_id: int
    challenge_id: int


class Wsrx:
    """Application-lifetime owner of retained libwsrx client endpoints."""

    def __init__(self) -> None:
        self._tunnels: dict[str, _Tunnel] = {}

    def pool_instances(self) -> list[dict[str, str]]:
        return [
            {"local": tunnel.local, "remote": tunnel.remote}
            for tunnel in self._tunnels.values()
        ]

    async def add(self, remote: str, *, game_id: int, challenge_id: int) -> str:
        existing = self._tunnels.get(remote)
        if existing is not None:
            return existing.local
        endpoint = await _bind_client_endpoint("127.0.0.1:0", remote)
        local = str(endpoint.local_addr)
        self._tunnels[remote] = _Tunnel(
            endpoint=endpoint,
            remote=remote,
            local=local,
            game_id=game_id,
            challenge_id=challenge_id,
        )
        return local

    async def remove(self, local: str) -> None:
        selected = next(
            ((remote, tunnel) for remote, tunnel in self._tunnels.items() if tunnel.local == local),
            None,
        )
        if selected is None:
            return
        remote, tunnel = selected
        del self._tunnels[remote]
        result = (await asyncio.gather(tunnel.endpoint.shutdown(), return_exceptions=True))[0]
        if isinstance(result, BaseException):
            raise result

    async def remove_for(self, *, game_id: int, challenge_id: int | None = None) -> int:
        selected = [
            (remote, tunnel)
            for remote, tunnel in self._tunnels.items()
            if tunnel.game_id == game_id
            and (challenge_id is None or tunnel.challenge_id == challenge_id)
        ]
        for remote, _ in selected:
            del self._tunnels[remote]
        if selected:
            await asyncio.gather(
                *(tunnel.endpoint.shutdown() for _, tunnel in selected),
                return_exceptions=True,
            )
        return len(selected)

    async def close(self) -> None:
        selected = list(self._tunnels.values())
        self._tunnels.clear()
        if selected:
            await asyncio.gather(
                *(tunnel.endpoint.shutdown() for tunnel in selected),
                return_exceptions=True,
            )


def wsrx_link(api_base: str, traffic_id: str, port: int) -> str:
    """Build the platform WebSocket URL for one exposed challenge port."""
    parsed = urlparse(api_base)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return f"{scheme}://{parsed.netloc}/api/traffic/{traffic_id}?port={port}"


def tunnel_status(state: Wsrx, traffic_id: str) -> str:
    """Present live local addresses retained for a traffic identifier."""
    locals_ = [
        item["local"]
        for item in state.pool_instances()
        if traffic_id in item["remote"]
    ]
    return ", ".join(locals_) if locals_ else "未连接"
