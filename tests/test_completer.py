"""fs 路径补全测试（同步遍历已加载节点）。"""

import asyncio

import pytest
from prompt_toolkit.document import Document
from prompt_toolkit.completion import Completion

from ret2console.completer import Ret2ConsoleCompleter
from ret2console.vfs import Vfs, dir_node, file_node


def _build_vfs() -> Vfs:
    vfs = Vfs()

    async def contests_loader():
        return [dir_node("3", game_id=3, meta="测试比赛", loader=_game3_loader)]

    async def _game3_loader():
        return [
            file_node("info", game_id=3, reader=lambda: "i"),
            dir_node("challenges", game_id=3, loader=_challenges_loader),
        ]

    async def _challenges_loader():
        return [dir_node("5", game_id=3, challenge_id=5, meta="签到题", loader=_ch5_loader)]

    async def _ch5_loader():
        return [
            file_node("README", game_id=3, challenge_id=5, reader=lambda: "r"),
            file_node("hint", game_id=3, challenge_id=5, reader=lambda: "h"),
        ]

    vfs.build(contests_loader=contests_loader)
    return vfs


def _completer(vfs: Vfs, path: str = "/") -> Ret2ConsoleCompleter:
    return Ret2ConsoleCompleter(
        commands=lambda: {"cd": [], "ls": [], "cat": [], "auth": ["login"]},
        vfs=vfs,
        current_path=lambda: path,
    )


def _candidates(comp: Ret2ConsoleCompleter, text: str) -> list[str]:
    return [c.text for c in comp.get_completions(Document(text), None)]


def _meta(c: Completion) -> str:
    return "".join(fragment[1] for fragment in c.display_meta or ())


@pytest.mark.asyncio
async def test_complete_root_children():
    vfs = _build_vfs()
    await vfs.resolve("/contests")  # 触发加载
    comp = _completer(vfs)
    cands = _candidates(comp, "cd /")
    assert "/contests/" in cands
    assert "/notice" in cands


@pytest.mark.asyncio
async def test_complete_nested_path():
    vfs = _build_vfs()
    await vfs.resolve("/contests/3/challenges/5")
    comp = _completer(vfs)
    cands = _candidates(comp, "cd /contests/3/ch")
    assert "/contests/3/challenges/" in cands


@pytest.mark.asyncio
async def test_complete_relative_in_challenge_dir():
    vfs = _build_vfs()
    await vfs.resolve("/contests/3/challenges/5")
    comp = _completer(vfs, path="/contests/3/challenges/5")
    cands = _candidates(comp, "cat R")
    assert "README" in cands


@pytest.mark.asyncio
async def test_complete_unloaded_dir_is_empty():
    vfs = _build_vfs()  # 未加载任何比赛
    comp = _completer(vfs)
    cands = _candidates(comp, "cd /contests/3/cha")
    assert cands == []


def test_command_completion():
    comp = _completer(_build_vfs())
    cands = _candidates(comp, "au")
    assert "auth" in cands


@pytest.mark.asyncio
async def test_complete_shows_directory_meta():
    """比赛/题目目录补全时右侧显示描述（比赛标题/题目标题）。"""
    vfs = _build_vfs()
    await vfs.resolve("/contests/3/challenges/5")
    comp = _completer(vfs)
    by_text = {c.text: c for c in comp.get_completions(Document("cd /contests/"), None)}
    assert _meta(by_text["/contests/3/"]) == "测试比赛"
    by_text2 = {c.text: c for c in comp.get_completions(Document("cd /contests/3/challenges/"), None)}
    assert _meta(by_text2["/contests/3/challenges/5/"]) == "签到题"


def test_complete_dynamic_candidate_meta():
    """动态候选（比赛/题目 id）同样携带标题描述。"""
    vfs = _build_vfs()
    comp = Ret2ConsoleCompleter(
        commands=lambda: {"contest": []},
        vfs=vfs,
        dynamic_candidates=lambda _c, _b, _w: [("3", "测试比赛"), ("7", "新生赛")],
    )
    by_text = {c.text: c for c in comp.get_completions(Document("contest "), None)}
    assert _meta(by_text["3"]) == "测试比赛"
    assert _meta(by_text["7"]) == "新生赛"


def test_profile_subcommand_completion():
    """auth profile 空格后 Tab：补全 profile 子命令，而不是 auth 的 login/logout。"""
    from ret2console.repl import _command_table
    table = _command_table()
    assert table["auth profile"] == ["list", "create", "use", "delete", "status"]
    comp = Ret2ConsoleCompleter(
        commands=lambda: table,
        vfs=_build_vfs(),
        dynamic_candidates=lambda _c, _b, _w: [],
    )
    results = list(comp.get_completions(Document("auth profile "), None))
    texts = [c.text for c in results]
    assert "list" in texts and "create" in texts and "use" in texts
    assert "delete" in texts and "status" in texts
    assert "login" not in texts and "logout" not in texts
    assert all(c.start_position == 0 for c in results)
    # 前缀匹配：auth profile u → use
    results = list(comp.get_completions(Document("auth profile u"), None))
    assert [c.text for c in results] == ["use"]


