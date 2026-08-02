"""三层补全（SPEC §10）：静态命令 → fs 路径（未加载目录自动异步加载）→ 动态缓存。"""

from __future__ import annotations

import asyncio
from typing import Callable, Iterable

from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document

from ret2console.vfs import Vfs


class Ret2ConsoleCompleter(Completer):
    def __init__(
        self,
        commands: Callable[[], dict[str, list[str]]],
        vfs: Vfs | None = None,
        current_path: Callable[[], str] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        dynamic_candidates: Callable[[str, str], list[str]] | None = None,
    ) -> None:
        self._commands = commands
        self._vfs = vfs
        self._current_path = current_path or (lambda: "/")
        self._loop = loop
        self._dynamic_candidates = dynamic_candidates or (lambda _a, _b: [])

    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text = document.text_before_cursor
        # 末尾是空白 → 正在输入新词（word 为空，before 为已输入词）
        typing_new_word = text.endswith((" ", "\t"))
        words = text.split()
        if not words and not typing_new_word:
            for name in self._commands():
                yield Completion(name, start_position=0)
            return
        if typing_new_word:
            word = ""
            before = words
        else:
            word = words[-1] if words else ""
            before = words[:-1]

        # 第一个词：命令名
        if not before:
            for name in self._commands():
                if name.startswith(word):
                    yield Completion(name, start_position=-len(word))
            return

        # 后续词：子命令 + 动态 + 路径（支持两级命令：admin bulletin list）
        cmd = before[0]
        key = cmd
        if len(before) > 1:
            two = " ".join(before[:2])
            if two in self._commands():
                key = two
        nested = key != cmd and len(before) >= 3  # 两级命令第三层起不再给静态子命令
        if not typing_new_word:
            if not nested:
                for sub in self._commands().get(key, []):
                    if sub.startswith(word):
                        yield Completion(sub, start_position=-len(word))
            for cand in self._dynamic_candidates(cmd, word):
                text, meta = _split_candidate(cand)
                yield Completion(text, start_position=-len(word), display_meta=meta)
            for text, meta in self._path_candidates(cmd, before, word):
                yield Completion(text, start_position=-len(word), display_meta=meta)
        else:
            # 空格后开始新词：直接给出该命令的全部候选（start_position=0，
            # 避免把已输入内容误当成被替换的 word，产生 aauth 之类的结果）
            if not nested:
                for sub in self._commands().get(key, []):
                    yield Completion(sub, start_position=0)
            for cand in self._dynamic_candidates(cmd, ""):
                text, meta = _split_candidate(cand)
                yield Completion(text, start_position=0, display_meta=meta)
            # 路径类命令：新参数视为从当前目录开始的新路径，不继承上一个词
            if cmd in self._PATH_COMMANDS:
                for text, meta in self._path_candidates(cmd, [cmd], ""):
                    yield Completion(text, start_position=0, display_meta=meta)

    # ---- fs 路径补全（同步遍历已加载节点） ----

    _PATH_COMMANDS = {"cd", "ls", "cat", "download", "wget"}

    def _path_candidates(self, cmd: str, before: list[str], word: str) -> list[tuple[str, str | None]]:
        if self._vfs is None:
            return []
        # 仅 cd/ls/cat/download 补路径；其他命令（challenge/auth/...）不补
        if cmd not in self._PATH_COMMANDS:
            return []
        if word.startswith("/"):
            base, prefix = _split_path_word(word)
            names = self._ensure_loaded(base)
            if names is None:
                return []
            return [(f"{base}{n}", meta) for n, meta in names if n.startswith(prefix)]
        # 相对路径：基于当前虚拟路径，保留相对形式
        cur = self._current_path()
        rel_dir, prefix = (word.rsplit("/", 1) + [""])[:2] if "/" in word else ("", word)
        base = (cur if cur.endswith("/") else cur + "/") + rel_dir + ("/" if rel_dir else "")
        names = self._ensure_loaded(base)
        if names is None:
            return []
        lead = rel_dir + "/" if rel_dir else ""
        return [(f"{lead}{n}", meta) for n, meta in names if n.startswith(prefix)]

    def _ensure_loaded(self, path: str) -> list[tuple[str, str | None]] | None:
        """返回目录子节点 (名, meta)；未加载时提交异步加载到主事件循环（限时等待）。"""
        children = self._vfs.sync_children(path)
        if children is not None:
            return children
        if self._loop is None:
            return None
        future = asyncio.run_coroutine_threadsafe(self._vfs.resolve(path), self._loop)
        try:
            future.result(timeout=3)
        except Exception:
            return None
        return self._vfs.sync_children(path)


def _split_path_word(word: str) -> tuple[str, str]:
    """拆分路径词为（目录基址, 前缀）。如 /contests/3/ch → ('/contests/3/', 'ch')"""
    if word.endswith("/"):
        return word, ""
    idx = word.rfind("/")
    if idx < 0:
        return "/", word
    return word[: idx + 1], word[idx + 1 :]


def _split_candidate(cand: str | tuple[str, str | None]) -> tuple[str, str | None]:
    """动态候选兼容 (text,) 与 (text, meta) 两种形态。"""
    if isinstance(cand, tuple):
        return cand[0], cand[1]
    return cand, None
