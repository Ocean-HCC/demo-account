"""外部 HTTP 数据源的公共调用策略（实现 3.2、3.3）：串行、最小间隔、退避重试、服务器时间回调。"""

from __future__ import annotations

import email.utils
import logging
import threading
import time
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import httpx

from .base import MarketDataError

log = logging.getLogger(__name__)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
RETRY_WAITS = (1.0, 2.0, 4.0)


class HttpSource:
    """同一来源的请求串行执行，间隔不低于 min_interval 秒；429、5xx、网络错误按 1s、2s、4s 重试。"""

    def __init__(
        self,
        name: str,
        trust_env: bool,
        min_interval: float = 0.5,
        timeout: float = 10.0,
        headers: Mapping[str, str] | None = None,
        on_server_time: Callable[[datetime], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.min_interval = min_interval
        self.on_server_time = on_server_time
        self._sleep = sleep
        self._lock = threading.Lock()
        self._last = 0.0
        self._client = httpx.Client(
            timeout=timeout,
            trust_env=trust_env,
            transport=transport,
            headers={"User-Agent": UA, **(headers or {})},
            follow_redirects=True,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        retries: int = 3,
    ) -> httpx.Response:
        last_error = ""
        for attempt in range(retries + 1):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last)
                if wait > 0:
                    self._sleep(wait)
                try:
                    resp = self._client.request(
                        method, url, params=params, json=json, headers=headers
                    )
                except httpx.HTTPError as e:
                    resp = None
                    last_error = f"{type(e).__name__}: {e}"
                finally:
                    self._last = time.monotonic()
            if resp is not None:
                self._observe_date(resp)
                if resp.status_code != 429 and resp.status_code < 500:
                    return resp
                last_error = f"HTTP {resp.status_code}"
            if attempt < retries:
                self._sleep(RETRY_WAITS[min(attempt, len(RETRY_WAITS) - 1)])
        raise MarketDataError(f"{self.name} 请求失败 {url}: {last_error}")

    def get_json(
        self,
        url: str,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        resp = self.request("GET", url, params=params, headers=headers)
        if resp.status_code >= 400:
            raise MarketDataError(f"{self.name} 返回 HTTP {resp.status_code}: {url}")
        try:
            return resp.json()
        except ValueError as e:
            raise MarketDataError(f"{self.name} 返回的不是 JSON: {url}") from e

    def _observe_date(self, resp: httpx.Response) -> None:
        if self.on_server_time is None:
            return
        value = resp.headers.get("date")
        if not value:
            return
        try:
            server_time = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return
        if server_time.tzinfo is None:
            return
        try:
            self.on_server_time(server_time)
        except Exception:  # 时间回调失败不影响取数
            log.exception("server time callback failed")

    def close(self) -> None:
        self._client.close()
