"""订单接口（实现 6.2）。校验阶段被拒的订单已落库留痕，接口以错误响应返回并附带该订单。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..core.models import OrderStatus
from ..services.container import Container
from ..services.errors import ServiceError
from ..store import repos
from .deps import get_container
from .schemas import OrderCreate

router = APIRouter(prefix="/api/accounts/{account_id}/orders", tags=["orders"])


@router.post("", status_code=202)
def submit_order(
    account_id: str, body: OrderCreate, c: Container = Depends(get_container)
) -> dict[str, Any]:
    order = c.orders.submit(account_id, body.to_request())
    if order.status is OrderStatus.REJECTED:
        code = order.reason_code or "ORDER_REJECTED"
        status = 409 if code == "ACCOUNT_NOT_ACTIVE" else 400
        raise ServiceError(code, order.reason or "订单被拒绝", status, {"order": order.to_dict()})
    return order.to_dict()


@router.post("/preview")
def preview_order(
    account_id: str, body: OrderCreate, c: Container = Depends(get_container)
) -> dict[str, Any]:
    return c.orders.preview(account_id, body.to_request())


@router.get("")
def list_orders(
    account_id: str,
    status: OrderStatus | None = None,
    symbol: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    c: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    c.accounts.get(account_id)
    orders = repos.list_orders(
        c.db.read(),
        account_id,
        status,
        symbol.upper() if symbol else None,
        from_,
        to,
        limit,
        offset,
    )
    return [o.to_dict() for o in orders]


@router.get("/{order_id}")
def get_order(
    account_id: str, order_id: str, c: Container = Depends(get_container)
) -> dict[str, Any]:
    return c.orders.get(account_id, order_id).to_dict()


@router.delete("/{order_id}")
def cancel_order(
    account_id: str, order_id: str, c: Container = Depends(get_container)
) -> dict[str, Any]:
    return c.orders.cancel(account_id, order_id).to_dict()
