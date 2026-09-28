"""时钟抽象：所有业务时间通过 Clock 获取，测试用 FakeClock 推进。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from .core.rules import BEIJING, to_beijing


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=BEIJING)


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self._now = to_beijing(start)

    def now(self) -> datetime:
        return self._now

    def set(self, dt: datetime) -> None:
        self._now = to_beijing(dt)

    def advance(self, seconds: float = 0, **kwargs: float) -> None:
        self._now = self._now + timedelta(seconds=seconds, **kwargs)
