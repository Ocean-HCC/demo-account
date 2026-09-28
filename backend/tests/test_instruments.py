from decimal import Decimal

import pytest

from demo_account.core.instruments import (
    ST_DAILY_BUY_CAP,
    SymbolError,
    board_of,
    is_st,
    limit_rate,
    max_order_qty,
    new_listing_no_limit,
    parse_symbol,
    price_limits,
    round_lot_down,
    st_order_allowed,
    validate_qty,
)
from demo_account.core.models import AssetType, Board, Exchange, OrderType, Side
from demo_account.core.money import TICK_ETF, TICK_STOCK


def test_parse_symbol() -> None:
    assert parse_symbol("600000.SH") == ("600000", Exchange.SH)
    assert parse_symbol("000001.SZ") == ("000001", Exchange.SZ)
    for bad in ("600000", "sh600000", "600000.sh", "430047.BJ", "920519.BJ", "60000.SH"):
        with pytest.raises(SymbolError):
            parse_symbol(bad)


def test_board_of() -> None:
    assert board_of("600000.SH", AssetType.STOCK) is Board.MAIN
    assert board_of("000001.SZ", AssetType.STOCK) is Board.MAIN
    assert board_of("300750.SZ", AssetType.STOCK) is Board.CHINEXT
    assert board_of("301001.SZ", AssetType.STOCK) is Board.CHINEXT
    assert board_of("688981.SH", AssetType.STOCK) is Board.STAR
    assert board_of("689009.SH", AssetType.STOCK) is Board.STAR
    assert board_of("588000.SH", AssetType.ETF) is Board.MAIN
    assert board_of("510300.SH", AssetType.ETF) is Board.MAIN


def test_is_st() -> None:
    assert is_st("*ST康佳A")
    assert is_st("ST华微")
    assert is_st("st 测试")
    assert not is_st("浦发银行")
    assert not is_st("沪深300ETF华泰柏瑞")


def test_limit_rate() -> None:
    assert limit_rate(Board.MAIN, AssetType.STOCK) == Decimal("0.10")
    assert limit_rate(Board.CHINEXT, AssetType.STOCK) == Decimal("0.20")
    assert limit_rate(Board.STAR, AssetType.STOCK) == Decimal("0.20")
    assert limit_rate(Board.MAIN, AssetType.ETF) == Decimal("0.10")
    assert limit_rate(Board.MAIN, AssetType.ETF, etf_20pct=True) == Decimal("0.20")


def test_new_listing_no_limit() -> None:
    assert new_listing_no_limit(1)
    assert new_listing_no_limit(5)
    assert not new_listing_no_limit(6)
    assert not new_listing_no_limit(None)


def test_price_limits_main_board_matches_exchange() -> None:
    # 浦发银行 2026-09-24 前收 8.98，交易所公布涨停 9.88、跌停 8.08
    lim = price_limits(Decimal("8.98"), Decimal("0.10"), TICK_STOCK)
    assert lim.up == Decimal("9.88")
    assert lim.down == Decimal("8.08")


def test_price_limits_20pct_and_etf() -> None:
    lim = price_limits(Decimal("100"), Decimal("0.20"), TICK_STOCK)
    assert (lim.up, lim.down) == (Decimal("120.00"), Decimal("80.00"))
    lim = price_limits(Decimal("4.578"), Decimal("0.10"), TICK_ETF)
    assert (lim.up, lim.down) == (Decimal("5.036"), Decimal("4.120"))


def test_price_limits_tiny_price_rules() -> None:
    # 差额不足一个最小变动单位时按一个单位；下限不低于一个单位
    lim = price_limits(Decimal("0.05"), Decimal("0.10"), TICK_STOCK)
    assert lim.up == Decimal("0.06")
    assert lim.down == Decimal("0.04")
    lim = price_limits(Decimal("0.01"), Decimal("0.10"), TICK_STOCK)
    assert lim.up == Decimal("0.02")
    assert lim.down == Decimal("0.01")


