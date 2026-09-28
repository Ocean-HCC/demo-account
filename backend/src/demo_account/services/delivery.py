"""Webhook 投递：至少一次、递增间隔重试、HMAC 签名（方案 6.10；实现 5.5、6.3）。"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import timedelta

import httpx

from ..clock import Clock
from ..config import Settings
from ..store import repos
from ..store.db import Database
from .events import AlertService

log = logging.getLogger(__name__)
RETRY_SECONDS = [10, 30, 120, 600, 1800]


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class DeliveryService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        alerts: AlertService,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.db = db
        self.clock = clock
        self.alerts = alerts
        self.settings = settings
        self.transport = transport

    def run_once(self) -> int:
        now = self.clock.now()
        due = repos.due_events(self.db.read(), now, self.settings.webhook_max_attempts)
        if not due:
            return 0
        delivered = 0
        with httpx.Client(
            timeout=5.0, trust_env=self.settings.http_trust_env, transport=self.transport
        ) as client:
            for e in due:
                a = repos.get_account(self.db.read(), e.account_id)
                if a is None or not a.webhook_url:
                    with self.db.write() as tx:
                        repos.mark_delivered(tx, e.account_id, e.seq, now)
                    continue
                d = e.to_dict()
                body = json.dumps(
                    {
                        "event_id": d["event_id"],
                        "account_id": d["account_id"],
                        "seq": d["seq"],
                        "type": d["type"],
                        "occurred_at": d["occurred_at"],
                        "basis": d["basis"],
                        "data": d["data"],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                headers = {"Content-Type": "application/json", "X-Demo-Account-Event": e.type.value}
                if a.webhook_secret:
                    headers["X-Demo-Account-Signature"] = sign(a.webhook_secret, body)
                try:
                    r = client.post(a.webhook_url, content=body, headers=headers)
                    ok = 200 <= r.status_code < 300
                    err = f"HTTP {r.status_code}"
                except httpx.HTTPError as ex:
                    ok = False
                    err = str(ex)
                with self.db.write() as tx:
                    if ok:
                        repos.mark_delivered(tx, e.account_id, e.seq, now)
                        delivered += 1
                        continue
                    attempts = e.attempts + 1
                    if attempts >= self.settings.webhook_max_attempts:
                        repos.mark_attempt(tx, e.account_id, e.seq, None)
                        self.alerts.raise_alert(
                            "warning",
                            "WEBHOOK_FAILED",
                            f"{e.account_id} 事件 {e.seq} 投递 {attempts} 次失败：{err}",
                            tx=tx,
                        )
                    else:
                        wait = RETRY_SECONDS[min(attempts - 1, len(RETRY_SECONDS) - 1)]
                        repos.mark_attempt(tx, e.account_id, e.seq, now + timedelta(seconds=wait))
        return delivered
