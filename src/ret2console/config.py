"""配置与会话持久化（多 profile：凭证与 API endpoint 按 profile 独立）。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_API_BASE = "https://ctf.xidian.edu.cn/api"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ret2console"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ret2console"
LEGACY_DIR = Path.home() / ".ret2console"
DEFAULT_PROFILE = "default"
_PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _ensure_dirs() -> None:
    LEGACY_DIR.mkdir(parents=True, exist_ok=True)


def profiles_dir() -> Path:
    return LEGACY_DIR / "profiles"


def profile_dir(name: str) -> Path:
    return profiles_dir() / name


def profile_config_path(name: str) -> Path:
    return profile_dir(name) / "config.json"


def profile_session_path(name: str) -> Path:
    return profile_dir(name) / "session.json"


def valid_profile_name(name: str) -> bool:
    """profile 名：字母/数字开头，仅含 [A-Za-z0-9._-]，最长 32。"""
    return bool(_PROFILE_NAME_RE.fullmatch(name))


def list_profiles() -> list[str]:
    d = profiles_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.name for p in d.iterdir()
        if p.is_dir() and valid_profile_name(p.name)
    )


def profile_exists(name: str) -> bool:
    # default 是内置兜底 profile：目录被删除后 use/delete 仍可引用，用时再实体化
    return name == DEFAULT_PROFILE or profile_dir(name).is_dir()


@dataclass
class Config:
    api_base: str = DEFAULT_API_BASE
    download_dir: str = "."  # 附件下载目录（真实 cwd 语义）
    autoplay_dir: str = str(Path.home() / ".ret2console" / "autoplay")
    insecure: bool = False  # 跳过 TLS 校验（自签证书的测试服/内网实例）
    active_profile: str = DEFAULT_PROFILE  # 当前 profile（凭证/endpoint 归属）
    path: Path | None = field(default=None, repr=False, compare=False)  # 全局配置存储路径

    @classmethod
    def load(cls, path: Path | None = None, *, profile: str | None = None) -> "Config":
        """全局配置 + 当前 profile 的 endpoint 合并加载。

        兼容旧版：全局 config.json 里的 api_base/insecure 视为 default
        profile 的迁移数据（profile 级文件存在时优先）。
        `profile` 仅覆盖本次加载的 active_profile，不写回磁盘（供 --profile 覆盖）。
        """
        _ensure_dirs()
        path = Path(path or LEGACY_DIR / "config.json")
        cfg: dict = {}
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        active = str(profile or cfg.get("active_profile") or DEFAULT_PROFILE)
        if not valid_profile_name(active):
            active = DEFAULT_PROFILE
        # 旧版全局 endpoint → default profile 迁移兜底
        legacy_ep = {k: v for k, v in cfg.items() if k in ("api_base", "insecure")}
        ep: dict = {}
        try:
            ep = json.loads(profile_config_path(active).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            ep = legacy_ep if active == DEFAULT_PROFILE else {}
        merged = {**cfg, **ep, "active_profile": active}
        conf = cls(**{k: v for k, v in merged.items() if k in cls.__dataclass_fields__})
        conf.path = path
        return conf

    def save(self, path: Path | None = None) -> None:
        """全局设置写入 config.json；api_base/insecure 写入当前 profile 的 config.json。"""
        _ensure_dirs()
        path = Path(path or self.path or LEGACY_DIR / "config.json")
        global_keys = ("download_dir", "autoplay_dir", "active_profile")
        data = {k: v for k, v in asdict(self).items() if k in global_keys}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        ep_keys = ("api_base", "insecure")
        ep = {k: v for k, v in asdict(self).items() if k in ep_keys}
        ppath = profile_config_path(self.active_profile)
        ppath.parent.mkdir(parents=True, exist_ok=True)
        ppath.write_text(json.dumps(ep, ensure_ascii=False, indent=2), encoding="utf-8")

    def switch_profile(self, name: str) -> None:
        """切换到另一 profile：加载其 endpoint，保留全局设置。"""
        self.active_profile = name
        ep: dict = {}
        try:
            ep = json.loads(profile_config_path(name).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            ep = {}
        self.api_base = str(ep.get("api_base") or DEFAULT_API_BASE)
        self.insecure = bool(ep.get("insecure", False))
        self.save()


@dataclass
class Session:
    token: str | None = None
    account: str | None = None
    nickname: str | None = None
    permissions: list[str] = field(default_factory=list)
    path: Path | None = field(default=None, repr=False, compare=False)  # 当前 profile 会话存储路径

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
    def load(cls, name: str | None = None, *, path: Path | None = None) -> "Session":
        """加载会话：name 指定 profile；无参时读旧版全局路径（兼容）。"""
        _ensure_dirs()
        if path is None:
            if name is None:
                path = LEGACY_DIR / "session.json"
            else:
                profile_path = profile_session_path(name)
                legacy = LEGACY_DIR / "session.json"
                if name == DEFAULT_PROFILE and not profile_path.exists() and legacy.exists():
                    # 旧版全局 session.json → default profile 一次性迁移（原文件保留作备份）
                    profile_path.parent.mkdir(parents=True, exist_ok=True)
                    legacy.replace(profile_path)
                path = profile_path
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            session = cls()
        session.path = Path(path)
        return session

    def save(self, path: Path | None = None) -> None:
        _ensure_dirs()
        path = Path(path or self.path or LEGACY_DIR / "session.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        path.chmod(0o600)

    def clear(self, path: Path | None = None) -> None:
        self.token = None
        self.account = None
        self.nickname = None
        self.permissions = []
        path = Path(path or self.path or LEGACY_DIR / "session.json")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
