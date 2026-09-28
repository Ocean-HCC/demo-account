"""标的识别与交易所申报规则：代码格式、板块、风险警示股、申报数量、涨跌停价。

规则依据见需求文档「跨角色规则和外部约束 1. 交易所与税务规定」。
"""

from __future__ import annotations

import re
from decimal import Decimal

from .models import AssetType, Board, Exchange, OrderType, PriceLimits, Side
from .money import D, round_to_tick

SYMBOL_RE = re.compile(r"^(\d{6})\.(SH|SZ)$")

LIMIT_RATE_MAIN = Decimal("0.10")
LIMIT_RATE_20 = Decimal("0.20")
NEW_LISTING_NO_LIMIT_DAYS = 5  # 新股上市前 5 个交易日不设涨跌幅
ST_DAILY_BUY_CAP = 500_000  # 风险警示股当日累计买入上限（股）

LOT = 100
STAR_MIN_QTY = 200


class SymbolError(ValueError):
    """标的代码不受支持。"""


def parse_symbol(symbol: str) -> tuple[str, Exchange]:
    """校验并拆分代码，只接受 6 位数字加 .SH 或 .SZ；北交所等一律拒绝。"""
    m = SYMBOL_RE.match(symbol)
    if m is None:
        raise SymbolError(f"不支持的标的代码: {symbol}")
    return m.group(1), Exchange(m.group(2))


def guess_asset_type(symbol: str) -> AssetType:
    """仅凭代码猜资产类型：沪市 5 开头、深市 1 开头为基金（ETF），其余为股票。

    只用于行情价格取整等不影响账务判定的场合；账务判定以参考数据中的资产类型为准。
    """
    code, exchange = parse_symbol(symbol)
    if (exchange is Exchange.SH and code.startswith("5")) or (
        exchange is Exchange.SZ and code.startswith("1")
    ):
        return AssetType.ETF
    return AssetType.STOCK


def board_of(symbol: str, asset_type: AssetType) -> Board:
    """688、689 开头为科创板；300、301 开头为创业板；其余为主板。ETF 一律按主板规则。"""
    code, _ = parse_symbol(symbol)
    if asset_type is AssetType.ETF:
        return Board.MAIN
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("300", "301")):
        return Board.CHINEXT
    return Board.MAIN


def is_st(name: str) -> bool:
    """名称含 ST 或 *ST 视为风险警示股。"""
    return "ST" in name.upper()


def limit_rate(board: Board, asset_type: AssetType, etf_20pct: bool = False) -> Decimal:
    """涨跌幅比例：主板 10%（含风险警示股），创业板与科创板 20%，ETF 10%，名单内 ETF 20%。"""
    if asset_type is AssetType.ETF:
        return LIMIT_RATE_20 if etf_20pct else LIMIT_RATE_MAIN
    return LIMIT_RATE_20 if board in (Board.STAR, Board.CHINEXT) else LIMIT_RATE_MAIN


def new_listing_no_limit(trading_day_rank: int | None) -> bool:
    """trading_day_rank 为以上市日为 1 的交易日序号；None 表示未知，按有涨跌幅处理。"""
    return trading_day_rank is not None and trading_day_rank <= NEW_LISTING_NO_LIMIT_DAYS


def price_limits(prev_close: Decimal | int | str, rate: Decimal, tick: Decimal) -> PriceLimits:
    """涨跌幅限制价格 = 前收盘价 × (1 ± 比例)，按最小变动单位四舍五入。

    与前收盘价之差不足一个最小变动单位时按增减一个单位计算；下限低于一个单位时取一个单位
    （上交所交易规则 3.3.17）。
    """
    pc = D(prev_close)
    up = round_to_tick(pc * (1 + rate), tick)
    down = round_to_tick(pc * (1 - rate), tick)
    if up - pc < tick:
        up = (pc + tick).quantize(tick)
    if pc - down < tick:
        down = (pc - tick).quantize(tick)
    if down < tick:
        down = tick
    return PriceLimits(up=up, down=down)


def min_buy_qty(board: Board) -> int:
    return STAR_MIN_QTY if board is Board.STAR else LOT


def max_order_qty(board: Board, order_type: OrderType) -> int:
    """单笔申报数量上限。开盘单与即时单按市价申报口径，限价单按限价申报口径。"""
    if order_type is OrderType.CLOSE:
        return 1_000_000
    if board is Board.STAR:
        return 100_000 if order_type is OrderType.LIMIT else 50_000
    if board is Board.CHINEXT:
        return 300_000 if order_type is OrderType.LIMIT else 150_000
    return 1_000_000


def validate_qty(
    qty: int,
    side: Side,
    board: Board,
    order_type: OrderType,
    position_qty: int | None = None,
) -> str | None:
    """校验申报数量，返回错误码或 None。

    主板、创业板、ETF：买入 100 股整数倍；卖出时不足 100 股的零股须一次性卖出。
    科创板：买入不少于 200 股、超出部分 1 股递增；卖出不足 200 股的余股须一次性卖出。
    position_qty 为卖出时的当前持仓数量，用于判断零股或余股是否一次性卖出。
    """
    if qty <= 0:
        return "LOT_SIZE"
    if qty > max_order_qty(board, order_type):
        return "QTY_LIMIT"
    if board is Board.STAR:
        if side is Side.BUY:
            return None if qty >= STAR_MIN_QTY else "LOT_SIZE"
        if qty >= STAR_MIN_QTY:
            return None
        if position_qty is not None and qty in (position_qty, position_qty % STAR_MIN_QTY):
            return None
        return "LOT_SIZE"
    if side is Side.BUY:
        return None if qty % LOT == 0 else "LOT_SIZE"
    odd = qty % LOT
    if odd == 0:
        return None
    if position_qty is not None and qty <= position_qty and odd == position_qty % LOT:
        return None
    return "LOT_SIZE"


def round_lot_down(qty: int, board: Board) -> int:
    """向下调整为合规的买入数量；不足最小申报数量返回 0。"""
    if qty <= 0:
        return 0
    if board is Board.STAR:
        return qty if qty >= STAR_MIN_QTY else 0
    return (qty // LOT) * LOT


def st_order_allowed(order_type: OrderType, protect_price: Decimal | None) -> bool:
    """风险警示股只接受限价单和带保护限价的收盘单（需求跨角色规则 1.7）。"""
    return order_type is OrderType.LIMIT or (
        order_type is OrderType.CLOSE and protect_price is not None
    )
