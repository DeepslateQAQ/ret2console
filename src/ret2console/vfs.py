"""虚拟文件系统（pseudo filesystem, SPEC §7）。

结构（递归懒加载，数据来自 API）：
/  notice  contests/<game_id>/{info,notice,scoreboard,docs,team,challenges/<challenge_id>/{README,hint,files,instance,flag}}
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Awaitable, Callable


class VfsError(Exception):
    pass


@dataclass
class Node:
    name: str
    kind: str  # "dir" | "file"
    game_id: int | None = None
    challenge_id: int | None = None
    meta: str | None = None  # 描述（如比赛/题目标题），补全菜单右侧展示
    loader: Callable[[], Awaitable[list["Node"]]] | None = None
    reader: Callable[[], Awaitable[str]] | None = None
    children: list["Node"] | None = field(default=None, repr=False)
    loaded: bool = False


def dir_node(
    name: str, *, game_id: int | None = None, challenge_id: int | None = None,
    meta: str | None = None, loader: Callable[[], Awaitable[list[Node]]] | None = None,
) -> Node:
    return Node(name, "dir", game_id=game_id, challenge_id=challenge_id, meta=meta, loader=loader)


def file_node(
    name: str, *, game_id: int | None = None, challenge_id: int | None = None,
    meta: str | None = None, reader: Callable[[], Awaitable[str]],
) -> Node:
    return Node(name, "file", game_id=game_id, challenge_id=challenge_id, meta=meta, reader=reader)


def normalize_path(path: str) -> str:
    """把路径中的 . / .. 段折叠为规范形式；.. 越过根目录时钳制在根。"""
    parts: list[str] = []
    for seg in path.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    return "/" + "/".join(parts)


class Vfs:
    """只读虚拟文件系统。当前比赛/题目上下文由外部 Context 维护（cd 即 use）。"""

    def __init__(self) -> None:
        self.root = dir_node("/")

    def build(
        self,
        contests_loader: Callable[[], Awaitable[list[Node]]] | None = None,
        bulletin_reader: Callable[[], Awaitable[str]] | None = None,
    ) -> None:
        contests = dir_node("contests", loader=contests_loader or (lambda: []))
        notice = file_node("notice", reader=bulletin_reader or (lambda: "（暂无全局公告）"))
        self.root.children = [contests, notice]
        self.root.loaded = True

    async def _ensure_loaded(self, node: Node) -> None:
        if node.loaded or node.kind != "dir" or node.loader is None:
            return
        result = node.loader()
        node.children = await result if inspect.isawaitable(result) else result
        node.loaded = True

    def _find(self, node: Node, name: str) -> Node | None:
        if node.children is None:
            return None
        for c in node.children:
            if c.name == name:
                return c
        return None

    async def resolve(self, path: str) -> Node:
        node = self.root
        walked: list[str] = []
        for seg in normalize_path(path).split("/"):
            if not seg:
                continue
            walked.append(seg)
            if node.kind != "dir":
                raise VfsError(f"不是目录: /{'/'.join(walked)}")
            await self._ensure_loaded(node)
            node = self._find(node, seg)
            if node is None:
                raise VfsError(f"路径不存在: /{'/'.join(walked)}")
        await self._ensure_loaded(node)
        return node

    async def list_dir(self, path: str) -> list[Node]:
        node = await self.resolve(path)
        if node.kind != "dir":
            raise VfsError(f"{path} 不是目录")
        return node.children or []

    async def read(self, path: str) -> str:
        node = await self.resolve(path)
        if node.kind != "file" or node.reader is None:
            raise VfsError(f"{path} 不是可读文件")
        result = node.reader()
        return await result if inspect.isawaitable(result) else result

    def parent_path(self, path: str) -> str:
        path = path.rstrip("/")
        if "/" not in path:
            return "/"
        return path.rsplit("/", 1)[0] or "/"

    # ---- 同步补全辅助（仅遍历已加载节点，供 completer 使用） ----

    def sync_resolve(self, path: str) -> Node | None:
        """同步解析路径；任何未加载的目录段都视为失败（返回 None）。"""
        node = self.root
        for seg in normalize_path(path).split("/"):
            if not seg:
                continue
            if node.kind != "dir" or not node.loaded or node.children is None:
                return None
            node = self._find(node, seg)
            if node is None:
                return None
        return node

    def sync_children(self, path: str) -> list[tuple[str, str | None]] | None:
        """返回目录已加载子节点 (名[含目录 '/' 后缀], meta)；未加载返回 None。"""
        node = self.sync_resolve(path)
        if node is None or node.kind != "dir" or not node.loaded or node.children is None:
            return None
        out: list[tuple[str, str | None]] = []
        for c in node.children:
            out.append((c.name + ("/" if c.kind == "dir" else ""), c.meta))
        return out


async def _not_implemented(name: str, api: str) -> str:
    return f"（{name}：待接入 {api}）"
