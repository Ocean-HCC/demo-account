"""SSE 事件流（实现 6.3）：推送全部账户事件与告警，连接时可带 Last-Event-ID 补发。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from ..services.container import Container
from ..store import repos
from .deps import get_container

router = APIRouter(prefix="/api/events", tags=["events"])
PING_SECONDS = 15.0


def format_sse(message: dict[str, Any]) -> str:
    kind = message.get("kind")
    event = message.get("type") if kind == "event" else "alert"
    lines = []
    if kind == "event" and message.get("event_id"):
        lines.append(f"id: {message['event_id']}")
    lines.append(f"event: {event}")
    lines.append("data: " + json.dumps(message, ensure_ascii=False))
    return "\n".join(lines) + "\n\n"


@router.get("/stream")
async def stream(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    c: Container = Depends(get_container),
) -> StreamingResponse:
    queue = c.bus.subscribe()

    async def gen() -> AsyncIterator[str]:
        try:
            if last_event_id and ":" in last_event_id:
                account_id, _, seq = last_event_id.rpartition(":")
                if seq.isdigit():
                    for e in repos.list_events(c.db.read(), account_id, after=int(seq), limit=1000):
                        yield format_sse({"kind": "event", **e.to_dict()})
            yield ": connected\n\n"
            while not await request.is_disconnected():
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=PING_SECONDS)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield format_sse(msg)
        finally:
            c.bus.unsubscribe(queue)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
