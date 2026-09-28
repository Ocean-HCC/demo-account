"""账户事件序列与告警（方案 5.6、6.10；实现 5.5）。

事件在业务事务内写入，提交后广播给进程内订阅者（SSE）。是否需要 Webhook 投递在写入时决定。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from datetime import date, datetime
from typing import Any

from ..clock import Clock
from ..core.models import NOTIFY_EVENT_TYPES, EventType
from ..store import repos
from ..store.db import Database, Tx
from ..store.records import Account, Event
from ..timeutil import jsonable

log = logging.getLogger(__name__)


class EventBus:
    """进程内广播：线程安全地把消息投到每个订阅者的 asyncio 队列。"""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subs.discard(q)

    def publish(self, message: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                loop.call_soon_threadsafe(_put, q, message)
            except RuntimeError:
                return


def _put(q: asyncio.Queue[dict[str, Any]], message: dict[str, Any]) -> None:
    with contextlib.suppress(asyncio.QueueFull):
        q.put_nowait(message)


class EventService:
    def __init__(self, bus: EventBus, clock: Clock) -> None:
        self.bus = bus
        self.clock = clock

    def emit(
        self,
        tx: Tx,
        account: Account,
        type: EventType,
        *,
        occurred_at: datetime | None = None,
        order_id: str | None = None,
        fill_seq: int | None = None,
        trade_date: date | None = None,
        symbol: str | None = None,
        basis: dict[str, Any] | None = None,
        summary: str = "",
        payload: dict[str, Any] | None = None,
    ) -> Event:
        seq = repos.next_event_seq(tx, account.id)
        event = Event(
            account_id=account.id,
            seq=seq,
            type=type,
            occurred_at=occurred_at or self.clock.now(),
            order_id=order_id,
            fill_seq=fill_seq,
            trade_date=trade_date,
            symbol=symbol,
            basis=jsonable(basis or {}),
            summary=summary,
            payload=jsonable(payload or {}),
            notify=type in NOTIFY_EVENT_TYPES and account.webhook_url is not None,
        )
        repos.insert_event(tx, event)
        message = {"kind": "event", **event.to_dict()}
        tx.after_commit(lambda: self.bus.publish(message))
        return event


class AlertService:
    def __init__(self, db: Database, bus: EventBus, clock: Clock) -> None:
        self.db = db
        self.bus = bus
        self.clock = clock

    def raise_alert(
        self,
        level: str,
        code: str,
        message: str,
        trade_date: date | None = None,
        tx: Tx | None = None,
    ) -> None:
        now = self.clock.now()
        log.warning("alert %s %s: %s", level, code, message)
        payload = jsonable(
            {
                "kind": "alert",
                "level": level,
                "code": code,
                "message": message,
                "trade_date": trade_date,
                "created_at": now,
            }
        )
        if tx is not None:
            repos.insert_alert(tx, level, code, message, trade_date, now)
            tx.after_commit(lambda: self.bus.publish(payload))
            return
        with self.db.write() as t:
            repos.insert_alert(t, level, code, message, trade_date, now)
            t.after_commit(lambda: self.bus.publish(payload))
