import random
from datetime import date, datetime
from decimal import Decimal

import pytest

from demo_account.core.fees import compute_fees
from demo_account.core.ledger import LedgerError, apply_fill, new_state, replay, unlock_t1
from demo_account.core.models import AssetType, FeeParams, Fill, FillKind, Side
from demo_account.core.money import ZERO, round_cent
from demo_account.core.rules import BEIJING

ACC = "acc_test"
D1 = date(2026, 9, 24)
D2 = date(2026, 9, 25)
D3 = date(2026, 9, 28)
FP = FeeParams(Decimal("0.00013"), Decimal("5"), Decimal("0.0005"))


def trade(
    seq: int,
    symbol: str,
    side: Side,
    qty: int,
    price: str,
    d: date,
    asset_type: AssetType = AssetType.STOCK,
) -> Fill:
    gross = round_cent(Decimal(qty) * Decimal(price))
    fees = compute_fees(gross, side, asset_type, FP)
    cash_delta = -(gross + fees.total) if side is Side.BUY else gross - fees.total
    return Fill(
        seq=seq,
        account_id=ACC,
        order_id=f"ord_{seq}",
        symbol=symbol,
        kind=FillKind.TRADE,
        side=side,
        qty=qty,
        price=Decimal(price),
        gross_amount=gross,
        commission=fees.commission,
        stamp_tax=fees.stamp_tax,
        transfer_fee=fees.transfer_fee,
        cash_delta=cash_delta,
        trade_date=d,
        occurred_at=datetime(d.year, d.month, d.day, 10, 0, tzinfo=BEIJING),
    )


def action(seq: int, symbol: str, side: Side, qty: int, cash: str, d: date, note: str = "") -> Fill:
    return Fill(
        seq=seq,
        account_id=ACC,
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
        cash_delta=Decimal(cash),
        trade_date=d,
        occurred_at=datetime(d.year, d.month, d.day, 16, 0, tzinfo=BEIJING),
        note=note,
    )


def reversal(seq: int, symbol: str, side: Side, qty: int, cost: str, cash: str, d: date) -> Fill:
    return Fill(
        seq=seq,
        account_id=ACC,
        order_id=None,
        symbol=symbol,
        kind=FillKind.REVERSAL,
        side=side,
        qty=qty,
        price=ZERO,
        gross_amount=Decimal(cost),
        commission=ZERO,
        stamp_tax=ZERO,
        transfer_fee=ZERO,
        cash_delta=Decimal(cash),
        trade_date=d,
        occurred_at=datetime(d.year, d.month, d.day, 17, 0, tzinfo=BEIJING),
    )


def test_buy_sets_cost_with_fees_and_locks_t1() -> None:
    state = replay(Decimal("100000"), [trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1)])
    pos = state.positions["600000.SH"]
    assert pos.qty == 1000
    assert pos.today_bought_qty == 1000
    assert pos.sellable_qty == 0
    # 成本 9000 + 佣金 5 + 过户费 0.09
    assert pos.cost_total == Decimal("9005.09")
    assert state.cash == Decimal("100000") - Decimal("9005.09")
    assert pos.avg_cost is not None and round_cent(pos.avg_cost) == Decimal("9.01")


def test_next_day_unlocks_and_partial_sell_reduces_cost_proportionally() -> None:
    fills = [
        trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1),
        trade(2, "600000.SH", Side.SELL, 400, "9.50", D2),
    ]
    state = replay(Decimal("100000"), fills)
    pos = state.positions["600000.SH"]
    assert pos.qty == 600
    assert pos.today_bought_qty == 0
    assert pos.cost_total == Decimal("9005.09") - round_cent(Decimal("9005.09") * 400 / 1000)
    sell_gross = Decimal("3800.00")
    sell_fees = compute_fees(sell_gross, Side.SELL, AssetType.STOCK, FP).total
    assert state.cash == Decimal("100000") - Decimal("9005.09") + sell_gross - sell_fees


def test_full_sell_removes_position_and_zeroes_cost() -> None:
    fills = [
        trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1),
        trade(2, "600000.SH", Side.SELL, 1000, "9.50", D2),
    ]
    state = replay(Decimal("100000"), fills)
    assert "600000.SH" not in state.positions


