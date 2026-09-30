"""按主机路由的假上游：TSP（127.0.0.1）、深交所、东方财富数据中心、新浪。字段取自各自接口的真实结构。"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta
from typing import Any

import httpx

REAL_2026_HOLIDAYS = frozenset({date(2026, 9, 25), *(date(2026, 10, d) for d in (1, 2, 5, 6, 7))})


def month_rows(year: int, month: int, holidays: frozenset[date]) -> list[dict[str, Any]]:
    rows = []
    for day in range(1, calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        is_open = d.weekday() < 5 and d not in holidays
        rows.append(
            {
                "zrxh": (d.weekday() + 2) % 7 or 7,
                "jybz": "1" if is_open else "0",
                "jyrq": d.isoformat(),
            }
        )
    return rows


def epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class FakeUpstream:
    def __init__(
        self,
        year: int = 2026,
        holidays: frozenset[date] = REAL_2026_HOLIDAYS,
        months: range = range(1, 13),
    ) -> None:
        self.months = {f"{year}-{m:02d}": month_rows(year, m, holidays) for m in months}
        self.password: str | None = None
        self.status: dict[str, Any] = {
            "enabled": True,
            "running": True,
            "realtime_allowed": True,
            "is_polling_window": True,
            "market_phase": "morning",
            "last_fetch_ms": None,
            "quote_age_ms": 1000,
        }
        self.latest: dict[str, dict[str, Any] | None] = {}
        self.daily: dict[str, list[dict[str, Any]]] = {}
        self.index: dict[str, list[dict[str, Any]]] = {}
        self.instruments: list[dict[str, Any]] = []
        self.suspend_rows: list[dict[str, Any]] = []
        self.bonus_rows: dict[str, list[dict[str, Any]]] = {}
        self.sina_index: list[dict[str, Any]] = []
        self.calls: list[httpx.Request] = []
        self.fail_tsp = False
        self.live_clock: Any = None  # 设置后行情状态的抓取时间随时钟推进，模拟 TSP 持续轮询

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def paths(self, host: str) -> list[str]:
        return [r.url.path for r in self.calls if r.url.host == host]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        host = request.url.host
        params = request.url.params
        if host == "127.0.0.1":
            return self._tsp(request)
        if host == "www.szse.cn":
            return self._json(
                {"data": self.months.get(params.get("month", ""), []), "nowdate": "2026-09-24"}
            )
        if host == "datacenter-web.eastmoney.com":
            report = params.get("reportName")
            if report == "RPT_CUSTOM_SUSPEND_DATA_INTERFACE":
                rows = self.suspend_rows
            elif report == "RPT_SHAREBONUS_DET":
                code = params.get("filter", "").split('"')[1]
                rows = self.bonus_rows.get(code, [])
            else:
                return httpx.Response(404)
            if not rows:
                return self._json(
                    {
                        "version": "x",
                        "result": None,
                        "success": False,
                        "message": "返回数据为空",
                        "code": 9201,
                    }
                )
            return self._json(
                {
                    "version": "x",
                    "result": {"pages": 1, "count": len(rows), "data": rows},
                    "success": True,
                    "code": 0,
                }
            )
        if host == "quotes.sina.cn":
            return self._json(self.sina_index)
        return httpx.Response(404)

    def _tsp(self, request: httpx.Request) -> httpx.Response:
        if self.fail_tsp:
            return httpx.Response(503)
        path = request.url.path
        params = request.url.params
        if path == "/health":
            return self._json({"status": "ok", "version": "0.3.1", "mode": "api_key"})
        if path == "/api/auth/login":
            body = json.loads(request.content)
            if self.password and body.get("password") == self.password:
                return httpx.Response(
                    200,
                    json={"ok": True},
                    headers={"set-cookie": "tf_session=ok; Path=/; HttpOnly"},
                )
            return httpx.Response(401, json={"detail": "密码错误"})
        if self.password and "tf_session=ok" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json={"detail": "未登录"})
        if path == "/api/intraday/status":
            status = dict(self.status)
            if self.live_clock is not None:
                status["last_fetch_ms"] = epoch_ms(self.live_clock.now()) - 1000
                status["quote_age_ms"] = 1000
            return self._json(status)
        if path == "/api/kline/daily/latest":
            s = params["symbol"]
            row = self.latest.get(s)
            return self._json({"symbol": s, "row": row, "source": "live" if row else "none"})
        if path in ("/api/kline/daily", "/api/index/daily"):
            s = params["symbol"]
            src = self.daily if path == "/api/kline/daily" else self.index
            lo, hi = params.get("start_date", "0000"), params.get("end_date", "9999")
            rows = [r for r in src.get(s, []) if lo <= r["date"] <= hi]
            return self._json(
                {"symbol": s, "name": "", "rows": rows, "source": "enriched" if rows else "none"}
            )
        if path == "/api/kline/instruments/search":
            q = params.get("q", "")
            return self._json({"results": [r for r in self.instruments if r["code"].startswith(q)]})
        return httpx.Response(404)

    @staticmethod
    def _json(obj: Any) -> httpx.Response:
        return httpx.Response(200, json=obj)


PF = "600000.SH"


def trading_days(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in REAL_2026_HOLIDAYS:
            out.append(d)
        d += timedelta(days=1)
    return out


def stock_rows(start: date, end: date, close: float = 9.0) -> list[dict[str, Any]]:
    return [
        {
            "date": d.isoformat(),
            "symbol": PF,
            "open": close - 0.02,
            "high": close + 0.05,
            "low": close - 0.05,
            "close": close,
            "raw_close": close,
            "volume": 500000,
        }
        for d in trading_days(start, end)
    ]


def upstream_for_day() -> FakeUpstream:
    """2026-09-24（周四）盘中的上游。

    包含浦发银行标的、日线到前一交易日、当日实时行、指数、停牌与分红。
    """
    up = FakeUpstream()
    up.instruments = [
        {"symbol": PF, "name": "浦发银行", "code": "600000", "asset_type": "stock"},
    ]
    up.daily[PF] = stock_rows(date(2026, 8, 1), date(2026, 9, 23)) + [
        {
            "date": "2026-09-24",
            "open": 8.99,
            "high": 9.05,
            "low": 8.97,
            "close": 9.02,
            "is_live": True,
        }
    ]
    up.index["000300.SH"] = [
        {"date": d.isoformat(), "close": 4500.0}
        for d in trading_days(date(2026, 9, 1), date(2026, 9, 23))
    ]
    up.suspend_rows = [
        {
            "SECUCODE": "000016.SZ",
            "SUSPEND_START_TIME": "2026-09-04 09:30:00",
            "SUSPEND_END_TIME": None,
            "SUSPEND_EXPIRE": "连续停牌",
            "SUSPEND_REASON": "公告",
        }
    ]
    up.bonus_rows["600000"] = [
        {
            "ASSIGN_PROGRESS": "实施分配",
            "EX_DIVIDEND_DATE": "2026-07-16 00:00:00",
            "EQUITY_RECORD_DATE": "2026-07-15 00:00:00",
            "PRETAX_BONUS_RMB": 4.2,
        }
    ]
    up.latest[PF] = {
        "date": "2026-09-24",
        "open": 8.99,
        "high": 9.05,
        "low": 8.97,
        "close": 9.02,
        "change_pct": 0.002222,
        "is_live": True,
    }
    return up
