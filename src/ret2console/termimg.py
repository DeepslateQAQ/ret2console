"""终端内图片渲染：Kitty Graphics / Sixel 协议（SPEC §13 cat 图片支持）。"""

from __future__ import annotations

import base64
import io
import math
import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from collections import Counter
from urllib.parse import urljoin

from ret2console.output import is_tty

_KITTY_CHUNK = 4096  # kitty 协议单块 base64 载荷上限（字节）
_IMAGE_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


# ---- 终端能力检测 ----


def _query_terminal(payload: bytes, timeout: float = 0.15) -> bytes | None:
    """向终端发送查询序列并读取响应（临时 raw 模式，结束后恢复）。"""
    try:
        fd_in = sys.stdin.fileno()
        fd_out = sys.stdout.fileno()
        old = termios.tcgetattr(fd_in)
    except (termios.error, OSError, ValueError):
        return None
    try:
        tty.setraw(fd_in)
        os.write(fd_out, payload)
        os.write(fd_out, b"\x1b[?2026l")  # 关闭同步输出，避免响应被缓冲
        buf = b""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            r, _, _ = select.select([fd_in], [], [], max(0.0, end - time.monotonic()))
            if not r:
                break
            try:
                chunk = os.read(fd_in, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
        return buf
    finally:
        try:
            termios.tcsetattr(fd_in, termios.TCSADRAIN, old)
        except termios.error:
            pass


def _supports_kitty() -> bool:
    """Kitty 协议：环境变量预判 + 主动查询确认（\x1b_Gi=1,a=q → 响应含 OK）。

    仅凭环境变量（tmux/ssh 透传 KITTY_WINDOW_ID）可能误判，查询无响应则不算。
    """
    env = os.environ
    if not (env.get("KITTY_WINDOW_ID") or "kitty" in env.get("TERM", "").lower()):
        return False
    resp = _query_terminal(b"\x1b_Gi=1,a=q\x1b\\")
    return resp is not None and b"OK" in resp


def _supports_sixel() -> bool:
    """Sixel：DA1（\x1b[c）响应参数表含 4（DEC graphics）。"""
    resp = _query_terminal(b"\x1b[c")
    if resp is None:
        return False
    m = re.search(rb"\x1b\[\?([0-9;]*)([A-Za-z])", resp)
    return bool(m and "4" in m.group(1).decode().split(";"))


def detect_protocol() -> str | None:
    """检测当前终端支持的图片协议：kitty / sixel / None（非 TTY 或未知终端）。"""
    if not is_tty():
        return None
    if _supports_kitty():
        return "kitty"
    if _supports_sixel():
        return "sixel"
    return None


# ---- Markdown 图片提取与 URL 解析 ----


def extract_images(text: str) -> list[tuple[str, str]]:
    """提取 Markdown 图片标记，返回 [(alt, url), ...]。"""
    return [(alt, url.strip()) for alt, url in _IMG_RE.findall(text)]


def resolve_media_url(url: str, api_base: str) -> str:
    """把 Markdown 里的图片链接解析为可下载的完整 URL。

    平台内链接（/api/media?hash=...）拼回站点根；外链原样返回。
    """
    if url.startswith(("http://", "https://")):
        return url
    root = api_base[:-4] if api_base.endswith("/api") else api_base
    return urljoin(root + "/", url.lstrip("/"))


# ---- 图片编码 ----


def _to_rgb(img) -> "Image":
    """转 RGB：带透明通道的图片垫白底。"""
    if img.mode in ("RGBA", "LA"):
        rgba = img.convert("RGBA")
        bg = rgba.__class__.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return img.convert("RGB")


def _resize(img, max_cols: int) -> "Image":
    """按终端宽度缩放，限制面积与高度，避免巨型图片刷屏。"""
    from PIL import Image
    w, h = img.size
    target_w = min(w, max(240, int(max_cols * 8 * 0.6)))
    if target_w < w:
        img = img.resize((target_w, max(1, int(h * target_w / w))), Image.Resampling.LANCZOS)
    w, h = img.size
    if h > 2400:
        img = img.resize((max(1, int(w * 2400 / h)), 2400), Image.Resampling.LANCZOS)
    w, h = img.size
    if w * h > 2_000_000:
        scale = (2_000_000 / (w * h)) ** 0.5
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS
        )
    return img


