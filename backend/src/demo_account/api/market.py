"""市场状态、标的信息与快照接口（实现 6.2）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..core.instruments import SymbolError, parse_symbol
from ..core.rules import CalendarUnavailable
from ..market.base import MarketDataError
from ..services.container import Container
from ..services.errors import ServiceError
from ..services.market_status import snapshot_basis
from ..timeutil import jsonable
from .deps import get_container

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/status")
def status(c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.market_status.status()


def _inst_dict(inst: Any) -> dict[str, Any]:
    return jsonable(
        {
            "symbol": inst.symbol,
            "name": inst.name,
            "asset_type": inst.asset_type,
            "board": inst.board,
            "exchange": inst.exchange,
            "list_date": inst.list_date,
            "is_st": inst.is_st,
        }
    )


@router.get("/instruments")
def search(
    q: str = Query(min_length=1, max_length=20),
    limit: int = Query(default=20, ge=1, le=50),
    c: Container = Depends(get_container),
) -> list[dict[str, Any]]:
    found = {i.symbol: i for i in c.reference.search(q, limit)}
    candidates = (
        [q.upper()] if "." in q else [f"{q}.SH", f"{q}.SZ"] if q.isdigit() and len(q) == 6 else []
    )
    for sym in candidates:
        if sym in found:
            continue
        try:
            parse_symbol(sym)
            inst = c.reference.instrument(sym)
        except (SymbolError, MarketDataError):
            continue
        if inst is not None:
            found[sym] = inst
    return [_inst_dict(i) for i in list(found.values())[:limit]]


@router.get("/instruments/{symbol}")
def instrument(symbol: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    symbol = symbol.upper()
    try:
        parse_symbol(symbol)
    except SymbolError as e:
        raise ServiceError("SYMBOL_UNSUPPORTED", str(e)) from e
    try:
        inst = c.reference.instrument(symbol)
    except MarketDataError as e:
        raise ServiceError("SYMBOL_INFO_MISSING", f"标的信息缺失：{e}", 502) from e
    if inst is None:
        raise ServiceError("SYMBOL_UNSUPPORTED", f"不支持的标的: {symbol}", 404)
    today = c.clock.now().date()
    out = _inst_dict(inst)
    try:
        trading = c.reference.calendar().is_trading_day(today)
        d = today if trading else c.reference.calendar().next_trading_day(today)
        limits = c.reference.price_limits(symbol, d, inst, today=today)
        out["prev_close"] = jsonable(c.reference.reference_prev_close(symbol, d, today))
        out["limits_date"] = d.isoformat()
        out["up_limit"] = jsonable(limits.up)
        out["down_limit"] = jsonable(limits.down)
        out["suspended"] = trading and c.reference.is_suspended(symbol, today)
    except (MarketDataError, CalendarUnavailable) as e:
        out["reference_error"] = str(e)
    snap = c.quotes.get(symbol)
    out["snapshot"] = snapshot_basis(snap) if snap is not None else None
    return out


@router.get("/quotes")
def quotes(
    symbols: str = Query(min_length=1), c: Container = Depends(get_container)
) -> dict[str, Any]:
    wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()][:50]
    cached = c.quotes.many(wanted)
    missing = [s for s in wanted if s not in cached]
    if missing:
        cached.update(c.engine.refresh_snapshots(missing))
    return {s: snapshot_basis(v) for s, v in cached.items()}