def test_profile_name_completion():
    """auth profile use/delete 空格后 Tab：补全已有 profile 名（带 endpoint 描述）。"""
    from ret2console.config import profile_config_path
    from ret2console.repl import _command_table
    from pathlib import Path
    profile_config_path("default").parent.mkdir(parents=True, exist_ok=True)
    (profile_config_path("default")).write_text(
        '{"api_base": "https://ctf.xidian.edu.cn/api", "insecure": false}', encoding="utf-8"
    )
    profile_config_path("dev").parent.mkdir(parents=True, exist_ok=True)
    (profile_config_path("dev")).write_text(
        '{"api_base": "https://ret2shell.home.undef.work:8443/api", "insecure": true}',
        encoding="utf-8",
    )
    comp = Ret2ConsoleCompleter(
        commands=lambda: _command_table(),
        vfs=_build_vfs(),
        dynamic_candidates=lambda _c, _b, _w: [],
    )
    # 用真实 repl 的 dynamic 回调（模拟 run_repl 的接线）
    from ret2console.repl import _dynamic_candidates
    from ret2console.state import App
    app = App()
    comp._dynamic_candidates = _dynamic_candidates(app)
    results = list(comp.get_completions(Document("auth profile use "), None))
    by_text = {c.text: c for c in results}
    assert set(by_text) >= {"default", "dev"}
    assert _meta(by_text["dev"]) == "https://ret2shell.home.undef.work:8443/api"
    assert all(c.start_position == 0 for c in results)
    # 前缀匹配：auth profile delete dev → dev（de 同时是 default/dev 的前缀）
    results = list(comp.get_completions(Document("auth profile delete dev"), None))
    assert [c.text for c in results] == ["dev"]


def test_complete_after_space_yields_subcommands():
    """auth<空格> 后 Tab：补全子命令且 start_position=0（不会替换已输入内容）。"""
    vfs = _build_vfs()
    await_loaded = asyncio.new_event_loop()
    await_loaded.run_until_complete(vfs.resolve("/"))
    await_loaded.close()
    comp = _completer(vfs)
    results = list(comp.get_completions(Document("auth "), None))
    texts = [c.text for c in results]
    assert "login" in texts
    assert "auth" not in texts, "不应再把 auth 当命令名补全（会造成 aauth）"
    assert "contests/" not in texts, "非路径命令的空格后不应混入 fs 候选"
    assert all(c.start_position == 0 for c in results)


def test_leaf_command_space_yields_nothing():
    """叶子命令（auth login / challenge start / team create）空格后 Tab：
    不应回退弹父级子命令菜单。"""
    from ret2console.repl import _command_table
    comp = Ret2ConsoleCompleter(
        commands=lambda: _command_table(),
        vfs=_build_vfs(),
        dynamic_candidates=lambda _c, _b, _w: [],
    )
    for line in ["auth login ", "auth endpoint ", "challenge start ", "team create ", "admin bulletin create "]:
        results = list(comp.get_completions(Document(line), None))
        assert results == [], f"{line!r} 不应有任何补全，实际: {[c.text for c in results]}"


def test_leaf_command_typing_word_yields_nothing():
    """叶子命令后的自由参数（auth login u / challenge start 1）不弹任何菜单。"""
    from ret2console.repl import _command_table
    comp = Ret2ConsoleCompleter(
        commands=lambda: _command_table(),
        vfs=_build_vfs(),
        dynamic_candidates=lambda _c, _b, _w: [],
    )
    for line in ["auth login u", "auth endpoint set", "challenge start 1"]:
        results = list(comp.get_completions(Document(line), None))
        assert results == [], f"{line!r} 不应有任何补全，实际: {[c.text for c in results]}"


def test_two_level_command_still_completes():
    """两级命令（auth profile / admin bulletin）仍是子命令位置，补全不受影响。"""
    from ret2console.repl import _command_table
    comp = Ret2ConsoleCompleter(
        commands=lambda: _command_table(),
        vfs=_build_vfs(),
        dynamic_candidates=lambda _c, _b, _w: [],
    )
    assert "list" in [c.text for c in comp.get_completions(Document("auth profile "), None)]
    assert "create" in [c.text for c in comp.get_completions(Document("admin bulletin "), None)]
    assert "login" not in [c.text for c in comp.get_completions(Document("auth profile "), None)]


def test_complete_after_path_with_space_uses_cwd():
    """cd contests/ 空格后 Tab：从当前目录补新路径，而不是继承 contests/ 前缀。"""
    vfs = _build_vfs()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(vfs.resolve("/"))
    loop.close()
    comp = _completer(vfs)
    results = list(comp.get_completions(Document("cd contests/ "), None))
    texts = [c.text for c in results]
    assert "contests/" in texts
    assert all(c.start_position == 0 for c in results)


@pytest.mark.asyncio
async def test_complete_triggers_async_load_across_levels():
    """输入 cd contests/ 后 Tab：自动异步加载 contests 并补出下面的比赛 id。"""
    import threading

    vfs = _build_vfs()  # 尚未加载 contests
    comp = Ret2ConsoleCompleter(
        commands=lambda: {"cd": []},
        vfs=vfs,
        current_path=lambda: "/",
        loop=asyncio.get_running_loop(),
    )
    results: list[str] = []

    def run():
        doc = Document("cd contests/")
        results.extend(c.text for c in comp.get_completions(doc, None))

    t = threading.Thread(target=run)
    t.start()
    await asyncio.sleep(0.3)  # 主 loop 空闲，处理加载任务
    t.join(timeout=5)

    assert not t.is_alive(), "补全线程卡死"
    assert "contests/3/" in results
