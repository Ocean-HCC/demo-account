"""撮合判定（方案 6.1、6.3）：成交价与滑点、涨跌停、限价触及、保护限价、冻结金额、按金额折算。"""

from __future__ import annotations

from decimal import Decimal

from .fees import compute_fees
from .instruments import round_lot_down
from .models import AssetType, Board, FeeParams, PriceLimits, Side
from .money import D, round_cent, round_down_to_tick, round_up_to_tick


def slippage_price(ref_price: Decimal, side: Side, slippage: Decimal, tick: Decimal) -> Decimal:
    """最新价加不利滑点，按最小变动单位向不利方向取整。"""
    if side is Side.BUY:
        return round_up_to_tick(ref_price * (1 + slippage), tick)
    return round_down_to_tick(ref_price * (1 - slippage), tick)


def market_fill_price(
    ref_price: Decimal,
    side: Side,
    slippage: Decimal,
    tick: Decimal,
    limits: PriceLimits,
) -> Decimal:
    """即时单与开盘单的成交价：参考价加不利滑点，不越过涨跌停价。"""
    price = slippage_price(ref_price, side, slippage, tick)
    if limits.up is not None and price > limits.up:
        price = limits.up
    if limits.down is not None and price < limits.down:
        price = limits.down
    return price


def limit_hit(ref_price: Decimal, side: Side, limits: PriceLimits) -> bool:
    """买入时参考价已达涨停价、卖出时已达跌停价，即时单与开盘单应拒绝。"""
    if side is Side.BUY:
        return limits.up is not None and ref_price >= limits.up
    return limits.down is not None and ref_price <= limits.down


def limit_can_fill(last: Decimal, limit_price: Decimal, side: Side) -> bool:
    """限价单触及判定：买入且最新价不高于限价，或卖出且最新价不低于限价。"""
    if side is Side.BUY:
        return last <= limit_price
    return last >= limit_price


def close_fill(close: Decimal, side: Side, protect_price: Decimal | None) -> bool:
    """收盘单保护限价判定：买入时收盘价高于限价、卖出时低于限价则不成交。"""
    if protect_price is None:
        return True
    if side is Side.BUY:
        return close <= protect_price
    return close >= protect_price


def freeze_amount(
    qty: int,
    freeze_price: Decimal,
    fee_params: FeeParams,
    asset_type: AssetType,
) -> Decimal:
    """买单冻结金额 = 数量 × 冻结价 + 按冻结价估算的买入费用。"""
    gross = round_cent(D(qty) * freeze_price)
    fees = compute_fees(gross, Side.BUY, asset_type, fee_params)
    return gross + fees.total


def amount_to_qty(
    amount: Decimal,
    freeze_price: Decimal,
    board: Board,
    asset_type: AssetType,
    fee_params: FeeParams,
) -> int:
    """按金额买入：在冻结价下，冻结金额不超过给定金额的最大合规数量；不足最小申报数量返回 0。"""
    if freeze_price <= 0 or amount <= 0:
        return 0
    qty = round_lot_down(int(amount // freeze_price), board)
    step = 1 if board is Board.STAR else 100
    while qty > 0 and freeze_amount(qty, freeze_price, fee_params, asset_type) > amount:
        qty = round_lot_down(qty - step, board)
    return qty
