"""编号生成：账户 acc_ 加 8 位十六进制；订单 ord_ 加时间前缀与 6 位随机。"""

from __future__ import annotations

import secrets
import string
from datetime import datetime

from ..core.rules import to_beijing

_ALNUM = string.ascii_lowercase + string.digits


def new_account_id() -> str:
    return "acc_" + secrets.token_hex(4)


def new_order_id(now: datetime) -> str:
    stamp = to_beijing(now).strftime("%Y%m%d%H%M%S")
    return "ord_" + stamp + "".join(secrets.choice(_ALNUM) for _ in range(6))
