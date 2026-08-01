"""PoW 验证码求解（ret2shell /api/account/captcha/cli 通道）。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

POW_CHALLENGE_RE = re.compile(r"^(\d+)#(.+)$")


@dataclass(frozen=True)
class PowChallenge:
    captcha_id: str
    difficulty: int
    seed: str

    @classmethod
    def parse(cls, captcha_id: str, challenge: str) -> "PowChallenge":
        m = POW_CHALLENGE_RE.match(challenge)
        if not m:
            raise ValueError(f"无法解析 PoW challenge: {challenge!r}")
        return cls(captcha_id=captcha_id, difficulty=int(m.group(1)), seed=m.group(2))


def solve(challenge: PowChallenge) -> str:
    """求解：答案以 seed 开头，且 sha256(答案) 十六进制以 difficulty 个 0 开头。

    难度 4 平均 65536 次迭代，毫秒级。
    """
    prefix = "0" * challenge.difficulty
    suffix = 0
    while True:
        answer = f"{challenge.seed}{suffix}"
        if hashlib.sha256(answer.encode()).hexdigest().startswith(prefix):
            return answer
        suffix += 1
