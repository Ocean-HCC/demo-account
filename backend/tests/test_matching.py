from decimal import Decimal

from demo_account.core.matching import (
    amount_to_qty,
    close_fill,
    freeze_amount,
    limit_can_fill,
    limit_hit,
    market_fill_price,
    slippage_price,
)
from demo_account.core.models import AssetType, Board, FeeParams, PriceLimits, Side
from demo_account.core.money import TICK_ETF, TICK_STOCK

SLIP = Decimal("0.0005")
NO_LIMIT = PriceLimits(up=None, down=None)


def test_slippage_price_rounds_against_trader() -> None:
    # 9.00 × 1.0005 = 9.0045 → 买入向上取整 9.01；卖出 8.9955 → 向下取整 8.99
    assert slippage_price(Decimal("9.00"), Side.BUY, SLIP, TICK_STOCK) == Decimal("9.01")
    assert slippage_price(Decimal("9.00"), Side.SELL, SLIP, TICK_STOCK) == Decimal("8.99")
    # ETF 三位小数：4.515 × 1.0005 = 4.5172575 → 4.518；卖出 4.5127425 → 4.512
    assert slippage_price(Decimal("4.515"), Side.BUY, SLIP, TICK_ETF) == Decimal("4.518")
    assert slippage_price(Decimal("4.515"), Side.SELL, SLIP, TICK_ETF) == Decimal("4.512")


def test_market_fill_price_clamped_to_limits() -> None:
    limits = PriceLimits(up=Decimal("9.88"), down=Decimal("8.08"))
    assert market_fill_price(Decimal("9.87"), Side.BUY, SLIP, TICK_STOCK, limits) == Decimal("9.88")
    assert market_fill_price(Decimal("8.09"), Side.SELL, SLIP, TICK_STOCK, limits) == Decimal(
        "8.08"
    )
    assert market_fill_price(Decimal("9.00"), Side.BUY, SLIP, TICK_STOCK, limits) == Decimal("9.01")
    assert market_fill_price(Decimal("9.87"), Side.BUY, SLIP, TICK_STOCK, NO_LIMIT) == Decimal(
        "9.88"
    )


def test_limit_hit() -> None:
    limits = PriceLimits(up=Decimal("9.88"), down=Decimal("8.08"))
    assert limit_hit(Decimal("9.88"), Side.BUY, limits)
    assert not limit_hit(Decimal("9.87"), Side.BUY, limits)
    assert limit_hit(Decimal("8.08"), Side.SELL, limits)
    assert not limit_hit(Decimal("8.09"), Side.SELL, limits)
    assert not limit_hit(Decimal("9.88"), Side.SELL, limits)
    assert not limit_hit(Decimal("100"), Side.BUY, NO_LIMIT)


def test_limit_can_fill() -> None:
    assert limit_can_fill(Decimal("9.00"), Decimal("9.00"), Side.BUY)
    assert limit_can_fill(Decimal("8.99"), Decimal("9.00"), Side.BUY)
    assert not limit_can_fill(Decimal("9.01"), Decimal("9.00"), Side.BUY)
    assert limit_can_fill(Decimal("9.01"), Decimal("9.00"), Side.SELL)
    assert not limit_can_fill(Decimal("8.99"), Decimal("9.00"), Side.SELL)


def test_close_fill_protect_price() -> None:
    assert close_fill(Decimal("9.00"), Side.BUY, None)
    assert close_fill(Decimal("9.00"), Side.BUY, Decimal("9.00"))
    assert not close_fill(Decimal("9.01"), Side.BUY, Decimal("9.00"))
    assert close_fill(Decimal("9.00"), Side.SELL, Decimal("9.00"))
    assert not close_fill(Decimal("8.99"), Side.SELL, Decimal("9.00"))


def test_freeze_amount_includes_estimated_buy_fees(fee_params: FeeParams) -> None:
    # 1100 × 9.88 = 10868.00；佣金 1.41 → 最低 5；过户费 0.11
    assert freeze_amount(1100, Decimal("9.88"), fee_params, AssetType.STOCK) == Decimal("10873.11")
    # ETF 无过户费
    assert freeze_amount(1000, Decimal("4.578"), fee_params, AssetType.ETF) == Decimal("4583.00")


def test_amount_to_qty(fee_params: FeeParams) -> None:
    # 10000 元按 9.00 冻结价：1111 → 1100，冻结 9900 + 5 + 0.10 = 9905.10 ≤ 10000
    assert (
        amount_to_qty(Decimal("10000"), Decimal("9.00"), Board.MAIN, AssetType.STOCK, fee_params)
        == 1100
    )
    # 恰好卡在边界：9905.10 元只够 1100 股；9905.00 元只够 1000 股
    assert (
        amount_to_qty(Decimal("9905.10"), Decimal("9.00"), Board.MAIN, AssetType.STOCK, fee_params)
        == 1100
    )
    assert (
        amount_to_qty(Decimal("9905.00"), Decimal("9.00"), Board.MAIN, AssetType.STOCK, fee_params)
        == 1000
    )
    # 不足一手
    assert (
        amount_to_qty(Decimal("800"), Decimal("9.00"), Board.MAIN, AssetType.STOCK, fee_params) == 0
    )
    # 科创板 1 股递增：20000 元按 50 元冻结价 → 400 股，冻结 20000 + 5 + 0.20 超出 → 399
    assert (
        amount_to_qty(Decimal("20000"), Decimal("50"), Board.STAR, AssetType.STOCK, fee_params)
        == 399
    )
    assert (
        amount_to_qty(Decimal("9000"), Decimal("50"), Board.STAR, AssetType.STOCK, fee_params) == 0
    )
    assert (
        amount_to_qty(Decimal("1000"), Decimal("0"), Board.MAIN, AssetType.STOCK, fee_params) == 0
    )
