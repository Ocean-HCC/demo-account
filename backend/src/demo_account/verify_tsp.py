"""TSP 接入验证（实现 8.6 M5）：在 TSP 所在机器上运行，检查接口与数据，并用临时数据库跑通完整流程。

结果写入输出目录：report.md 给人看，report.json 给程序看，samples/ 是原始响应样本（用于契约测试）。
不写入任何密码；只调用 TSP 的查询接口；流程验证用临时数据库，默认跑完删除。
"""

from __future__ import annotations

import json
import platform
import shutil
import sys
import tempfile
import time as _time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .clock import Clock, FakeClock, SystemClock
from .config import Settings
from .core.instruments import guess_asset_type, parse_symbol
from .core.matching import slippage_price
from .core.models import AssetType, OrderStatus, OrderType, Session, Side
from .core.money import D, round_cent, tick_for
from .core.rules import BEIJING, SimpleCalendar, session_of, to_beijing
from .market.base import DailyBar, MarketDataError
from .market.public_source import PublicSource
from .market.tsp_source import (
    TspClient,
    TspQuoteSource,
    TspReferenceSource,
    detect_factors,
    parse_daily_rows,
    parse_latest_row,
)
from .services.container import build_container
from .services.orders import OrderRequest
from .store import repos
from .timeutil import iso, jsonable

DEFAULT_SYMBOLS = ["600000.SH", "000001.SZ", "300750.SZ", "688981.SH", "510300.SH"]
PASS, WARN, FAIL, INFO, SKIP = "PASS", "WARN", "FAIL", "INFO", "SKIP"
LABEL = {PASS: "通过", WARN: "注意", FAIL: "失败", INFO: "信息", SKIP: "跳过"}
FRESH_MS = 180_000  # 与快照有效期一致
LIVE_WAIT_SECONDS = 90
CLOCK_LIMIT_SECONDS = 60
SETTLED_AFTER = time(16, 0)


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _at(d: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BEIJING)


def _trim(value: Any, keep: int = 5) -> Any:
    """样本里的长列表只留头尾，避免报告过大。"""
    if isinstance(value, list) and len(value) > keep * 2 + 2:
        return value[:keep] + [{"_trimmed": len(value) - keep * 2}] + value[-keep:]
    if isinstance(value, dict):
        return {k: _trim(v, keep) for k, v in value.items()}
    return value


