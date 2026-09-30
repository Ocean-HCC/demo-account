"""用 TSP 所在机器的真实响应做契约测试（实现 3.2 当日日线；8.1 market）。

样本从 backend/verify-output/samples 复制到 tests/fixtures/tsp_2026-09-28 固定下来（verify-output
每次验证都会被覆盖）。录制于 2026-09-28 18:06，当时 TSP 0.2.4 已收盘定版，但当日日线行仍带
is_live（已落盘的行被实时值覆盖）。样本按长度裁剪过，裁剪处留有 {"_trimmed": N} 标记行，加载时去掉。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from demo_account.clock import FakeClock
from demo_account.core.money import TICK
from demo_account.market.public_source import PublicSource
from demo_account.market.tsp_source import (
    TspClient,
    TspReferenceSource,
    close_final,
    parse_daily_rows,
    parse_latest_row,
)
from support import MON, THU, at

SAMPLES = Path(__file__).resolve().parent / "fixtures" / "tsp_2026-09-28"
PF = "600000.SH"
NOSLEEP = lambda s: None  # noqa: E731


def sample(name: str) -> Any:
    data = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        data["rows"] = [r for r in data["rows"] if "_trimmed" not in r]
    return data


def source(status: dict[str, Any]) -> TspReferenceSource:
    """真实样本作上游：日线与指数原样返回，行情状态可替换。"""
    daily = {PF: sample(f"tsp_daily_{PF}")}
    index = sample("tsp_index_daily")

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/api/intraday/status":
            return httpx.Response(200, json=status)
        if path == "/api/kline/daily":
            return httpx.Response(200, json=daily[req.url.params["symbol"]])
        if path == "/api/index/daily":
            return httpx.Response(200, json=index)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    client = TspClient("http://127.0.0.1:3018", None, False, transport, NOSLEEP)
    public = PublicSource(False, transport=transport, sleep=NOSLEEP)
    return TspReferenceSource(client, public, FakeClock(at(MON, 18, 6)))


def test_status_sample_is_close_final() -> None:
    status = sample("tsp_intraday_status")
    assert close_final(status)
    assert not close_final({**status, "final_sync_done": False})
    assert not close_final({**status, "final_sync_failed": "unconfirmed_snapshot"})
    assert not close_final({**status, "market_phase": "afternoon"})


def test_today_row_stays_live_after_close_final() -> None:
    rows = sample(f"tsp_daily_{PF}")["rows"]
    today = [r for r in rows if r["date"] == "2026-09-28"]
    assert len(today) == 1 and today[0]["is_live"] is True
    assert "raw_close" in today[0]  # 落盘行被实时值覆盖后返回
    assert parse_daily_rows(PF, rows, TICK)[-1].trade_date == THU
    last = parse_daily_rows(PF, rows, TICK, MON)[-1]
    assert (last.trade_date, last.close, last.adj_close) == (MON, Decimal("9.16"), Decimal("9.16"))
    assert last.prev_close == Decimal("9.00")
    assert last.low is not None and last.high is not None and last.low <= last.close <= last.high


def test_reference_source_takes_today_only_after_final_sync() -> None:
    status = sample("tsp_intraday_status")
    ok = source(status)
    assert [(b.trade_date, b.close) for b in ok.daily_bars(PF, MON, MON)] == [
        (MON, Decimal("9.16"))
    ]
    assert ok.index_daily("000300.SH", THU, MON)[-1] == (MON, Decimal("4340.755"))
    for bad in ({"final_sync_done": False}, {"final_sync_failed": "unconfirmed_snapshot"}):
        assert source({**status, **bad}).daily_bars(PF, MON, MON) == []


def test_latest_sample_is_a_valid_snapshot() -> None:
    snap = parse_latest_row(PF, sample(f"tsp_latest_{PF}"), MON, at(MON, 18, 5, 54))
    assert snap is not None
    assert (snap.last, snap.prev_close) == (Decimal("9.16"), Decimal("9.00"))
