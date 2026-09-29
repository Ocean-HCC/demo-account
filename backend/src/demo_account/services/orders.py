"""下单、预估与撤单（方案 3.2、3.3、4.1、6.1、6.11；实现 5.2）。

校验分两步：先在事务外取得行情与基础数据（可能访问数据源），再在写事务内按方案 4.1 的顺序
判定账户状态、标的、时段、数量与价格、资金或可卖数量，并冻结资金或股份。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..clock import Clock
from ..config import Settings
from ..core.fees import compute_fees
from ..core.instruments import (
    ST_DAILY_BUY_CAP,
    SymbolError,
    parse_symbol,
    st_order_allowed,
    validate_qty,
)
from ..core.matching import amount_to_qty, freeze_amount
from ..core.models import (
    AccountStatus,
    EventType,
    Instrument,
    OrderStatus,
    OrderType,
    PriceLimits,
    Session,
    Side,
)
from ..core.money import ZERO, is_on_tick, round_cent, round_to_tick, tick_for
from ..core.rules import CalendarUnavailable, assign_trade_date, can_submit, session_of
from ..market.base import MarketDataError
from ..market.reference import ReferenceService
from ..store import repos
from ..store.db import Database, Executor
from ..store.records import Account, Order
from .errors import ServiceError
from .events import EventService
from .execution import SIDE_CN, Execution
from .ids import new_order_id
from .market_status import QuoteCache, snapshot_basis

SESSION_CN = {
    Session.PRE_OPEN: "开盘前",
    Session.OPENING_AUCTION: "开盘集合竞价",
    Session.CONTINUOUS: "连续竞价",
    Session.LUNCH: "午间休市",
    Session.CLOSING_AUCTION: "收盘集合竞价",
    Session.AFTER_HOURS: "盘后固定价格交易",
    Session.CLOSED: "休市",
}


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    order_type: OrderType
    qty: int | None = None
    amount: Decimal | None = None
    limit_price: Decimal | None = None
    protect_price: Decimal | None = None
    idempotency_key: str | None = None
    note: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class RuntimeState:
    """进程级运行状态：时钟异常时拒绝下单。结算未完成的暂停由待结算日推导，不在这里保存。"""

    clock_skew: bool = False


@dataclass
class _Market:
    inst: Instrument | None
    prev_close: Decimal | None
    limits: PriceLimits | None
    suspended: bool
    error: str | None
    code: str | None


@dataclass
class Checked:
    ok: bool
    code: str | None
    reason: str | None
    qty: int
    trade_date: date
    freeze_price: Decimal | None
    frozen_cash: Decimal
    frozen_qty: int
    est_fees: Decimal
    basis: dict[str, Any]


class OrderService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        reference: ReferenceService,
        quotes: QuoteCache,
        events: EventService,
        execution: Execution,
        settings: Settings,
        state: RuntimeState,
        settlement_pending: Callable[[], date | None],
    ) -> None:
        self.db = db
        self.clock = clock
        self.reference = reference
        self.quotes = quotes
        self.events = events
        self.execution = execution
        self.settings = settings
        self.state = state
        self.settlement_pending = settlement_pending

    # ---------------------------------------------------------------- 对外

    def submit(self, account_id: str, req: OrderRequest) -> Order:
        self._guard()
        _check_shape(req)
        now = self.clock.now()
        existing = self._idempotent(account_id, req)
        if existing is not None:
            return existing
        trade_date, session = self._timing(req.order_type, now)
        market = self._market(req.symbol, trade_date, now.date())
        with self.db.write() as tx:
            account = repos.get_account(tx, account_id)
            if account is None:
                raise ServiceError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_id}", 404)
            if account.status is AccountStatus.ARCHIVED:
                raise ServiceError("ACCOUNT_NOT_ACTIVE", "账户已归档，只读", 409)
            if req.idempotency_key:
                again = repos.get_order_by_idem(tx, account_id, req.idempotency_key)
                if again is not None:
                    return again
            chk = self._check(tx, account, req, now, trade_date, session, market)
            order = Order(
                id=new_order_id(now),
                account_id=account_id,
                symbol=req.symbol,
                side=req.side,
                order_type=req.order_type,
                qty=chk.qty,
                amount=req.amount,
                limit_price=req.limit_price,
                protect_price=req.protect_price,
                idempotency_key=req.idempotency_key,
                note=req.note,
                tags=list(req.tags),
                trade_date=trade_date,
                defer_count=0,
                status=OrderStatus.PENDING if chk.ok else OrderStatus.REJECTED,
                reason_code=chk.code,
                reason=chk.reason,
                frozen_cash=chk.frozen_cash if chk.ok else ZERO,
                frozen_qty=chk.frozen_qty if chk.ok else 0,
                created_at=now,
                finished_at=None if chk.ok else now,
            )
            repos.insert_order(tx, order)
            verb = "提交" if chk.ok else "被拒绝"
            self.events.emit(
                tx,
                account,
                EventType.ORDER_SUBMITTED if chk.ok else EventType.ORDER_REJECTED,
                occurred_at=now,
                order_id=order.id,
                trade_date=trade_date,
                symbol=req.symbol,
                basis=chk.basis,
                summary=(
                    f"{SIDE_CN[req.side]} {req.symbol} {chk.qty} 股（{req.order_type.value}）{verb}"
                    + ("" if chk.ok else f"：{chk.reason}")
                ),
                payload={"order": order.to_dict()},
            )
        return order

    def preview(self, account_id: str, req: OrderRequest) -> dict[str, Any]:
        _check_shape(req)
        now = self.clock.now()
        trade_date, session = self._timing(req.order_type, now)
        market = self._market(req.symbol, trade_date, now.date())
        ex = self.db.read()
        account = repos.get_account(ex, account_id)
        if account is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_id}", 404)
        chk = self._check(ex, account, req, now, trade_date, session, market)
        return {
            "ok": chk.ok,
            "reason_code": chk.code,
            "reason": chk.reason,
            "qty": chk.qty,
            "trade_date": trade_date.isoformat(),
            "freeze_price": str(chk.freeze_price) if chk.freeze_price is not None else None,
            "frozen_cash": str(chk.frozen_cash),
            "frozen_qty": chk.frozen_qty,
            "estimated_fees": str(chk.est_fees),
            "basis": chk.basis,
        }

    def cancel(self, account_id: str, order_id: str) -> Order:
        now = self.clock.now()
        with self.db.write() as tx:
            account = repos.get_account(tx, account_id)
            if account is None:
                raise ServiceError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_id}", 404)
            order = repos.get_order(tx, account_id, order_id)
            if order is None:
                raise ServiceError("ORDER_NOT_FOUND", f"订单不存在: {order_id}", 404)
            if order.status is not OrderStatus.PENDING:
                raise ServiceError(
                    "ORDER_NOT_PENDING", f"订单状态为 {order.status.value}，不可撤销", 409
                )
            self.execution.cancel_order(tx, account, order, "CANCELLED_BY_USER", "调用方撤单", now)
        return order

    def get(self, account_id: str, order_id: str) -> Order:
        order = repos.get_order(self.db.read(), account_id, order_id)
        if order is None:
            raise ServiceError("ORDER_NOT_FOUND", f"订单不存在: {order_id}", 404)
        return order

    # ---------------------------------------------------------------- 内部

    def _guard(self) -> None:
        pending = self.settlement_pending()
        if pending is not None:
            raise ServiceError(
                "SETTLEMENT_CATCHUP", f"{pending} 的日终结算尚未完成，暂停交易，完成后再下单", 503
            )
        if self.state.clock_skew:
            raise ServiceError("CLOCK_SKEW", "系统时钟与北京时间偏差过大，暂停下单", 503)

    def _idempotent(self, account_id: str, req: OrderRequest) -> Order | None:
        if not req.idempotency_key:
            return None
        return repos.get_order_by_idem(self.db.read(), account_id, req.idempotency_key)

    def _timing(self, order_type: OrderType, now: datetime) -> tuple[date, Session]:
        try:
            self.reference.ensure_calendar(now.date())
            cal = self.reference.calendar()
            trading = cal.is_trading_day(now.date())
            return assign_trade_date(order_type, now, cal), session_of(now, trading)
        except (CalendarUnavailable, MarketDataError) as e:
            raise ServiceError("CALENDAR_UNAVAILABLE", f"交易日历不可用：{e}", 503) from e

    def _market(self, symbol: str, trade_date: date, today: date) -> _Market:
        try:
            parse_symbol(symbol)
        except SymbolError as e:
            return _Market(None, None, None, False, str(e), "SYMBOL_UNSUPPORTED")
        try:
            inst = self.reference.instrument(symbol)
        except MarketDataError as e:
            return _Market(None, None, None, False, f"标的信息缺失：{e}", "SYMBOL_INFO_MISSING")
        if inst is None:
            return _Market(None, None, None, False, f"不支持的标的: {symbol}", "SYMBOL_UNSUPPORTED")
        try:
            prev_close = self.reference.reference_prev_close(symbol, trade_date, today)
            limits = self.reference.price_limits(symbol, trade_date, inst, today=today)
        except MarketDataError as e:
            return _Market(inst, None, None, False, f"标的信息缺失：{e}", "SYMBOL_INFO_MISSING")
        if prev_close is None:
            return _Market(
                inst, None, None, False, "标的信息缺失：取不到前收盘价", "SYMBOL_INFO_MISSING"
            )
        suspended = trade_date == today and self.reference.is_suspended(symbol, today)
        return _Market(inst, prev_close, limits, suspended, None, None)

    def _check(
        self,
        ex: Executor,
        account: Account,
        req: OrderRequest,
        now: datetime,
        trade_date: date,
        session: Session,
        m: _Market,
    ) -> Checked:
        snap = self.quotes.get(req.symbol)
        basis: dict[str, Any] = {
            "checked_at": now,
            "session": session.value,
            "trade_date": trade_date,
            "prev_close": m.prev_close,
            "up_limit": m.limits.up if m.limits else None,
            "down_limit": m.limits.down if m.limits else None,
            "suspended": m.suspended,
            "is_st": m.inst.is_st if m.inst else None,
        }
        if snap is not None and snap.ts.date() == now.date():
            basis["snapshot"] = snapshot_basis(snap)
        qty = req.qty or 0

        def reject(code: str, reason: str) -> Checked:
            return Checked(False, code, reason, qty, trade_date, None, ZERO, 0, ZERO, basis)

        # 1. 账户状态
        if account.status is not AccountStatus.ACTIVE:
            return reject("ACCOUNT_NOT_ACTIVE", "账户已冻结")
        # 2. 标的合法且可交易
        if m.code is not None or m.inst is None or m.limits is None or m.prev_close is None:
            return reject(m.code or "SYMBOL_INFO_MISSING", m.error or "标的信息缺失")
        inst, limits = m.inst, m.limits
        if m.suspended:
            return reject("SYMBOL_SUSPENDED", "标的当日停牌")
        if inst.is_st:
            if req.side is Side.BUY and account.block_st:
                return reject("ST_BLOCKED", "账户禁止买入风险警示股")
            if not st_order_allowed(req.order_type, req.protect_price):
                return reject(
                    "ST_ORDER_TYPE_NOT_ALLOWED", "风险警示股只接受限价单和带保护限价的收盘单"
                )
        # 3. 时段
        if not can_submit(req.order_type, session):
            return reject(
                "OUTSIDE_SESSION", f"即时市价单只能在连续竞价时段提交，当前为{SESSION_CN[session]}"
            )
        # 4. 数量与价格
        tick = tick_for(inst.asset_type)
        for label, price in (("限价", req.limit_price), ("保护限价", req.protect_price)):
            if price is None:
                continue
            if price <= 0 or not is_on_tick(price, tick):
                return reject("PRICE_TICK", f"{label}不符合最小变动单位 {tick}")
            if (limits.up is not None and price > limits.up) or (
                limits.down is not None and price < limits.down
            ):
                return reject(
                    "PRICE_OUT_OF_LIMIT",
                    f"{label} {price} 超出所属交易日涨跌停区间 [{limits.down}, {limits.up}]",
                )
        freeze_price = _freeze_price(req, limits, m.prev_close, tick)
        basis["freeze_price"] = freeze_price
        if req.amount is not None:
            qty = amount_to_qty(
                req.amount, freeze_price, inst.board, inst.asset_type, account.fee_params
            )
            if qty <= 0:
                return reject("LOT_SIZE", "按金额折算后不足最小申报数量")
        position = repos.get_position(ex, account.id, req.symbol)
        err = validate_qty(
            qty, req.side, inst.board, req.order_type, position.qty if position else None
        )
        if err == "QTY_LIMIT":
            return reject("QTY_LIMIT", "超过单笔申报数量上限")
        if err is not None:
            return reject("LOT_SIZE", "申报数量不符合规则")
        if inst.is_st and req.side is Side.BUY:
            bought = repos.bought_qty_on(ex, account.id, req.symbol, now.date())
            pending = repos.pending_buy_qty(ex, account.id, req.symbol)
            if bought + pending + qty > ST_DAILY_BUY_CAP:
                return reject("ST_DAILY_BUY_LIMIT", "当日累计买入该风险警示股将超过 50 万股")
        # 5. 资金或可卖数量
        if req.side is Side.BUY:
            need = freeze_amount(qty, freeze_price, account.fee_params, inst.asset_type)
            cash = repos.account_cash(ex, account.id, account.initial_cash)
            available = cash - repos.frozen_cash_total(ex, account.id)
            basis["available_cash"] = available
            basis["need"] = need
            fees = need - _gross(qty, freeze_price)
            if need > available:
                return Checked(
                    False,
                    "INSUFFICIENT_CASH",
                    f"可用现金 {available} 不足 {need}",
                    qty,
                    trade_date,
                    freeze_price,
                    ZERO,
                    0,
                    fees,
                    basis,
                )
            return Checked(True, None, None, qty, trade_date, freeze_price, need, 0, fees, basis)
        sellable = (position.sellable_qty if position else 0) - repos.frozen_sell_qty(
            ex, account.id, req.symbol
        )
        basis["sellable_qty"] = sellable
        gross = _gross(qty, freeze_price)
        fees = compute_fees(gross, Side.SELL, inst.asset_type, account.fee_params).total
        if qty > sellable:
            return Checked(
                False,
                "INSUFFICIENT_SELLABLE",
                f"可卖数量 {sellable} 不足 {qty}",
                qty,
                trade_date,
                freeze_price,
                ZERO,
                0,
                fees,
                basis,
            )
        return Checked(True, None, None, qty, trade_date, freeze_price, ZERO, qty, fees, basis)


def _gross(qty: int, price: Decimal) -> Decimal:
    return round_cent(Decimal(qty) * price)


def _freeze_price(
    req: OrderRequest, limits: PriceLimits, prev_close: Decimal, tick: Decimal
) -> Decimal:
    """冻结价（方案 6.1）：限价单用限价，带保护限价的收盘单用保护限价，其余用涨停价；
    无涨跌幅限制的新股用前收盘价的 2 倍估算（方案 8.1）。"""
    if req.order_type is OrderType.LIMIT and req.limit_price is not None:
        return req.limit_price
    if req.order_type is OrderType.CLOSE and req.protect_price is not None:
        return req.protect_price
    if req.side is Side.SELL:
        return limits.down if limits.down is not None else prev_close
    if limits.up is not None:
        return limits.up
    return round_to_tick(prev_close * 2, tick)


def _check_shape(req: OrderRequest) -> None:
    """请求结构校验：不合法的请求直接返回 400，不形成订单记录。"""
    if (req.qty is None) == (req.amount is None):
        raise ServiceError("INVALID_REQUEST", "数量和金额必须且只能给出一个")
    if req.amount is not None and req.side is not Side.BUY:
        raise ServiceError("INVALID_REQUEST", "按金额下单只用于买入")
    if req.amount is not None and req.amount <= 0:
        raise ServiceError("INVALID_REQUEST", "金额必须为正数")
    if req.qty is not None and req.qty <= 0:
        raise ServiceError("INVALID_REQUEST", "数量必须为正整数")
    if req.order_type is OrderType.LIMIT and req.limit_price is None:
        raise ServiceError("INVALID_REQUEST", "限价单必须给出限价")
    if req.order_type is not OrderType.LIMIT and req.limit_price is not None:
        raise ServiceError("INVALID_REQUEST", "只有限价单可以给出限价")
    if req.protect_price is not None and req.order_type is not OrderType.CLOSE:
        raise ServiceError("INVALID_REQUEST", "只有收盘单可以给出保护限价")
    if len(req.note) > 500:
        raise ServiceError("INVALID_REQUEST", "备注不超过 500 字")
    if len(req.tags) > 20 or any(len(t) > 50 for t in req.tags):
        raise ServiceError("INVALID_REQUEST", "标签最多 20 个，每个不超过 50 字")
