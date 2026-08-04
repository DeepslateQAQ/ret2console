"""wsrx 检测、后台 daemon 与 HTTP 隧道控制（SPEC §13）。

启动策略：
1. 检测 wsrx-desktop GUI（`.rx.is.alive` 锁 + GET /version 存活确认）
2. GUI 存在 → 注册为 GUI 控制源（POST /connect，GUI 弹窗确认），
   之后所有隧道经 GUI 的 HTTP API（/pool）建立，可在 GUI 中查看管理
3. GUI 不存在 → 用 CLI binary（PATH 或 `~/.ret2console/bin/wsrx`，
   缺失自动从 GitHub 下载）在后台启动 `wsrx daemon`，同样走 HTTP 控制

不再前台 spawn `wsrx connect`；daemon 状态持久化在 `~/.ret2console/daemon.json`。
"""

from __future__ import annotations

import os
import json
import platform
import signal
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from secrets import token_hex
from urllib.parse import urlparse

import httpx

from ret2console.config import LEGACY_DIR

BIN_DIR = LEGACY_DIR / "bin"
# Windows 下二进制带 .exe 后缀（zip 内为 wsrx.exe）
BINARY = BIN_DIR / ("wsrx.exe" if sys.platform == "win32" else "wsrx")
GITHUB_REPO = "XDSEC/WebSocketReflectorX"
RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
LOCK_NAME = ".rx.is.alive"
TUNNEL_FILE = LEGACY_DIR / "tunnel.json"
DAEMON_FILE = LEGACY_DIR / "daemon.json"
DAEMON_LOG = LEGACY_DIR / "wsrx-daemon.log"
# 控制源标识：wsrx-desktop 以 Origin 头区分来源，GUI 中按此分组展示。
# 实际使用时 origin 从配置的 r2s 平台域名派生（wsrx_origin），
# 与网页端（浏览器 location.host）处于同一 scope，隧道可并列管理。
OUR_ORIGIN = "http://ret2console.local"


def wsrx_origin(api_base: str) -> str:
    """从 API 地址派生控制源 Origin：`https://ctf.xidian.edu.cn/api` → `https://ctf.xidian.edu.cn`。"""
    u = urlparse(api_base)
    if not u.netloc:
        return OUR_ORIGIN
    return f"{u.scheme}://{u.netloc}"


@dataclass
class Wsrx:
    mode: str = "none"  # "cli"(仅 binary 探测) | "desktop" | "daemon" | "none"
    binary: str | None = None
    api_port: int | None = None
    secret: str | None = None
    origin: str = OUR_ORIGIN
    source: str = ""
    pid: int | None = None

    @property
    def available(self) -> bool:
        return self.mode != "none"

    @property
    def http_base(self) -> str | None:
        if self.api_port is None:
            return None
        return f"http://127.0.0.1:{self.api_port}"


def detect() -> Wsrx:
    """检测当前环境中的 wsrx（不触发下载）。"""
    port = _desktop_port()
    if port is not None:
        return Wsrx("desktop", api_port=port, source=f"wsrx-desktop 127.0.0.1:{port}")
    which = shutil.which("wsrx")
    if which:
        return Wsrx("cli", binary=which, source=f"PATH: {which}")
    if BINARY.is_file():
        return Wsrx("cli", binary=str(BINARY), source=str(BINARY))
    return Wsrx("none")


def ensure_wsrx(origin: str | None = None) -> Wsrx:
    """启动阶段：GUI 优先，其次后台 daemon；返回最终控制状态。"""
    found = detect()
    if found.mode == "desktop":
        if origin:
            found.origin = origin
        return found
    if found.mode != "cli" and not download_binary():
        return Wsrx("none")
    return ensure_daemon()


def _cli_binary() -> str | None:
    which = shutil.which("wsrx")
    if which:
        return which
    if BINARY.is_file():
        return str(BINARY)
    return None


