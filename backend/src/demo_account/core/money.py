"""金额与价格精度。所有金额计算只用 Decimal，浮点只允许出现在数据源边界。"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from .models import AssetType

ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
TICK_STOCK = Decimal("0.01")
TICK_ETF = Decimal("0.001")


def D(value: Decimal | int | str | float) -> Decimal:  # noqa: N802
    """把外部值转成 Decimal。浮点经 repr 转换，避免二进制误差扩散。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def round_cent(value: Decimal | int | str) -> Decimal:
    """金额按分四舍五入（half-up）。"""
    return D(value).quantize(CENT, rounding=ROUND_HALF_UP)


def tick_for(asset_type: AssetType) -> Decimal:
    """申报价格最小变动单位：股票 0.01 元，ETF 0.001 元。"""
    return TICK_ETF if asset_type is AssetType.ETF else TICK_STOCK


def round_to_tick(
    price: Decimal | int | str, tick: Decimal, rounding: str = ROUND_HALF_UP
) -> Decimal:
    """按最小变动单位取整，默认四舍五入。"""
    steps = (D(price) / tick).quantize(ONE, rounding=rounding)
    return (steps * tick).quantize(tick)


def round_up_to_tick(price: Decimal | int | str, tick: Decimal) -> Decimal:
    return round_to_tick(price, tick, ROUND_CEILING)


def round_down_to_tick(price: Decimal | int | str, tick: Decimal) -> Decimal:
    return round_to_tick(price, tick, ROUND_FLOOR)


def is_on_tick(price: Decimal | int | str, tick: Decimal) -> bool:
    steps = D(price) / tick
    return steps == steps.to_integral_value()