def _kitty_encode(img) -> str:
    """Kitty Graphics：传输 PNG（t=d,f=100）后以占位符嵌入文本流。

    占位符（a=p）让图片随文本滚动，REPL 重绘/滚动不会丢图；
    图片按 16px 单元格估计高度，末尾补空行使后续内容从图片下方开始。
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    w, h = img.size
    first_m = "0" if len(b64) <= _KITTY_CHUNK else "1"
    parts = [f"\x1b_Gf=100,a=T,q=2,t=d,s={w},v={h},m={first_m};{b64[:_KITTY_CHUNK]}"]
    for i in range(_KITTY_CHUNK, len(b64), _KITTY_CHUNK):
        m = "0" if i + _KITTY_CHUNK >= len(b64) else "1"
        parts.append(f"\x1b_Gm={m};{b64[i:i + _KITTY_CHUNK]}")
    parts.append("\x1b_Ga=p,q=2")
    rows = max(1, math.ceil(h / 16))
    return "".join(parts) + "\n" * rows


_ANSI16 = [
    (0, 0, 0), (170, 0, 0), (0, 170, 0), (170, 85, 0),
    (0, 0, 170), (170, 0, 170), (0, 170, 170), (170, 170, 170),
    (85, 85, 85), (255, 85, 85), (85, 255, 85), (255, 255, 85),
    (85, 85, 255), (255, 85, 255), (85, 255, 255), (255, 255, 255),
]


def _sixel_encode(img) -> str:
    """Sixel：量化到 16 色，每 6 行一个 band，列内取主色。"""
    from PIL import Image
    img = img.convert("RGB")
    q = img.quantize(colors=16, method=Image.Quantize.MEDIANCUT)
    palette = list(q.getpalette() or [])
    palette += [0, 0, 0] * (16 - len(palette) // 3)
    palette = palette[:48]
    px = q.load()
    w, h = img.size
    out = ["\x1bPq"]
    for i in range(16):
        r, g, b = palette[i * 3:i * 3 + 3]
        out.append(f"#{i};2;{int(r / 255 * 100)};{int(g / 255 * 100)};{int(b / 255 * 100)}")
    cur = -1
    for y0 in range(0, h, 6):
        if y0 > 0:
            out.append("-")
        for x in range(w):
            bits = 0
            counts: Counter[int] = Counter()
            for dy in range(6):
                y = y0 + dy
                if y < h:
                    c = px[x, y]
                    counts[c] += 1
                    bits |= 1 << dy
            if not bits:
                out.append("?")
                continue
            c = counts.most_common(1)[0][0]
            if c != cur:
                out.append(f"#{c}")
                cur = c
            out.append(chr(0x3F + bits))
        cur = -1
    out.append("\x1b\\")
    return "".join(out) + "\n"


def render_image(data: bytes, protocol: str, max_cols: int = 80) -> str | None:
    """把图片字节渲染为终端转义序列；Pillow 缺失或解码失败返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        img = _resize(_to_rgb(img), max_cols)
    except Exception:
        return None
    if protocol == "kitty":
        return _kitty_encode(img)
    if protocol == "sixel":
        return _sixel_encode(img)
    return None


def show_image(data: bytes, protocol: str, max_cols: int = 80) -> bool:
    """显示图片到终端。

    kitty 协议优先调用官方 `kitten icat`（自动处理占位符、缩放与布局），
    失败时回退内置协议编码器；无法渲染返回 False。
    """
    if protocol == "kitty":
        kitten = shutil.which("kitten")
        if kitten:
            try:
                proc = subprocess.run(
                    [kitten, "icat", "--stdin", "yes"],
                    input=data, timeout=30, stderr=subprocess.DEVNULL,
                )
                if proc.returncode == 0:
                    return True
            except (subprocess.SubprocessError, OSError):
                pass
    seq = render_image(data, protocol, max_cols)
    if not seq:
        return False
    sys.stdout.write(seq)
    sys.stdout.flush()
    return True


def is_image_name(name: str) -> bool:
    return name.lower().endswith(_IMAGE_EXT)
