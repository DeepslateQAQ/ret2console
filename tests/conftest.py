import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

# 测试用独立数据目录，避免污染真实配置（必须强制覆盖：setdefault 会沿用真实 HOME）
_TEST_HOME = Path(__file__).resolve().parents[1] / ".test-home"
os.environ["HOME"] = str(_TEST_HOME)
os.environ["XDG_DATA_HOME"] = str(_TEST_HOME / ".local" / "share")

# 注册全部命令（与 CLI 入口一致）
from ret2console.cli import _register_all

_register_all()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前清空隔离的配置/会话文件。"""
    import shutil
    from pathlib import Path
    home = Path(os.environ["HOME"])
    r2c_dir = home / ".ret2console"
    if r2c_dir.exists():
        shutil.rmtree(r2c_dir)
    yield