def _headers(*, origin: str | None = None, secret: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if origin:
        headers["Origin"] = origin
    if secret:
        headers["Authorization"] = secret
    return headers


def _pool_client(base: str, headers: dict[str, str]) -> httpx.Client:
    """指向 wsrx HTTP API 的客户端（独立函数便于测试替换）。"""
    return httpx.Client(base_url=base, timeout=5.0, headers=headers)


def _load_daemon() -> dict | None:
    try:
        data = json.loads(DAEMON_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("port") else None


def _save_daemon(port: int, secret: str, pid: int | None) -> None:
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    DAEMON_FILE.write_text(
        json.dumps({"pid": pid, "port": port, "secret": secret}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        DAEMON_FILE.chmod(0o600)
    except OSError:
        pass


def _daemon_alive(port: int, secret: str | None) -> bool:
    try:
        with httpx.Client(timeout=2.0) as client:
            resp = client.get(f"http://127.0.0.1:{port}/pool", headers=_headers(secret=secret))
            return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _wait_ready(port: int, secret: str, timeout: float = 8.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _daemon_alive(port, secret):
            return True
        time.sleep(0.2)
    return False


def stop_daemon() -> None:
    """退出清理：终止我们管理的后台 daemon 并删除状态文件。"""
    saved = _load_daemon()
    if saved is None:
        return
    pid = saved.get("pid")
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            pass
    try:
        DAEMON_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_daemon() -> Wsrx:
    """启动/复用后台 `wsrx daemon`，返回其 HTTP 控制句柄。"""
    saved = _load_daemon()
    if saved is not None and _daemon_alive(int(saved["port"]), saved.get("secret")):
        return Wsrx(
            "daemon",
            api_port=int(saved["port"]),
            secret=saved.get("secret"),
            pid=saved.get("pid"),
            source="wsrx daemon（复用）",
        )
    binary = _cli_binary()
    if binary is None:
        return Wsrx("none")
    port = pick_free_port()
    secret = token_hex(16)
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    log = open(DAEMON_LOG, "ab")
    proc: subprocess.Popen | None = None
    try:
        kwargs = {}
        if hasattr(os, "setsid") and os.name != "nt":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(
            [binary, "daemon", "--host", "127.0.0.1", "-p", str(port), "--secret", secret],
            stdout=log,
            stderr=log,
            **kwargs,
        )
    except OSError:
        log.close()
        return Wsrx("none")
    if not _wait_ready(port, secret):
        log.close()
        proc.kill()
        return Wsrx("none")
    _save_daemon(port, secret, proc.pid)
    return Wsrx("daemon", api_port=port, secret=secret, pid=proc.pid, source="wsrx daemon（新启动）")


def download_binary(timeout: float = 60.0) -> bool:
    """从 GitHub releases 下载当前平台的 wsrx prebuilt 到 ~/.ret2console/bin/。"""
    try:
        version = _latest_version()
        asset = _asset_name(version)
        if asset is None:
            return False
        url = f"https://github.com/{GITHUB_REPO}/releases/download/{version}/{asset}"
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            blob = resp.content
        BIN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = BIN_DIR / "wsrx.tmp"
        tmp.write_bytes(_extract_binary(blob, asset))
        if sys.platform != "win32":
            tmp.chmod(0o755)
        os.replace(tmp, BINARY)
        return True
    except Exception:
        return False


def _latest_version() -> str:
    with httpx.Client(timeout=15.0, follow_redirects=True) as client:
        resp = client.get(RELEASES_API)
        resp.raise_for_status()
        tag = resp.json().get("tag_name")
        if not tag:
            raise RuntimeError("no release tag")
        return tag


def _is_musl() -> bool:
    try:
        out = subprocess.run(
            ["ldd", "--version"], capture_output=True, text=True, timeout=5,
        ).stderr.lower()
        return "musl" in out
    except Exception:
        return False


def _asset_name(version: str) -> str | None:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux"):
        if machine not in ("x86_64", "amd64"):
            return None
        libc = "musl" if _is_musl() else "gnu"
        return f"wsrx-cli-{version}-linux-{libc}-x86_64.tar.gz"
    if sys.platform == "win32":
        if machine not in ("x86_64", "amd64"):
            return None
        return f"wsrx-cli-{version}-windows-msvc-x86_64.zip"
    if sys.platform == "darwin":
        arch = "aarch64" if machine in ("arm64", "aarch64") else "x86_64"
        return f"wsrx-cli-{version}-macos-{arch}.zip"
    return None


def _extract_binary(blob: bytes, asset: str) -> bytes:
    """从 release 包中取出 wsrx 可执行文件字节。"""
    if asset.endswith(".zip"):
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "wsrx.zip"
            zp.write_bytes(blob)
            with zipfile.ZipFile(zp) as zf:
                # Windows: target/release/wsrx.exe；macOS: target/<triple>/release/wsrx
                for name in zf.namelist():
                    base = name.rsplit("/", 1)[-1]
                    if base in ("wsrx", "wsrx.exe"):
                        return zf.read(name)
        raise RuntimeError("wsrx not found in zip")
    with tempfile.TemporaryDirectory() as td:
        tp = Path(td) / "wsrx.tar.gz"
        tp.write_bytes(blob)
        with tarfile.open(tp, "r:gz") as tf:
            member = tf.getmember("wsrx")
            data = tf.extractfile(member)
            if data is None:
                raise RuntimeError("wsrx is not a file")
            return data.read()
    raise RuntimeError("wsrx not found in tarball")


def _desktop_port() -> int | None:
    """wsrx-desktop 单实例锁：找到 .rx.is.alive 并验证 /version 存活。"""
    lock = _find_lock_file()
    if lock is None:
        return None
    try:
        port = int(lock.read_text(encoding="utf-8").strip().splitlines()[0])
    except (OSError, ValueError):
        return None
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"http://127.0.0.1:{port}/version")
            if resp.status_code == 200:
                return port
    except httpx.HTTPError:
        return None
    return None


def _find_lock_file() -> Path | None:
    candidates: list[Path] = []
    if xdg := os.environ.get("XDG_DATA_HOME"):
        candidates.append(Path(xdg) / LOCK_NAME)
    candidates.append(Path.home() / ".local" / "share" / LOCK_NAME)
    for c in candidates:
        if c.is_file():
            return c
    # 兜底：数据/配置目录下两级深度的锁文件
    for root in (Path.home() / ".local" / "share", Path.home() / ".config"):
        try:
            for sub in root.glob("*"):
                p = sub / LOCK_NAME
                if p.is_file():
                    return p
        except OSError:
            continue
    return None


# ---- 隧道（instance connect, SPEC §13.4；desktop/daemon 统一走 HTTP） ----


def wsrx_link(api_base: str, traffic_id: str, port: int) -> str:
    """实例的真实 ws 地址：wss://{平台域名}/api/traffic/{traffic_id}?port={port}。

    ret2shell 返回的 instance.traffic 只是 21 位 nanoid（pod label），
    平台按此路径把 ws 流量转发到对应实例端口（与前端 getWsrxLink 一致）。
    """
    u = urlparse(api_base)
    scheme = "wss" if u.scheme == "https" else "ws"
    return f"{scheme}://{u.netloc}/api/traffic/{traffic_id}?port={port}"


def pick_free_port() -> int:
    """选一个空闲的本地端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def control_status(state: Wsrx) -> str:
    """GET /connect：202=已授权、201=待 GUI 确认、denied=未注册。"""
    if state.mode != "desktop" or state.http_base is None:
        return "denied"
    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.get(f"{state.http_base}/connect", headers=_headers(origin=state.origin))
        if resp.status_code == 202:
            return "approved"
        if resp.status_code == 201:
            return "pending"
    except httpx.HTTPError:
        pass
    return "denied"


def register_control_source(state: Wsrx, name: str = "ret2console") -> str:
    """注册 GUI 控制源（POST /connect，wsrx-desktop 会弹窗要求用户确认）。

    body 对齐 @xdsec/wsrx 前端：host 为占位（实际取 Origin 头），
    features 是字符串数组（后端 FeatureFlags 的 serde 为 as_feature_vec）。
    """
    if state.mode != "desktop" or state.http_base is None:
        return "denied"
    current = control_status(state)
    if current != "denied":
        return current
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(
                f"{state.http_base}/connect",
                headers=_headers(origin=state.origin),
                json={
                    "host": "IN_HEADER",
                    "name": name,
                    "state": "pending",
                    "features": ["basic"],
                    "settings": {},
                },
            )
    except httpx.HTTPError:
        return "denied"
    return control_status(state)


def tunnel_add(state: Wsrx, local_port: int, remote: str, label: str | None = None) -> None:
    """POST /pool 建立隧道（不再前台 spawn `wsrx connect`）。"""
    if state.http_base is None:
        raise RuntimeError("wsrx 不可用")
    payload: dict[str, str] = {"local": f"127.0.0.1:{local_port}", "remote": remote}
    if state.mode == "desktop":
        payload["label"] = label or f"r2c-{local_port}"
    headers = _headers(origin=state.origin if state.mode == "desktop" else None, secret=state.secret)
    with _pool_client(state.http_base, headers) as client:
        resp = client.post("/pool", json=payload)
        resp.raise_for_status()


def tunnel_remove(state: Wsrx, local_port: int) -> None:
    """DELETE /pool 关闭隧道（key=本地地址，desktop/daemon 通用）。"""
    if state.http_base is None:
        return
    headers = _headers(origin=state.origin if state.mode == "desktop" else None, secret=state.secret)
    with _pool_client(state.http_base, headers) as client:
        resp = client.request("DELETE", "/pool", json={"key": f"127.0.0.1:{local_port}"})
        if resp.status_code not in (200, 404):
            resp.raise_for_status()


def pool_instances(state: Wsrx) -> list[dict]:
    """GET /pool：当前控制源下全部隧道 [{local, remote, ...}]。"""
    if state.http_base is None:
        return []
    headers = _headers(origin=state.origin if state.mode == "desktop" else None, secret=state.secret)
    with _pool_client(state.http_base, headers) as client:
        resp = client.get("/pool")
        resp.raise_for_status()
        data = resp.json()
    if isinstance(data, dict):
        # wsrx daemon：HashMap<local, Tunnel> 序列化成对象
        return list(data.values())
    return data if isinstance(data, list) else []


def load_tunnels() -> dict[str, dict]:
    """traffic 地址 -> {port, pid}。"""
    try:
        data = json.loads(TUNNEL_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_tunnel(
    traffic: str, port: int, game_id: int | None = None,
    challenge_id: int | None = None, pid: int | None = None,
) -> None:
    """记录隧道归属（traffic:port -> 本地端口 + 题目上下文），供 disconnect 清理。"""
    tunnels = load_tunnels()
    tunnels[f"{traffic}:{port}"] = {
        "port": port,
        "pid": pid,
        "game_id": game_id,
        "challenge_id": challenge_id,
    }
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)
    TUNNEL_FILE.write_text(json.dumps(tunnels, ensure_ascii=False, indent=2), encoding="utf-8")


def remove_tunnel(traffic: str) -> None:
    tunnels = load_tunnels()
    tunnels.pop(traffic, None)
    TUNNEL_FILE.write_text(json.dumps(tunnels, ensure_ascii=False, indent=2), encoding="utf-8")


def tunnel_status(wsrx_state: Wsrx, traffic: str) -> str:
    """返回该实例的本地隧道状态：「已建立 127.0.0.1:PORT」/「未建立」。"""
    if not traffic or wsrx_state.mode == "none":
        return "未建立"
    if wsrx_state.mode in ("desktop", "daemon"):
        try:
            pools = pool_instances(wsrx_state)
        except Exception:
            pools = []
        locals_ = []
        for p in pools:
            remote = str(p.get("remote") or p.get("to") or "")
            if traffic in remote:
                local = p.get("local") or p.get("from")
                if local:
                    locals_.append(str(local))
        return f"已建立 {', '.join(locals_)}" if locals_ else "未建立"
    # cli 模式（旧前台进程遗留记录）兜底
    alive = []
    for key, rec in load_tunnels().items():
        if not key.startswith(traffic + ":"):
            continue
        pid = rec.get("pid")
        if pid is not None:
            try:
                os.kill(int(pid), 0)
                alive.append(f"127.0.0.1:{rec['port']}")
            except (OSError, ValueError):
                pass
    return f"已建立 {', '.join(alive)}" if alive else "未建立"
