"""请求模型。金额与价格用 Decimal 接收，响应中一律为字符串。"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from ..core.models import OrderType, Side
from ..services.orders import OrderRequest


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=1000)
    initial_cash: Decimal | None = None
    commission_rate: Decimal | None = None
    min_commission: Decimal | None = None
    slippage_rate: Decimal | None = None
    block_st: bool | None = None


class AccountUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    commission_rate: Decimal | None = None
    min_commission: Decimal | None = None
    slippage_rate: Decimal | None = None
    block_st: bool | None = None


class WebhookSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str | None = Field(default=None, max_length=1000)
    secret: str | None = Field(default=None, max_length=200)


class OrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1, max_length=20)
    side: Side
    order_type: OrderType
    qty: int | None = None
    amount: Decimal | None = None
    limit_price: Decimal | None = None
    protect_price: Decimal | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=500)
    tags: list[str] = Field(default_factory=list)

    def to_request(self) -> OrderRequest:
        return OrderRequest(
            symbol=self.symbol.strip().upper(),
            side=self.side,
            order_type=self.order_type,
            qty=self.qty,
            amount=self.amount,
            limit_price=self.limit_price,
            protect_price=self.protect_price,
            idempotency_key=self.idempotency_key or None,
            note=self.note,
            tags=list(self.tags),
        )
