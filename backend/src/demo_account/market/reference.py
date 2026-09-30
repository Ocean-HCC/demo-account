"""参考数据缓存与日历服务（实现 3.4）。services 只通过这里读基础数据，数据落在 store 中。"""

from __future__ import annotations

import logging
import threading
from datetime import date, timedelta
from decimal import Decimal

from ..clock import Clock
from ..config import Settings
from ..core.instruments import limit_rate, new_listing_no_limit, price_limits
from ..core.models import Instrument, PriceLimits
from ..core.money import TICK
from ..core.rules import CalendarUnavailable, SimpleCalendar
from ..store import repos
from ..store.db import Database
from .base import CorporateAction, DailyBar, MarketDataError, ReferenceDataSource

log = logging.getLogger(__name__)


class ReferenceService:
    def __init__(
        self, db: Database, source: ReferenceDataSource, clock: Clock, settings: Settings
    ) -> None:
        self.db = db
        self.source = source
        self.clock = clock
        self.settings = settings
        self._cal: SimpleCalendar | None = None
        self._lock = threading.Lock()
        self._susp_loaded: set[date] = set()
        self._inst_checked: dict[str, date] = {}
        self._limits: dict[tuple[str, date], PriceLimits] = {}
        self._recent_loaded: set[tuple[str, date]] = set()
        self._cal_attempts: dict[int, date] = {}
        self.failures = 0
        self.last_error: str | None = None

    def _ok(self) -> None:
        self.failures = 0

    def _fail(self, e: Exception) -> None:
        self.failures += 1
        self.last_error = str(e)
        log.warning("reference source error: %s", e)

    # ---------------------------------------------------------------- calendar

    def ensure_calendar(self, d: date) -> None:
        """确保日历覆盖 d 所在年份；12 月时顺带拉取下一年（交易所通常年底公布）。"""
        years = [d.year] + ([d.year + 1] if d.month == 12 else [])
        cov = repos.calendar_coverage(self.db.read())
        need = [
            y
            for y in years
            if cov is None or not (cov[0] <= date(y, 1, 1) and date(y, 12, 31) <= cov[1])
        ]
        today = self.clock.now().date()
        for y in need:
            if self._cal_attempts.get(y) == today and (cov is not None and cov[1] >= d):
                continue  # 当天已拉取过且已覆盖目标日期，交易所尚未公布的部分次日再试
            self._cal_attempts[y] = today
            try:
                data = self.source.trading_calendar(y)
            except MarketDataError as e:
                self._fail(e)
                if y == d.year:
                    raise
                continue
            self._ok()
            open_set = set(data.open_days)
            rows = []
            day = data.start
            while day <= data.end:
                rows.append((day, day in open_set))
                day += timedelta(days=1)
            with self.db.write() as tx:
                repos.upsert_calendar(tx, rows, self.source.name)
            with self._lock:
                self._cal = None

    def calendar(self) -> SimpleCalendar:
        with self._lock:
            if self._cal is None:
                rows = repos.calendar_rows(self.db.read())
                if not rows:
                    raise CalendarUnavailable("交易日历为空")
                self._cal = SimpleCalendar(
                    [d for d, is_open in rows if is_open], coverage=(rows[0][0], rows[-1][0])
                )
            return self._cal

    # ---------------------------------------------------------------- instruments

    def instrument(self, symbol: str, force: bool = False) -> Instrument | None:
        """标的信息：每个标的每天向数据源确认一次（名称变化影响 ST 判定），失败时用缓存。"""
        today = self.clock.now().date()
        cached = repos.get_instrument(self.db.read(), symbol)
        if cached is not None and not force and self._inst_checked.get(symbol) == today:
            return cached
        try:
            inst = self.source.instrument(symbol)
        except MarketDataError as e:
            self._fail(e)
            if cached is None:
                raise
            return cached
        self._ok()
        self._inst_checked[symbol] = today
        if inst is None:
            return cached
        with self.db.write() as tx:
            repos.upsert_instrument(tx, inst, self.clock.now())
        return inst

    def search(self, q: str, limit: int = 20) -> list[Instrument]:
        return repos.search_instruments(self.db.read(), q, limit)

    # ---------------------------------------------------------------- daily bars

    def daily_bar(self, symbol: str, d: date, refresh: bool = False) -> DailyBar | None:
        bar = repos.get_bar(self.db.read(), symbol, d)
        if bar is not None and not refresh:
            return bar
        try:
            bars = self.source.daily_bars(symbol, d, d)
        except MarketDataError as e:
            self._fail(e)
            return bar
        self._ok()
        if bars:
            with self.db.write() as tx:
                for b in bars:
                    repos.upsert_bar(tx, b)
        return repos.get_bar(self.db.read(), symbol, d)

    def _ensure_recent(self, symbol: str, d: date) -> None:
        key = (symbol, d)
        if key in self._recent_loaded:
            return
        try:
            bars = self.source.daily_bars(symbol, d - timedelta(days=40), d)
        except MarketDataError as e:
            self._fail(e)
            return
        self._ok()
        if bars:
            with self.db.write() as tx:
                for b in bars:
                    repos.upsert_bar(tx, b)
        self._recent_loaded.add(key)

    def last_close_before(self, symbol: str, d: date) -> DailyBar | None:
        """严格早于 d 的最近一根日线。"""
        bar = repos.latest_bar_on_or_before(self.db.read(), symbol, d - timedelta(days=1))
        if bar is None:
            self._ensure_recent(symbol, d)
            bar = repos.latest_bar_on_or_before(self.db.read(), symbol, d - timedelta(days=1))
        return bar

    def close_on_or_before(self, symbol: str, d: date) -> DailyBar | None:
        bar = repos.latest_bar_on_or_before(self.db.read(), symbol, d)
        if bar is None:
            self._ensure_recent(symbol, d)
            bar = repos.latest_bar_on_or_before(self.db.read(), symbol, d)
        return bar

    def reference_prev_close(self, symbol: str, trade_date: date, today: date) -> Decimal | None:
        """所属交易日的前收盘价。所属交易日尚未开始时用今天及以前最近的收盘价推算。"""
        if trade_date <= today:
            bar = repos.get_bar(self.db.read(), symbol, trade_date)
            if bar is not None and bar.prev_close is not None:
                return bar.prev_close
            prev = self.last_close_before(symbol, trade_date)
            return prev.close if prev else None
        latest = self.close_on_or_before(symbol, today)
        return latest.close if latest else None

    # ---------------------------------------------------------------- price limits

    def price_limits(
        self, symbol: str, d: date, inst: Instrument | None = None, today: date | None = None
    ) -> PriceLimits:
        """涨跌停价：日线或数据源给出的值优先，否则按规则由前收盘价计算。"""
        key = (symbol, d)
        if key in self._limits:
            return self._limits[key]
        inst = inst or self.instrument(symbol)
        if inst is None:
            raise MarketDataError(f"标的信息缺失: {symbol}")
        bar = repos.get_bar(self.db.read(), symbol, d)
        if bar is not None and bar.up_limit is not None and bar.down_limit is not None:
            result = PriceLimits(up=bar.up_limit, down=bar.down_limit)
            self._limits[key] = result
            return result
        src: PriceLimits | None = None
        today = today or self.clock.now().date()
        if d <= today:
            try:
                src = self.source.price_limits(symbol, d)
                self._ok()
            except MarketDataError as e:
                self._fail(e)
        if src is not None:
            self._limits[key] = src
            return src
        cal = self.calendar()
        rank = cal.trading_day_rank(inst.list_date, d) if inst.list_date else None
        if new_listing_no_limit(rank):
            result = PriceLimits(up=None, down=None)
        else:
            pc = self.reference_prev_close(symbol, d, today)
            if pc is None:
                raise MarketDataError(f"缺少前收盘价: {symbol} {d}")
            result = price_limits(pc, limit_rate(inst.board), TICK)
        if d <= today:
            self._limits[key] = result
        return result

    # ---------------------------------------------------------------- suspensions

    def refresh_suspensions(self, d: date) -> None:
        rows = self.source.suspensions(d)
        self._ok()
        with self.db.write() as tx:
            repos.replace_suspensions(tx, d, rows, self.source.name)
        self._susp_loaded.add(d)

    def is_suspended(self, symbol: str, d: date) -> bool:
        if d not in self._susp_loaded:
            try:
                self.refresh_suspensions(d)
            except MarketDataError as e:
                self._fail(e)
        return repos.is_suspended(self.db.read(), symbol, d)

    # ---------------------------------------------------------------- corporate actions

    def refresh_corporate_actions(self, symbol: str) -> None:
        actions = self.source.corporate_actions(symbol)
        self._ok()
        if actions:
            with self.db.write() as tx:
                for a in actions:
                    repos.upsert_corporate_action(tx, a)

    def corporate_actions_for(self, symbol: str) -> list[CorporateAction]:
        return repos.corporate_actions_for(self.db.read(), symbol)

    # ---------------------------------------------------------------- benchmark

    def benchmark_close(self, d: date) -> Decimal | None:
        code = self.settings.benchmark_code
        v = repos.get_benchmark(self.db.read(), code, d)
        if v is not None:
            return v
        try:
            rows = self.source.index_daily(code, d - timedelta(days=40), d)
        except MarketDataError as e:
            self._fail(e)
            return None
        self._ok()
        if rows:
            with self.db.write() as tx:
                for day, close in rows:
                    repos.upsert_benchmark(tx, code, day, close, self.source.name)
        return repos.get_benchmark(self.db.read(), code, d)

    def benchmark_base(self, created: date) -> Decimal | None:
        """基准从账户创建日起归一化：创建日为交易日取当日收盘，否则取之前最近的交易日。"""
        cal = self.calendar()
        base_day = created if cal.is_trading_day(created) else cal.prev_trading_day(created)
        return self.benchmark_close(base_day)

    # ---------------------------------------------------------------- daily refresh

    def refresh_day(self, d: date, symbols: list[str]) -> None:
        """8:30 刷新：日历、当日停复牌、相关标的信息与分红送转、涨跌停价。"""
        self.ensure_calendar(d)
        try:
            self.refresh_suspensions(d)
        except MarketDataError as e:
            self._fail(e)
        for s in symbols:
            try:
                inst = self.instrument(s, force=True)
                self.refresh_corporate_actions(s)
                if inst is not None and self.calendar().is_trading_day(d):
                    self.price_limits(s, d, inst, today=d)
            except MarketDataError as e:
                self._fail(e)
