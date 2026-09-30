"""标的识别与交易所申报规则：代码格式、板块、风险警示股、申报数量、涨跌停价。

规则依据见需求文档「跨角色规则和外部约束 1. 交易所与税务规定」。
"""

from __future__ import annotations

import re
from decimal import Decimal

from .models import Board, Exchange, OrderType, PriceLimits, Side
from .money import D, round_to_tick

SYMBOL_RE = re.compile(r"^(\d{6})\.(SH|SZ)$")
STOCK_PREFIXES = {Exchange.SH: ("6",), Exchange.SZ: ("00", "30")}  # 沪市 6；深市 00、30

LIMIT_RATE_MAIN = Decimal("0.10")
LIMIT_RATE_20 = Decimal("0.20")
NEW_LISTING_NO_LIMIT_DAYS = 5  # 新股上市前 5 个交易日不设涨跌幅
ST_DAILY_BUY_CAP = 500_000  # 风险警示股当日累计买入上限（股）

LOT = 100
STAR_MIN_QTY = 200


class SymbolError(ValueError):
    """标的代码不受支持。"""


def split_symbol(symbol: str) -> tuple[str, Exchange]:
    """只校验格式并拆分：6 位数字加 .SH 或 .SZ。指数代码（如基准 000300.SH）也用它。"""
    m = SYMBOL_RE.match(symbol)
    if m is None:
        raise SymbolError(f"不支持的标的代码: {symbol}")
    return m.group(1), Exchange(m.group(2))


def parse_symbol(symbol: str) -> tuple[str, Exchange]:
    """校验可交易标的并拆分：只支持沪深 A 股股票（沪市 6 开头，深市 00、30 开头）。

    ETF、LOF 等基金，B 股、债券、指数与北交所一律拒绝（方案 8.1 标的不支持）。
    """
    code, exchange = split_symbol(symbol)
    if not code.startswith(STOCK_PREFIXES[exchange]):
        raise SymbolError(f"不支持的标的代码: {symbol}，只支持沪深 A 股股票")
    return code, exchange


def board_of(symbol: str) -> Board:
    """688、689 开头为科创板；300、301 开头为创业板；其余为主板。"""
    code, _ = parse_symbol(symbol)
    if code.startswith(("688", "689")):
        return Board.STAR
    if code.startswith(("300", "301")):
        return Board.CHINEXT
    return Board.MAIN


def is_st(name: str) -> bool:
    """名称含 ST 或 *ST 视为风险警示股。"""
    return "ST" in name.upper()


def limit_rate(board: Board) -> Decimal:
    """涨跌幅比例：主板 10%（含风险警示股），创业板与科创板 20%。"""
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

    主板、创业板：买入 100 股整数倍；卖出时不足 100 股的零股须一次性卖出。
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
