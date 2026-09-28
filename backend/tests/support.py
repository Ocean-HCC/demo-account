"""M2 起的服务与接口测试公共设施：假时钟、Mock 行情、临时数据库。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from demo_account.clock import FakeClock
from demo_account.config import Settings
from demo_account.core.models import Fill, OrderType, PriceLimits, Side
from demo_account.core.rules import BEIJING
from demo_account.market.mock_source import MockMarket
from demo_account.services.container import Container, build_container
from demo_account.services.orders import OrderRequest
from demo_account.store import repos
from demo_account.store.records import Account, Event, Order, Position

THU = date(2026, 9, 24)
FRI = date(2026, 9, 25)
MON = date(2026, 9, 28)
TUE = date(2026, 9, 29)
WED = date(2026, 9, 30)


def at(d: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BEIJING)


def make_settings(tmp: Path, **extra: str) -> Settings:
    env = {
        "DEMO_ACCOUNT_DB_PATH": str(tmp / "test.sqlite3"),
        "DEMO_ACCOUNT_MARKET": "mock",
        "DEMO_ACCOUNT_SCHEDULER": "false",
    }
    env.update(extra)
    return Settings.from_env(env)


@dataclass
class Env:
    c: Container
    clock: FakeClock
    mock: MockMarket

    def go(self, d: date, h: int, m: int, s: int = 0) -> None:
        self.clock.set(at(d, h, m, s))

    def account(self, name: str = "策略A", **kw: Any) -> Account:
        return self.c.accounts.create(name, **kw)

    def today(self) -> date:
        return self.clock.now().date()

    def prev_close(self, symbol: str) -> Decimal:
        d = self.today()
        v = self.c.reference.reference_prev_close(symbol, d, d)
        assert v is not None
        return v

    def limits(self, symbol: str, d: date | None = None) -> PriceLimits:
        return self.c.reference.price_limits(symbol, d or self.today(), today=self.today())

    def order(
        self,
        acc: Account,
        symbol: str,
        side: Side,
        qty: int | None = None,
        order_type: OrderType = OrderType.MARKET,
        **kw: Any,
    ) -> Order:
        req = OrderRequest(symbol=symbol, side=side, order_type=order_type, qty=qty, **kw)
        return self.c.orders.submit(acc.id, req)

    def buy(self, acc: Account, symbol: str, qty: int | None = None, **kw: Any) -> Order:
        return self.order(acc, symbol, Side.BUY, qty, **kw)

    def sell(self, acc: Account, symbol: str, qty: int | None = None, **kw: Any) -> Order:
        return self.order(acc, symbol, Side.SELL, qty, **kw)

    def cycle(self) -> dict[str, int]:
        self.clock.advance(1)
        return self.c.engine.run_intraday_cycle()

    def settle(self, d: date) -> Any:
        self.go(d, 16, 0)
        return self.c.settlement.settle(d)

    def get(self, acc: Account, order: Order) -> Order:
        return self.c.orders.get(acc.id, order.id)

    def events(self, acc: Account) -> list[Event]:
        return repos.list_events(self.c.db.read(), acc.id, limit=1000)

    def types(self, acc: Account) -> list[str]:
        return [e.type.value for e in self.events(acc)]

    def position(self, acc: Account, symbol: str) -> Position | None:
        return repos.get_position(self.c.db.read(), acc.id, symbol)

    def cash(self, acc: Account) -> Decimal:
        return repos.account_cash(self.c.db.read(), acc.id, acc.initial_cash)

    def fills(self, acc: Account) -> list[Fill]:
        return repos.all_fills(self.c.db.read(), acc.id)

    def alert_codes(self) -> list[str]:
        return [a.code for a in repos.list_alerts(self.c.db.read(), unresolved_only=False)]


def make_env(tmp: Path, start: datetime | None = None, seed: int = 7, **extra: str) -> Env:
    clock = FakeClock(start or at(THU, 8, 0))
    mock = MockMarket(clock, seed=seed)
    c = build_container(make_settings(tmp, **extra), clock=clock, market=mock)
    c.reference.ensure_calendar(clock.now().date())
    return Env(c, clock, mock)