def test_max_order_qty() -> None:
    assert max_order_qty(Board.MAIN, OrderType.LIMIT) == 1_000_000
    assert max_order_qty(Board.MAIN, OrderType.MARKET) == 1_000_000
    assert max_order_qty(Board.CHINEXT, OrderType.LIMIT) == 300_000
    assert max_order_qty(Board.CHINEXT, OrderType.MARKET) == 150_000
    assert max_order_qty(Board.CHINEXT, OrderType.OPEN) == 150_000
    assert max_order_qty(Board.STAR, OrderType.LIMIT) == 100_000
    assert max_order_qty(Board.STAR, OrderType.MARKET) == 50_000
    assert max_order_qty(Board.STAR, OrderType.CLOSE) == 1_000_000


def test_validate_qty_main_board() -> None:
    assert validate_qty(100, Side.BUY, Board.MAIN, OrderType.MARKET) is None
    assert validate_qty(150, Side.BUY, Board.MAIN, OrderType.MARKET) == "LOT_SIZE"
    assert validate_qty(0, Side.BUY, Board.MAIN, OrderType.MARKET) == "LOT_SIZE"
    assert validate_qty(1_000_100, Side.BUY, Board.MAIN, OrderType.LIMIT) == "QTY_LIMIT"
    # 卖出：零股一次性卖出
    assert validate_qty(150, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=150) is None
    assert validate_qty(50, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=150) is None
    assert validate_qty(100, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=150) is None
    assert (
        validate_qty(130, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=150) == "LOT_SIZE"
    )
    # 持仓 250：卖 150 = 100 整手 + 50 零股一次性卖出，允许；
    # 卖 170 的零股部分 70 与持仓零股不符，拒绝
    assert validate_qty(150, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=250) is None
    assert (
        validate_qty(170, Side.SELL, Board.MAIN, OrderType.MARKET, position_qty=250) == "LOT_SIZE"
    )
    assert validate_qty(50, Side.SELL, Board.MAIN, OrderType.MARKET) == "LOT_SIZE"


def test_validate_qty_star_board() -> None:
    assert validate_qty(200, Side.BUY, Board.STAR, OrderType.LIMIT) is None
    assert validate_qty(201, Side.BUY, Board.STAR, OrderType.LIMIT) is None
    assert validate_qty(199, Side.BUY, Board.STAR, OrderType.LIMIT) == "LOT_SIZE"
    assert validate_qty(50_001, Side.BUY, Board.STAR, OrderType.MARKET) == "QTY_LIMIT"
    assert validate_qty(100_000, Side.BUY, Board.STAR, OrderType.LIMIT) is None
    assert validate_qty(250, Side.SELL, Board.STAR, OrderType.LIMIT, position_qty=250) is None
    assert validate_qty(50, Side.SELL, Board.STAR, OrderType.LIMIT, position_qty=250) is None
    assert validate_qty(150, Side.SELL, Board.STAR, OrderType.LIMIT, position_qty=150) is None
    assert validate_qty(30, Side.SELL, Board.STAR, OrderType.LIMIT, position_qty=250) == "LOT_SIZE"


def test_round_lot_down() -> None:
    assert round_lot_down(1111, Board.MAIN) == 1100
    assert round_lot_down(99, Board.MAIN) == 0
    assert round_lot_down(250, Board.STAR) == 250
    assert round_lot_down(199, Board.STAR) == 0
    assert round_lot_down(-5, Board.MAIN) == 0


def test_st_order_allowed() -> None:
    assert st_order_allowed(OrderType.LIMIT, None)
    assert st_order_allowed(OrderType.CLOSE, Decimal("5.00"))
    assert not st_order_allowed(OrderType.CLOSE, None)
    assert not st_order_allowed(OrderType.MARKET, None)
    assert not st_order_allowed(OrderType.OPEN, None)
    assert ST_DAILY_BUY_CAP == 500_000
