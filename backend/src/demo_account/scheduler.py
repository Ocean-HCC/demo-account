"""后台调度（实现 5.6）：按北京时间触发参考数据刷新、盘中撮合、开盘撮合、日终结算与投递。

scheduler 只决定什么时候调用哪个 service；所有业务逻辑都在 services 中。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import date, datetime, time, timedelta

from .core.rules import CalendarUnavailable, Session, session_of
from .market.base import MarketDataError
from .services.container import Container

log = logging.getLogger(__name__)
REFRESH_AT = time(8, 30)
SETTLE_GIVE_UP = time(23, 0)
SETTLE_RETRY = timedelta(minutes=30)
DELIVERY_EVERY = 5.0


class Scheduler:
    def __init__(self, c: Container) -> None:
        self.c = c
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._refreshed: date | None = None
        self._last_cycle: datetime | None = None
        self._settled: date | None = None
        self._settle_next: datetime | None = None
        self._last_delivery: datetime | None = None
        self._calendar_alerted: date | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                await self.tick()
            except Exception:  # 调度循环不能因单次失败退出
                log.exception("scheduler tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)

    async def tick(self) -> None:
        c = self.c
        now = c.clock.now()
        today = now.date()
        t = now.time()
        if (
            self._last_delivery is None
            or (now - self._last_delivery).total_seconds() >= DELIVERY_EVERY
        ):
            self._last_delivery = now
            await asyncio.to_thread(c.delivery.run_once)
        try:
            await asyncio.to_thread(c.reference.ensure_calendar, today)
            trading = c.reference.calendar().is_trading_day(today)
        except (CalendarUnavailable, MarketDataError) as e:
            if self._calendar_alerted != today:
                self._calendar_alerted = today
                c.alerts.raise_alert("error", "CALENDAR_UNAVAILABLE", f"交易日历不可用：{e}", today)
            return
        if not trading:
            return
        if self._refreshed != today and t >= REFRESH_AT:
            symbols = c.engine.watch_symbols(today)
            await asyncio.to_thread(c.reference.refresh_day, today, symbols)
            self._refreshed = today
        if session_of(now, trading) is Session.CONTINUOUS and (
            self._last_cycle is None
            or (now - self._last_cycle).total_seconds() >= c.settings.snapshot_interval
        ):
            self._last_cycle = now
            await asyncio.to_thread(c.engine.run_open_matching)
            await asyncio.to_thread(c.engine.run_intraday_cycle)
        if (
            c.settings.settle_time <= t < SETTLE_GIVE_UP
            and self._settled != today
            and (self._settle_next is None or now >= self._settle_next)
        ):
            run = await asyncio.to_thread(c.settlement.settle, today)
            if run.status == "done":
                self._settled = today
                self._settle_next = None
            else:
                self._settle_next = now + SETTLE_RETRY
