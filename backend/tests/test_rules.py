from datetime import UTC, date, datetime, timedelta

import pytest

from demo_account.core.models import OrderType, Session
from demo_account.core.rules import (
    BEIJING,
    CalendarUnavailable,
    SimpleCalendar,
    assign_trade_date,
    can_submit,
    session_of,
    to_beijing,
)

THU = date(2026, 9, 24)
FRI = date(2026, 9, 25)
MON = date(2026, 9, 28)
SAT = date(2026, 9, 26)


def bj(d: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BEIJING)


def test_to_beijing() -> None:
    naive = datetime(2026, 9, 24, 9, 30)
    assert to_beijing(naive).tzinfo == BEIJING
    utc = datetime(2026, 9, 24, 1, 30, tzinfo=UTC)
    assert to_beijing(utc).time().hour == 9


@pytest.mark.parametrize(
    ("h", "m", "expected"),
    [
        (9, 0, Session.PRE_OPEN),
        (9, 15, Session.OPENING_AUCTION),
        (9, 24, Session.OPENING_AUCTION),
        (9, 25, Session.PRE_OPEN),
        (9, 30, Session.CONTINUOUS),
        (11, 29, Session.CONTINUOUS),
        (11, 30, Session.LUNCH),
        (12, 59, Session.LUNCH),
        (13, 0, Session.CONTINUOUS),
        (14, 56, Session.CONTINUOUS),
        (14, 57, Session.CLOSING_AUCTION),
        (14, 59, Session.CLOSING_AUCTION),
        (15, 0, Session.CLOSED),
        (15, 4, Session.CLOSED),
        (15, 5, Session.AFTER_HOURS),
        (15, 29, Session.AFTER_HOURS),
        (15, 30, Session.CLOSED),
        (20, 0, Session.CLOSED),
    ],
)
def test_session_of(h: int, m: int, expected: Session) -> None:
    assert session_of(bj(THU, h, m), trading_day=True) is expected


def test_session_of_non_trading_day() -> None:
    assert session_of(bj(SAT, 10, 0), trading_day=False) is Session.CLOSED


def test_can_submit() -> None:
    assert can_submit(OrderType.MARKET, Session.CONTINUOUS)
    assert not can_submit(OrderType.MARKET, Session.OPENING_AUCTION)
    assert not can_submit(OrderType.MARKET, Session.CLOSED)
    for ot in (OrderType.LIMIT, OrderType.OPEN, OrderType.CLOSE):
        assert can_submit(ot, Session.CLOSED)


def test_assign_trade_date(calendar: SimpleCalendar) -> None:
    assert assign_trade_date(OrderType.MARKET, bj(THU, 10, 0), calendar) == THU
    assert assign_trade_date(OrderType.LIMIT, bj(THU, 14, 59), calendar) == THU
    assert assign_trade_date(OrderType.LIMIT, bj(THU, 15, 0), calendar) == FRI
    assert assign_trade_date(OrderType.OPEN, bj(THU, 9, 24), calendar) == THU
    assert assign_trade_date(OrderType.OPEN, bj(THU, 9, 25), calendar) == FRI
    assert assign_trade_date(OrderType.CLOSE, bj(THU, 15, 29), calendar) == THU
    assert assign_trade_date(OrderType.CLOSE, bj(THU, 15, 30), calendar) == FRI
    # 周五收盘后的订单属于下周一；周六提交的一律属于下周一
    assert assign_trade_date(OrderType.LIMIT, bj(FRI, 16, 0), calendar) == MON
    assert assign_trade_date(OrderType.OPEN, bj(SAT, 10, 0), calendar) == MON
    # 国庆假期前最后一个交易日 9 月 30 日收盘后的开盘单属于 10 月 8 日
    assert assign_trade_date(OrderType.OPEN, bj(date(2026, 9, 30), 16, 0), calendar) == date(
        2026, 10, 8
    )


def test_simple_calendar_navigation(calendar: SimpleCalendar) -> None:
    assert calendar.is_trading_day(THU)
    assert not calendar.is_trading_day(SAT)
    assert not calendar.is_trading_day(date(2026, 10, 1))
    assert calendar.next_trading_day(FRI) == MON
    assert calendar.next_trading_day(SAT) == MON
    assert calendar.prev_trading_day(MON) == FRI
    assert calendar.prev_trading_day(date(2026, 10, 8)) == date(2026, 9, 30)
    assert calendar.trading_day_rank(THU, THU) == 1
    assert calendar.trading_day_rank(THU, MON) == 3
    assert calendar.trading_day_rank(date(2020, 1, 1), THU) > 5


def test_simple_calendar_unavailable(calendar: SimpleCalendar) -> None:
    with pytest.raises(CalendarUnavailable):
        calendar.is_trading_day(date(2026, 11, 1))
    with pytest.raises(CalendarUnavailable):
        calendar.next_trading_day(date(2026, 10, 30))
    with pytest.raises(CalendarUnavailable):
        calendar.prev_trading_day(date(2026, 9, 1))


def test_simple_calendar_default_coverage() -> None:
    cal = SimpleCalendar([THU, FRI, MON])
    assert cal.is_trading_day(FRI)
    assert not cal.is_trading_day(SAT)
    with pytest.raises(CalendarUnavailable):
        cal.is_trading_day(MON + timedelta(days=1))
