"""领域核心的枚举与数据类型。纯数据，不含 IO。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderType(StrEnum):
    MARKET = "market"  # 即时市价单
    LIMIT = "limit"  # 限价单
    OPEN = "open"  # 开盘单
    CLOSE = "close"  # 收盘单，对应盘后固定价格交易


class OrderStatus(StrEnum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    ARCHIVED = "archived"


class AssetType(StrEnum):
    STOCK = "stock"
    ETF = "etf"


class Board(StrEnum):
    MAIN = "main"  # 主板，ETF 也按主板规则
    CHINEXT = "chinext"  # 创业板
    STAR = "star"  # 科创板


class Exchange(StrEnum):
    SH = "SH"
    SZ = "SZ"


class Session(StrEnum):
    PRE_OPEN = "pre_open"
    OPENING_AUCTION = "opening_auction"
    CONTINUOUS = "continuous"
    LUNCH = "lunch"
    CLOSING_AUCTION = "closing_auction"
    AFTER_HOURS = "after_hours"  # 盘后固定价格交易 15:05 至 15:30
    CLOSED = "closed"


class FillKind(StrEnum):
    TRADE = "trade"
    CORPORATE_ACTION = "corporate_action"
    REVERSAL = "reversal"


class EventType(StrEnum):
    ORDER_SUBMITTED = "order_submitted"
    ORDER_REJECTED = "order_rejected"
    ORDER_FILLED = "order_filled"
    ORDER_CANCELLED = "order_cancelled"
    ORDER_EXPIRED = "order_expired"
    SETTLEMENT_DONE = "settlement_done"
    CORPORATE_ACTION = "corporate_action"
    REVERSAL = "reversal"
    ACCOUNT_CREATED = "account_created"
    ACCOUNT_FROZEN = "account_frozen"
    ACCOUNT_UNFROZEN = "account_unfrozen"
    ACCOUNT_ARCHIVED = "account_archived"
    FEE_PARAMS_CHANGED = "fee_params_changed"


# 方案 3.5：主动推送给调用方的事件类型
NOTIFY_EVENT_TYPES: frozenset[EventType] = frozenset(
    {
        EventType.ORDER_FILLED,
        EventType.ORDER_REJECTED,
        EventType.ORDER_EXPIRED,
        EventType.ORDER_CANCELLED,
        EventType.SETTLEMENT_DONE,
    }
)


@dataclass(frozen=True)
class FeeParams:
    """账户级费用参数。"""

    commission_rate: Decimal
    min_commission: Decimal
    slippage_rate: Decimal


@dataclass(frozen=True)
class Fees:
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal

    @property
    def total(self) -> Decimal:
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass(frozen=True)
class PriceLimits:
    """涨跌停价，None 表示不设涨跌幅限制。"""

    up: Decimal | None
    down: Decimal | None


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str
    asset_type: AssetType
    board: Board
    exchange: Exchange
    list_date: date | None
    is_st: bool


@dataclass(frozen=True)
class Snapshot:
    """一笔行情快照。价格已按最小变动单位取整。"""

    symbol: str
    ts: datetime
    last: Decimal
    open: Decimal
    high: Decimal
    low: Decimal
    prev_close: Decimal
    halted: bool
    up_limit: Decimal | None
    down_limit: Decimal | None
    source: str


@dataclass(frozen=True)
class Fill:
    """成交台账的一条记录。

    kind 为 trade 时 side/qty/price/gross_amount/费用/cash_delta 都是实际成交口径；
    kind 为 corporate_action 时 qty 为数量变动（side 表方向），cash_delta 为现金变动，
    gross_amount 为 0；
    kind 为 reversal 时 qty、cash_delta 为显式增量，gross_amount 为成本总额的增量绝对值。
    """

    seq: int | None
    account_id: str
    order_id: str | None
    symbol: str
    kind: FillKind
    side: Side
    qty: int
    price: Decimal
    gross_amount: Decimal
    commission: Decimal
    stamp_tax: Decimal
    transfer_fee: Decimal
    cash_delta: Decimal
    trade_date: date
    occurred_at: datetime
    note: str = ""

    @property
    def fees(self) -> Decimal:
        return self.commission + self.stamp_tax + self.transfer_fee
