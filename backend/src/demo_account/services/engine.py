"""盘中撮合与开盘撮合（方案 4.2、4.3；实现 5.3）。

有待结算日时暂停交易（方案 4.4）：盘中撮合只刷新快照供估值，不处理订单；开盘撮合跳过。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from ..clock import Clock
from ..config import Settings
from ..core.matching import limit_can_fill, limit_hit, market_fill_price
from ..core.models import Instrument, OrderStatus, OrderType, PriceLimits, Session, Side, Snapshot
from ..core.money import TICK
from ..core.rules import T_0930, CalendarUnavailable, session_of, to_beijing
from ..market.base import MarketDataError, QuoteSource
from ..market.reference import ReferenceService
from ..store import repos
from ..store.db import Database
from ..store.records import Order
from .events import AlertService
from .execution import Execution
from .market_status import QuoteCache, SourceHealth, snapshot_basis

log = logging.getLogger(__name__)


def _limit_word(side: Side) -> str:
    return "触及涨停" if side is Side.BUY else "触及跌停"


class Engine:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        reference: ReferenceService,
        source: QuoteSource,
        quotes: QuoteCache,
        health: SourceHealth,
        execution: Execution,
        alerts: AlertService,
        settings: Settings,
        settlement_pending: Callable[[], date | None],
    ) -> None:
        self.db = db
        self.clock = clock
        self.reference = reference
        self.source = source
        self.quotes = quotes
        self.health = health
        self.execution = execution
        self.alerts = alerts
        self.settings = settings
        self.settlement_pending = settlement_pending

    # ---------------------------------------------------------------- 行情

    def watch_symbols(self, today: date) -> list[str]:
        """有等待中订单或有持仓的标的。"""
        ex = self.db.read()
        syms = set(repos.all_position_symbols(ex))
        for o in repos.pending_orders(
            ex, [OrderType.MARKET, OrderType.LIMIT, OrderType.OPEN], trade_date=today
        ):
            syms.add(o.symbol)
        return sorted(syms)

    def refresh_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        now = self.clock.now()
        if not symbols:
            return {}
        try:
            snaps = self.source.get_snapshots(symbols)
        except MarketDataError as e:
            self.health.fail(str(e))
            if not self.health.available and not self.health.alerted:
                self.health.alerted = True
                self.alerts.raise_alert(
                    "error",
                    "QUOTE_SOURCE_DOWN",
                    f"行情源 {self.health.name} 连续失败 {self.health.failures} 次：{e}",
                    now.date(),
                )
            return {}
        self.health.ok(now)
        self.quotes.update(snaps, now)
        return snaps

    def _limits(
        self, symbol: str, today: date, inst: Instrument, snap: Snapshot | None
    ) -> PriceLimits | None:
        if snap is not None and snap.up_limit is not None and snap.down_limit is not None:
            return PriceLimits(up=snap.up_limit, down=snap.down_limit)
        try:
            return self.reference.price_limits(symbol, today, inst, today=today)
        except MarketDataError:
            return None

    def valid_snapshot(self, snap: Snapshot | None, order: Order, now: datetime) -> bool:
        """快照有效：存在、价格为正、不早于订单创建时间、未超过有效期（方案 6.6）。"""
        if snap is None or snap.last <= 0:
            return False
        ts = to_beijing(snap.ts)
        if ts < order.created_at:
            return False
        return (now - ts).total_seconds() <= self.settings.snapshot_max_age

    def _instrument(self, symbol: str) -> Instrument | None:
        inst = repos.get_instrument(self.db.read(), symbol)
        if inst is None:
            try:
                inst = self.reference.instrument(symbol)
            except MarketDataError:
                return None
        return inst

    # ---------------------------------------------------------------- 盘中撮合

    def run_intraday_cycle(self) -> dict[str, int]:
        now = self.clock.now()
        today = now.date()
        try:
            trading = self.reference.calendar().is_trading_day(today)
        except CalendarUnavailable:
            return {}
        if session_of(now, trading) is not Session.CONTINUOUS:
            return {}
        stats = {"filled": 0, "rejected": 0, "expired": 0, "waiting": 0}
        snaps = self.refresh_snapshots(self.watch_symbols(today))
        if self.settlement_pending() is not None:
            return {}
        allowed = self.health.available
        orders = repos.pending_orders(
            self.db.read(), [OrderType.MARKET, OrderType.LIMIT], trade_date=today
        )
        for order in orders:
            result = self._process(order, snaps.get(order.symbol), now, today, allowed)
            stats[result] += 1
        return stats

    def _process(
        self, order: Order, snap: Snapshot | None, now: datetime, today: date, allowed: bool
    ) -> str:
        inst = self._instrument(order.symbol)
        if inst is None:
            return "waiting"
        limits = self._limits(order.symbol, today, inst, snap)
        tick = TICK
        with self.db.write() as tx:
            o = repos.get_order(tx, order.account_id, order.id)
            account = repos.get_account(tx, order.account_id)
            if o is None or account is None or o.status is not OrderStatus.PENDING:
                return "waiting"
            valid = allowed and self.valid_snapshot(snap, o, now) and limits is not None
            if o.order_type is OrderType.MARKET:
                if not valid or snap is None or limits is None:
                    waited = (now - o.created_at).total_seconds()
                    if waited > self.settings.instant_order_timeout:
                        self.execution.expire_order(
                            tx,
                            account,
                            o,
                            "MARKET_DATA_MISSING",
                            f"等待 {int(waited)} 秒仍无有效行情",
                            now,
                            {"snapshot": snapshot_basis(snap) if snap else None},
                        )
                        return "expired"
                    return "waiting"
                basis: dict[str, Any] = {
                    "snapshot": snapshot_basis(snap),
                    "up_limit": limits.up,
                    "down_limit": limits.down,
                    "slippage_rate": account.fee_params.slippage_rate,
                }
                if snap.halted:
                    self.execution.reject_order(
                        tx, account, o, "SYMBOL_SUSPENDED", "标的停牌", now, basis
                    )
                    return "rejected"
                if limit_hit(snap.last, o.side, limits):
                    self.execution.reject_order(
                        tx, account, o, "PRICE_LIMIT_HIT", _limit_word(o.side), now, basis
                    )
                    return "rejected"
                price = market_fill_price(
                    snap.last, o.side, account.fee_params.slippage_rate, tick, limits
                )
                fill = self.execution.fill_order(tx, account, o, inst, price, now, today, basis)
                return "filled" if fill is not None else "rejected"
            # 限价单：停牌、封板（买入遇涨停、卖出遇跌停）时继续等待
            if not valid or snap is None or limits is None or snap.halted:
                return "waiting"
            assert o.limit_price is not None
            if limit_hit(snap.last, o.side, limits):
                return "waiting"
            if limit_can_fill(snap.last, o.limit_price, o.side):
                basis = {
                    "snapshot": snapshot_basis(snap),
                    "limit_price": o.limit_price,
                    "up_limit": limits.up,
                    "down_limit": limits.down,
                }
                fill = self.execution.fill_order(
                    tx, account, o, inst, o.limit_price, now, today, basis
                )
                return "filled" if fill is not None else "rejected"
            return "waiting"

    # ---------------------------------------------------------------- 开盘撮合

    def run_open_matching(self) -> dict[str, int]:
        now = self.clock.now()
        today = now.date()
        try:
            trading = self.reference.calendar().is_trading_day(today)
        except CalendarUnavailable:
            return {}
        if not trading or now.time() < T_0930 or self.settlement_pending() is not None:
            return {}
        orders = repos.pending_orders(self.db.read(), [OrderType.OPEN], trade_date=today)
        if not orders:
            return {}
        stats = {"filled": 0, "rejected": 0, "waiting": 0}
        snaps = self.refresh_snapshots(sorted({o.symbol for o in orders}))
        for order in orders:
            snap = snaps.get(order.symbol)
            if snap is None or snap.halted or snap.open <= 0 or to_beijing(snap.ts).date() != today:
                stats["waiting"] += 1  # 当日取不到开盘价的订单留到结算时按停牌顺延
                continue
            inst = self._instrument(order.symbol)
            if inst is None:
                stats["waiting"] += 1
                continue
            limits = self._limits(order.symbol, today, inst, snap)
            tick = TICK
            with self.db.write() as tx:
                o = repos.get_order(tx, order.account_id, order.id)
                account = repos.get_account(tx, order.account_id)
                if o is None or account is None or o.status is not OrderStatus.PENDING:
                    continue
                basis = {
                    "snapshot": snapshot_basis(snap),
                    "open": snap.open,
                    "up_limit": limits.up if limits else None,
                    "down_limit": limits.down if limits else None,
                    "slippage_rate": account.fee_params.slippage_rate,
                }
                eff = limits or PriceLimits(up=None, down=None)
                if limit_hit(snap.open, o.side, eff):
                    self.execution.reject_order(
                        tx,
                        account,
                        o,
                        "PRICE_LIMIT_HIT",
                        "开盘价" + _limit_word(o.side),
                        now,
                        basis,
                    )
                    stats["rejected"] += 1
                    continue
                price = market_fill_price(
                    snap.open, o.side, account.fee_params.slippage_rate, tick, eff
                )
                fill = self.execution.fill_order(tx, account, o, inst, price, now, today, basis)
                stats["filled" if fill is not None else "rejected"] += 1
        return stats
