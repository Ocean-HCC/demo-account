"""时间、日期与金额的序列化约定：ISO 8601 带 +08:00，日期 YYYY-MM-DD，Decimal 存为字符串。"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .core.rules import to_beijing


def iso(dt: datetime) -> str:
    return to_beijing(dt).isoformat(timespec="seconds")


def parse_iso(s: str) -> datetime:
    return to_beijing(datetime.fromisoformat(s))


def d2s(d: date) -> str:
    return d.isoformat()


def s2d(s: str) -> date:
    return date.fromisoformat(s)


def dec(s: str | None) -> Decimal | None:
    return None if s is None else Decimal(s)


def dec0(s: str | None) -> Decimal:
    return Decimal(s) if s is not None else Decimal("0")


def sdec(x: Decimal | None) -> str | None:
    return None if x is None else str(x)


def jsonable(value: Any) -> Any:
    """把 Decimal、日期、时间、枚举递归转成 JSON 可表示的值；Decimal 一律转字符串。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return d2s(value)
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    if hasattr(value, "value") and isinstance(getattr(value, "value", None), str):
        return value.value
    return value


def jdict(value: dict[str, Any]) -> dict[str, Any]:
    """jsonable 的字典版本，保留类型信息。"""
    result: dict[str, Any] = jsonable(value)
    return result
