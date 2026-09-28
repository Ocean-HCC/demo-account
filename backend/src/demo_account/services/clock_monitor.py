"""时钟偏差监测（实现 5.6）：用公开接口响应的 Date 头比对本机时间，偏差过大时暂停下单并告警。"""

from __future__ import annotations

from datetime import datetime

from ..clock import Clock
from .events import AlertService
from .orders import RuntimeState

CLOCK_SKEW_SECONDS = 60


class ClockMonitor:
    def __init__(
        self, clock: Clock, state: RuntimeState, threshold: int = CLOCK_SKEW_SECONDS
    ) -> None:
        self.clock = clock
        self.state = state
        self.threshold = threshold
        self.alerts: AlertService | None = None
        self.offset_seconds: float | None = None
        self._alerted = False

    def observe(self, server_time: datetime) -> None:
        offset = (self.clock.now() - server_time).total_seconds()
        self.offset_seconds = offset
        skew = abs(offset) > self.threshold
        self.state.clock_skew = skew
        if skew and not self._alerted:
            self._alerted = True
            if self.alerts is not None:
                self.alerts.raise_alert(
                    "error",
                    "CLOCK_SKEW",
                    f"本机时间与公开接口服务器时间相差 {int(offset)} 秒，暂停下单",
                    self.clock.now().date(),
                )
        elif not skew:
            self._alerted = False
