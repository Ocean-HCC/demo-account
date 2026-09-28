"""公开接口适配器（实现 3.3）：深交所交易日历、东方财富停复牌与股票分红送转、新浪指数日线兜底。

解析函数独立成纯函数，便于用录制样本测试；接口字段缺失或结构不符时抛 MarketDataError，不猜测含义。
"""

from __future__ import annotations

import calendar as _calendar
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import httpx

from ..core.instruments import SymbolError, parse_symbol
from ..core.money import D
from ..core.rules import BEIJING
from .base import CalendarData, CorporateAction, MarketDataError, Suspension
from .http import HttpSource

SZSE_CALENDAR = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
EM_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
SINA_KLINE = "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
CLOSE_TIME = time(15, 0)
DIVIDEND_LOOKBACK_DAYS = 400


# ---------------------------------------------------------------- 解析


def parse_szse_month(payload: Any) -> list[tuple[date, bool]]:
    """深交所月历：data 为每天一条，jyrq 为日期，jybz 为交易标志（1 交易，0 休市）。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MarketDataError("深交所日历返回结构不符")
    out = []
    for row in payload["data"]:
        try:
            d = date.fromisoformat(str(row["jyrq"]))
            flag = str(row["jybz"])
        except (KeyError, ValueError) as e:
            raise MarketDataError(f"深交所日历字段不符: {row}") from e
        if flag not in ("0", "1"):
            raise MarketDataError(f"深交所日历交易标志未知: {row}")
        out.append((d, flag == "1"))
    return out


def _em_rows(payload: Any, what: str) -> tuple[list[dict[str, Any]], int]:
    if not isinstance(payload, dict) or "success" not in payload:
        raise MarketDataError(f"东方财富{what}返回结构不符")
    if not payload.get("success"):
        # 无数据时 success 为 false、result 为空，视为空列表
        if payload.get("result") in (None, {}) and payload.get("code") in (9201, None):
            return [], 0
        return [], 0
    result = payload.get("result") or {}
    rows = result.get("data") or []
    if not isinstance(rows, list):
        raise MarketDataError(f"东方财富{what}数据不是列表")
    return rows, int(result.get("pages") or 1)


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=BEIJING)
    except ValueError as e:
        raise MarketDataError(f"时间格式不符: {value}") from e


def parse_suspensions(rows: list[dict[str, Any]], d: date) -> list[Suspension]:
    """当日收盘时仍处于停牌的证券：停牌开始不晚于当日 15:00，且结束为空或不早于当日 15:00。

    东方财富按日期查询会带出近几天已结束的停牌记录，必须按起止时间过滤；盘中临时停牌在收盘前
    结束，不算停牌。
    """
    close_at = datetime.combine(d, CLOSE_TIME, tzinfo=BEIJING)
    out: dict[str, Suspension] = {}
    for row in rows:
        secucode = str(row.get("SECUCODE") or "")
        try:
            parse_symbol(secucode)
        except SymbolError:
            continue
        start = _dt(row.get("SUSPEND_START_TIME"))
        end = _dt(row.get("SUSPEND_END_TIME"))
        if start is None or start > close_at:
            continue
        if end is not None and end < close_at:
            continue
        out[secucode] = Suspension(symbol=secucode, reason=str(row.get("SUSPEND_REASON") or ""))
    return list(out.values())


def _per_share(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    return (D(float(value)) if isinstance(value, float) else D(str(value))) / 10


def parse_bonus_rows(rows: list[dict[str, Any]], symbol: str, since: date) -> list[CorporateAction]:
    """股票分红送转：只取实施阶段且有除权除息日的记录；比例字段为每 10 股，换算为每股。"""
    out = []
    for row in rows:
        progress = str(row.get("ASSIGN_PROGRESS") or "")
        if "实施" not in progress:
            continue
        ex = _dt(row.get("EX_DIVIDEND_DATE"))
        if ex is None or ex.date() < since:
            continue
        record = _dt(row.get("EQUITY_RECORD_DATE"))
        pay = _dt(row.get("PAY_CASH_DATE"))
        bonus = _per_share(row.get("BONUS_RATIO"))
        transfer = _per_share(row.get("IT_RATIO"))
        if bonus == 0 and transfer == 0:
            bonus = _per_share(row.get("BONUS_IT_RATIO"))
        cash = _per_share(row.get("PRETAX_BONUS_RMB"))
        if bonus == 0 and transfer == 0 and cash == 0:
            continue
        out.append(
            CorporateAction(
                symbol=symbol,
                ex_date=ex.date(),
                record_date=record.date() if record else None,
                pay_date=pay.date() if pay else None,
                bonus_per_share=bonus,
                transfer_per_share=transfer,
                cash_per_share=cash,
                factor=None,
                source="eastmoney",
            )
        )
    return out


def parse_sina_kline(payload: Any) -> list[tuple[date, Decimal]]:
    if not isinstance(payload, list):
        raise MarketDataError("新浪日线返回结构不符")
    out = []
    for row in payload:
        try:
            out.append((date.fromisoformat(str(row["day"])[:10]), D(str(row["close"]))))
        except (KeyError, ValueError) as e:
            raise MarketDataError(f"新浪日线字段不符: {row}") from e
    return out


# ---------------------------------------------------------------- 数据源


class PublicSource:
    name = "public"

    def __init__(
        self,
        trust_env: bool,
        on_server_time: Callable[[datetime], None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        kw: dict[str, Any] = {"transport": transport, "on_server_time": on_server_time}
        if sleep is not None:
            kw["sleep"] = sleep
        self.szse = HttpSource(
            "szse", trust_env, headers={"Referer": "https://www.szse.cn/aboutus/calendar/"}, **kw
        )
        self.em = HttpSource("eastmoney", trust_env, **kw)
        self.sina = HttpSource(
            "sina", trust_env, headers={"Referer": "https://finance.sina.com.cn"}, **kw
        )
        self.last_error: str | None = None

    def trading_calendar(self, year: int) -> CalendarData:
        """逐月拉取；某月无数据表示交易所尚未公布，覆盖范围截至最后一个有数据的月份。"""
        open_days: list[date] = []
        last_covered: date | None = None
        for month in range(1, 13):
            rows = parse_szse_month(
                self.szse.get_json(SZSE_CALENDAR, params={"month": f"{year}-{month:02d}"})
            )
            if not rows:
                break
            open_days += [d for d, is_open in rows if is_open]
            last_covered = date(year, month, _calendar.monthrange(year, month)[1])
        if last_covered is None:
            raise MarketDataError(f"深交所尚未公布 {year} 年交易日历")
        return CalendarData(start=date(year, 1, 1), end=last_covered, open_days=open_days)

    def suspensions(self, d: date) -> list[Suspension]:
        rows: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.em.get_json(
                EM_DATACENTER,
                params={
                    "reportName": "RPT_CUSTOM_SUSPEND_DATA_INTERFACE",
                    "columns": "ALL",
                    "pageSize": 500,
                    "pageNumber": page,
                    "sortColumns": "SUSPEND_START_DATE",
                    "sortTypes": -1,
                    "source": "WEB",
                    "client": "WEB",
                    "filter": f"(MARKET=\"全部\")(DATETIME='{d.isoformat()}')",
                },
            )
            batch, pages = _em_rows(payload, "停复牌")
            rows += batch
            if page >= pages or not batch:
                break
            page += 1
        return parse_suspensions(rows, d)

    def stock_corporate_actions(self, symbol: str, today: date) -> list[CorporateAction]:
        code, _ = parse_symbol(symbol)
        payload = self.em.get_json(
            EM_DATACENTER,
            params={
                "reportName": "RPT_SHAREBONUS_DET",
                "columns": "ALL",
                "pageSize": 50,
                "pageNumber": 1,
                "sortColumns": "EX_DIVIDEND_DATE",
                "sortTypes": -1,
                "source": "WEB",
                "client": "WEB",
                "filter": f'(SECURITY_CODE="{code}")',
            },
        )
        rows, _ = _em_rows(payload, "分红送转")
        return parse_bonus_rows(rows, symbol, today - timedelta(days=DIVIDEND_LOOKBACK_DAYS))

    def index_daily(self, code: str, start: date, end: date) -> list[tuple[date, Decimal]]:
        num, exchange = parse_symbol(code)
        days = max((end - start).days + 10, 10)
        payload = self.sina.get_json(
            SINA_KLINE,
            params={
                "symbol": f"{exchange.value.lower()}{num}",
                "scale": 240,
                "ma": "no",
                "datalen": min(days, 1000),
            },
        )
        return [(d, c) for d, c in parse_sina_kline(payload) if start <= d <= end]
