"""行情快照源与基础数据源的接口抽象（实现 3.1）。"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from ..core.models import Instrument, PriceLimits, Snapshot


class MarketDataError(Exception):
    """数据源不可用或返回不可解析的数据。"""


@dataclass(frozen=True)
class DailyBar:
    symbol: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal  # 未复权收盘价
    adj_close: Decimal | None  # 数据源的前复权收盘价，用于识别 ETF 除权除息
    prev_close: Decimal | None
    volume: int | None
    up_limit: Decimal | None
    down_limit: Decimal | None


@dataclass(frozen=True)
class Suspension:
    symbol: str
    reason: str


@dataclass(frozen=True)
class CorporateAction:
    symbol: str
    ex_date: date
    record_date: date | None
    pay_date: date | None
    bonus_per_share: Decimal  # 每股送股
    transfer_per_share: Decimal  # 每股转股
    cash_per_share: Decimal  # 每股税前派现
    factor: Decimal | None  # ETF 因子，数量按 factor 调整
    source: str


@dataclass(frozen=True)
class CalendarData:
    start: date
    end: date
    open_days: list[date]


class QuoteSource(Protocol):
    name: str

    def get_snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]: ...

    def health(self) -> dict[str, Any]: ...


class ReferenceDataSource(Protocol):
    name: str

    def trading_calendar(self, year: int) -> CalendarData: ...

    def instrument(self, symbol: str) -> Instrument | None: ...

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]: ...

    def price_limits(self, symbol: str, d: date) -> PriceLimits | None: ...

    def suspensions(self, d: date) -> list[Suspension]: ...

    def corporate_actions(self, symbol: str) -> list[CorporateAction]: ...

    def index_daily(self, code: str, start: date, end: date) -> list[tuple[date, Decimal]]: ...

    def health(self) -> dict[str, Any]: ...
