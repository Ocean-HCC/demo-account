"""持仓、成交、净值、统计与事件序列接口（实现 6.2）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..core.models import EventType
from ..services.container import Container
from ..services.errors import ServiceError
from ..store import repos
from .deps import get_container

router = APIRouter(prefix="/api/accounts/{account_id}", tags=["portfolio"])


@router.get("/positions")
def positions(account_id: str, c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return c.portfolio.positions(c.accounts.get(account_id))


@router.get("/fills")
def fills(
    account_id: str,
    symbol: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    c: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    c.accounts.get(account_id)
    rows = repos.list_fills(
        c.db.read(), account_id, symbol.upper() if symbol else None, from_, to, limit, offset
    )
    return [repos.fill_to_dict(f) for f in rows]


@router.get("/nav")
def nav(account_id: str, c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return c.portfolio.nav(c.accounts.get(account_id))


@router.get("/stats")
def stats(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.portfolio.stats(c.accounts.get(account_id))


@router.get("/events")
def events(
    account_id: str,
    after: int = Query(default=0, ge=0),
    types: str | None = None,
    symbol: str | None = None,
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    c: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    c.accounts.get(account_id)
    type_list: list[EventType] | None = None
    if types:
        try:
            type_list = [EventType(t.strip()) for t in types.split(",") if t.strip()]
        except ValueError as e:
            raise ServiceError("INVALID_REQUEST", f"未知的事件类型：{e}") from e
    rows = repos.list_events(
        c.db.read(),
        account_id,
        after,
        type_list,
        symbol.upper() if symbol else None,
        from_,
        to,
        limit,
    )
    return [e.to_dict() for e in rows]
