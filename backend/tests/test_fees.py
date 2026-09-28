from decimal import Decimal

from demo_account.core.fees import compute_fees
from demo_account.core.models import AssetType, FeeParams, Side


def test_commission_minimum_and_rate(fee_params: FeeParams) -> None:
    fees = compute_fees(Decimal("10000"), Side.BUY, AssetType.STOCK, fee_params)
    assert fees.commission == Decimal("5.00")  # 1.30 元不足最低 5 元
    fees = compute_fees(Decimal("100000"), Side.BUY, AssetType.STOCK, fee_params)
    assert fees.commission == Decimal("13.00")


def test_stamp_tax_only_on_stock_sell(fee_params: FeeParams) -> None:
    buy = compute_fees(Decimal("100000"), Side.BUY, AssetType.STOCK, fee_params)
    sell = compute_fees(Decimal("100000"), Side.SELL, AssetType.STOCK, fee_params)
    assert buy.stamp_tax == Decimal("0")
    assert sell.stamp_tax == Decimal("50.00")
    etf_sell = compute_fees(Decimal("100000"), Side.SELL, AssetType.ETF, fee_params)
    assert etf_sell.stamp_tax == Decimal("0")


def test_transfer_fee_stock_both_sides_etf_free(fee_params: FeeParams) -> None:
    buy = compute_fees(Decimal("100000"), Side.BUY, AssetType.STOCK, fee_params)
    sell = compute_fees(Decimal("100000"), Side.SELL, AssetType.STOCK, fee_params)
    assert buy.transfer_fee == Decimal("1.00")
    assert sell.transfer_fee == Decimal("1.00")
    etf = compute_fees(Decimal("100000"), Side.BUY, AssetType.ETF, fee_params)
    assert etf.transfer_fee == Decimal("0")
    assert etf.total == Decimal("13.00")


def test_fee_rounding_to_cent(fee_params: FeeParams) -> None:
    fees = compute_fees(Decimal("12345.67"), Side.SELL, AssetType.STOCK, fee_params)
    assert fees.commission == Decimal("5.00")
    assert fees.stamp_tax == Decimal("6.17")  # 6.172835
    assert fees.transfer_fee == Decimal("0.12")  # 0.1234567
    assert fees.total == Decimal("11.29")
