"""TSP 与公开接口适配器（实现 3.2、3.3、5.6；8.1 market）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from demo_account.clock import FakeClock
from demo_account.core.matching import slippage_price
from demo_account.core.models import OrderStatus, OrderType, Side
from demo_account.core.money import TICK, round_cent
from demo_account.market.base import MarketDataError
from demo_account.market.http import HttpSource
from demo_account.market.public_source import (
    PublicSource,
    parse_bonus_rows,
    parse_suspensions,
    parse_szse_month,
)
from demo_account.market.tsp_source import (
    TspClient,
    TspQuoteSource,
    parse_daily_rows,
    parse_latest_row,
)
from demo_account.services.container import build_container
from demo_account.services.errors import ServiceError
from demo_account.services.orders import OrderRequest
from demo_account.store import repos
from fake_upstream import (
    FakeUpstream,
    epoch_ms,
    month_rows,
    upstream_for_day,
)
from support import THU, at, make_settings

PF = "600000.SH"
NOSLEEP = lambda s: None  # noqa: E731


# ---------------------------------------------------------------- 公开接口解析


def test_parse_szse_month() -> None:
    rows = parse_szse_month(
        {"data": month_rows(2026, 9, frozenset({date(2026, 9, 25)})), "nowdate": ""}
    )
    days = dict(rows)
    assert days[date(2026, 9, 24)] is True and days[date(2026, 9, 25)] is False
    assert days[date(2026, 9, 26)] is False  # 周六
    with pytest.raises(MarketDataError):
        parse_szse_month({"oops": 1})


def test_public_calendar_coverage_and_missing_year() -> None:
    up = FakeUpstream(months=range(1, 13))
    pub = PublicSource(False, transport=up.transport(), sleep=NOSLEEP)
    cal = pub.trading_calendar(2026)
    assert cal.end == date(2026, 12, 31)
    assert date(2026, 9, 25) not in cal.open_days and date(2026, 9, 28) in cal.open_days
    partial = FakeUpstream(months=range(1, 7))
    cal2 = PublicSource(False, transport=partial.transport(), sleep=NOSLEEP).trading_calendar(2026)
    assert cal2.end == date(2026, 6, 30)
    with pytest.raises(MarketDataError):
        PublicSource(False, transport=up.transport(), sleep=NOSLEEP).trading_calendar(2027)


def test_parse_suspensions_uses_close_time_window() -> None:
    d = date(2026, 9, 28)
    rows = [
        {
            "SECUCODE": "002860.SZ",
            "SUSPEND_START_TIME": "2026-09-22 09:30:00",
            "SUSPEND_END_TIME": "2026-10-13 15:00:00",
            "SUSPEND_EXPIRE": "连续停牌",
            "SUSPEND_REASON": "刊登重要公告",
        },
        {
            "SECUCODE": "300211.SZ",
            "SUSPEND_START_TIME": "2026-09-28 09:30:00",
            "SUSPEND_END_TIME": "2026-09-28 15:00:00",
            "SUSPEND_EXPIRE": "停牌一天",
            "SUSPEND_REASON": "公告",
        },
        {
            "SECUCODE": "300096.SZ",
            "SUSPEND_START_TIME": "2026-09-24 09:30:00",
            "SUSPEND_END_TIME": "2026-09-24 15:00:00",
            "SUSPEND_EXPIRE": "停牌一天",
            "SUSPEND_REASON": "公告",
        },
        {
            "SECUCODE": "600111.SH",
            "SUSPEND_START_TIME": "2026-09-28 09:30:00",
            "SUSPEND_END_TIME": "2026-09-28 10:30:00",
            "SUSPEND_EXPIRE": "盘中停牌",
            "SUSPEND_REASON": "异常波动",
        },
        {
            "SECUCODE": "600222.SH",
            "SUSPEND_START_TIME": "2026-09-28 10:30:00",
            "SUSPEND_END_TIME": None,
            "SUSPEND_EXPIRE": "连续停牌",
            "SUSPEND_REASON": "重大事项",
        },
        {
            "SECUCODE": "430047.BJ",
            "SUSPEND_START_TIME": "2026-09-01 09:30:00",
            "SUSPEND_END_TIME": None,
            "SUSPEND_EXPIRE": "连续停牌",
            "SUSPEND_REASON": "x",
        },
    ]
    got = sorted(s.symbol for s in parse_suspensions(rows, d))
    assert got == ["002860.SZ", "300211.SZ", "600222.SH"]


def test_parse_bonus_rows() -> None:
    since = date(2025, 9, 1)
    rows = [
        {
            "ASSIGN_PROGRESS": "实施分配",
            "EX_DIVIDEND_DATE": "2026-07-16 00:00:00",
            "EQUITY_RECORD_DATE": "2026-07-15 00:00:00",
            "PAY_CASH_DATE": None,
            "BONUS_RATIO": None,
            "IT_RATIO": None,
            "BONUS_IT_RATIO": None,
            "PRETAX_BONUS_RMB": 4.2,
        },
        {
            "ASSIGN_PROGRESS": "实施分配",
            "EX_DIVIDEND_DATE": "2026-06-10 00:00:00",
            "EQUITY_RECORD_DATE": "2026-06-09 00:00:00",
            "PAY_CASH_DATE": "2026-06-10 00:00:00",
            "BONUS_RATIO": 3,
            "IT_RATIO": 2,
            "BONUS_IT_RATIO": 5,
            "PRETAX_BONUS_RMB": 1,
        },
        {"ASSIGN_PROGRESS": "董事会预案", "EX_DIVIDEND_DATE": None, "PRETAX_BONUS_RMB": 5},
        {
            "ASSIGN_PROGRESS": "实施分配",
            "EX_DIVIDEND_DATE": "2024-07-16 00:00:00",
            "PRETAX_BONUS_RMB": 3.9,
        },
        {
            "ASSIGN_PROGRESS": "实施分配",
            "EX_DIVIDEND_DATE": "2026-05-20 00:00:00",
            "BONUS_RATIO": None,
            "IT_RATIO": None,
            "BONUS_IT_RATIO": 4,
            "PRETAX_BONUS_RMB": None,
        },
    ]
    acts = parse_bonus_rows(rows, PF, since)
    assert len(acts) == 3
    a1, a2, a3 = acts
    assert (a1.cash_per_share, a1.record_date, a1.pay_date) == (
        Decimal("0.42"),
        date(2026, 7, 15),
        None,
    )
    assert (a2.bonus_per_share, a2.transfer_per_share, a2.cash_per_share) == (
        Decimal("0.3"),
        Decimal("0.2"),
        Decimal("0.1"),
    )
    assert a3.bonus_per_share == Decimal("0.4") and a3.transfer_per_share == 0


# ---------------------------------------------------------------- TSP 解析


def test_parse_latest_row() -> None:
    ts = at(THU, 9, 35)
    row = {
        "date": "2026-09-24",
        "symbol": PF,
        "open": 8.99,
        "high": 9.05,
        "low": 8.97,
        "close": 9.02,
        "change_pct": 0.002222,
        "is_live": True,
    }
    snap = parse_latest_row(PF, {"row": row}, THU, ts)
    assert snap is not None and snap.last == Decimal("9.02") and snap.ts == ts
    assert snap.prev_close == Decimal("9.00") and not snap.halted
    assert parse_latest_row(PF, {"row": {**row, "is_live": False}}, THU, ts) is None
    assert parse_latest_row(PF, {"row": {**row, "date": "2026-09-23"}}, THU, ts) is None
    assert parse_latest_row(PF, {"row": None}, THU, ts) is None
    with pytest.raises(MarketDataError):
        parse_latest_row(PF, {"nope": 1}, THU, ts)


def test_parse_daily_rows_restores_raw_prices_and_skips_live() -> None:
    # 平安银行 2026-09-24 除息前后的形状：前复权价与未复权价在除息日之前不同
    rows = [
        {
            "date": "2026-09-23",
            "open": 11.20,
            "high": 11.40,
            "low": 11.10,
            "close": 11.35,
            "raw_close": 11.60,
            "raw_high": 11.65,
            "raw_low": 11.34,
        },
        {
            "date": "2026-09-24",
            "open": 11.35,
            "high": 11.47,
            "low": 11.29,
            "close": 11.30,
            "raw_close": 11.30,
        },
        {"date": "2026-09-28", "close": 11.30, "is_live": True},
    ]
    bars = parse_daily_rows("000001.SZ", rows, TICK)
    assert [b.trade_date for b in bars] == [date(2026, 9, 23), date(2026, 9, 24)]
    b1, b2 = bars
    assert b1.close == Decimal("11.60") and b1.high == Decimal("11.65")
    assert b1.open == Decimal("11.45")  # 11.20 × 11.60 / 11.35
    assert b1.adj_close == Decimal("11.35")
    assert b2.prev_close == Decimal("11.60") and b2.close == Decimal("11.30")


# ---------------------------------------------------------------- TSP 客户端行为


def _tsp_quote(up: FakeUpstream, clock: FakeClock, password: str | None = None) -> TspQuoteSource:
    client = TspClient("http://127.0.0.1:3018", password, False, up.transport(), NOSLEEP)
    return TspQuoteSource(client, clock)


def test_tsp_login_when_password_is_set() -> None:
    clock = FakeClock(at(THU, 9, 35, 5))
    up = FakeUpstream()
    up.password = "pw"
    up.status["last_fetch_ms"] = epoch_ms(at(THU, 9, 35, 1))
    up.latest[PF] = {
        "date": "2026-09-24",
        "close": 9.02,
        "open": 8.99,
        "high": 9.05,
        "low": 8.97,
        "change_pct": 0.0022,
        "is_live": True,
    }
    snaps = _tsp_quote(up, clock, "pw").get_snapshots([PF])
    assert snaps[PF].last == Decimal("9.02")
    assert "/api/auth/login" in up.paths("127.0.0.1")
    with pytest.raises(MarketDataError, match="DEMO_ACCOUNT_TSP_PASSWORD"):
        _tsp_quote(up, clock, None).get_snapshots([PF])


def test_tsp_status_guards() -> None:
    clock = FakeClock(at(THU, 10, 0))
    up = FakeUpstream()
    q = _tsp_quote(up, clock)
    with pytest.raises(MarketDataError, match="last_fetch_ms"):
        q.get_snapshots([PF])
    up.status["last_fetch_ms"] = epoch_ms(at(THU, 9, 45))  # 15 分钟前，且正在轮询
    with pytest.raises(MarketDataError, match="停滞"):
        q.get_snapshots([PF])
    up.status["realtime_allowed"] = False
    with pytest.raises(MarketDataError, match="实时行情"):
        q.get_snapshots([PF])


def test_http_source_retries_with_backoff() -> None:
    waits: list[float] = []
    answers = iter([500, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers), json={"ok": True})

    src = HttpSource(
        "t", False, min_interval=0, transport=httpx.MockTransport(handler), sleep=waits.append
    )
    assert src.get_json("http://example.test/x") == {"ok": True}
    assert waits == [1.0, 2.0]
    bad = HttpSource(
        "t",
        False,
        min_interval=0,
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
        sleep=waits.append,
    )
    with pytest.raises(MarketDataError):
        bad.get_json("http://example.test/x")


# ---------------------------------------------------------------- 接入服务


def _tsp_env(tmp_path: Path, up: FakeUpstream, start: object) -> object:
    clock = FakeClock(start)  # type: ignore[arg-type]
    settings = make_settings(tmp_path, DEMO_ACCOUNT_MARKET="tsp")
    c = build_container(settings, clock=clock, market_transport=up.transport(), sleep=NOSLEEP)
    c.reference.ensure_calendar(clock.now().date())
    return c, clock


_upstream_for_day = upstream_for_day


def test_trading_day_through_tsp_and_public_sources(tmp_path: Path) -> None:
    up = _upstream_for_day()
    c, clock = _tsp_env(tmp_path, up, at(THU, 9, 35))  # type: ignore[misc]
    a = c.accounts.create("TSP 账户")
    o = c.orders.submit(a.id, OrderRequest(PF, Side.BUY, OrderType.MARKET, qty=1000))
    assert o.status is OrderStatus.PENDING
    up.status["last_fetch_ms"] = epoch_ms(at(THU, 9, 35, 1))
    clock.set(at(THU, 9, 35, 3))
    assert c.engine.run_intraday_cycle()["filled"] == 1
    fill = repos.all_fills(c.db.read(), a.id)[0]
    assert fill.price == slippage_price(Decimal("9.02"), Side.BUY, Decimal("0.0005"), TICK)
    # 盘中注入的实时蜡烛不会被当成日线存下
    assert repos.get_bar(c.db.read(), PF, THU) is None
    # 收盘后 TSP 落盘当日日线与指数
    up.daily[PF] = [r for r in up.daily[PF] if not r.get("is_live")] + [
        {
            "date": "2026-09-24",
            "open": 8.99,
            "high": 9.08,
            "low": 8.97,
            "close": 9.05,
            "raw_close": 9.05,
        }
    ]
    up.index["000300.SH"].append({"date": "2026-09-24", "close": 4512.5})
    clock.set(at(THU, 16, 0))
    run = c.settlement.settle(THU)
    assert run.status == "done", run.error
    nav = repos.list_nav(c.db.read(), a.id)[0]
    cash = repos.account_cash(c.db.read(), a.id, a.initial_cash)
    assert nav.total_assets == cash + round_cent(Decimal(1000) * Decimal("9.05"))
    assert nav.benchmark_nav == Decimal("1.00000000")
    assert c.settlement.reconcile(a.id)["ok"]
    assert repos.is_suspended(c.db.read(), "000016.SZ", THU)
    assert "/api/kline/minute" not in up.paths("127.0.0.1")


def test_clock_skew_blocks_orders(tmp_path: Path) -> None:
    up = _upstream_for_day()
    c, clock = _tsp_env(tmp_path, up, at(THU, 9, 35))  # type: ignore[misc]
    a = c.accounts.create("时钟")
    c.clock_monitor.observe(clock.now() - timedelta(seconds=5))
    assert c.state.clock_skew is False
    c.clock_monitor.observe(clock.now() - timedelta(seconds=120))
    assert c.state.clock_skew is True
    with pytest.raises(ServiceError) as ei:
        c.orders.submit(a.id, OrderRequest(PF, Side.BUY, OrderType.MARKET, qty=100))
    assert ei.value.code == "CLOCK_SKEW" and ei.value.status == 503
    assert "CLOCK_SKEW" in [al.code for al in repos.list_alerts(c.db.read())]
    c.clock_monitor.observe(clock.now())
    assert c.state.clock_skew is False


def test_index_daily_falls_back_to_sina(tmp_path: Path) -> None:
    up = _upstream_for_day()
    up.index.clear()
    up.sina_index = [
        {"day": "2026-09-23", "close": "4517.279"},
        {"day": "2026-09-24", "close": "4439.144"},
    ]
    c, _ = _tsp_env(tmp_path, up, at(THU, 16, 0))  # type: ignore[misc]
    assert c.reference.benchmark_close(THU) == Decimal("4439.144")
