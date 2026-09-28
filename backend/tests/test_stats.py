from datetime import date, datetime
from decimal import Decimal

from demo_account.core.fees import compute_fees
from demo_account.core.models import AssetType, FeeParams, Fill, FillKind, Side
from demo_account.core.money import ZERO, round_cent
from demo_account.core.rules import BEIJING, SimpleCalendar
from demo_account.core.stats import compute_stats, fifo_rounds, max_drawdown

FP = FeeParams(Decimal("0.00013"), Decimal("5"), Decimal("0.0005"))
D1, D2, D3 = date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 28)


def trade(seq: int, side: Side, qty: int, price: str, d: date, symbol: str = "600000.SH") -> Fill:
    gross = round_cent(Decimal(qty) * Decimal(price))
    fees = compute_fees(gross, side, AssetType.STOCK, FP)
    return Fill(
        seq=seq,
        account_id="acc",
        order_id=f"o{seq}",
        symbol=symbol,
        kind=FillKind.TRADE,
        side=side,
        qty=qty,
        price=Decimal(price),
        gross_amount=gross,
        commission=fees.commission,
        stamp_tax=fees.stamp_tax,
        transfer_fee=fees.transfer_fee,
        cash_delta=-(gross + fees.total) if side is Side.BUY else gross - fees.total,
        trade_date=d,
        occurred_at=datetime(d.year, d.month, d.day, 10, 0, tzinfo=BEIJING),
    )


def bonus(seq: int, qty: int, d: date) -> Fill:
    return Fill(
        seq=seq,
        account_id="acc",
        order_id=None,
        symbol="600000.SH",
        kind=FillKind.CORPORATE_ACTION,
        side=Side.BUY,
        qty=qty,
        price=ZERO,
        gross_amount=ZERO,
        commission=ZERO,
        stamp_tax=ZERO,
        transfer_fee=ZERO,
        cash_delta=ZERO,
        trade_date=d,
        occurred_at=datetime(d.year, d.month, d.day, 16, 0, tzinfo=BEIJING),
    )


def days_between(calendar: SimpleCalendar) -> object:
    def fn(a: date, b: date) -> int:
        return calendar.trading_day_rank(a, b) - 1

    return fn


def test_fifo_rounds_split_across_batches(calendar: SimpleCalendar) -> None:
    fills = [
        trade(1, Side.BUY, 100, "10.00", D1),
        trade(2, Side.BUY, 100, "12.00", D2),
        trade(3, Side.SELL, 150, "13.00", D3),
    ]
    rounds = fifo_rounds(fills, days_between(calendar))  # type: ignore[arg-type]
    assert len(rounds) == 2
    r1, r2 = rounds
    assert (r1.qty, r1.open_date, r1.close_date, r1.holding_days) == (100, D1, D3, 2)
    assert (r2.qty, r2.open_date, r2.close_date, r2.holding_days) == (50, D2, D3, 1)
    # 第一批成本 1000 + 5 + 0.01；第二批 1200 + 5 + 0.01 的一半
    assert r1.cost == Decimal("1005.01")
    assert r2.cost == round_cent(Decimal("1205.01") * 50 / 100)
    sell_net = (
        Decimal("1950.00") - compute_fees(Decimal("1950.00"), Side.SELL, AssetType.STOCK, FP).total
    )
    assert r1.proceeds + r2.proceeds == round_cent(sell_net * 100 / 150) + round_cent(
        sell_net * 50 / 150
    )
    assert r1.pnl > 0 and r2.pnl > 0


def test_bonus_shares_dilute_batches_without_rounds(calendar: SimpleCalendar) -> None:
    fills = [
        trade(1, Side.BUY, 1000, "10.00", D1),
        bonus(2, 200, D2),
        trade(3, Side.SELL, 1200, "9.00", D3),
    ]
    rounds = fifo_rounds(fills, days_between(calendar))  # type: ignore[arg-type]
    assert len(rounds) == 1
    assert rounds[0].qty == 1200
    assert rounds[0].cost == Decimal("10005.10")  # 成本总额不变
    assert rounds[0].pnl > 0  # 1200 × 9 = 10800 扣费仍高于 10005.10


def test_max_drawdown() -> None:
    navs = [Decimal("1"), Decimal("1.1"), Decimal("0.99"), Decimal("1.2"), Decimal("1.05")]
    assert max_drawdown(navs) == Decimal("0.125000")  # (1.2 − 1.05) / 1.2
    assert max_drawdown([Decimal("1"), Decimal("1.2")]) == Decimal("0")
    assert max_drawdown([]) is None


def test_compute_stats(calendar: SimpleCalendar) -> None:
    fills = [
        trade(1, Side.BUY, 100, "10.00", D1),
        trade(2, Side.SELL, 100, "11.00", D2),
        trade(3, Side.BUY, 100, "10.00", D2, symbol="000001.SZ"),
        trade(4, Side.SELL, 100, "9.00", D3, symbol="000001.SZ"),
    ]
    rounds = fifo_rounds(fills, days_between(calendar))  # type: ignore[arg-type]
    navs = [Decimal("1"), Decimal("1.01"), Decimal("0.995")]
    stats = compute_stats(rounds, navs, account_created=D1, today=D3)
    assert stats.rounds == 2
    assert stats.win_rate == Decimal("0.500000")
    assert stats.profit_factor is not None and stats.profit_factor > 0
    assert stats.avg_holding_days == Decimal("1.000000")
    assert stats.max_drawdown == Decimal("0.014851")  # (1.01 − 0.995) / 1.01
    assert stats.cumulative_return == Decimal("-0.005000")
    assert stats.annualized_return is None  # 账龄不足 30 天


def test_annualized_after_30_days() -> None:
    stats = compute_stats(
        [], [Decimal("1.1")], account_created=date(2026, 1, 1), today=date(2027, 1, 1)
    )
    assert stats.annualized_return == Decimal("0.100000")
    assert stats.win_rate is None
    assert stats.profit_factor is None
