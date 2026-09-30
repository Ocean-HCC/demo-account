"""费用计算（方案 6.4）：佣金双向含最低收费；印花税只在卖出；过户费双向。"""

from __future__ import annotations

from decimal import Decimal

from .models import FeeParams, Fees, Side
from .money import ZERO, D, round_cent

STAMP_TAX_RATE = Decimal("0.0005")  # 2023-08-28 起卖出单边 0.05%
TRANSFER_FEE_RATE = Decimal("0.00001")  # 2022-04-29 起双向 0.001%


def compute_fees(
    gross_amount: Decimal | int | str,
    side: Side,
    fee_params: FeeParams,
) -> Fees:
    gross = D(gross_amount)
    commission = round_cent(gross * fee_params.commission_rate)
    if commission < fee_params.min_commission:
        commission = round_cent(fee_params.min_commission)
    stamp_tax = round_cent(gross * STAMP_TAX_RATE) if side is Side.SELL else ZERO
    transfer_fee = round_cent(gross * TRANSFER_FEE_RATE)
    return Fees(commission=commission, stamp_tax=stamp_tax, transfer_fee=transfer_fee)
