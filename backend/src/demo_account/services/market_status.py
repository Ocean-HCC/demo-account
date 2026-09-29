"""快照缓存、数据源健康与市场状态（方案 3.4 市场状态；实现 5.3、8.2）。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from ..clock import Clock
from ..core.models import Snapshot
from ..core.rules import T_0930, T_1500, CalendarUnavailable, session_of
from ..store import repos
from ..store.db import Database
from ..timeutil import iso, jdict


class QuoteCache:
    """最新快照的内存缓存，供估值、查询与撮合共用。"""

    def __init__(self) -> None:
        self._snaps: dict[str, Snapshot] = {}
        self._lock = threading.Lock()
        self.last_update: datetime | None = None

    def update(self, snaps: dict[str, Snapshot], at: datetime) -> None:
        with self._lock:
            self._snaps.update(snaps)
            self.last_update = at

    def get(self, symbol: str) -> Snapshot | None:
        with self._lock:
            return self._snaps.get(symbol)

    def many(self, symbols: list[str]) -> dict[str, Snapshot]:
        with self._lock:
            return {s: self._snaps[s] for s in symbols if s in self._snaps}


class SourceHealth:
    """数据源健康：连续失败超过阈值置为不可用（实现 3.2）。"""

    def __init__(self, name: str, threshold: int = 5) -> None:
        self.name = name
        self.threshold = threshold
        self.failures = 0
        self.last_ok_at: datetime | None = None
        self.last_error: str | None = None
        self.alerted = False

    @property
    def available(self) -> bool:
        return self.failures < self.threshold

    def ok(self, at: datetime) -> None:
        self.failures = 0
        self.last_ok_at = at
        self.alerted = False

    def fail(self, error: str) -> None:
        self.failures += 1
        self.last_error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "consecutive_failures": self.failures,
            "last_ok_at": iso(self.last_ok_at) if self.last_ok_at else None,
            "last_error": self.last_error,
        }


def snapshot_basis(s: Snapshot) -> dict[str, Any]:
    return jdict(
        {
            "symbol": s.symbol,
            "ts": s.ts,
            "last": s.last,
            "open": s.open,
            "prev_close": s.prev_close,
            "halted": s.halted,
            "source": s.source,
        }
    )


class MarketStatusService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        reference: Any,
        quotes: QuoteCache,
        quote_health: SourceHealth,
        settlement_pending: Callable[[], date | None],
    ) -> None:
        self.db = db
        self.clock = clock
        self.reference = reference
        self.quotes = quotes
        self.quote_health = quote_health
        self.settlement_pending = settlement_pending

    def status(self) -> dict[str, Any]:
        now = self.clock.now()
        today = now.date()
        out: dict[str, Any] = {"now": iso(now), "date": today.isoformat()}
        try:
            cal = self.reference.calendar()
            trading = cal.is_trading_day(today)
            out["is_trading_day"] = trading
            out["session"] = session_of(now, trading).value
            open_day = today if (trading and now.time() < T_0930) else cal.next_trading_day(today)
            close_day = today if (trading and now.time() < T_1500) else cal.next_trading_day(today)
            out["next_open"] = f"{open_day.isoformat()}T09:30:00+08:00"
            out["next_close"] = f"{close_day.isoformat()}T15:00:00+08:00"
            out["calendar_available"] = True
        except CalendarUnavailable as e:
            out.update(
                {
                    "is_trading_day": None,
                    "session": None,
                    "calendar_available": False,
                    "calendar_error": str(e),
                }
            )
        out["quote_source"] = self.quote_health.to_dict()
        out["reference_source"] = {
            "name": self.reference.source.name,
            "consecutive_failures": self.reference.failures,
            "last_error": self.reference.last_error,
        }
        out["last_snapshot_at"] = iso(self.quotes.last_update) if self.quotes.last_update else None
        last = repos.last_done_run(self.db.read())
        out["last_settlement_date"] = last.isoformat() if last else None
        pending = self.settlement_pending()
        out["trading_paused"] = pending is not None  # 结算未完成时暂停交易（方案 4.4）
        out["pending_settlement_date"] = pending.isoformat() if pending else None
        out["unresolved_alerts"] = repos.count_unresolved_alerts(self.db.read())
        return out
