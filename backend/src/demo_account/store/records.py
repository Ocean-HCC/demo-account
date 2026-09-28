"""各表对应的记录类型。金额用 Decimal，时间为北京时间。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..core.models import AccountStatus, EventType, FeeParams, OrderStatus, OrderType, Side
from ..timeutil import jsonable


@dataclass
class Account:
    id: str
    name: str
    note: str
    initial_cash: Decimal
    fee_params: FeeParams
    block_st: bool
    status: AccountStatus
    webhook_url: str | None
    webhook_secret: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "id": self.id,
                "name": self.name,
                "note": self.note,
                "initial_cash": self.initial_cash,
                "commission_rate": self.fee_params.commission_rate,
                "min_commission": self.fee_params.min_commission,
                "slippage_rate": self.fee_params.slippage_rate,
                "block_st": self.block_st,
                "status": self.status,
                "webhook_url": self.webhook_url,
                "webhook_configured": self.webhook_url is not None,
                "created_at": self.created_at,
                "updated_at": self.updated_at,
            }
        )


@dataclass
class Order:
    id: str
    account_id: str
    symbol: str
    side: Side
    order_type: OrderType
    qty: int
    amount: Decimal | None
    limit_price: Decimal | None
    protect_price: Decimal | None
    idempotency_key: str | None
    note: str
    tags: list[str]
    trade_date: date
    defer_count: int
    status: OrderStatus
    reason_code: str | None
    reason: str | None
    frozen_cash: Decimal
    frozen_qty: int
    created_at: datetime
    finished_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "id": self.id,
                "account_id": self.account_id,
                "symbol": self.symbol,
                "side": self.side,
                "order_type": self.order_type,
                "qty": self.qty,
                "amount": self.amount,
                "limit_price": self.limit_price,
                "protect_price": self.protect_price,
                "idempotency_key": self.idempotency_key,
                "note": self.note,
                "tags": list(self.tags),
                "trade_date": self.trade_date,
                "defer_count": self.defer_count,
                "status": self.status,
                "reason_code": self.reason_code,
                "reason": self.reason,
                "frozen_cash": self.frozen_cash,
                "frozen_qty": self.frozen_qty,
                "created_at": self.created_at,
                "finished_at": self.finished_at,
            }
        )


@dataclass
class Position:
    account_id: str
    symbol: str
    qty: int
    today_bought_qty: int
    cost_total: Decimal
    updated_at: datetime

    @property
    def sellable_qty(self) -> int:
        return self.qty - self.today_bought_qty


@dataclass
class NavDaily:
    account_id: str
    trade_date: date
    cash: Decimal
    market_value: Decimal
    total_assets: Decimal
    nav: Decimal
    day_pnl: Decimal
    benchmark_nav: Decimal | None
    finalized_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "trade_date": self.trade_date,
                "cash": self.cash,
                "market_value": self.market_value,
                "total_assets": self.total_assets,
                "nav": self.nav,
                "day_pnl": self.day_pnl,
                "benchmark_nav": self.benchmark_nav,
                "finalized_at": self.finalized_at,
            }
        )


@dataclass
class Event:
    account_id: str
    seq: int
    type: EventType
    occurred_at: datetime
    order_id: str | None
    fill_seq: int | None
    trade_date: date | None
    symbol: str | None
    basis: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    notify: bool = False
    delivered_at: datetime | None = None
    attempts: int = 0
    next_attempt_at: datetime | None = None

    @property
    def event_id(self) -> str:
        return f"{self.account_id}:{self.seq}"

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "event_id": self.event_id,
                "account_id": self.account_id,
                "seq": self.seq,
                "type": self.type,
                "occurred_at": self.occurred_at,
                "order_id": self.order_id,
                "fill_seq": self.fill_seq,
                "trade_date": self.trade_date,
                "symbol": self.symbol,
                "basis": self.basis,
                "summary": self.summary,
                "data": self.payload,
            }
        )


@dataclass
class SettlementRun:
    trade_date: date
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "trade_date": self.trade_date,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "error": self.error,
            }
        )


@dataclass
class Alert:
    id: int
    level: str
    code: str
    message: str
    trade_date: date | None
    created_at: datetime
    resolved_at: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return jsonable(
            {
                "id": self.id,
                "level": self.level,
                "code": self.code,
                "message": self.message,
                "trade_date": self.trade_date,
                "created_at": self.created_at,
                "resolved_at": self.resolved_at,
            }
        )