def test_replay_as_of_unlocks_after_last_trade_date() -> None:
    state = replay(Decimal("100000"), [trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1)], as_of=D2)
    assert state.positions["600000.SH"].sellable_qty == 1000
    assert state.as_of == D2


def test_unlock_t1_and_same_day_sell_is_rejected_by_services_not_ledger() -> None:
    state = new_state(Decimal("100000"))
    apply_fill(state, trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1))
    unlock_t1(state)
    assert state.positions["600000.SH"].sellable_qty == 1000


def test_oversell_raises() -> None:
    with pytest.raises(LedgerError):
        replay(
            Decimal("100000"),
            [
                trade(1, "600000.SH", Side.BUY, 100, "9.00", D1),
                trade(2, "600000.SH", Side.SELL, 200, "9.00", D2),
            ],
        )


def test_trade_date_cannot_go_backwards() -> None:
    with pytest.raises(LedgerError):
        replay(
            Decimal("100000"),
            [
                trade(1, "600000.SH", Side.BUY, 100, "9.00", D2),
                trade(2, "600000.SH", Side.BUY, 100, "9.00", D1),
            ],
        )


def test_corporate_action_bonus_shares_and_cash_dividend() -> None:
    fills = [
        trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1),
        # 10 送 2：数量 +200，成本总额不变；10 派 4.2：现金 +420
        action(2, "600000.SH", Side.BUY, 200, "0", D2, "送股"),
        action(3, "600000.SH", Side.BUY, 0, "420", D2, "分红"),
    ]
    state = replay(Decimal("100000"), fills)
    pos = state.positions["600000.SH"]
    assert pos.qty == 1200
    assert pos.sellable_qty == 1200  # 送转到账当日可卖
    assert pos.cost_total == Decimal("9005.09")
    assert state.cash == Decimal("100000") - Decimal("9005.09") + Decimal("420")


def test_reversal_explicit_deltas() -> None:
    fills = [
        trade(1, "600000.SH", Side.BUY, 1000, "9.00", D1),
        # 冲正：减少 100 股，成本减 900.51，现金加回 900.51
        reversal(2, "600000.SH", Side.SELL, 100, "900.51", "900.51", D2),
    ]
    state = replay(Decimal("100000"), fills)
    pos = state.positions["600000.SH"]
    assert pos.qty == 900
    assert pos.cost_total == Decimal("9005.09") - Decimal("900.51")
    assert state.cash == Decimal("100000") - Decimal("9005.09") + Decimal("900.51")


def test_random_sequences_keep_invariants() -> None:
    """随机成交序列：现金等于初始资金加全部现金变动之和，数量等于买卖差，成本非负。"""
    rng = random.Random(20260928)
    symbols = ["600000.SH", "000001.SZ", "300750.SZ", "510300.SH"]
    for _ in range(50):
        fills: list[Fill] = []
        held: dict[str, int] = dict.fromkeys(symbols, 0)
        seq = 0
        for day in (D1, D2, D3):
            for _ in range(rng.randint(1, 8)):
                sym = rng.choice(symbols)
                asset = AssetType.ETF if sym == "510300.SH" else AssetType.STOCK
                price = f"{rng.uniform(3, 60):.2f}"
                if held[sym] > 0 and rng.random() < 0.4:
                    qty = rng.randint(1, held[sym])
                    fills.append(trade(seq := seq + 1, sym, Side.SELL, qty, price, day, asset))
                    held[sym] -= qty
                else:
                    qty = rng.randint(1, 20) * 100
                    fills.append(trade(seq := seq + 1, sym, Side.BUY, qty, price, day, asset))
                    held[sym] += qty
        state = replay(Decimal("1000000"), fills)
        assert state.cash == Decimal("1000000") + sum((f.cash_delta for f in fills), ZERO)
        for sym, qty in held.items():
            if qty == 0:
                assert sym not in state.positions
            else:
                pos = state.positions[sym]
                assert pos.qty == qty
                assert pos.cost_total >= ZERO
                assert 0 <= pos.today_bought_qty <= pos.qty
