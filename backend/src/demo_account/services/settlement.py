"""日终结算、台账核对与启动补跑（方案 4.4 至 4.6；实现 5.4）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..clock import Clock
from ..config import Settings
from ..core.ledger import LedgerError, replay
from ..core.matching import close_fill, limit_hit
from ..core.models import EventType, Fill, FillKind, OrderType, PriceLimits, Side
from ..core.money import ZERO, D, round_cent
from ..core.rules import CalendarUnavailable, SimpleCalendar
from ..market.base import CorporateAction, DailyBar, MarketDataError
from ..market.reference import ReferenceService
from ..store import repos
from ..store.db import Database, Tx
from ..store.records import Account, NavDaily, SettlementRun
from .errors import ServiceError
from .events import AlertService, EventService
from .execution import Execution

log = logging.getLogger(__name__)
NAV_Q = Decimal("0.00000001")


class SettlementDataMissing(Exception):
    """结算所需数据不完整（方案 4.4、8.3）。"""


@dataclass
class _Ctx:
    bars: dict[str, DailyBar]  # 当日日线；停牌标的为最近有效日线
    suspended: set[str]  # 当日无日线且在停牌名单中
    limits: dict[str, PriceLimits | None]
    bench: Decimal
    next_day: date
    cal: SimpleCalendar


class SettlementService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        reference: ReferenceService,
        execution: Execution,
        events: EventService,
        alerts: AlertService,
        settings: Settings,
    ) -> None:
        self.db = db
        self.clock = clock
        self.reference = reference
        self.execution = execution
        self.events = events
        self.alerts = alerts
        self.settings = settings

    # ---------------------------------------------------------------- 结算

    def settle(self, d: date) -> SettlementRun:
        now = self.clock.now()
        try:
            self.reference.ensure_calendar(d)
            cal = self.reference.calendar()
            trading = cal.is_trading_day(d)
        except (CalendarUnavailable, MarketDataError) as e:
            raise ServiceError("CALENDAR_UNAVAILABLE", f"交易日历不可用：{e}", 503) from e
        if not trading:
            raise ServiceError("NOT_TRADING_DAY", f"{d} 不是交易日")
        if d > now.date():
            raise ServiceError("INVALID_REQUEST", "不能结算未来日期")
        existing = repos.get_run(self.db.read(), d)
        if existing is not None and existing.status == "done":
            return existing
        earlier = [x for x in self.pending_days(now) if x < d]
        if earlier:
            raise ServiceError("SETTLEMENT_CATCHUP", f"须先完成 {earlier[0]} 的结算", 409)
        with self.db.write() as tx:
            repos.upsert_run(tx, SettlementRun(d, "running", now, None, None))
        accounts: list[Account] = []
        try:
            ctx = self._prepare(d, cal)
            accounts = [
                a
                for a in repos.list_accounts(self.db.read(), include_archived=False)
                if a.created_at.date() <= d
            ]
            for a in accounts:
                base = self.reference.benchmark_base(a.created_at.date())
                with self.db.write() as tx:
                    self._settle_account(tx, a, d, ctx, base)
        except SettlementDataMissing as e:
            return self._failed(
                d, now, "SETTLEMENT_DATA_MISSING", f"{d} 结算数据不完整：{e}", "warning"
            )
        except (MarketDataError, CalendarUnavailable, LedgerError) as e:
            log.exception("settlement failed")
            return self._failed(d, now, "SETTLEMENT_FAILED", f"{d} 结算失败：{e}", "error")
        done = SettlementRun(d, "done", now, self.clock.now(), None)
        with self.db.write() as tx:
            repos.upsert_run(tx, done)
        for a in accounts:
            self.reconcile(a.id)
        return done

    def _failed(
        self, d: date, started: datetime, code: str, message: str, level: str
    ) -> SettlementRun:
        """记失败并告警；同一交易日同一原因已有未解决告警时不重复写入（重试不刷屏）。"""
        run = SettlementRun(d, "failed", started, self.clock.now(), message)
        with self.db.write() as tx:
            repos.upsert_run(tx, run)
            if not repos.has_unresolved_alert(tx, code, d):
                self.alerts.raise_alert(level, code, message, d, tx=tx)
        return run

    def _prepare(self, d: date, cal: SimpleCalendar) -> _Ctx:
        """事务外取齐结算数据：日线、停牌、涨跌停价、分红送转、基准。"""
        ex = self.db.read()
        symbols = set(repos.all_position_symbols(ex)) | set(repos.pending_symbols(ex))
        for a in repos.list_accounts(ex, include_archived=False):
            for f in repos.list_fills(ex, a.id, limit=1000):
                if f.trade_date == d:
                    symbols.add(f.symbol)
        try:
            self.reference.refresh_suspensions(d)
        except MarketDataError as e:
            raise SettlementDataMissing(f"停复牌名单（{e}）") from e
        missing: list[str] = []
        bars: dict[str, DailyBar] = {}
        suspended: set[str] = set()
        limits: dict[str, PriceLimits | None] = {}
        for s in sorted(symbols):
            bar = self.reference.daily_bar(s, d, refresh=True)
            if bar is not None:
                bars[s] = bar
            elif self.reference.is_suspended(s, d):
                prev = self.reference.close_on_or_before(s, d)
                if prev is None:
                    missing.append(f"{s} 最近收盘价")
                else:
                    bars[s] = prev
                    suspended.add(s)
            else:
                missing.append(f"{s} 收盘价")
            try:
                self.reference.refresh_corporate_actions(s)
            except MarketDataError as e:
                missing.append(f"{s} 分红送转（{e}）")
            inst = self.reference.instrument(s)
            try:
                limits[s] = (
                    self.reference.price_limits(s, d, inst, today=d) if inst is not None else None
                )
            except MarketDataError:
                limits[s] = None
        bench = self.reference.benchmark_close(d)
        if bench is None:
            missing.append("基准指数收盘")
        if missing:
            raise SettlementDataMissing("、".join(missing))
        assert bench is not None
        return _Ctx(bars, suspended, limits, bench, cal.next_trading_day(d), cal)

    def _settle_account(self, tx: Tx, a: Account, d: date, ctx: _Ctx, base: Decimal | None) -> None:
        now = self.clock.now()
        summary: dict[str, list[dict[str, Any]]] = {
            "filled": [],
            "expired": [],
            "rejected": [],
            "deferred": [],
            "corporate_actions": [],
        }

        def note(kind: str, o: Any, **extra: Any) -> None:
            summary[kind].append({"order_id": o.id, "symbol": o.symbol, **extra})

        # 1. 收盘单
        for o in repos.pending_orders(tx, [OrderType.CLOSE], trade_date_upto=d, account_id=a.id):
            if o.symbol in ctx.suspended or o.symbol not in ctx.bars:
                if self.execution.defer_order(
                    tx, a, o, ctx.next_day, now, "停牌顺延", {"trade_date": d}
                ):
                    note("deferred", o, new_trade_date=ctx.next_day, reason="当日 15:00 仍停牌")
                else:
                    note("expired", o, reason="停牌顺延超限")
                continue
            close = ctx.bars[o.symbol].close
            basis: dict[str, Any] = {
                "close": close,
                "trade_date": d,
                "protect_price": o.protect_price,
            }
            if not close_fill(close, o.side, o.protect_price):
                self.execution.expire_order(
                    tx,
                    a,
                    o,
                    "PROTECT_PRICE_NOT_MET",
                    f"收盘价 {close} 未满足保护限价 {o.protect_price}",
                    now,
                    basis,
                )
                note("expired", o, reason="保护限价未满足")
                continue
            lim = ctx.limits.get(o.symbol)
            if lim is not None and limit_hit(close, o.side, lim):
                word = "收盘价触及涨停" if o.side is Side.BUY else "收盘价触及跌停"
                self.execution.reject_order(tx, a, o, "PRICE_LIMIT_HIT", word, now, basis)
                note("rejected", o, reason=word)
                continue
            inst = repos.get_instrument(tx, o.symbol)
            if inst is None:
                continue
            fill = self.execution.fill_order(tx, a, o, inst, close, now, d, basis)
            if fill is not None:
                note("filled", o, price=close)
        # 2. 失效与顺延
        for o in repos.pending_orders(
            tx, [OrderType.LIMIT, OrderType.MARKET], trade_date_upto=d, account_id=a.id
        ):
            self.execution.expire_order(
                tx, a, o, "EXPIRED_AT_CLOSE", "所属交易日收盘未成交", now, {"trade_date": d}
            )
            note("expired", o, reason="收盘未成交")
        for o in repos.pending_orders(tx, [OrderType.OPEN], trade_date_upto=d, account_id=a.id):
            if self.execution.defer_order(
                tx, a, o, ctx.next_day, now, "停牌顺延", {"trade_date": d}
            ):
                note("deferred", o, new_trade_date=ctx.next_day, reason="停牌或未取得开盘价")
            else:
                note("expired", o, reason="停牌顺延超限")
        # 3. 公司行动
        self._corporate_actions(tx, a, d, ctx, now, summary)
        # 4. T+1 解锁
        repos.unlock_positions(tx, a.id, now)
        # 5. 净值定版
        cash = repos.account_cash(tx, a.id, a.initial_cash)
        mv = ZERO
        for p in repos.list_positions(tx, a.id):
            bar = ctx.bars.get(p.symbol) or repos.latest_bar_on_or_before(tx, p.symbol, d)
            price = bar.close if bar is not None else (p.cost_total / p.qty if p.qty else ZERO)
            mv += round_cent(D(p.qty) * price)
        total = cash + mv
        nav = (total / a.initial_cash).quantize(NAV_Q)
        prev = repos.last_nav(tx, a.id, before=d)
        day_pnl = total - (prev.total_assets if prev is not None else a.initial_cash)
        bench_nav = (ctx.bench / base).quantize(NAV_Q) if base else None
        row = NavDaily(a.id, d, cash, mv, total, nav, day_pnl, bench_nav, now)
        repos.upsert_nav(tx, row)
        # 6. 结算完成事件
        self.events.emit(
            tx,
            a,
            EventType.SETTLEMENT_DONE,
            occurred_at=now,
            trade_date=d,
            basis={"benchmark_close": ctx.bench, "benchmark_base": base},
            summary=f"{d} 结算完成，净值 {nav}，当日盈亏 {day_pnl}",
            payload={"nav": row.to_dict(), **summary},
        )

    # ---------------------------------------------------------------- 公司行动

    def _entitled(self, tx: Tx, a: Account, act: CorporateAction, cal: SimpleCalendar) -> int:
        record = act.record_date or cal.prev_trading_day(act.ex_date)
        state = replay(a.initial_cash, repos.all_fills(tx, a.id, up_to=record))
        p = state.positions.get(act.symbol)
        return p.qty if p is not None else 0

    def _corporate_fill(
        self,
        tx: Tx,
        a: Account,
        symbol: str,
        side: Side,
        qty: int,
        cash: Decimal,
        d: date,
        at: datetime,
        note: str,
    ) -> Fill:
        return self.execution.write_fill(
            tx,
            Fill(
                seq=None,
                account_id=a.id,
                order_id=None,
                symbol=symbol,
                kind=FillKind.CORPORATE_ACTION,
                side=side,
                qty=qty,
                price=ZERO,
                gross_amount=ZERO,
                commission=ZERO,
                stamp_tax=ZERO,
                transfer_fee=ZERO,
                cash_delta=cash,
                trade_date=d,
                occurred_at=at,
                note=note,
            ),
            at,
        )

    def _corporate_actions(
        self,
        tx: Tx,
        a: Account,
        d: date,
        ctx: _Ctx,
        now: datetime,
        summary: dict[str, list[dict[str, Any]]],
    ) -> None:
        # 股份变动在除权日：按送转明细
        for act in repos.corporate_actions_on(tx, d):
            ratio = act.bonus_per_share + act.transfer_per_share
            if ratio <= 0:
                continue
            marker = f"bonus:{act.symbol}:{act.ex_date.isoformat()}"
            if repos.corporate_fill_exists(tx, a.id, marker):
                continue
            entitled = self._entitled(tx, a, act, ctx.cal)
            if entitled <= 0:
                continue
            bar = ctx.bars.get(act.symbol)
            close = bar.close if bar is not None else ZERO
            exact = D(entitled) * (1 + ratio)
            new_qty = int(exact)
            delta = new_qty - entitled
            frac_cash = round_cent((exact - new_qty) * close)
            fill = self._corporate_fill(
                tx, a, act.symbol, Side.BUY, delta, frac_cash, d, now, marker
            )
            basis = {
                "entitled_qty": entitled,
                "record_date": act.record_date,
                "ex_date": act.ex_date,
                "bonus_per_share": act.bonus_per_share,
                "transfer_per_share": act.transfer_per_share,
                "close": close,
                "fraction_cash": frac_cash,
                "source": act.source,
            }
            self.events.emit(
                tx,
                a,
                EventType.CORPORATE_ACTION,
                occurred_at=now,
                fill_seq=fill.seq,
                trade_date=d,
                symbol=act.symbol,
                basis=basis,
                summary=f"{act.symbol} 除权：数量 {entitled} → {new_qty}",
                payload={"fill": repos.fill_to_dict(fill)},
            )
            summary["corporate_actions"].append(
                {"symbol": act.symbol, "kind": "bonus", "qty_delta": delta}
            )
        # 现金分红在派息日（派息日为空时按除权日）
        for act in repos.corporate_actions_paying(tx, d):
            if act.cash_per_share <= 0:
                continue
            marker = f"dividend:{act.symbol}:{act.ex_date.isoformat()}"
            if repos.corporate_fill_exists(tx, a.id, marker):
                continue
            entitled = self._entitled(tx, a, act, ctx.cal)
            if entitled <= 0:
                continue
            cash = round_cent(D(entitled) * act.cash_per_share)
            fill = self._corporate_fill(tx, a, act.symbol, Side.BUY, 0, cash, d, now, marker)
            self.events.emit(
                tx,
                a,
                EventType.CORPORATE_ACTION,
                occurred_at=now,
                fill_seq=fill.seq,
                trade_date=d,
                symbol=act.symbol,
                basis={
                    "entitled_qty": entitled,
                    "cash_per_share": act.cash_per_share,
                    "record_date": act.record_date,
                    "ex_date": act.ex_date,
                    "pay_date": act.pay_date,
                    "source": act.source,
                },
                summary=f"{act.symbol} 现金分红入账 {cash} 元（税前）",
                payload={"fill": repos.fill_to_dict(fill)},
            )
            summary["corporate_actions"].append(
                {"symbol": act.symbol, "kind": "dividend", "cash": str(cash)}
            )

    # ---------------------------------------------------------------- 核对与补跑

    def reconcile(self, account_id: str) -> dict[str, Any]:
        """用台账重放核对物化持仓；不一致只告警，不自动修改（方案 4.5）。"""
        ex = self.db.read()
        a = repos.get_account(ex, account_id)
        if a is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_id}", 404)
        fills = repos.all_fills(ex, a.id)
        last_done = repos.last_done_run(ex)
        as_of = None
        if fills and last_done is not None and last_done >= fills[-1].trade_date:
            as_of = last_done + timedelta(days=1)
        state = replay(a.initial_cash, fills, as_of=as_of)
        rows = {p.symbol: p for p in repos.list_positions(ex, a.id)}
        diffs: list[dict[str, Any]] = []
        for sym in sorted(set(state.positions) | set(rows)):
            exp = state.positions.get(sym)
            got = rows.get(sym)
            e = (exp.qty, exp.today_bought_qty, exp.cost_total) if exp else (0, 0, ZERO)
            g = (got.qty, got.today_bought_qty, got.cost_total) if got else (0, 0, ZERO)
            if e != g:
                diffs.append(
                    {
                        "symbol": sym,
                        "replay": [e[0], e[1], str(e[2])],
                        "positions": [g[0], g[1], str(g[2])],
                    }
                )
        if diffs:
            self.alerts.raise_alert(
                "error", "LEDGER_MISMATCH", f"{a.id} 台账重放与持仓不一致：{diffs[:3]}"
            )
        return {"account_id": a.id, "ok": not diffs, "diffs": diffs, "cash": str(state.cash)}

    def _pending_span(self, now: datetime) -> tuple[date, date] | None:
        """上次 done 之后（没有时从最早的账户创建日起）到已过结算时间的最后一天。"""
        ex = self.db.read()
        accounts = repos.list_accounts(ex, include_archived=True)
        if not accounts:
            return None
        last = repos.last_done_run(ex)
        start = last + timedelta(days=1) if last else min(a.created_at.date() for a in accounts)
        today = now.date()
        end = today if now.time() >= self.settings.settle_time else today - timedelta(days=1)
        return (start, end) if start <= end else None

    def pending_days(self, now: datetime | None = None) -> list[date]:
        """已过结算时间而未完成结算的交易日，按日期排序（实现 5.4 待结算日与补跑）。

        只读已缓存的日历；日历不可用时返回空，由下单与撮合各自的日历检查拒绝。
        """
        span = self._pending_span(now or self.clock.now())
        if span is None:
            return []
        try:
            cal = self.reference.calendar()
            days = []
            d = span[0]
            while d <= span[1]:
                if cal.is_trading_day(d):
                    days.append(d)
                d += timedelta(days=1)
            return days
        except CalendarUnavailable:
            return []

    def first_pending_day(self) -> date | None:
        """最早的待结算日；不为空时暂停交易（方案 4.4 结算未完成时暂停交易）。"""
        days = self.pending_days()
        return days[0] if days else None

    def catch_up(self) -> list[date]:
        """按日期顺序结算全部待结算日，某一天失败即停（方案 4.4、4.6；实现 5.4）。"""
        now = self.clock.now()
        span = self._pending_span(now)
        if span is None:
            return []
        self.reference.ensure_calendar(span[0])
        self.reference.ensure_calendar(span[1])
        done: list[date] = []
        for day in self.pending_days(now):
            run = self.settle(day)
            if run.status != "done":
                break
            done.append(day)
        return done
