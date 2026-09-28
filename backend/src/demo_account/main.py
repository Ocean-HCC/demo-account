"""FastAPI 应用工厂。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI

from . import __version__
from .api import accounts, admin, events, market, orders, portfolio
from .api.deps import require_api_key
from .api.errors import install_error_handlers
from .api.static import mount_frontend
from .config import Settings
from .scheduler import Scheduler
from .services.container import Container, build_container

log = logging.getLogger(__name__)
DEFAULT_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _startup(c: Container) -> None:
    today = c.clock.now().date()
    try:
        c.reference.ensure_calendar(today)
    except Exception as e:  # 日历不可用时服务仍然启动，下单返回 503
        c.alerts.raise_alert("error", "CALENDAR_UNAVAILABLE", f"启动时交易日历不可用：{e}", today)
        return
    try:
        done = c.settlement.catch_up()
        if done:
            log.info("catch-up settled %s", [d.isoformat() for d in done])
    except Exception as e:
        log.exception("catch-up failed")
        c.alerts.raise_alert("error", "CATCH_UP_FAILED", f"启动补跑失败：{e}", today)


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    if container is None:
        container = build_container(settings or Settings.from_env())
    c = container

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        c.bus.bind_loop(asyncio.get_running_loop())
        await asyncio.to_thread(_startup, c)
        sched = Scheduler(c) if c.settings.scheduler_enabled else None
        if sched is not None:
            sched.start()
        try:
            yield
        finally:
            if sched is not None:
                await sched.stop()

    app = FastAPI(title="demo-account", version=__version__, lifespan=lifespan)
    app.state.container = c
    install_error_handlers(app)
    deps = [Depends(require_api_key)]
    for r in (
        accounts.router,
        orders.router,
        portfolio.router,
        market.router,
        events.router,
        admin.router,
    ):
        app.include_router(r, dependencies=deps)

    @app.get("/api/health", dependencies=deps, tags=["admin"])
    def health() -> dict[str, Any]:
        s = c.market_status.status()
        return {
            "status": "ok",
            "version": __version__,
            "market": c.settings.market,
            "catching_up": c.state.catching_up,
            "quote_source": {**s["quote_source"], "detail": c.quote_source.health()},
            "reference_source": {**s["reference_source"], "detail": c.reference_source.health()},
            "clock_skew": c.state.clock_skew,
            "clock_offset_seconds": c.clock_monitor.offset_seconds,
            "last_snapshot_at": s["last_snapshot_at"],
            "last_settlement_date": s["last_settlement_date"],
            "unresolved_alerts": s["unresolved_alerts"],
        }

    mount_frontend(app, c.settings.frontend_dist or DEFAULT_DIST)
    return app
