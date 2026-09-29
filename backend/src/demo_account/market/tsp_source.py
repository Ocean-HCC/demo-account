"""tick-stock-panel 适配器（实现 3.2）。

行情快照、日线、指数日线与标的信息只读 TSP 的本地接口；交易日历、停复牌与股票分红送转转交
公开接口适配器。TSP 在北京时间当天 24:00 前一直把当日日线标为 is_live（已落盘的也会被实时值覆盖），
只有 TSP 已收盘定版时才把当日行当作日线，其余 is_live 行一律丢弃（实现 3.2 当日日线）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from ..clock import Clock
from ..config import Settings
from ..core.instruments import board_of, guess_asset_type, is_st, parse_symbol
from ..core.models import AssetType, Instrument, PriceLimits, Snapshot
from ..core.money import D, round_to_tick, tick_for
from ..core.rules import BEIJING
from .base import CalendarData, CorporateAction, DailyBar, MarketDataError, Suspension
from .http import HttpSource
from .public_source import PublicSource

TSP_STALE_SECONDS = 600  # TSP 报告正在轮询但最近抓取时间早于 10 分钟，视为行情停滞
FACTOR_TOLERANCE = Decimal("0.001")  # 复权比值变化小于 0.1% 视为取整噪声
NEW_LISTING_MAX_ROWS = 5
_ADJ_Q = Decimal("0.000001")
_INDEX_Q = Decimal("0.001")


def _num(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return D(value) if isinstance(value, float | int) else D(str(value))
    except (InvalidOperation, ValueError) as e:
        raise MarketDataError(f"数值字段不可解析: {value!r}") from e


def _price(value: Any, tick: Decimal) -> Decimal | None:
    v = _num(value)
    if v is None or v <= 0:
        return None
    return round_to_tick(v, tick)


# ---------------------------------------------------------------- 解析


def parse_latest_row(symbol: str, payload: Any, today: date, ts: datetime) -> Snapshot | None:
    """/api/kline/daily/latest：只有当日的实时行才是有效快照。"""
    if not isinstance(payload, dict) or "row" not in payload:
        raise MarketDataError("TSP 最新行情返回结构不符")
    row = payload["row"]
    if not row:
        return None
    if not row.get("is_live") or str(row.get("date"))[:10] != today.isoformat():
        return None
    tick = tick_for(guess_asset_type(symbol))
    last = _price(row.get("close"), tick)
    if last is None:
        return None
    open_ = _price(row.get("open"), tick) or last
    high = _price(row.get("high"), tick) or last
    low = _price(row.get("low"), tick) or last
    chg = _num(row.get("change_pct"))
    prev = round_to_tick(last / (1 + chg), tick) if chg is not None and chg > -1 else last
    return Snapshot(
        symbol=symbol,
        ts=ts,
        last=last,
        open=open_,
        high=max(high, last),
        low=min(low, last),
        prev_close=prev,
        halted=False,
        up_limit=None,
        down_limit=None,
        source="tsp",
    )


def close_final(status: Any) -> bool:
    """TSP 已完成当日收盘定版：取得了 15:00 之后的快照且未报定版失败（实现 3.2 当日日线）。"""
    return (
        isinstance(status, dict)
        and status.get("market_phase") == "close_final"
        and status.get("final_sync_done") is True
        and not status.get("final_sync_failed")
    )


def _is_live_on(row: Any, day: date) -> bool:
    return isinstance(row, dict) and bool(row.get("is_live")) and _row_day(row) == day.isoformat()


def _row_day(row: dict[str, Any]) -> str:
    return str(row.get("date"))[:10]


def parse_daily_rows(
    symbol: str, rows: Any, tick: Decimal, final_live_date: date | None = None
) -> list[DailyBar]:
    """/api/kline/daily：close 为前复权价，raw_close 为未复权价；开盘价按比值还原。

    is_live 行只在日期等于 final_live_date（TSP 已收盘定版的当天）时保留：收盘价取落盘的
    raw_close，缺失时取 close；当日是前复权锚点，复权价视同未复权价。
    """
    if not isinstance(rows, list):
        raise MarketDataError("TSP 日线返回结构不符")
    live_day = final_live_date.isoformat() if final_live_date else None
    parsed: list[tuple[date, dict[str, Any], bool]] = []
    for row in rows:
        live = bool(row.get("is_live"))
        if live and _row_day(row) != live_day:
            continue
        try:
            parsed.append((date.fromisoformat(_row_day(row)), row, live))
        except ValueError as e:
            raise MarketDataError(f"TSP 日线字段不符: {row}") from e
    parsed.sort(key=lambda x: x[0])
    bars: list[DailyBar] = []
    prev_raw: Decimal | None = None
    for d, row, live in parsed:
        if live:
            final = _price(row.get("raw_close"), tick) or _price(row.get("close"), tick)
            if final is None:
                continue
            raw, adj = final, final
        else:
            close = _num(row.get("close"))
            if close is None or close <= 0:
                continue
            raw = _price(row.get("raw_close"), tick) or round_to_tick(close, tick)
            adj = close
        ratio = raw / adj
        open_adj = _num(row.get("open"))
        high = _price(row.get("raw_high"), tick)
        low = _price(row.get("raw_low"), tick)
        if high is None and _num(row.get("high")) is not None:
            high = round_to_tick(_num(row.get("high")) * ratio, tick)  # type: ignore[operator]
        if low is None and _num(row.get("low")) is not None:
            low = round_to_tick(_num(row.get("low")) * ratio, tick)  # type: ignore[operator]
        volume = row.get("volume")
        bars.append(
            DailyBar(
                symbol=symbol,
                trade_date=d,
                open=round_to_tick(open_adj * ratio, tick) if open_adj and open_adj > 0 else None,
                high=high,
                low=low,
                close=raw,
                adj_close=adj.quantize(_ADJ_Q),
                prev_close=prev_raw,
                volume=int(volume) if isinstance(volume, int | float) else None,
                up_limit=None,
                down_limit=None,
            )
        )
        prev_raw = raw
    return bars


def detect_factors(symbol: str, bars: Sequence[DailyBar]) -> list[CorporateAction]:
    """ETF 除权除息：相邻两日 raw/adj 比值之比即当日因子 f（实现 3.4），数量按 f 调整。"""
    out = []
    for prev, cur in zip(bars, bars[1:], strict=False):
        if not prev.adj_close or not cur.adj_close:
            continue
        r_prev = prev.close / prev.adj_close
        r_cur = cur.close / cur.adj_close
        if r_cur <= 0:
            continue
        f = r_prev / r_cur
        if abs(f - 1) >= FACTOR_TOLERANCE:
            out.append(
                CorporateAction(
                    symbol=symbol,
                    ex_date=cur.trade_date,
                    record_date=prev.trade_date,
                    pay_date=None,
                    bonus_per_share=Decimal("0"),
                    transfer_per_share=Decimal("0"),
                    cash_per_share=Decimal("0"),
                    factor=f.quantize(_ADJ_Q),
                    source="tsp-factor",
                )
            )
    return out


# ---------------------------------------------------------------- 客户端


class TspClient:
    """TSP 本地接口客户端。TSP 设了密码时用密码登录取 tf_session cookie，401/403 时重登一次。"""

    def __init__(
        self,
        base_url: str,
        password: str | None,
        trust_env: bool,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.base = base_url.rstrip("/")
        self.password = password
        kw: dict[str, Any] = {"transport": transport, "min_interval": 0.0}
        if sleep is not None:
            kw["sleep"] = sleep
        self.http = HttpSource("tsp", trust_env, **kw)
        self._login_lock = threading.Lock()

    def _login(self) -> None:
        with self._login_lock:
            resp = self.http.request(
                "POST", self.base + "/api/auth/login", json={"password": self.password}, retries=0
            )
            if resp.status_code != 200:
                raise MarketDataError(f"TSP 登录失败：HTTP {resp.status_code}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self.base + path
        resp = self.http.request("GET", url, params=params, retries=1)
        if resp.status_code in (401, 403):
            if not self.password:
                raise MarketDataError("TSP 要求登录，请设置 DEMO_ACCOUNT_TSP_PASSWORD")
            self._login()
            resp = self.http.request("GET", url, params=params, retries=1)
        if resp.status_code >= 400:
            raise MarketDataError(f"TSP 返回 HTTP {resp.status_code}: {path}")
        try:
            return resp.json()
        except ValueError as e:
            raise MarketDataError(f"TSP 返回的不是 JSON: {path}") from e


# ---------------------------------------------------------------- 快照源


class TspQuoteSource:
    name = "tsp"

    def __init__(self, client: TspClient, clock: Clock) -> None:
        self.client = client
        self.clock = clock
        self.last_status: dict[str, Any] | None = None

    def get_snapshots(self, symbols: Sequence[str]) -> dict[str, Snapshot]:
        status = self.client.get_json("/api/intraday/status")
        if not isinstance(status, dict):
            raise MarketDataError("TSP 行情状态返回结构不符")
        self.last_status = status
        if status.get("realtime_allowed") is False or status.get("enabled") is False:
            raise MarketDataError("TSP 未开启实时行情，请在 TSP 配置能提供实时行情的数据源")
        last_fetch = status.get("last_fetch_ms")
        if not last_fetch:
            raise MarketDataError("TSP 尚未取得实时行情（last_fetch_ms 为空）")
        now = self.clock.now()
        ts = datetime.fromtimestamp(float(last_fetch) / 1000, tz=BEIJING)
        age = (now - ts).total_seconds()
        if status.get("is_polling_window") and age > TSP_STALE_SECONDS:
            raise MarketDataError(f"TSP 正在轮询但行情已停滞 {int(age)} 秒")
        out: dict[str, Snapshot] = {}
        for symbol in symbols:
            payload = self.client.get_json("/api/kline/daily/latest", {"symbol": symbol})
            snap = parse_latest_row(symbol, payload, now.date(), ts)
            if snap is not None:
                out[symbol] = snap
        return out

    def health(self) -> dict[str, Any]:
        s = self.last_status or {}
        keys = (
            "mode",
            "realtime_allowed",
            "market_phase",
            "is_polling_window",
            "quote_age_ms",
            "last_fetch_ms",
            "symbol_count",
            "etf_symbol_count",
        )
        return {"name": self.name, "base_url": self.client.base, **{k: s.get(k) for k in keys}}


# ---------------------------------------------------------------- 基础数据源


class TspReferenceSource:
    name = "tsp+public"

    def __init__(self, client: TspClient, public: PublicSource, clock: Clock) -> None:
        self.client = client
        self.public = public
        self.clock = clock

    def trading_calendar(self, year: int) -> CalendarData:
        return self.public.trading_calendar(year)

    def instrument(self, symbol: str) -> Instrument | None:
        code, exchange = parse_symbol(symbol)
        payload = self.client.get_json(
            "/api/kline/instruments/search", {"q": code, "limit": 20, "asset_types": "stock,etf"}
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise MarketDataError("TSP 标的搜索返回结构不符")
        hit = next((r for r in results if r.get("symbol") == symbol), None)
        if hit is None:
            return None
        asset_type = AssetType.ETF if hit.get("asset_type") == "etf" else AssetType.STOCK
        name = str(hit.get("name") or "")
        return Instrument(
            symbol=symbol,
            name=name,
            asset_type=asset_type,
            board=board_of(symbol, asset_type),
            exchange=exchange,
            list_date=self._estimate_list_date(symbol, asset_type),
            is_st=asset_type is AssetType.STOCK and is_st(name),
        )

    def _estimate_list_date(self, symbol: str, asset_type: AssetType) -> date | None:
        """TSP 不提供上市日：近 40 个自然日内日线不超过 5 根时，以首根日线日期作为上市日估计。"""
        today = self.clock.now().date()
        bars = self.daily_bars(symbol, today - timedelta(days=40), today)
        if not bars or len(bars) > NEW_LISTING_MAX_ROWS:
            return None
        return bars[0].trade_date

    def final_live_date(self, rows: Any) -> date | None:
        """响应里有当日 is_live 行时读取 TSP 行情状态：已收盘定版则当日行可作日线，返回今天。"""
        today = self.clock.now().date()
        if not isinstance(rows, list) or not any(_is_live_on(r, today) for r in rows):
            return None
        return today if close_final(self.client.get_json("/api/intraday/status")) else None

    def daily_bars(self, symbol: str, start: date, end: date) -> list[DailyBar]:
        payload = self.client.get_json(
            "/api/kline/daily",
            {"symbol": symbol, "start_date": start.isoformat(), "end_date": end.isoformat()},
        )
        rows = payload.get("rows") if isinstance(payload, dict) else None
        tick = tick_for(guess_asset_type(symbol))
        bars = parse_daily_rows(symbol, rows, tick, self.final_live_date(rows))
        return [b for b in bars if start <= b.trade_date <= end]

    def price_limits(self, symbol: str, d: date) -> PriceLimits | None:
        return None  # 按规则由前收盘价计算，避免 TSP 分时接口触发上游拉取

    def suspensions(self, d: date) -> list[Suspension]:
        return self.public.suspensions(d)

    def corporate_actions(self, symbol: str) -> list[CorporateAction]:
        today = self.clock.now().date()
        if guess_asset_type(symbol) is AssetType.ETF:
            return detect_factors(
                symbol, self.daily_bars(symbol, today - timedelta(days=20), today)
            )
        return self.public.stock_corporate_actions(symbol, today)

    def index_daily(self, code: str, start: date, end: date) -> list[tuple[date, Decimal]]:
        try:
            payload = self.client.get_json(
                "/api/index/daily",
                {"symbol": code, "start_date": start.isoformat(), "end_date": end.isoformat()},
            )
            rows = payload.get("rows") if isinstance(payload, dict) else None
            live_day = self.final_live_date(rows)
            out = []
            for r in rows or []:
                close = _num(r.get("close"))
                if close is None or close <= 0:
                    continue
                if r.get("is_live") and (live_day is None or _row_day(r) != live_day.isoformat()):
                    continue
                out.append((date.fromisoformat(_row_day(r)), close.quantize(_INDEX_Q)))
            if out:
                return out
        except MarketDataError:
            pass
        return self.public.index_daily(code, start, end)

    def health(self) -> dict[str, Any]:
        return {"name": self.name, "base_url": self.client.base}


def build_tsp_sources(
    settings: Settings,
    clock: Clock,
    transport: httpx.BaseTransport | None = None,
    on_server_time: Callable[[datetime], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[TspQuoteSource, TspReferenceSource]:
    client = TspClient(
        settings.tsp_base_url, settings.tsp_password, settings.http_trust_env, transport, sleep
    )
    public = PublicSource(
        settings.http_trust_env, on_server_time=on_server_time, transport=transport, sleep=sleep
    )
    return TspQuoteSource(client, clock), TspReferenceSource(client, public, clock)
