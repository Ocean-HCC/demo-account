"""成交台账重放（方案 4.5、实现 2.4）。

由成交记录推导每个标的的数量、可卖数量、今日买入、成本与现金。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import Fill, FillKind, Side
from .money import ZERO, round_cent


class LedgerError(Exception):
    """台账记录不自洽。"""


@dataclass
class PositionState:
    qty: int = 0
    today_bought_qty: int = 0
    cost_total: Decimal = ZERO

    @property
    def sellable_qty(self) -> int:
        return self.qty - self.today_bought_qty

    @property
    def avg_cost(self) -> Decimal | None:
        if self.qty <= 0:
            return None
        return self.cost_total / self.qty


@dataclass
class LedgerState:
    cash: Decimal
    positions: dict[str, PositionState] = field(default_factory=dict)
    as_of: date | None = None
    last_seq: int | None = None

    def position(self, symbol: str) -> PositionState:
        return self.positions.setdefault(symbol, PositionState())


def new_state(initial_cash: Decimal) -> LedgerState:
    return LedgerState(cash=initial_cash)


def unlock_t1(state: LedgerState) -> None:
    """日终结算：当日买入转为可卖。"""
    for pos in state.positions.values():
        pos.today_bought_qty = 0


def apply_fill(state: LedgerState, fill: Fill) -> None:
    """按一条台账记录更新状态。记录必须按序号顺序、交易日不倒退地应用。"""
    if state.as_of is not None:
        if fill.trade_date < state.as_of:
            raise LedgerError(f"成交日期倒退: {fill.trade_date} < {state.as_of}")
        if fill.trade_date > state.as_of:
            unlock_t1(state)
    state.as_of = fill.trade_date
    pos = state.position(fill.symbol)

    if fill.kind is FillKind.TRADE:
        if fill.side is Side.BUY:
            pos.qty += fill.qty
            pos.today_bought_qty += fill.qty
            pos.cost_total += fill.gross_amount + fill.fees
        else:
            if fill.qty > pos.qty:
                raise LedgerError(f"卖出数量超过持仓: {fill.symbol} {fill.qty} > {pos.qty}")
            if fill.qty == pos.qty:
                pos.cost_total = ZERO
            else:
                pos.cost_total -= round_cent(pos.cost_total * fill.qty / pos.qty)
            pos.qty -= fill.qty
    elif fill.kind is FillKind.CORPORATE_ACTION:
        # 送转到账当日即可卖，成本总额不变；现金分红与零头折现走 cash_delta
        if fill.side is Side.BUY:
            pos.qty += fill.qty
        else:
            if fill.qty > pos.qty:
                raise LedgerError(f"公司行动减少数量超过持仓: {fill.symbol}")
            pos.qty -= fill.qty
    else:  # REVERSAL：显式增量
        if fill.side is Side.BUY:
            pos.qty += fill.qty
            pos.cost_total += fill.gross_amount
        else:
            if fill.qty > pos.qty:
                raise LedgerError(f"冲正减少数量超过持仓: {fill.symbol}")
            pos.qty -= fill.qty
            pos.cost_total -= fill.gross_amount

    pos.today_bought_qty = min(pos.today_bought_qty, pos.qty)
    if pos.cost_total < ZERO:
        pos.cost_total = ZERO
    state.cash += fill.cash_delta
    if pos.qty == 0:
        del state.positions[fill.symbol]
    state.last_seq = fill.seq


def replay(initial_cash: Decimal, fills: Iterable[Fill], as_of: date | None = None) -> LedgerState:
    """从头重放全部成交记录。as_of 晚于最后一笔成交的交易日时，视为其后各日已结算。"""
    state = new_state(initial_cash)
    ordered = sorted(fills, key=lambda f: (f.seq if f.seq is not None else 0, f.occurred_at))
    for fill in ordered:
        apply_fill(state, fill)
    if as_of is not None and state.as_of is not None and as_of > state.as_of:
        unlock_t1(state)
        state.as_of = as_of
    return state
