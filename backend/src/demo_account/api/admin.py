"""管理接口：手动结算、手动撮合、台账核对、告警与结算记录（实现 6.2）。"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Query

from ..services.container import Container
from ..store import repos
from .deps import get_container

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/settle")
def settle(
    date_: date | None = Query(default=None, alias="date"), c: Container = Depends(get_container)
) -> dict[str, Any]:
    d = date_ or c.clock.now().date()
    return c.settlement.settle(d).to_dict()


@router.post("/match")
def match(c: Container = Depends(get_container)) -> dict[str, Any]:
    """立即执行一次开盘撮合与盘中撮合，供调试与 Mock 演示。"""
    return {"open": c.engine.run_open_matching(), "intraday": c.engine.run_intraday_cycle()}


@router.post("/reconcile/{account_id}")
def reconcile(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.settlement.reconcile(account_id)


@router.get("/alerts")
def alerts(
    include_resolved: bool = False, c: Container = Depends(get_container)
) -> list[dict[str, Any]]:
    return [
        a.to_dict() for a in repos.list_alerts(c.db.read(), unresolved_only=not include_resolved)
    ]


@router.post("/alerts/{alert_id}/resolve")
def resolve(alert_id: int, c: Container = Depends(get_container)) -> dict[str, Any]:
    with c.db.write() as tx:
        repos.resolve_alert(tx, alert_id, c.clock.now())
    return {"id": alert_id, "resolved": True}


@router.get("/settlements")
def settlements(c: Container = Depends(get_container)) -> list[dict[str, Any]]:
    return [r.to_dict() for r in repos.list_runs(c.db.read())]
