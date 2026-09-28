"""回合统计（方案 6.9）：先进先出配对、胜率、盈亏比、持有天数、最大回撤、累计与年化收益。"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .models import Fill, FillKind, Side
from .money import ZERO, round_cent

MIN_DAYS_FOR_ANNUALIZED = 30
_SIX = Decimal("0.000001")


@dataclass(frozen=True)
class Round:
    symbol: str
    open_date: date
    close_date: date
    qty: int
    cost: Decimal  # 买入成本，含费用
    proceeds: Decimal  # 卖出净额，扣费用
    pnl: Decimal
    holding_days: int  # 持有交易日数


@dataclass(frozen=True)
class Stats:
    rounds: int
    win_rate: Decimal | None
    profit_factor: Decimal | None
    avg_holding_days: Decimal | None
    max_drawdown: Decimal | None
    cumulative_return: Decimal | None
    annualized_return: Decimal | None


@dataclass
class _Batch:
    qty: int
    cost: Decimal
    open_date: date


def _distribute(batches: deque[_Batch], delta: int) -> None:
    """公司行动带来的数量变化按剩余数量比例分摊到各批次，余数给最早的批次；成本不变。"""
    total = sum(b.qty for b in batches)
    if total <= 0 or delta == 0:
        return
    if delta > 0:
        shares = [delta * b.qty // total for b in batches]
        remainder = delta - sum(shares)
        for i, b in enumerate(batches):
            b.qty += shares[i] + (1 if i < remainder else 0)
    else:
        removing = -delta
        for b in reversed(batches):
            take = min(b.qty, removing)
            b.qty -= take
            removing -= take
            if removing == 0:
                break
        while batches and batches[-1].qty == 0:
            batches.pop()


def fifo_rounds(
    fills: Iterable[Fill],
    trading_days_between: Callable[[date, date], int],
) -> list[Round]:
    """按标的先进先出配对：每次卖出依次消耗最早未平的买入批次，每个消耗的批次形成一个回合。"""
    batches: dict[str, deque[_Batch]] = {}
    rounds: list[Round] = []
    ordered = sorted(fills, key=lambda f: (f.seq if f.seq is not None else 0, f.occurred_at))
    for f in ordered:
        q = batches.setdefault(f.symbol, deque())
        if f.kind is FillKind.TRADE and f.side is Side.BUY:
            q.append(_Batch(f.qty, f.gross_amount + f.fees, f.trade_date))
        elif f.kind is FillKind.TRADE:
            remaining = f.qty
            net = f.gross_amount - f.fees
            while remaining > 0 and q:
                b = q[0]
                take = min(b.qty, remaining)
                cost = b.cost if take == b.qty else round_cent(b.cost * take / b.qty)
                proceeds = round_cent(net * take / f.qty)
                rounds.append(
                    Round(
                        symbol=f.symbol,
                        open_date=b.open_date,
                        close_date=f.trade_date,
                        qty=take,
                        cost=cost,
                        proceeds=proceeds,
                        pnl=proceeds - cost,
                        holding_days=trading_days_between(b.open_date, f.trade_date),
                    )
                )
                b.qty -= take
                b.cost -= cost
                remaining -= take
                if b.qty == 0:
                    q.popleft()
        elif f.kind is FillKind.CORPORATE_ACTION:
            _distribute(q, f.qty if f.side is Side.BUY else -f.qty)
        elif f.side is Side.BUY:  # 冲正增加数量：新批次，成本为冲正给出的成本增量
            q.append(_Batch(f.qty, f.gross_amount, f.trade_date))
        else:  # 冲正减少数量：消耗批次，不形成回合
            _distribute(q, -f.qty)
    return rounds


def max_drawdown(navs: Sequence[Decimal]) -> Decimal | None:
    """按定版净值序列计算最大回撤，序列为空返回 None。"""
    if not navs:
        return None
    peak = navs[0]
    worst = ZERO
    for nav in navs:
        if nav > peak:
            peak = nav
        if peak > ZERO:
            dd = (peak - nav) / peak
            if dd > worst:
                worst = dd
    return worst.quantize(_SIX)


def compute_stats(
    rounds: Sequence[Round],
    navs: Sequence[Decimal],
    account_created: date,
    today: date,
) -> Stats:
    n = len(rounds)
    wins = [r.pnl for r in rounds if r.pnl > ZERO]
    losses = [-r.pnl for r in rounds if r.pnl < ZERO]
    win_rate = (Decimal(len(wins)) / n).quantize(_SIX) if n else None
    profit_factor: Decimal | None = None
    if wins and losses:
        avg_win = sum(wins, ZERO) / len(wins)
        avg_loss = sum(losses, ZERO) / len(losses)
        profit_factor = (avg_win / avg_loss).quantize(_SIX) if avg_loss > ZERO else None
    avg_holding = (Decimal(sum(r.holding_days for r in rounds)) / n).quantize(_SIX) if n else None
    mdd = max_drawdown(navs)
    cumulative = (navs[-1] - 1).quantize(_SIX) if navs else None
    annualized: Decimal | None = None
    days = (today - account_created).days
    if navs and days >= MIN_DAYS_FOR_ANNUALIZED and navs[-1] > ZERO:
        annualized = (navs[-1] ** (Decimal(365) / Decimal(days)) - 1).quantize(_SIX)
    return Stats(
        rounds=n,
        win_rate=win_rate,
        profit_factor=profit_factor,
        avg_holding_days=avg_holding,
        max_drawdown=mdd,
        cumulative_return=cumulative,
        annualized_return=annualized,
    )
