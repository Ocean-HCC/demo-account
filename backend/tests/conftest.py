"""测试公共夹具。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from demo_account.core.models import FeeParams
from demo_account.core.rules import SimpleCalendar


def weekdays(start: date, end: date, holidays: set[date] | None = None) -> list[date]:
    holidays = holidays or set()
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5 and d not in holidays:
            days.append(d)
        d += timedelta(days=1)
    return days


# 2026-09-24 是周四。测试日历：9 月到 10 月的工作日，假定 10 月 1 日至 7 日休市。
HOLIDAYS = {date(2026, 10, d) for d in range(1, 8)}


@pytest.fixture
def calendar() -> SimpleCalendar:
    return SimpleCalendar(
        weekdays(date(2026, 9, 1), date(2026, 10, 31), HOLIDAYS),
        coverage=(date(2026, 9, 1), date(2026, 10, 31)),
    )


@pytest.fixture
def fee_params() -> FeeParams:
    return FeeParams(
        commission_rate=Decimal("0.00013"),
        min_commission=Decimal("5"),
        slippage_rate=Decimal("0.0005"),
    )
