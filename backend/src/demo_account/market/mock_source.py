"""Mock 行情与基础数据源（实现 3.5）：由种子生成确定性的价格路径、日历、停牌与公司行动。

同一个对象同时实现快照源与基础数据源。测试可以用 set_price、set_halted、add_suspension、
add_corporate_action 制造特定情形。
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..clock import Clock
from ..core.instruments import board_of, is_st, parse_symbol
from ..core.models import AssetType, Instrument, PriceLimits, Snapshot
from ..core.money import D, round_to_tick, tick_for
from ..core.rules import BEIJING, T_0930, T_1130, T_1300, T_1500, to_beijing
from .base import CalendarData, CorporateAction, DailyBar, Suspension

DEFAULT_HOLIDAYS: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        *[date(2026, 2, d) for d in range(16, 21)],
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        *[date(2026, 10, d) for d in range(1, 8)],
        date(2027, 1, 1),
    }
)

# 代码、名称、类型、基准价、上市日
DEFAULT_UNIVERSE: list[tuple[str, str, AssetType, str, date | None]] = [
    ("600000.SH", "浦发银行", AssetType.STOCK, "9.00", date(1999, 11, 10)),
    ("000001.SZ", "平安银行", AssetType.STOCK, "11.30", date(1991, 4, 3)),
    ("300750.SZ", "宁德时代", AssetType.STOCK, "250.00", date(2018, 6, 11)),
    ("688981.SH", "中芯国际", AssetType.STOCK, "90.00", date(2020, 7, 16)),
    ("510300.SH", "沪深300ETF", AssetType.ETF, "4.515", date(2012, 5, 28)),
    ("000016.SZ", "*ST康佳A", AssetType.STOCK, "3.20", date(1992, 3, 27)),
]

BENCHMARK_BASE = Decimal("4500")
ANCHOR = date(2026, 1, 1)  # 生成日线的起点


class MockMarket:
    name = "mock"

    def __init__(
        self,
        clock: Clock,
        seed: int = 2026,
        holidays: frozenset[date] = DEFAULT_HOLIDAYS,
        universe: Sequence[tuple[str, str, AssetType, str, date | None]] | None = None,
    ) -> None:
        self.clock = clock
        self.seed = seed
        self.holidays = holidays
        self._instruments: dict[str, Instrument] = {}
        self._base: dict[str, Decimal] = {}
        for symbol, name, asset_type, base, list_date in universe or DEFAULT_UNIVERSE:
            self.add_instrument(symbol, name, asset_type, Decimal(base), list_date)
        self._bars: dict[tuple[str, date], DailyBar] = {}
        self._suspensions: dict[date, list[Suspension]] = {}
        self._actions: dict[str, list[CorporateAction]] = {}
        self._price_override: dict[str, Decimal] = {}
        self._halted: set[str] = set()
        self._factor_override: dict[tuple[str, date], Decimal] = {}
        self.fail_quotes = False
        self.fail_reference = False

    # ---- 测试控制 ----
    def add_instrument(
        self,
        symbol: str,
        name: str,
        asset_type: AssetType,
        base_price: Decimal,
        list_date: date | None,
    ) -> None:
        code, exchange = parse_symbol(symbol)
        self._instruments[symbol] = Instrument(
            symbol=symbol,
            name=name,
            asset_type=asset_type,
            board=board_of(symbol, asset_type),
            exchange=exchange,
            list_date=list_date,
            is_st=is_st(name),
        )
        self._base[symbol] = base_price

    def set_price(self, symbol: str, price: Decimal | None) -> None:
        if price is None:
            self._price_override.pop(symbol, None)
        else:
            self._price_override[symbol] = D(price)

    def set_halted(self, symbol: str, halted: bool) -> None:
        if halted:
            self._halted.add(symbol)
        else:
            self._halted.discard(symbol)

    def add_suspension(self, symbol: str, d: date, reason: str = "临时停牌") -> None:
        self._suspensions.setdefault(d, []).append(Suspension(symbol=symbol, reason=reason))

    def add_corporate_action(self, action: CorporateAction) -> None:
        self._actions.setdefault(action.symbol, []).append(action)
        if action.factor is not None:
            self._factor_override[(action.symbol, action.ex_date)] = action.factor

    def set_daily_bar(self, bar: DailyBar) -> None:
        self._bars[(bar.symbol, bar.trade_date)] = bar

    # ---- 日历 ----
    def _open_days(self, start: date, end: date) -> list[date]:
        days = []
        d = start
        while d <= end:
            if d.weekday() < 5 and d not in self.holidays:
                days.append(d)
            d += timedelta(days=1)
        return days

    def is_open(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def trading_calendar(self, year: int) -> CalendarData:
        start, end = date(year, 1, 1), date(year, 12, 31)
        return CalendarData(start=start, end=end, open_days=self._open_days(start, end))

    # ---- 基础数据 ----
    def instrument(self, symbol: str) -> Instrument | None:
        self._check_reference()
        return self._instruments.get(symbol)

    def _rng(self, *parts: object) -> random.Random:
        return random.Random(f"{self.seed}:" + ":".join(str(p) for p in parts))

    def _prev_open_day(self, d: date) -> date:
        p = d - timedelta(days=1)
        while not self.is_open(p):
            p -= timedelta(days=1)
        return p

    def _is_suspended(self, symbol: str, d: date) -> bool:
        return any(s.symbol == symbol for s in self._suspensions.get(d, []))

    def bar(self, symbol: str, d: date) -> DailyBar | None:
        """确定性日线：从基准价出发按交易日逐日随机游走，停牌日无日线，结果缓存。"""
        if symbol not in self._instruments or not self.is_open(d) or d < ANCHOR:
            return None
        key = (symbol, d)
        if key in self._bars:
            return self._bars[key]
        if self._is_suspended(symbol, d):
            return None
        tick = tick_for(self._instruments[symbol].asset_type)
        day = ANCHOR if self.is_open(ANCHOR) else self._next_open_day(ANCHOR)
        close_prev = self._base[symbol]
        while day <= d:
            k = (symbol, day)
            existing = self._bars.get(k)
            if existing is not None:
                close_prev = existing.close
            elif not self._is_suspended(symbol, day):
                rng = self._rng(symbol, day)
                factor = self._factor_override.get(k)
                ref = close_prev if factor is None else round_to_tick(close_prev / factor, tick)
                r = max(-0.09, min(0.09, rng.gauss(0, 0.012)))
                close = round_to_tick(ref * (1 + Decimal(repr(r))), tick)
                open_ = round_to_tick(ref * (1 + Decimal(repr(rng.gauss(0, 0.004)))), tick)
                high = max(open_, close, round_to_tick(close * Decimal("1.004"), tick))
                low = min(open_, close, round_to_tick(close * Decimal("0.996"), tick))
                self._bars[k] = DailyBar(
                    symbol=symbol,
                    trade_date=day,
                    open=open_,
                    high=high,
                    low=low,
                    close=close,
                    adj_close=close,
                    prev_close=ref,
                    volume=rng.randint(100_000, 5_000_000),
                    up_limit=None,
                    down_limit=None,
                )
                close_prev = close
            day = self._next_open_day(day)
        return self._bars.get(key)

    def _next_open_day(self, d: date) -> date:
        n = d + timedelta(days=1)
        while not self.is_open(n):
            n += timedelta(days=1)
        return n

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        self._check_reference()
        out = []
        for d in self._open_days(start, end):
            b = self.bar(symbol, d)
            if b is not None:
                out.append(b)
        return out

    def price_limits(self, symbol: str, d: date) -> PriceLimits | None:
        self._check_reference()
        return None  # 由 ReferenceService 按规则计算

    def suspensions(self, d: date) -> list[Suspension]:
        self._check_reference()
        return list(self._suspensions.get(d, []))

    def corporate_actions(self, symbol: str) -> list[CorporateAction]:
        self._check_reference()
        return list(self._actions.get(symbol, []))

    def index_daily(self, code: str, start: date, end: date) -> list[tuple[date, Decimal]]:
        self._check_reference()
        out = []
        level = BENCHMARK_BASE
        day = date(2026, 1, 1)
        while day <= end:
            if self.is_open(day):
                rng = self._rng(code, day)
                level = (level * (1 + Decimal(repr(rng.gauss(0, 0.008))))).quantize(
                    Decimal("0.001")
                )
                if day >= start:
                    out.append((day, level))
            day += timedelta(days=1)
        return out

    # ---- 快照 ----
    def get_snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]:
        if self.fail_quotes:
            from .base import MarketDataError

            raise MarketDataError("mock 行情源不可用")
        now = to_beijing(self.clock.now())
        today = now.date()
        out: dict[str, Snapshot] = {}
        if not self.is_open(today):
            return out
        for symbol in symbols:
            inst = self._instruments.get(symbol)
            if inst is None:
                continue
            if symbol in self._halted or self._is_suspended(symbol, today):
                # 停牌：只给带停牌标志的快照
                prev = self.bar(symbol, self._prev_open_day(today))
                pc = prev.close if prev else self._base[symbol]
                out[symbol] = Snapshot(
                    symbol=symbol,
                    ts=now,
                    last=pc,
                    open=pc,
                    high=pc,
                    low=pc,
                    prev_close=pc,
                    halted=True,
                    up_limit=None,
                    down_limit=None,
                    source=self.name,
                )
                continue
            bar = self.bar(symbol, today)
            if bar is None or bar.open is None or bar.prev_close is None:
                continue
            tick = tick_for(inst.asset_type)
            override = self._price_override.get(symbol)
            if override is not None:
                last = override
            else:
                last = self._intraday_price(symbol, bar, now, tick)
            out[symbol] = Snapshot(
                symbol=symbol,
                ts=now,
                last=last,
                open=bar.open,
                high=max(bar.high or last, last),
                low=min(bar.low or last, last),
                prev_close=bar.prev_close,
                halted=False,
                up_limit=None,
                down_limit=None,
                source=self.name,
            )
        return out

    def _intraday_price(self, symbol: str, bar: DailyBar, now: datetime, tick: Decimal) -> Decimal:
        """开盘价到收盘价之间按已过交易时间线性插值，叠加确定性噪声。"""
        t = now.time()
        if t < T_0930:
            frac = Decimal("0")
        elif t >= T_1500:
            frac = Decimal("1")
        else:
            minutes = 0
            if t > T_0930:
                end = min(t, T_1130)
                minutes += (end.hour * 60 + end.minute) - (9 * 60 + 30)
            if t > T_1300:
                end = min(t, T_1500)
                minutes += (end.hour * 60 + end.minute) - (13 * 60)
            frac = Decimal(minutes) / Decimal(240)
        assert bar.open is not None
        rng = self._rng(symbol, now.date(), now.hour, now.minute)
        noise = Decimal(repr(rng.gauss(0, 0.002)))
        price = bar.open + (bar.close - bar.open) * frac
        price = price * (1 + noise)
        return round_to_tick(price, tick)

    def _check_reference(self) -> None:
        if self.fail_reference:
            from .base import MarketDataError

            raise MarketDataError("mock 基础数据源不可用")

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "ok": not (self.fail_quotes or self.fail_reference)}


def mock_now(d: date, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hh, mm, ss, tzinfo=BEIJING)
