"""配置与会话持久化。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_API_BASE = "https://ctf.xidian.edu.cn/api"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ret2console"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ret2console"
LEGACY_DIR = Path.home() / ".ret2console"


def _ensure_dirs() -> None:
    (LEGACY_DIR).mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    api_base: str = DEFAULT_API_BASE
    download_dir: str = "."  # 附件下载目录（真实 cwd 语义）
    autoplay_dir: str = str(Path.home() / ".ret2console" / "autoplay")
    insecure: bool = False  # 跳过 TLS 校验（自签证书的测试服/内网实例）

    @classmethod
    def load(cls, path: Path = LEGACY_DIR / "config.json") -> "Config":
        _ensure_dirs()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: Path = LEGACY_DIR / "config.json") -> None:
        _ensure_dirs()
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class Session:
    token: str | None = None
    account: str | None = None
    nickname: str | None = None
    permissions: list[str] = field(default_factory=list)

    # r2s 权限：字符串名（部分实例）或 int 枚举（JWT 常见，0=Basic…9=DevOps）
    _ADMIN_NAMES = {"game", "host", "devops"}
    _ADMIN_IDS = {5, 6, 9}

    @property
    def logged_in(self) -> bool:
        return bool(self.token)

    @property
    def is_admin(self) -> bool:
        return any(
            (isinstance(p, str) and p.lower() in self._ADMIN_NAMES)
            or (isinstance(p, int) and p in self._ADMIN_IDS)
            for p in self.permissions
        )

    @property
    def display_name(self) -> str:
        return self.nickname or self.account or "guest"

    @classmethod
    def load(cls, path: Path = LEGACY_DIR / "session.json") -> "Session":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            return cls()

    def save(self, path: Path = LEGACY_DIR / "session.json") -> None:
        _ensure_dirs()
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)

    def clear(self, path: Path = LEGACY_DIR / "session.json") -> None:
        self.token = None
        self.account = None
        self.nickname = None
        self.permissions = []
        try:
            path.unlink()
        except FileNotFoundError:
            pass
