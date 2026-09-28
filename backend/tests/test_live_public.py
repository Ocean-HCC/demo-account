"""真实公开接口测试，默认跳过。设置 DEMO_ACCOUNT_LIVE_TESTS=1 时运行。"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

import pytest

from demo_account.market.public_source import PublicSource

pytestmark = pytest.mark.skipif(
    not os.environ.get("DEMO_ACCOUNT_LIVE_TESTS"),
    reason="需要网络，设置 DEMO_ACCOUNT_LIVE_TESTS=1 运行",
)


def test_live_szse_calendar_2026() -> None:
    cal = PublicSource(False).trading_calendar(2026)
    assert date(2026, 9, 25) not in cal.open_days  # 中秋节
    assert date(2026, 9, 24) in cal.open_days
    assert cal.end >= date(2026, 12, 31)


def test_live_eastmoney_suspensions_and_dividends() -> None:
    src = PublicSource(False)
    susp = src.suspensions(date(2026, 9, 24))
    assert all(s.symbol.endswith((".SH", ".SZ")) for s in susp)
    acts = src.stock_corporate_actions("600000.SH", date(2026, 9, 24))
    assert any(a.ex_date == date(2026, 7, 16) and a.cash_per_share == Decimal("0.42") for a in acts)


def test_live_sina_index() -> None:
    rows = PublicSource(False).index_daily("000300.SH", date(2026, 9, 1), date(2026, 9, 24))
    assert rows and rows[-1][0] <= date(2026, 9, 24)
