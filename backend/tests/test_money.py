from decimal import Decimal

from demo_account.core.money import (
    TICK,
    D,
    is_on_tick,
    round_cent,
    round_down_to_tick,
    round_to_tick,
    round_up_to_tick,
)


def test_decimal_from_float_uses_repr() -> None:
    assert D(9.0) == Decimal("9.0")
    assert D(0.1) == Decimal("0.1")
    assert D("8.98") == Decimal("8.98")
    assert D(3) == Decimal("3")


def test_round_cent_half_up() -> None:
    assert round_cent(Decimal("0.005")) == Decimal("0.01")
    assert round_cent(Decimal("0.015")) == Decimal("0.02")
    assert round_cent(Decimal("1.004")) == Decimal("1.00")
    assert round_cent("12.345") == Decimal("12.35")


def test_tick_is_one_cent() -> None:
    assert Decimal("0.01") == TICK


def test_round_to_tick_directions() -> None:
    assert round_to_tick(Decimal("9.005"), TICK) == Decimal("9.01")
    assert round_to_tick(Decimal("9.004"), TICK) == Decimal("9.00")
    assert round_up_to_tick(Decimal("9.001"), TICK) == Decimal("9.01")
    assert round_down_to_tick(Decimal("9.009"), TICK) == Decimal("9.00")


def test_round_to_tick_keeps_exponent() -> None:
    assert str(round_to_tick(Decimal("9"), TICK)) == "9.00"


def test_is_on_tick() -> None:
    assert is_on_tick(Decimal("9.01"), TICK)
    assert not is_on_tick(Decimal("9.015"), TICK)