class Verifier:
    def __init__(
        self,
        settings: Settings,
        out_dir: Path,
        symbols: list[str] | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Clock | None = None,
        sleep: Callable[[float], None] | None = None,
        wait: Callable[[float], None] | None = None,
        run_e2e: bool = True,
        keep_db: bool = False,
    ) -> None:
        self.settings = replace(settings, market="tsp", scheduler_enabled=False)
        self.out = out_dir
        self.samples = out_dir / "samples"
        self.symbols = symbols or list(DEFAULT_SYMBOLS)
        self.transport = transport
        self.clock = clock or SystemClock()
        self.sleep = sleep
        self.wait = wait or _time.sleep
        self.run_e2e = run_e2e
        self.keep_db = keep_db
        self.checks: list[Check] = []
        self.offsets: dict[str, float] = {}
        self.client = TspClient(
            self.settings.tsp_base_url,
            self.settings.tsp_password,
            self.settings.http_trust_env,
            transport,
            sleep,
        )
        self.public = PublicSource(
            self.settings.http_trust_env,
            on_server_time=self._server_time,
            transport=transport,
            sleep=sleep,
        )
        self.quote = TspQuoteSource(self.client, self.clock)
        self.ref = TspReferenceSource(self.client, self.public, self.clock)
        self.calendar: SimpleCalendar | None = None
        self.status: dict[str, Any] | None = None
        self.bars: dict[str, list[DailyBar]] = {}
        self.tsp_ok = False
        self.realtime_ok = False

    # ------------------------------------------------------------------ 工具

    def add(self, cid: str, title: str, status: str, detail: str, /, **data: Any) -> None:
        self.checks.append(Check(cid, title, status, detail, jsonable(data)))

    def save(self, name: str, payload: Any) -> None:
        self.samples.mkdir(parents=True, exist_ok=True)
        path = self.samples / f"{name}.json"
        path.write_text(
            json.dumps(_trim(jsonable(payload)), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _server_time(self, server_time: datetime) -> None:
        offset = (self.clock.now() - server_time).total_seconds()
        key = "max_abs_offset"
        if abs(offset) >= abs(self.offsets.get(key, 0.0)):
            self.offsets[key] = offset

    def _guard(self, cid: str, title: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:  # 单项检查出错不影响其他检查
            self.add(
                cid,
                title,
                FAIL,
                f"检查过程出错：{type(e).__name__}: {e}",
                traceback=traceback.format_exc(),
            )

    def now(self) -> datetime:
        return to_beijing(self.clock.now())

    def session(self) -> Session:
        now = self.now()
        trading = self.calendar.is_trading_day(now.date()) if self.calendar else now.weekday() < 5
        return session_of(now, trading)

    def last_completed_day(self) -> date:
        """最近一个已收盘且 TSP 日线任务应已跑完的交易日。"""
        now = self.now()
        today = now.date()
        if self.calendar is None:
            d = today - timedelta(days=1)
            while d.weekday() >= 5:
                d -= timedelta(days=1)
            return d
        if self.calendar.is_trading_day(today) and now.time() >= SETTLED_AFTER:
            return today
        return self.calendar.prev_trading_day(today)

    def stocks(self) -> list[str]:
        return [s for s in self.symbols if guess_asset_type(s) is AssetType.STOCK]

    def etfs(self) -> list[str]:
        return [s for s in self.symbols if guess_asset_type(s) is AssetType.ETF]

    # ------------------------------------------------------------------ 主流程

    def run(self) -> list[Check]:
        self.out.mkdir(parents=True, exist_ok=True)
        self.check_env()
        self._load_calendar()
        self._guard("T1", "TSP 服务", self.check_tsp_health)
        if self.tsp_ok:
            self._guard("T2", "TSP 行情状态与鉴权", self.check_tsp_status)
        else:
            self.add("T2", "TSP 行情状态与鉴权", SKIP, "TSP 不可用，跳过")
        if self.tsp_ok:
            self._guard("T3", "实时行情已开启", self.check_realtime)
            self._guard("T4", "行情新鲜度", self.check_freshness)
            self._guard("T5", "最新行情快照", self.check_latest)
            self._guard("T6", "标的信息", self.check_instruments)
            self._guard("T7", "日线与未复权价", self.check_daily)
            self._guard("T8", "沪深 300 指数", self.check_index)
            self._guard("T9", "ETF 复权因子", self.check_factors)
        else:
            for cid, title in (
                ("T3", "实时行情已开启"),
                ("T4", "行情新鲜度"),
                ("T5", "最新行情快照"),
                ("T6", "标的信息"),
                ("T7", "日线与未复权价"),
                ("T8", "沪深 300 指数"),
                ("T9", "ETF 复权因子"),
            ):
                self.add(cid, title, SKIP, "TSP 不可用，跳过")
        self._guard("P1", "深交所交易日历", self.check_calendar)
        self._guard("P2", "东方财富停复牌", self.check_suspensions)
        self._guard("P3", "东方财富分红送转", self.check_dividends)
        self._guard("P4", "新浪指数兜底", self.check_sina)
        self._guard("P5", "本机时钟", self.check_clock)
        if not self.run_e2e:
            self.add("F1", "历史交易日完整流程", SKIP, "按参数跳过流程验证")
            self.add("F2", "盘中即时单", SKIP, "按参数跳过流程验证")
        elif not self.tsp_ok or self.calendar is None:
            self.add("F1", "历史交易日完整流程", SKIP, "TSP 或交易日历不可用，跳过")
            self.add("F2", "盘中即时单", SKIP, "TSP 或交易日历不可用，跳过")
        else:
            self._guard("F1", "历史交易日完整流程", self.check_history_flow)
            self._guard("F2", "盘中即时单", self.check_live_flow)
        self.write_report()
        return self.checks

    # ------------------------------------------------------------------ 环境

    def check_env(self) -> None:
        now = self.now()
        self.add(
            "E1",
            "运行环境",
            INFO,
            f"demo-account {__version__}，Python {platform.python_version()}，"
            f"{platform.system()} {platform.release()}",
            now_beijing=now,
            local_time=datetime.now().astimezone().isoformat(timespec="seconds"),
            tsp_base_url=self.settings.tsp_base_url,
            tsp_password_configured=bool(self.settings.tsp_password),
            http_trust_env=self.settings.http_trust_env,
            symbols=self.symbols,
            python=sys.version.split()[0],
        )

    def _load_calendar(self) -> None:
        today = self.now().date()
        try:
            data = self.public.trading_calendar(today.year)
            days = list(data.open_days)
            end = data.end
            if today.month == 1:
                try:
                    prev = self.public.trading_calendar(today.year - 1)
                    days = list(prev.open_days) + days
                    start = prev.start
                except MarketDataError:
                    start = data.start
            else:
                start = data.start
            self.calendar = SimpleCalendar(days, coverage=(start, end))
            self._calendar_data = {"start": start, "end": end, "open_days": len(days)}
        except MarketDataError as e:
            self.calendar = None
            self._calendar_error = str(e)

    # ------------------------------------------------------------------ TSP

    def check_tsp_health(self) -> None:
        url = self.client.base + "/health"
        try:
            resp = self.client.http.request("GET", url, retries=1)
        except MarketDataError as e:
            self.add(
                "T1",
                "TSP 服务",
                FAIL,
                f"连不上 TSP（{self.client.base}）：{e}。请确认 TSP 已启动，地址与端口正确",
            )
            return
        date_header = resp.headers.get("date")
        try:
            body = resp.json()
        except ValueError:
            body = {"raw": resp.text[:200]}
        self.save("tsp_health", body)
        if resp.status_code != 200:
            self.add("T1", "TSP 服务", FAIL, f"TSP 返回 HTTP {resp.status_code}", body=body)
            return
        self.tsp_ok = True
        self.add(
            "T1",
            "TSP 服务",
            PASS,
            f"版本 {body.get('version', '-')}，模式 {body.get('mode', '-')}",
            body=body,
            date_header=date_header,
        )

    def check_tsp_status(self) -> None:
        try:
            status = self.client.get_json("/api/intraday/status")
        except MarketDataError as e:
            msg = str(e)
            if "DEMO_ACCOUNT_TSP_PASSWORD" in msg:
                hint = "TSP 设了访问密码，请把密码填进 .env 的 DEMO_ACCOUNT_TSP_PASSWORD"
            elif "登录失败" in msg:
                hint = "TSP 登录失败，请核对 .env 的 DEMO_ACCOUNT_TSP_PASSWORD"
            elif "403" in msg:
                hint = "TSP 拒绝了请求：未设密码时它只放行本机和内网地址"
            else:
                hint = msg
            self.tsp_ok = False
            self.add("T2", "TSP 行情状态与鉴权", FAIL, hint)
            return
        if not isinstance(status, dict):
            self.tsp_ok = False
            self.add("T2", "TSP 行情状态与鉴权", FAIL, "行情状态返回结构不符", body=status)
            return
        self.status = status
        self.save("tsp_intraday_status", status)
        expected = (
            "realtime_allowed",
            "last_fetch_ms",
            "quote_age_ms",
            "is_polling_window",
            "market_phase",
        )
        missing = [k for k in expected if k not in status]
        login = "已登录" if self.settings.tsp_password else "未设密码，按本机访问"
        if missing:
            self.add(
                "T2",
                "TSP 行情状态与鉴权",
                WARN,
                f"{login}；状态缺少字段 {missing}，TSP 版本可能不同",
                status=status,
            )
        else:
            self.add(
                "T2",
                "TSP 行情状态与鉴权",
                PASS,
                f"{login}；模式 {status.get('mode')}，市场阶段 {status.get('market_phase')}",
                status=status,
            )

    def check_realtime(self) -> None:
        s = self.status or {}
        if s.get("enabled") is False:
            self.add("T3", "实时行情已开启", FAIL, "TSP 的行情服务未启用")
        elif s.get("realtime_allowed") is False:
            self.add(
                "T3",
                "实时行情已开启",
                FAIL,
                f"TSP 实时行情未开启（mode={s.get('mode')}）。"
                "请给 TSP 配置 TickFlow 付费 key，或启用 fuyao、stock-sdk 插件",
            )
        else:
            self.realtime_ok = True
            self.add(
                "T3",
                "实时行情已开启",
                PASS,
                f"模式 {s.get('mode')}，股票 {s.get('symbol_count')} 只，"
                f"ETF {s.get('etf_symbol_count')} 只，"
                f"轮询间隔 {s.get('interval_s')} 秒",
            )

    def check_freshness(self) -> None:
        s = self.status or {}
        session = self.session()
        age = s.get("quote_age_ms")
        last = s.get("last_fetch_ms")
        last_at = datetime.fromtimestamp(float(last) / 1000, tz=BEIJING) if last else None
        if session is not Session.CONTINUOUS:
            self.add(
                "T4",
                "行情新鲜度",
                INFO,
                f"当前为 {session.value}，不评估新鲜度；"
                f"最近抓取 {iso(last_at) if last_at else '无'}。交易时段内重跑可验证",
                quote_age_ms=age,
            )
            return
        if age is None:
            self.add("T4", "行情新鲜度", FAIL, "交易时段内 TSP 尚未取得行情（quote_age_ms 为空）")
        elif float(age) > FRESH_MS:
            self.add(
                "T4", "行情新鲜度", FAIL, f"交易时段内 TSP 行情已 {int(float(age) / 1000)} 秒未更新"
            )
        else:
            self.add(
                "T4", "行情新鲜度", PASS, f"行情 {float(age) / 1000:.1f} 秒前更新", quote_age_ms=age
            )

    def check_latest(self) -> None:
        now = self.now()
        s = self.status or {}
        last = s.get("last_fetch_ms")
        ts = datetime.fromtimestamp(float(last) / 1000, tz=BEIJING) if last else now
        rows: list[dict[str, Any]] = []
        for sym in self.symbols:
            payload = self.client.get_json("/api/kline/daily/latest", {"symbol": sym})
            self.save(f"tsp_latest_{sym}", payload)
            snap = parse_latest_row(sym, payload, now.date(), ts)
            row = payload.get("row") if isinstance(payload, dict) else None
            rows.append(
                {
                    "symbol": sym,
                    "row": bool(row),
                    "is_live": bool(row and row.get("is_live")),
                    "date": row.get("date") if row else None,
                    "last": str(snap.last) if snap else None,
                    "valid_snapshot": snap is not None,
                }
            )
        in_session = self.session() is Session.CONTINUOUS
        bad_stocks = [
            r["symbol"] for r in rows if not r["valid_snapshot"] and r["symbol"] in self.stocks()
        ]
        bad_etfs = [
            r["symbol"] for r in rows if not r["valid_snapshot"] and r["symbol"] in self.etfs()
        ]
        if not in_session:
            ok = sum(1 for r in rows if r["valid_snapshot"])
            self.add(
                "T5",
                "最新行情快照",
                INFO,
                f"非交易时段，{ok}/{len(rows)} 只有当日行情行",
                rows=rows,
            )
        elif bad_stocks:
            self.add(
                "T5",
                "最新行情快照",
                FAIL,
                f"交易时段内这些股票没有有效快照：{bad_stocks}",
                rows=rows,
            )
        elif bad_etfs:
            self.add(
                "T5",
                "最新行情快照",
                WARN,
                f"ETF 没有实时快照：{bad_etfs}。要交易 ETF 请在 TSP 开启 ETF 实时拉取",
                rows=rows,
            )
        else:
            self.add("T5", "最新行情快照", PASS, f"{len(rows)} 只全部取到当日实时快照", rows=rows)

    def check_instruments(self) -> None:
        found: list[dict[str, Any]] = []
        missing: list[str] = []
        mismatch: list[str] = []
        for sym in self.symbols:
            code, _ = parse_symbol(sym)
            raw = self.client.get_json(
                "/api/kline/instruments/search",
                {"q": code, "limit": 20, "asset_types": "stock,etf"},
            )
            self.save(f"tsp_search_{code}", raw)
            inst = self.ref.instrument(sym)
            if inst is None:
                missing.append(sym)
                continue
            if inst.asset_type is not guess_asset_type(sym):
                mismatch.append(sym)
            found.append(
                {
                    "symbol": sym,
                    "name": inst.name,
                    "asset_type": inst.asset_type,
                    "board": inst.board,
                    "is_st": inst.is_st,
                    "list_date_estimate": inst.list_date,
                }
            )
        if missing:
            self.add(
                "T6",
                "标的信息",
                FAIL,
                f"TSP 标的列表里没有 {missing}，请在 TSP 同步股票与 ETF 列表",
                found=found,
            )
        elif mismatch:
            self.add("T6", "标的信息", WARN, f"资产类型与代码规律不符：{mismatch}", found=found)
        else:
            self.add(
                "T6",
                "标的信息",
                PASS,
                "、".join(f"{f['symbol']} {f['name']}" for f in found),
                found=found,
            )

    def check_daily(self) -> None:
        now = self.now()
        expected = self.last_completed_day()
        start = now.date() - timedelta(days=45)
        results: list[dict[str, Any]] = []
        worst = PASS
        notes: list[str] = []
        for sym in self.symbols:
            payload = self.client.get_json(
                "/api/kline/daily",
                {
                    "symbol": sym,
                    "start_date": start.isoformat(),
                    "end_date": now.date().isoformat(),
                },
            )
            self.save(f"tsp_daily_{sym}", payload)
            rows = payload.get("rows") if isinstance(payload, dict) else None
            rows = rows if isinstance(rows, list) else []
            final_rows = [r for r in rows if not r.get("is_live")]
            has_raw = bool(final_rows) and all("raw_close" in r for r in final_rows)
            bars = parse_daily_rows(sym, rows, tick_for(guess_asset_type(sym)))
            self.bars[sym] = bars
            last = bars[-1].trade_date if bars else None
            results.append(
                {
                    "symbol": sym,
                    "rows": len(rows),
                    "live_rows": len(rows) - len(final_rows),
                    "has_raw_close": has_raw,
                    "last_date": last,
                    "last_close": bars[-1].close if bars else None,
                    "source": payload.get("source"),
                }
            )
            if not bars:
                worst = FAIL
                notes.append(f"{sym} 没有日线")
                continue
            if last is not None and last < expected:
                worst = FAIL if worst == FAIL else WARN
                notes.append(f"{sym} 日线只到 {last}，应有 {expected}")
            if not has_raw and guess_asset_type(sym) is AssetType.ETF:
                worst = FAIL if worst == FAIL else WARN
                notes.append(f"{sym} 缺少未复权价 raw_close，无法识别 ETF 除权除息")
        detail = "；".join(notes) if notes else f"全部标的日线齐全，最新到 {expected}，带未复权价"
        self.add(
            "T7", "日线与未复权价", worst, detail, expected_last_date=expected, results=results
        )

    def check_index(self) -> None:
        today = self.now().date()
        payload = self.client.get_json(
            "/api/index/daily",
            {
                "symbol": self.settings.benchmark_code,
                "start_date": (today - timedelta(days=30)).isoformat(),
                "end_date": today.isoformat(),
            },
        )
        self.save("tsp_index_daily", payload)
        rows = (
            [r for r in (payload.get("rows") or []) if not r.get("is_live")]
            if isinstance(payload, dict)
            else []
        )
        if rows:
            last = rows[-1]
            self.add(
                "T8",
                "沪深 300 指数",
                PASS,
                f"TSP 提供，最新 {last.get('date')} 收盘 {last.get('close')}",
                rows=len(rows),
            )
        else:
            self.add(
                "T8",
                "沪深 300 指数",
                WARN,
                "TSP 没有沪深 300 日线，结算会改用新浪（见 P4）。可在 TSP 同步指数列表",
            )

    def check_factors(self) -> None:
        etfs = self.etfs()
        if not etfs:
            self.add("T9", "ETF 复权因子", SKIP, "验证标的里没有 ETF")
            return
        found: list[dict[str, Any]] = []
        for sym in etfs:
            for a in detect_factors(sym, self.bars.get(sym, [])):
                found.append({"symbol": sym, "ex_date": a.ex_date, "factor": a.factor})
        if found:
            self.add(
                "T9", "ETF 复权因子", INFO, f"近 45 天识别到除权除息 {len(found)} 次", factors=found
            )
        else:
            self.add(
                "T9", "ETF 复权因子", INFO, "近 45 天未识别到 ETF 除权除息（没有发生时属正常）"
            )

    # ------------------------------------------------------------------ 公开接口

    def check_calendar(self) -> None:
        if self.calendar is None:
            self.add(
                "P1",
                "深交所交易日历",
                FAIL,
                f"取不到交易日历：{getattr(self, '_calendar_error', '')}",
            )
            return
        today = self.now().date()
        info = getattr(self, "_calendar_data", {})
        self.save(
            "szse_calendar_summary",
            {**info, "today": today, "today_open": self.calendar.is_trading_day(today)},
        )
        self.add(
            "P1",
            "深交所交易日历",
            PASS,
            f"覆盖至 {info.get('end')}；"
            f"今天{'是' if self.calendar.is_trading_day(today) else '不是'}交易日，"
            f"上一个交易日 {self.calendar.prev_trading_day(today)}",
        )

    def check_suspensions(self) -> None:
        today = self.now().date()
        rows = self.public.suspensions(today)
        self.save("eastmoney_suspensions", [{"symbol": s.symbol, "reason": s.reason} for s in rows])
        sample = "、".join(s.symbol for s in rows[:5])
        self.add(
            "P2",
            "东方财富停复牌",
            PASS,
            f"今天收盘时停牌 {len(rows)} 只" + (f"，如 {sample}" if rows else ""),
        )

    def check_dividends(self) -> None:
        today = self.now().date()
        sym = self.stocks()[0] if self.stocks() else "600000.SH"
        acts = self.public.stock_corporate_actions(sym, today)
        self.save("eastmoney_dividends", [jsonable(a.__dict__) for a in acts])
        desc = "；".join(
            f"{a.ex_date} 每股派 {a.cash_per_share} "
            f"送 {a.bonus_per_share} 转 {a.transfer_per_share}"
            for a in acts[:3]
        )
        self.add(
            "P3",
            "东方财富分红送转",
            PASS,
            f"{sym} 近 400 天 {len(acts)} 次实施记录" + (f"：{desc}" if acts else ""),
        )

    def check_sina(self) -> None:
        today = self.now().date()
        rows = self.public.index_daily(
            self.settings.benchmark_code, today - timedelta(days=20), today
        )
        self.save("sina_index", rows)
        if rows:
            self.add("P4", "新浪指数兜底", PASS, f"最新 {rows[-1][0]} 收盘 {rows[-1][1]}")
        else:
            self.add("P4", "新浪指数兜底", WARN, "新浪没有返回指数数据")

    def check_clock(self) -> None:
        if "max_abs_offset" not in self.offsets:
            self.add("P5", "本机时钟", INFO, "没有取到公开接口的服务器时间，无法比对")
            return
        off = self.offsets["max_abs_offset"]
        if abs(off) > CLOCK_LIMIT_SECONDS:
            self.add(
                "P5",
                "本机时钟",
                FAIL,
                f"本机时间与公开接口服务器相差 {off:.0f} 秒，"
                "demo-account 会暂停下单。请开启系统自动对时",
            )
        else:
            self.add("P5", "本机时钟", PASS, f"与公开接口服务器相差 {off:.1f} 秒")

    # ------------------------------------------------------------------ 完整流程

    def _temp_container(self, clock: Clock) -> tuple[Any, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="demo-account-verify-"))
        settings = replace(self.settings, db_path=tmp / "verify.sqlite3")
        c = build_container(
            settings, clock=clock, market_transport=self.transport, sleep=self.sleep
        )
        return c, tmp

    def _cleanup(self, c: Any, tmp: Path) -> str | None:
        c.db.close()
        if self.keep_db:
            return str(tmp / "verify.sqlite3")
        shutil.rmtree(tmp, ignore_errors=True)
        return None

    def check_history_flow(self) -> None:
        assert self.calendar is not None
        d = self.calendar.prev_trading_day(self.now().date())
        stock = self.stocks()[0] if self.stocks() else "600000.SH"
        etf = self.etfs()[0] if self.etfs() else None
        fake = FakeClock(_at(d, 10, 0))
        c, tmp = self._temp_container(fake)
        c.clock_monitor.threshold = (
            10**9
        )  # 假时钟与真实服务器时间必然不一致，流程验证中不判定时钟偏差
        steps: list[str] = []
        problems: list[str] = []
        try:
            c.reference.ensure_calendar(d)
            a = c.accounts.create("TSP 验证账户", note="临时库，验证完即删")
            o1 = c.orders.submit(
                a.id, OrderRequest(stock, Side.BUY, OrderType.CLOSE, qty=100, note="验证收盘单")
            )
            o2 = None
            if etf:
                o2 = c.orders.submit(
                    a.id,
                    OrderRequest(etf, Side.BUY, OrderType.CLOSE, qty=1000, note="验证 ETF 收盘单"),
                )
            rej = c.orders.submit(a.id, OrderRequest(stock, Side.SELL, OrderType.MARKET, qty=100))
            steps.append(
                f"{d} 10:00 开户并提交收盘单：{stock} {o1.status.value}"
                + (f"，{etf} {o2.status.value}" if o2 else "")
            )
            if o1.status is not OrderStatus.PENDING:
                problems.append(f"收盘单未进入等待：{o1.reason_code} {o1.reason}")
            if rej.reason_code != "INSUFFICIENT_SELLABLE":
                problems.append(
                    "无持仓卖出应被拒绝为 INSUFFICIENT_SELLABLE，"
                    f"实际 {rej.status.value} {rej.reason_code}"
                )
            else:
                steps.append("无持仓卖出按规则被拒绝并留痕")
            fake.set(_at(d, 16, 0))
            run = c.settlement.settle(d)
            if run.status != "done":
                problems.append(f"{d} 结算未完成：{run.error}")
                raise _Stop
            steps.append(f"{d} 16:00 结算完成")
            ex = c.db.read()
            for order in (o for o in (o1, o2) if o is not None):
                got = repos.get_order(ex, a.id, order.id)
                assert got is not None
                bar = next((b for b in self.bars.get(order.symbol, []) if b.trade_date == d), None)
                fills = [f for f in repos.all_fills(ex, a.id) if f.order_id == order.id]
                if got.status is OrderStatus.FILLED and fills:
                    price = fills[0].price
                    if bar is not None and price != bar.close:
                        problems.append(
                            f"{order.symbol} 收盘单成交价 {price} "
                            f"与 TSP 当日收盘 {bar.close} 不一致"
                        )
                    else:
                        steps.append(f"{order.symbol} 收盘单按当日收盘价 {price} 成交")
                elif got.reason_code in ("PRICE_LIMIT_HIT",):
                    steps.append(f"{order.symbol} 当日收盘触及涨跌停，按规则拒绝")
                elif got.status is OrderStatus.PENDING and got.defer_count:
                    steps.append(f"{order.symbol} 当日停牌，按规则顺延")
                else:
                    problems.append(
                        f"{order.symbol} 收盘单结果异常："
                        f"{got.status.value} {got.reason_code} {got.reason}"
                    )
            navs = repos.list_nav(ex, a.id)
            if not navs:
                problems.append("没有生成定版净值")
            else:
                nav = navs[-1]
                cash = repos.account_cash(ex, a.id, a.initial_cash)
                mv = sum(
                    (
                        round_cent(
                            D(p.qty)
                            * (
                                next(
                                    (
                                        b.close
                                        for b in self.bars.get(p.symbol, [])
                                        if b.trade_date == d
                                    ),
                                    None,
                                )
                                or 0
                            )
                        )
                        for p in repos.list_positions(ex, a.id)
                    ),
                    D(0),
                )
                if nav.total_assets != cash + mv:
                    problems.append(
                        f"定版总资产 {nav.total_assets} 与现金加市值 {cash + mv} 不一致"
                    )
                else:
                    steps.append(f"净值定版 {nav.nav}，总资产 {nav.total_assets}")
                if nav.benchmark_nav is None:
                    problems.append("定版净值缺少基准净值")
                else:
                    steps.append(f"基准净值 {nav.benchmark_nav}")
            rec = c.settlement.reconcile(a.id)
            if rec["ok"]:
                steps.append("台账重放核对一致")
            else:
                problems.append(f"台账核对不一致：{rec['diffs']}")
            types = [e.type.value for e in repos.list_events(ex, a.id, limit=1000)]
            for need in ("account_created", "order_submitted", "order_rejected", "settlement_done"):
                if need not in types:
                    problems.append(f"事件序列缺少 {need}")
            steps.append(f"事件 {len(types)} 条")
            self.save(
                "flow_history",
                {
                    "trade_date": d,
                    "steps": steps,
                    "problems": problems,
                    "event_types": types,
                    "fills": [repos.fill_to_dict(f) for f in repos.all_fills(ex, a.id)],
                },
            )
        except _Stop:
            pass
        finally:
            kept = self._cleanup(c, tmp)
        status = FAIL if problems else PASS
        detail = "；".join(problems) if problems else f"用 {d} 的真实数据跑通：" + "；".join(steps)
        self.add(
            "F1",
            "历史交易日完整流程",
            status,
            detail,
            trade_date=d,
            steps=steps,
            problems=problems,
            kept_db=kept,
        )

    def check_live_flow(self) -> None:
        if self.session() is not Session.CONTINUOUS:
            self.add(
                "F2",
                "盘中即时单",
                SKIP,
                "当前不在连续竞价时段，请在 9:30 至 11:30 或 13:00 至 14:57 重跑",
            )
            return
        if not self.realtime_ok:
            self.add("F2", "盘中即时单", SKIP, "TSP 实时行情未开启，跳过")
            return
        stock = self.stocks()[0] if self.stocks() else "600000.SH"
        c, tmp = self._temp_container(self.clock)
        steps: list[str] = []
        problems: list[str] = []
        try:
            today = self.now().date()
            c.reference.ensure_calendar(today)
            a = c.accounts.create("TSP 盘中验证", note="临时库，验证完即删")
            lim = c.reference.price_limits(stock, today, today=today)
            lo = c.orders.submit(
                a.id, OrderRequest(stock, Side.BUY, OrderType.LIMIT, qty=100, limit_price=lim.down)
            )
            mo = c.orders.submit(
                a.id, OrderRequest(stock, Side.BUY, OrderType.MARKET, qty=100, note="验证即时单")
            )
            steps.append(f"提交限价单 {lo.status.value}、即时单 {mo.status.value}")
            if mo.status is not OrderStatus.PENDING:
                problems.append(f"即时单未进入等待：{mo.reason_code} {mo.reason}")
                raise _Stop
            waited = 0.0
            got = mo
            while waited <= LIVE_WAIT_SECONDS:
                c.engine.run_intraday_cycle()
                got = repos.get_order(c.db.read(), a.id, mo.id) or mo
                if got.status is not OrderStatus.PENDING:
                    break
                self.wait(5)
                waited += 5
            if got.status is not OrderStatus.FILLED:
                problems.append(
                    f"即时单等待 {int(waited)} 秒后状态为 {got.status.value} "
                    f"{got.reason_code or ''} {got.reason or ''}"
                )
                raise _Stop
            ex = c.db.read()
            fill = next(f for f in repos.all_fills(ex, a.id) if f.order_id == mo.id)
            ev = next(
                e
                for e in repos.list_events(ex, a.id, limit=1000)
                if e.type.value == "order_filled" and e.order_id == mo.id
            )
            last = D(str(ev.basis["snapshot"]["last"]))
            expect = slippage_price(
                last, Side.BUY, a.fee_params.slippage_rate, tick_for(guess_asset_type(stock))
            )
            limits = (ev.basis.get("up_limit"), ev.basis.get("down_limit"))
            if fill.price != expect and str(limits[0]) != str(fill.price):
                problems.append(f"成交价 {fill.price} 与快照 {last} 加滑点的 {expect} 不一致")
            else:
                steps.append(f"即时单按快照 {last} 加滑点成交于 {fill.price}")
            sell = c.orders.submit(a.id, OrderRequest(stock, Side.SELL, OrderType.MARKET, qty=100))
            if sell.reason_code == "INSUFFICIENT_SELLABLE":
                steps.append("当日买入不可卖出，T+1 生效")
            else:
                problems.append(f"当日卖出应被拒绝，实际 {sell.status.value} {sell.reason_code}")
            if lo.status is OrderStatus.PENDING:
                c.orders.cancel(a.id, lo.id)
                steps.append("限价单撤销成功")
            rec = c.settlement.reconcile(a.id)
            if not rec["ok"]:
                problems.append(f"台账核对不一致：{rec['diffs']}")
            self.save(
                "flow_live",
                {
                    "steps": steps,
                    "problems": problems,
                    "fill": repos.fill_to_dict(fill),
                    "basis": ev.basis,
                },
            )
        except _Stop:
            pass
        finally:
            kept = self._cleanup(c, tmp)
        status = FAIL if problems else PASS
        detail = "；".join(problems) if problems else "；".join(steps)
        self.add("F2", "盘中即时单", status, detail, steps=steps, problems=problems, kept_db=kept)

    # ------------------------------------------------------------------ 报告

    def summary(self) -> dict[str, int]:
        out = {k: 0 for k in LABEL}
        for c in self.checks:
            out[c.status] += 1
        return out

    def write_report(self) -> None:
        summary = self.summary()
        data = {
            "generated_at": iso(self.now()),
            "demo_account_version": __version__,
            "summary": summary,
            "checks": [c.__dict__ for c in self.checks],
        }
        (self.out / "report.json").write_text(
            json.dumps(jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [
            "# demo-account TSP 接入验证报告",
            "",
            f"生成时间：{iso(self.now())}，demo-account {__version__}",
            "",
            "结果汇总：" + "，".join(f"{LABEL[k]} {v}" for k, v in summary.items() if v),
            "",
            "| 编号 | 检查项 | 结果 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for c in self.checks:
            detail = c.detail.replace("|", "／").replace("\n", " ")
            lines.append(f"| {c.id} | {c.title} | {LABEL[c.status]} | {detail} |")
        lines += ["", "详细数据见 report.json，原始响应样本在 samples/ 目录。", ""]
        (self.out / "report.md").write_text("\n".join(lines), encoding="utf-8")


class _Stop(Exception):
    """流程验证中遇到无法继续的问题。"""
