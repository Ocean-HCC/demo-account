"""用假时钟与 Mock 行情驱动交易日：下单、撮合、结算、公司行动、留痕（实现 8.1 services）。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from demo_account.core.matching import amount_to_qty, freeze_amount, slippage_price
from demo_account.core.models import (
    AssetType,
    Board,
    EventType,
    FillKind,
    OrderStatus,
    OrderType,
    Side,
    Snapshot,
)
from demo_account.core.money import TICK_STOCK, round_cent
from demo_account.market.base import CorporateAction
from demo_account.scheduler import Scheduler
from demo_account.services.delivery import sign
from demo_account.services.errors import ServiceError
from demo_account.store import repos
from support import FRI, MON, THU, TUE, WED, at, make_env

SLIP = Decimal("0.0005")
PF = "600000.SH"


def test_market_buy_fills_at_snapshot_plus_slippage(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    pc = env.prev_close(PF)
    env.mock.set_price(PF, pc)
    o = env.buy(a, PF, 1000)
    assert o.status is OrderStatus.PENDING
    assert o.frozen_cash > 0
    assert env.cycle()["filled"] == 1
    f = env.fills(a)[0]
    assert f.price == slippage_price(pc, Side.BUY, SLIP, TICK_STOCK)
    done = env.get(a, o)
    assert done.status is OrderStatus.FILLED and done.frozen_cash == 0
    pos = env.position(a, PF)
    assert pos is not None and (pos.qty, pos.today_bought_qty) == (1000, 1000)
    assert env.cash(a) == a.initial_cash + f.cash_delta
    assert env.types(a) == ["account_created", "order_submitted", "order_filled"]
    ev = env.events(a)[-1]
    assert ev.fill_seq == f.seq
    assert ev.basis["snapshot"]["last"] == str(pc)


def test_market_order_outside_session_is_rejected_and_recorded(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 0)
    a = env.account()
    o = env.buy(a, PF, 100)
    assert o.status is OrderStatus.REJECTED and o.reason_code == "OUTSIDE_SESSION"
    ev = env.events(a)[-1]
    assert ev.type is EventType.ORDER_REJECTED and ev.basis["session"] == "pre_open"


def test_t1_blocks_same_day_sell_and_next_day_allows(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    env.mock.set_price(PF, env.prev_close(PF))
    env.buy(a, PF, 1000)
    env.cycle()
    s = env.sell(a, PF, 1000)
    assert s.status is OrderStatus.REJECTED and s.reason_code == "INSUFFICIENT_SELLABLE"
    assert env.settle(THU).status == "done"
    env.go(FRI, 9, 35)
    env.mock.set_price(PF, env.prev_close(PF))
    s2 = env.sell(a, PF, 1000)
    assert s2.status is OrderStatus.PENDING
    assert env.cycle()["filled"] == 1
    assert env.position(a, PF) is None
    assert "LEDGER_MISMATCH" not in env.alert_codes()


def test_limit_order_waits_then_fills_at_limit_price(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    pc = env.prev_close(PF)
    env.mock.set_price(PF, pc)
    lp = pc - Decimal("0.10")
    o = env.buy(a, PF, 1000, order_type=OrderType.LIMIT, limit_price=lp)
    assert o.frozen_cash == freeze_amount(1000, lp, a.fee_params, AssetType.STOCK)
    assert env.cycle()["filled"] == 0
    env.mock.set_price(PF, pc - Decimal("0.12"))
    assert env.cycle()["filled"] == 1
    assert env.fills(a)[0].price == lp


def test_limit_buy_waits_while_sealed_at_up_limit_and_market_buy_is_rejected(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    lim = env.limits(PF)
    assert lim.up is not None
    env.mock.set_price(PF, lim.up)
    lo = env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.up)
    mo = env.buy(a, PF, 100)
    stats = env.cycle()
    assert stats["rejected"] == 1 and stats["waiting"] == 1
    assert env.get(a, lo).status is OrderStatus.PENDING
    rej = env.get(a, mo)
    assert rej.status is OrderStatus.REJECTED and rej.reason_code == "PRICE_LIMIT_HIT"
    env.mock.set_price(PF, lim.up - Decimal("0.01"))
    assert env.cycle()["filled"] == 1
    assert env.fills(a)[0].price == lim.up


def test_market_order_expires_without_quotes_and_source_alert(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    lim = env.limits(PF)
    # 一笔一直等待的限价单，让每个周期都去取行情，失败次数才会累积
    env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.down)
    env.mock.fail_quotes = True
    o = env.buy(a, PF, 100)
    assert o.status is OrderStatus.PENDING
    assert env.cycle()["waiting"] == 2
    env.clock.advance(181)
    assert env.c.engine.run_intraday_cycle()["expired"] == 1
    exp = env.get(a, o)
    assert exp.status is OrderStatus.EXPIRED and exp.reason_code == "MARKET_DATA_MISSING"
    assert exp.frozen_cash == 0
    for _ in range(5):
        env.cycle()
    assert "QUOTE_SOURCE_DOWN" in env.alert_codes()


def test_snapshot_validity_rules(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    pc = env.prev_close(PF)
    o = env.buy(a, PF, 100)
    base = Snapshot(PF, at(THU, 9, 34, 59), pc, pc, pc, pc, pc, False, None, None, "t")
    eng = env.c.engine
    assert not eng.valid_snapshot(base, o, at(THU, 9, 35, 1))  # 早于订单创建
    fresh = replace(base, ts=at(THU, 9, 35, 1))
    assert eng.valid_snapshot(fresh, o, at(THU, 9, 35, 2))
    assert not eng.valid_snapshot(fresh, o, at(THU, 9, 38, 2))  # 超过有效期 180 秒
    assert not eng.valid_snapshot(None, o, at(THU, 9, 35, 2))


def test_open_order_fills_next_day_at_open_plus_slippage(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 16, 30)
    a = env.account()
    assert env.c.settlement.catch_up() == [THU]  # 16:00 后当日先结算，才接受新订单
    o = env.buy(a, "000001.SZ", 100, order_type=OrderType.OPEN)
    assert o.status is OrderStatus.PENDING and o.trade_date == FRI
    env.go(FRI, 9, 31)
    assert env.c.engine.run_open_matching()["filled"] == 1
    bar = env.mock.bar("000001.SZ", FRI)
    assert bar is not None and bar.open is not None
    assert env.fills(a)[0].price == slippage_price(bar.open, Side.BUY, SLIP, TICK_STOCK)


def test_close_orders_fill_at_close_and_protect_price_expires(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 10, 0)
    a = env.account()
    bar = env.mock.bar(PF, THU)
    assert bar is not None
    o1 = env.buy(a, PF, 100, order_type=OrderType.CLOSE)
    o2 = env.buy(a, PF, 100, order_type=OrderType.CLOSE, protect_price=bar.close - Decimal("0.01"))
    assert o1.status is OrderStatus.PENDING and o2.status is OrderStatus.PENDING
    assert env.settle(THU).status == "done"
    f1 = env.get(a, o1)
    assert f1.status is OrderStatus.FILLED
    assert env.fills(a)[0].price == bar.close
    e2 = env.get(a, o2)
    assert e2.status is OrderStatus.EXPIRED and e2.reason_code == "PROTECT_PRICE_NOT_MET"


def test_settlement_finalizes_nav_unlocks_t1_expires_limits_and_is_idempotent(
    tmp_path: Path,
) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    env.mock.set_price(PF, env.prev_close(PF))
    env.buy(a, PF, 1000)
    env.cycle()
    lim = env.limits(PF)
    lo = env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.down)
    assert env.settle(THU).status == "done"
    exp = env.get(a, lo)
    assert exp.status is OrderStatus.EXPIRED and exp.reason_code == "EXPIRED_AT_CLOSE"
    navs = repos.list_nav(env.c.db.read(), a.id)
    assert len(navs) == 1
    bar = env.mock.bar(PF, THU)
    assert bar is not None
    total = env.cash(a) + round_cent(Decimal(1000) * bar.close)
    assert navs[0].total_assets == total
    assert navs[0].nav == (total / a.initial_cash).quantize(Decimal("0.00000001"))
    assert navs[0].day_pnl == total - a.initial_cash
    assert navs[0].benchmark_nav == Decimal("1.00000000")
    pos = env.position(a, PF)
    assert pos is not None and pos.today_bought_qty == 0
    n_fills = len(env.fills(a))
    assert env.c.settlement.settle(THU).status == "done"
    assert len(env.fills(a)) == n_fills
    assert len(repos.list_nav(env.c.db.read(), a.id)) == 1
    assert env.types(a).count("settlement_done") == 1
    assert env.c.settlement.reconcile(a.id)["ok"]


def test_corporate_actions_bonus_dividend_etf_factor_and_entitlement(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.mock.add_corporate_action(
        CorporateAction(
            PF, FRI, THU, FRI, Decimal("0.2"), Decimal("0"), Decimal("0.42"), None, "mock"
        )
    )
    env.mock.add_corporate_action(
        CorporateAction(
            "510300.SH",
            FRI,
            THU,
            None,
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
            Decimal("1.01234"),
            "mock",
        )
    )
    env.go(THU, 9, 35)
    a = env.account()
    for sym, qty in ((PF, 1000), ("510300.SH", 10000)):
        env.mock.set_price(sym, env.prev_close(sym))
        env.buy(a, sym, qty)
        env.cycle()
    assert env.settle(THU).status == "done"
    env.go(FRI, 9, 35)
    env.mock.set_price(PF, env.prev_close(PF))
    env.buy(a, PF, 500)  # 除权日买入，不享有本次送转与分红
    env.cycle()
    cash_before = env.cash(a)
    assert env.settle(FRI).status == "done"
    pos = env.position(a, PF)
    assert pos is not None and pos.qty == 1000 + 200 + 500
    etf = env.position(a, "510300.SH")
    assert etf is not None and etf.qty == 10123
    etf_bar = env.mock.bar("510300.SH", FRI)
    assert etf_bar is not None
    frac = round_cent(Decimal("0.4") * etf_bar.close)
    assert env.cash(a) - cash_before == Decimal("420.00") + frac
    types = env.types(a)
    assert types.count("corporate_action") == 3
    # 每个账务变更恰有一条事件
    fills = env.fills(a)
    trades = [f for f in fills if f.kind is FillKind.TRADE]
    corp = [f for f in fills if f.kind is FillKind.CORPORATE_ACTION]
    assert types.count("order_filled") == len(trades)
    assert types.count("corporate_action") == len(corp)
    assert env.c.settlement.settle(FRI).status == "done"
    assert len(env.fills(a)) == len(fills)
    assert env.c.settlement.reconcile(a.id)["ok"]


def test_suspension_defers_open_order_until_limit(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    for d in (FRI, MON, TUE, WED):
        env.mock.add_suspension("000001.SZ", d)
    env.go(THU, 16, 30)
    a = env.account()
    assert env.c.settlement.catch_up() == [THU]
    o = env.buy(a, "000001.SZ", 100, order_type=OrderType.OPEN)
    env.go(FRI, 9, 31)
    assert env.c.engine.run_open_matching()["waiting"] == 1
    assert env.settle(FRI).status == "done"
    o1 = env.get(a, o)
    assert (o1.status, o1.defer_count, o1.trade_date) == (OrderStatus.PENDING, 1, MON)
    done_fri = [e for e in env.events(a) if e.type is EventType.SETTLEMENT_DONE][-1]
    assert done_fri.payload["deferred"][0]["order_id"] == o.id
    env.settle(MON)
    env.settle(TUE)
    o3 = env.get(a, o)
    assert (o3.defer_count, o3.trade_date) == (3, WED)
    env.settle(WED)
    o4 = env.get(a, o)
    assert o4.status is OrderStatus.EXPIRED and o4.reason_code == "DEFER_LIMIT"
    assert o4.frozen_cash == 0


def test_freeze_cancels_pending_and_blocks_new_orders(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    lim = env.limits(PF)
    lo = env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.down)
    env.c.accounts.freeze(a.id)
    c1 = env.get(a, lo)
    assert c1.status is OrderStatus.CANCELLED and c1.reason_code == "ACCOUNT_FROZEN"
    o2 = env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.down)
    assert o2.status is OrderStatus.REJECTED and o2.reason_code == "ACCOUNT_NOT_ACTIVE"
    env.c.accounts.unfreeze(a.id)
    o3 = env.buy(a, PF, 100, order_type=OrderType.LIMIT, limit_price=lim.down)
    assert o3.status is OrderStatus.PENDING
    types = env.types(a)
    assert "account_frozen" in types and "order_cancelled" in types and "account_unfrozen" in types


def test_idempotency_key_returns_same_order(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    lim = env.limits(PF)
    kw = {"order_type": OrderType.LIMIT, "limit_price": lim.down}
    o1 = env.buy(a, PF, 100, idempotency_key="k1", **kw)
    o2 = env.buy(a, PF, 100, idempotency_key="k1", **kw)
    o3 = env.buy(a, PF, 100, idempotency_key="k2", **kw)
    assert o1.id == o2.id and o3.id != o1.id
    assert len(repos.list_orders(env.c.db.read(), a.id)) == 2


def test_risk_warning_stock_rules(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    st = "000016.SZ"
    pc = env.prev_close(st)
    a1 = env.account("A")
    r1 = env.buy(a1, st, 100, order_type=OrderType.LIMIT, limit_price=pc)
    assert r1.reason_code == "ST_BLOCKED"
    a2 = env.account("B", block_st=False)
    r2 = env.buy(a2, st, 100)
    assert r2.reason_code == "ST_ORDER_TYPE_NOT_ALLOWED"
    r3 = env.buy(a2, st, 100, order_type=OrderType.LIMIT, limit_price=pc)
    assert r3.status is OrderStatus.PENDING
    r4 = env.buy(a2, st, 500_000, order_type=OrderType.LIMIT, limit_price=pc)
    assert r4.reason_code == "ST_DAILY_BUY_LIMIT"


def test_amount_buy_converts_to_lots_at_freeze_price(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    lim = env.limits(PF)
    assert lim.up is not None
    o = env.buy(a, PF, None, amount=Decimal("10000"))
    assert o.qty == amount_to_qty(
        Decimal("10000"), lim.up, Board.MAIN, AssetType.STOCK, a.fee_params
    )
    assert o.amount == Decimal("10000") and o.qty % 100 == 0


def test_catch_up_settles_missed_trading_days(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 10, 0)
    a = env.account()
    env.go(MON, 17, 0)
    assert env.c.settlement.pending_days() == [THU, FRI, MON]
    with pytest.raises(ServiceError) as e:
        env.buy(a, PF, 100, order_type=OrderType.OPEN)
    assert e.value.code == "SETTLEMENT_CATCHUP" and e.value.status == 503
    assert env.c.settlement.catch_up() == [THU, FRI, MON]
    assert [n.trade_date for n in repos.list_nav(env.c.db.read(), a.id)] == [THU, FRI, MON]
    assert env.c.settlement.first_pending_day() is None


def test_failed_settlement_pauses_trading_and_catches_up_in_order(tmp_path: Path) -> None:
    """结算跨日未完成：暂停交易，数据恢复后按日期顺序补齐，前一日收盘单按前一日收盘价成交。"""
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    buy = env.buy(a, PF, 100)
    env.cycle()
    assert env.get(a, buy).status is OrderStatus.FILLED
    close = env.buy(a, PF, 100, order_type=OrderType.CLOSE)
    assert close.trade_date == THU
    env.mock.fail_reference = True
    env.go(THU, 16, 0)
    assert env.c.settlement.catch_up() == []
    env.go(THU, 16, 10)
    assert env.c.settlement.catch_up() == []
    assert env.alert_codes().count("SETTLEMENT_DATA_MISSING") == 1  # 重试不重复告警
    with pytest.raises(ServiceError) as e:
        env.buy(a, PF, 100)
    assert (e.value.code, e.value.status) == ("SETTLEMENT_CATCHUP", 503)
    assert "2026-09-24" in e.value.message
    st = env.c.market_status.status()
    assert st["trading_paused"] is True and st["pending_settlement_date"] == "2026-09-24"
    # 次日盘中仍未补齐：不撮合、不能越过前一日结算，快照照常刷新供估值
    env.go(FRI, 9, 35)
    assert env.cycle() == {}
    assert env.c.quotes.last_update == env.clock.now()
    with pytest.raises(ServiceError) as e2:
        env.c.settlement.settle(FRI)
    assert (e2.value.code, e2.value.status) == ("SETTLEMENT_CATCHUP", 409)
    env.mock.fail_reference = False
    env.go(FRI, 16, 5)
    assert env.c.settlement.catch_up() == [THU, FRI]
    bar = env.mock.bar(PF, THU)
    assert bar is not None
    fill = next(f for f in env.fills(a) if f.order_id == close.id)
    assert (fill.trade_date, fill.price) == (THU, bar.close)
    assert [n.trade_date for n in repos.list_nav(env.c.db.read(), a.id)] == [THU, FRI]
    assert env.c.market_status.status()["trading_paused"] is False


def test_scheduler_retries_settlement_without_cutoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """16:00 起结算当日，失败后每 10 分钟重试，过了 23:00 和零点也继续，直到补齐。"""
    env = make_env(tmp_path)
    env.go(THU, 10, 0)
    env.account()
    sched = Scheduler(env.c)
    calls: list[datetime] = []
    real = env.c.settlement.catch_up

    def counting() -> list[date]:
        calls.append(env.clock.now())
        return real()

    monkeypatch.setattr(env.c.settlement, "catch_up", counting)
    env.mock.fail_reference = True
    for h, m in ((15, 59), (16, 0), (16, 5), (16, 10), (23, 30)):
        env.go(THU, h, m)
        asyncio.run(sched.tick())
    assert [c.strftime("%H:%M") for c in calls] == ["16:00", "16:10", "23:30"]
    env.mock.fail_reference = False
    env.go(FRI, 0, 30)
    asyncio.run(sched.tick())
    assert env.c.settlement.first_pending_day() is None
    assert repos.last_done_run(env.c.db.read()) == THU


def test_webhook_delivery_signs_and_retries(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    env.c.accounts.set_webhook(a.id, "http://hook.test/cb", "s3cret")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500 if len(calls) == 1 else 200)

    env.c.delivery.transport = httpx.MockTransport(handler)
    env.mock.set_price(PF, env.prev_close(PF))
    env.buy(a, PF, 100)
    env.cycle()
    assert env.c.delivery.run_once() == 0
    filled = [e for e in env.events(a) if e.type is EventType.ORDER_FILLED][0]
    assert filled.attempts == 1 and filled.next_attempt_at is not None
    assert env.c.delivery.run_once() == 0 and len(calls) == 1
    env.clock.advance(11)
    assert env.c.delivery.run_once() == 1
    req = calls[-1]
    assert req.headers["X-Demo-Account-Signature"] == sign("s3cret", req.content)
    body = json.loads(req.content)
    assert body["type"] == "order_filled" and body["account_id"] == a.id
    assert env.c.delivery.run_once() == 0


def test_round_trip_stats(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.go(THU, 9, 35)
    a = env.account()
    env.mock.set_price(PF, env.prev_close(PF))
    env.buy(a, PF, 1000)
    env.cycle()
    env.settle(THU)
    env.go(FRI, 9, 35)
    env.mock.set_price(PF, env.prev_close(PF) + Decimal("0.20"))
    env.sell(a, PF, 1000)
    env.cycle()
    env.settle(FRI)
    st = env.c.portfolio.stats(a)
    assert st["rounds"] == 1
    r = st["round_details"][0]
    assert r["holding_days"] == 1
    assert Decimal(r["pnl"]) == Decimal(r["proceeds"]) - Decimal(r["cost"])
    assert st["win_rate"] in ("1.000000", "0.000000")
    assert st["max_drawdown"] is not None
