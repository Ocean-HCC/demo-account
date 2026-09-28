"""账户概览、持仓估值、净值序列与统计（方案 3.4、6.8、6.9）。"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from ..clock import Clock
from ..core.money import ZERO, D, round_cent
from ..core.rules import CalendarUnavailable
from ..core.stats import compute_stats, fifo_rounds
from ..market.base import MarketDataError
from ..market.reference import ReferenceService
from ..store import repos
from ..store.db import Database
from ..store.records import Account
from ..timeutil import jdict, jsonable
from .market_status import QuoteCache

NAV_Q = Decimal("0.00000001")


class PortfolioService:
    def __init__(
        self, db: Database, clock: Clock, reference: ReferenceService, quotes: QuoteCache
    ) -> None:
        self.db = db
        self.clock = clock
        self.reference = reference
        self.quotes = quotes

    def mark_price(self, symbol: str, today: date) -> tuple[Decimal | None, str]:
        """估值价（方案 6.8）：当日已结算则用定版收盘价；否则当日有效快照 → 上一交易日及以前的
        最近收盘价 → 前收盘价。不读当日尚未定版的日线，避免 Mock 源提前生成的当日收盘价泄露。"""
        ex = self.db.read()
        run = repos.get_run(ex, today)
        if run is not None and run.status == "done":
            bar_today = repos.get_bar(ex, symbol, today)
            if bar_today is not None:
                return bar_today.close, "close"
        snap = self.quotes.get(symbol)
        if snap is not None and snap.ts.date() == today and not snap.halted:
            return snap.last, "snapshot"
        bar = repos.latest_bar_on_or_before(ex, symbol, today - timedelta(days=1))
        if bar is not None:
            return bar.close, "close"
        try:
            pc = self.reference.reference_prev_close(symbol, today, today)
        except (MarketDataError, CalendarUnavailable):
            pc = None
        return pc, "prev_close"

    def _valuation(self, a: Account) -> tuple[Decimal, Decimal, list[dict[str, Any]]]:
        ex = self.db.read()
        today = self.clock.now().date()
        cash = repos.account_cash(ex, a.id, a.initial_cash)
        rows: list[dict[str, Any]] = []
        mv = ZERO
        for p in repos.list_positions(ex, a.id):
            price, source = self.mark_price(p.symbol, today)
            value = round_cent(D(p.qty) * price) if price is not None else ZERO
            mv += value
            avg = (p.cost_total / p.qty).quantize(Decimal("0.0001")) if p.qty else None
            frozen = repos.frozen_sell_qty(ex, a.id, p.symbol)
            inst = repos.get_instrument(ex, p.symbol)
            rows.append(
                {
                    "symbol": p.symbol,
                    "name": inst.name if inst else None,
                    "qty": p.qty,
                    "sellable_qty": max(p.sellable_qty - frozen, 0),
                    "today_bought_qty": p.today_bought_qty,
                    "frozen_qty": frozen,
                    "cost_total": p.cost_total,
                    "avg_cost": avg,
                    "last_price": price,
                    "price_source": source,
                    "market_value": value,
                    "unrealized_pnl": value - p.cost_total,
                }
            )
        return cash, mv, rows

    def overview(self, a: Account) -> dict[str, Any]:
        ex = self.db.read()
        today = self.clock.now().date()
        cash, mv, rows = self._valuation(a)
        frozen = repos.frozen_cash_total(ex, a.id)
        total = cash + mv
        prev = repos.last_nav(ex, a.id, before=today)
        base = prev.total_assets if prev is not None else a.initial_cash
        return jdict(
            {
                "account": a.to_dict(),
                "cash": cash,
                "frozen_cash": frozen,
                "available_cash": cash - frozen,
                "market_value": mv,
                "total_assets": total,
                "nav": (total / a.initial_cash).quantize(NAV_Q),
                "day_pnl": total - base,
                "estimated": True,
                "positions_count": len(rows),
                "as_of": self.clock.now(),
            }
        )

    def positions(self, a: Account) -> list[dict[str, Any]]:
        cash, mv, rows = self._valuation(a)
        total = cash + mv
        for r in rows:
            r["weight"] = (r["market_value"] / total).quantize(Decimal("0.0001")) if total else None
        return [jsonable(r) for r in rows]

    def nav(self, a: Account) -> list[dict[str, Any]]:
        return [n.to_dict() for n in repos.list_nav(self.db.read(), a.id)]

    def stats(self, a: Account) -> dict[str, Any]:
        ex = self.db.read()
        fills = repos.all_fills(ex, a.id)
        try:
            cal = self.reference.calendar()

            def between(x: date, y: date) -> int:
                return max(cal.trading_day_rank(x, y) - 1, 0)

        except CalendarUnavailable:

            def between(x: date, y: date) -> int:
                return (y - x).days

        rounds = fifo_rounds(fills, between)
        navs = [n.nav for n in repos.list_nav(ex, a.id)]
        st = compute_stats(rounds, navs, a.created_at.date(), self.clock.now().date())
        return jdict(
            {
                "rounds": st.rounds,
                "win_rate": st.win_rate,
                "profit_factor": st.profit_factor,
                "avg_holding_days": st.avg_holding_days,
                "max_drawdown": st.max_drawdown,
                "cumulative_return": st.cumulative_return,
                "annualized_return": st.annualized_return,
                "round_details": [
                    {
                        "symbol": r.symbol,
                        "open_date": r.open_date,
                        "close_date": r.close_date,
                        "qty": r.qty,
                        "cost": r.cost,
                        "proceeds": r.proceeds,
                        "pnl": r.pnl,
                        "holding_days": r.holding_days,
                    }
                    for r in rounds
                ],
            }
        )
