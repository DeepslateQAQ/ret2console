"""应用状态：登录会话、当前上下文（虚拟路径/比赛/题目）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ret2console.api.client import ApiClient
from ret2console.config import Config, Session
from ret2console.vfs import Vfs
from ret2console.wsrx import Wsrx


@dataclass
class Context:
    """当前导航上下文。虚拟路径与比赛/题目 id 互为镜像（cd 即 use）。"""

    vfs_path: str = "/"
    game_id: int | None = None
    challenge_id: int | None = None

    def set_game(self, game_id: int) -> None:
        self.game_id = game_id
        self.challenge_id = None
        self.vfs_path = f"/contests/{game_id}"

    def set_challenge(self, game_id: int, challenge_id: int) -> None:
        self.game_id = game_id
        self.challenge_id = challenge_id
        self.vfs_path = f"/contests/{game_id}/challenges/{challenge_id}"

    def set_path(self, path: str, game_id: int | None, challenge_id: int | None) -> None:
        self.vfs_path = path
        self.game_id = game_id
        self.challenge_id = challenge_id


@dataclass
class App:
    config: Config = field(default_factory=Config.load)
    session: Session = field(default_factory=Session.load)
    ctx: Context = field(default_factory=Context)
    api: ApiClient = field(default_factory=ApiClient)
    vfs: Vfs = field(default_factory=Vfs)
    # 动态补全缓存：game_id -> {id: 标题}；challenge 缓存挂在 game_id 下
    games_cache: dict[int, str] = field(default_factory=dict)
    challenges_cache: dict[int, dict[int, str]] = field(default_factory=dict)
    wsrx: Wsrx = field(default_factory=Wsrx)
    _started: bool = False

    def __post_init__(self) -> None:
        if not self._started:
            self._started = True
            # 用已保存的会话初始化 API 客户端
            self.api.configure(
                base_url=self.config.api_base, token=self.session.token,
                insecure=self.config.insecure,
            )

    def set_token(self, token: str | None) -> None:
        self.session.token = token
        self.api.configure(base_url=self.config.api_base, token=token)
        self.session.save()
