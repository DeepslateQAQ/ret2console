import pytest

from ret2console.vfs import Vfs, VfsError, dir_node, file_node


async def _simple_build():
    vfs = Vfs()

    async def contests_loader():
        return [dir_node("3", game_id=3, loader=_game3_loader)]

    async def _game3_loader():
        return [
            file_node("info", game_id=3, reader=lambda: "比赛 3 信息"),
            dir_node("challenges", game_id=3, loader=_challenges_loader),
        ]

    async def _challenges_loader():
        return [dir_node("5", game_id=3, challenge_id=5, loader=_ch5_loader)]

    async def _ch5_loader():
        return [file_node("README", game_id=3, challenge_id=5, reader=lambda: "# 题面")]

    vfs.build(contests_loader=contests_loader)
    return vfs


@pytest.mark.asyncio
async def test_resolve_root():
    vfs = await _simple_build()
    node = await vfs.resolve("/")
    assert node.name == "/"


@pytest.mark.asyncio
async def test_resolve_nested():
    vfs = await _simple_build()
    node = await vfs.resolve("/contests/3/challenges/5")
    assert node.challenge_id == 5


@pytest.mark.asyncio
async def test_read_file():
    vfs = await _simple_build()
    text = await vfs.read("/contests/3/challenges/5/README")
    assert "# 题面" in text


@pytest.mark.asyncio
async def test_missing_path():
    vfs = await _simple_build()
    with pytest.raises(VfsError):
        await vfs.resolve("/contests/99")


@pytest.mark.asyncio
async def test_parent_path():
    vfs = await _simple_build()
    assert vfs.parent_path("/contests/3/challenges/5") == "/contests/3/challenges"
    assert vfs.parent_path("/contests") == "/"
    assert vfs.parent_path("/") == "/"
