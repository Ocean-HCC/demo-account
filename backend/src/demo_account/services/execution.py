"""订单状态转换与成交落账，被下单、撮合引擎、日终结算与账户管理共用（实现 5.2 至 5.4）。

每个转换都在调用方给出的事务内完成，并写入对应的账户事件。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..clock import Clock
from ..config import Settings
from ..core.fees import compute_fees
from ..core.ledger import LedgerState, PositionState, apply_fill
from ..core.models import EventType, Fill, FillKind, Instrument, OrderStatus, Side
from ..core.money import ZERO, D, round_cent
from ..store import repos
from ..store.db import Tx
from ..store.records import Account, Order, Position
from .events import AlertService, EventService

SIDE_CN = {Side.BUY: "买入", Side.SELL: "卖出"}


class Execution:
    def __init__(
        self, clock: Clock, events: EventService, alerts: AlertService, settings: Settings
    ) -> None:
        self.clock = clock
        self.events = events
        self.alerts = alerts
        self.settings = settings

    # ---------------------------------------------------------------- 台账与持仓

    def write_fill(self, tx: Tx, fill: Fill, at: datetime) -> Fill:
        """写入一条台账记录，并用 core.ledger 同一套规则更新物化持仓。"""
        seq = repos.insert_fill(tx, fill)
        fill = replace(fill, seq=seq)
        pos = repos.get_position(tx, fill.account_id, fill.symbol)
        state = LedgerState(cash=ZERO, as_of=fill.trade_date)
        if pos is not None:
            state.positions[fill.symbol] = PositionState(
                qty=pos.qty, today_bought_qty=pos.today_bought_qty, cost_total=pos.cost_total
            )
        apply_fill(state, fill)
        p = state.positions.get(fill.symbol)
        if p is None:
            repos.delete_position(tx, fill.account_id, fill.symbol)
        else:
            repos.upsert_position(
                tx,
                Position(
                    account_id=fill.account_id,
                    symbol=fill.symbol,
                    qty=p.qty,
                    today_bought_qty=p.today_bought_qty,
                    cost_total=p.cost_total,
                    updated_at=at,
                ),
            )
        return fill

    # ---------------------------------------------------------------- 订单转换

    def fill_order(
        self,
        tx: Tx,
        account: Account,
        order: Order,
        inst: Instrument,
        price: Decimal,
        at: datetime,
        trade_date: date,
        basis: dict[str, Any],
    ) -> Fill | None:
        gross = round_cent(D(order.qty) * price)
        fees = compute_fees(gross, order.side, inst.asset_type, account.fee_params)
        if order.side is Side.BUY:
            cash = repos.account_cash(tx, account.id, account.initial_cash)
            others = repos.frozen_cash_total(tx, account.id, exclude_order_id=order.id)
            need = gross + fees.total
            if cash - others < need:
                self.reject_order(
                    tx,
                    account,
                    order,
                    "INSUFFICIENT_CASH",
                    "成交时可用现金不足",
                    at,
                    {**basis, "cash": cash, "other_frozen": others, "need": need},
                )
                self.alerts.raise_alert(
                    "warning",
                    "FILL_CASH_SHORT",
                    f"{account.id} 订单 {order.id} 成交时可用现金不足",
                    trade_date,
                    tx=tx,
                )
                return None
            cash_delta = -need
        else:
            pos = repos.get_position(tx, account.id, order.symbol)
            if pos is None or pos.sellable_qty < order.qty:
                self.reject_order(
                    tx, account, order, "INSUFFICIENT_SELLABLE", "成交时可卖数量不足", at, basis
                )
                return None
            cash_delta = gross - fees.total
        fill = self.write_fill(
            tx,
            Fill(
                seq=None,
                account_id=account.id,
                order_id=order.id,
                symbol=order.symbol,
                kind=FillKind.TRADE,
                side=order.side,
                qty=order.qty,
                price=price,
                gross_amount=gross,
                commission=fees.commission,
                stamp_tax=fees.stamp_tax,
                transfer_fee=fees.transfer_fee,
                cash_delta=cash_delta,
                trade_date=trade_date,
                occurred_at=at,
                note=order.note,
            ),
            at,
        )
        order.status = OrderStatus.FILLED
        order.finished_at = at
        order.frozen_cash = ZERO
        order.frozen_qty = 0
        repos.update_order(tx, order)
        self.events.emit(
            tx,
            account,
            EventType.ORDER_FILLED,
            occurred_at=at,
            order_id=order.id,
            fill_seq=fill.seq,
            trade_date=trade_date,
            symbol=order.symbol,
            basis={
                **basis,
                "fill_price": price,
                "fees": {
                    "commission": fees.commission,
                    "stamp_tax": fees.stamp_tax,
                    "transfer_fee": fees.transfer_fee,
                },
            },
            summary=f"{SIDE_CN[order.side]} {order.symbol} {order.qty} 股，成交价 {price}",
            payload={"order": order.to_dict(), "fill": repos.fill_to_dict(fill)},
        )
        return fill

    def _finish(
        self,
        tx: Tx,
        account: Account,
        order: Order,
        status: OrderStatus,
        event_type: EventType,
        code: str,
        reason: str,
        at: datetime,
        basis: dict[str, Any],
        verb: str,
    ) -> None:
        order.status = status
        order.reason_code = code
        order.reason = reason
        order.finished_at = at
        order.frozen_cash = ZERO
        order.frozen_qty = 0
        repos.update_order(tx, order)
        self.events.emit(
            tx,
            account,
            event_type,
            occurred_at=at,
            order_id=order.id,
            trade_date=order.trade_date,
            symbol=order.symbol,
            basis={**basis, "reason_code": code, "reason": reason},
            summary=f"{SIDE_CN[order.side]} {order.symbol} {order.qty} 股{verb}：{reason}",
            payload={"order": order.to_dict()},
        )

    def reject_order(
        self,
        tx: Tx,
        account: Account,
        order: Order,
        code: str,
        reason: str,
        at: datetime,
        basis: dict[str, Any],
    ) -> None:
        self._finish(
            tx,
            account,
            order,
            OrderStatus.REJECTED,
            EventType.ORDER_REJECTED,
            code,
            reason,
            at,
            basis,
            "被拒绝",
        )

    def expire_order(
        self,
        tx: Tx,
        account: Account,
        order: Order,
        code: str,
        reason: str,
        at: datetime,
        basis: dict[str, Any],
    ) -> None:
        self._finish(
            tx,
            account,
            order,
            OrderStatus.EXPIRED,
            EventType.ORDER_EXPIRED,
            code,
            reason,
            at,
            basis,
            "已失效",
        )

    def cancel_order(
        self, tx: Tx, account: Account, order: Order, code: str, reason: str, at: datetime
    ) -> None:
        self._finish(
            tx,
            account,
            order,
            OrderStatus.CANCELLED,
            EventType.ORDER_CANCELLED,
            code,
            reason,
            at,
            {},
            "已撤销",
        )

    def defer_order(
        self,
        tx: Tx,
        account: Account,
        order: Order,
        next_date: date,
        at: datetime,
        reason: str,
        basis: dict[str, Any],
    ) -> bool:
        """停牌顺延到下一交易日；累计顺延超过上限则失效。返回是否仍在等待。"""
        if order.defer_count + 1 > self.settings.max_defer_days:
            self.expire_order(
                tx,
                account,
                order,
                "DEFER_LIMIT",
                f"停牌顺延超过 {self.settings.max_defer_days} 个交易日",
                at,
                basis,
            )
            return False
        order.defer_count += 1
        order.trade_date = next_date
        repos.update_order(tx, order)
        return True
