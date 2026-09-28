"""交易时段、交易日历与订单所属交易日。所有时间为北京时间（固定 UTC+8）。"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol

from .models import OrderType, Session

BEIJING = timezone(timedelta(hours=8), "Asia/Shanghai")

T_0915 = time(9, 15)
T_0925 = time(9, 25)
T_0930 = time(9, 30)
T_1130 = time(11, 30)
T_1300 = time(13, 0)
T_1457 = time(14, 57)
T_1500 = time(15, 0)
T_1505 = time(15, 5)
T_1530 = time(15, 30)


class CalendarUnavailable(Exception):
    """交易日历未覆盖目标日期。"""


class Calendar(Protocol):
    def is_trading_day(self, d: date) -> bool: ...

    def next_trading_day(self, d: date) -> date: ...

    def prev_trading_day(self, d: date) -> date: ...

    def trading_day_rank(self, list_date: date, d: date) -> int: ...


class SimpleCalendar:
    """由已知开市日集合构成的日历。覆盖范围外的查询抛 CalendarUnavailable。"""

    def __init__(self, open_days: Iterable[date], coverage: tuple[date, date] | None = None):
        self._open = sorted(set(open_days))
        self._set = set(self._open)
        if coverage is not None:
            self._start, self._end = coverage
        elif self._open:
            self._start, self._end = self._open[0], self._open[-1]
        else:
            self._start, self._end = date.max, date.min

    def _check(self, d: date) -> None:
        if not (self._start <= d <= self._end):
            raise CalendarUnavailable(f"交易日历未覆盖 {d.isoformat()}")

    def is_trading_day(self, d: date) -> bool:
        self._check(d)
        return d in self._set

    def next_trading_day(self, d: date) -> date:
        """严格晚于 d 的第一个交易日。"""
        self._check(d)
        idx = bisect_right(self._open, d)
        if idx >= len(self._open):
            raise CalendarUnavailable(f"交易日历未覆盖 {d.isoformat()} 之后的日期")
        return self._open[idx]

    def prev_trading_day(self, d: date) -> date:
        """严格早于 d 的最后一个交易日。"""
        self._check(d)
        idx = bisect_left(self._open, d) - 1
        if idx < 0:
            raise CalendarUnavailable(f"交易日历未覆盖 {d.isoformat()} 之前的日期")
        return self._open[idx]

    def trading_day_rank(self, list_date: date, d: date) -> int:
        """d 是自 list_date 起的第几个交易日（上市日为 1）。

        list_date 早于覆盖范围时返回一个大数，表示肯定不是新股。
        """
        if list_date < self._start:
            return 10**6
        self._check(d)
        lo = bisect_left(self._open, list_date)
        hi = bisect_right(self._open, d)
        return max(hi - lo, 0)


def to_beijing(dt: datetime) -> datetime:
    """无时区的时间视为北京时间；有时区的转换到北京时间。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BEIJING)
    return dt.astimezone(BEIJING)


def session_of(now: datetime, trading_day: bool) -> Session:
    """按北京时间判断当前所处时段。非交易日一律为 closed。"""
    if not trading_day:
        return Session.CLOSED
    t = to_beijing(now).time()
    if t < T_0915:
        return Session.PRE_OPEN
    if t < T_0925:
        return Session.OPENING_AUCTION
    if t < T_0930:
        return Session.PRE_OPEN
    if t < T_1130:
        return Session.CONTINUOUS
    if t < T_1300:
        return Session.LUNCH
    if t < T_1457:
        return Session.CONTINUOUS
    if t < T_1500:
        return Session.CLOSING_AUCTION
    if t < T_1505:
        return Session.CLOSED
    if t < T_1530:
        return Session.AFTER_HOURS
    return Session.CLOSED


def can_submit(order_type: OrderType, session: Session) -> bool:
    """即时市价单只能在连续竞价时段提交，其余类型随时可提交（方案 3.2）。"""
    if order_type is OrderType.MARKET:
        return session is Session.CONTINUOUS
    return True


_CUTOFF: dict[OrderType, time] = {
    OrderType.LIMIT: T_1500,
    OrderType.OPEN: T_0925,
    OrderType.CLOSE: T_1530,
}


def assign_trade_date(order_type: OrderType, now: datetime, calendar: Calendar) -> date:
    """订单所属交易日（方案 3.2）。

    非交易日提交的一律属于下一个交易日；即时单属于当日；限价单 15:00 前属于当日；
    开盘单 9:25 前属于当日；收盘单 15:30 前属于当日；否则属于下一个交易日。
    """
    now_bj = to_beijing(now)
    today = now_bj.date()
    if not calendar.is_trading_day(today):
        return calendar.next_trading_day(today)
    if order_type is OrderType.MARKET:
        return today
    return today if now_bj.time() < _CUTOFF[order_type] else calendar.next_trading_day(today)
