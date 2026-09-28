"""依赖装配：按配置选择数据源并构造全部服务。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from ..clock import Clock, SystemClock
from ..config import Settings
from ..market.base import QuoteSource, ReferenceDataSource
from ..market.mock_source import MockMarket
from ..market.reference import ReferenceService
from ..store.db import Database
from .accounts import AccountService
from .clock_monitor import ClockMonitor
from .delivery import DeliveryService
from .engine import Engine
from .events import AlertService, EventBus, EventService
from .execution import Execution
from .market_status import MarketStatusService, QuoteCache, SourceHealth
from .orders import OrderService, RuntimeState
from .portfolio import PortfolioService
from .settlement import SettlementService


@dataclass
class Container:
    settings: Settings
    clock: Clock
    db: Database
    bus: EventBus
    state: RuntimeState
    quote_source: QuoteSource
    reference_source: ReferenceDataSource
    reference: ReferenceService
    quotes: QuoteCache
    quote_health: SourceHealth
    events: EventService
    alerts: AlertService
    execution: Execution
    accounts: AccountService
    orders: OrderService
    engine: Engine
    settlement: SettlementService
    portfolio: PortfolioService
    market_status: MarketStatusService
    delivery: DeliveryService
    clock_monitor: ClockMonitor
    mock: MockMarket | None


def build_sources(
    settings: Settings,
    clock: Clock,
    transport: httpx.BaseTransport | None = None,
    on_server_time: Callable[[datetime], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> tuple[QuoteSource, ReferenceDataSource, Any]:
    if settings.market == "mock":
        m = MockMarket(clock)
        return m, m, m
    if settings.market == "tsp":
        from ..market.tsp_source import build_tsp_sources

        quote, ref = build_tsp_sources(settings, clock, transport, on_server_time, sleep)
        return quote, ref, None
    raise RuntimeError(f"未知的 DEMO_ACCOUNT_MARKET: {settings.market}")


def build_container(
    settings: Settings,
    clock: Clock | None = None,
    market: Any | None = None,
    transport: httpx.BaseTransport | None = None,
    market_transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> Container:
    """transport 用于 Webhook 投递，market_transport 用于行情与基础数据源（测试注入）。"""
    clock = clock or SystemClock()
    db = Database(settings.db_path)
    db.migrate()
    state = RuntimeState()
    monitor = ClockMonitor(clock, state)
    if market is not None:
        quote_source, reference_source, mock = market, market, market
    else:
        quote_source, reference_source, mock = build_sources(
            settings, clock, market_transport, monitor.observe, sleep
        )
    bus = EventBus()
    reference = ReferenceService(db, reference_source, clock, settings)
    quotes = QuoteCache()
    quote_health = SourceHealth(quote_source.name)
    events = EventService(bus, clock)
    alerts = AlertService(db, bus, clock)
    monitor.alerts = alerts
    execution = Execution(clock, events, alerts, settings)
    accounts = AccountService(db, clock, events, execution, settings)
    orders = OrderService(db, clock, reference, quotes, events, execution, settings, state)
    engine = Engine(
        db, clock, reference, quote_source, quotes, quote_health, execution, alerts, settings
    )
    settlement = SettlementService(db, clock, reference, execution, events, alerts, settings, state)
    portfolio = PortfolioService(db, clock, reference, quotes)
    market_status = MarketStatusService(db, clock, reference, quotes, quote_health)
    delivery = DeliveryService(db, clock, alerts, settings, transport)
    return Container(
        settings=settings,
        clock=clock,
        db=db,
        bus=bus,
        state=state,
        quote_source=quote_source,
        reference_source=reference_source,
        reference=reference,
        quotes=quotes,
        quote_health=quote_health,
        events=events,
        alerts=alerts,
        execution=execution,
        accounts=accounts,
        orders=orders,
        engine=engine,
        settlement=settlement,
        portfolio=portfolio,
        market_status=market_status,
        delivery=delivery,
        clock_monitor=monitor,
        mock=mock if isinstance(mock, MockMarket) else None,
    )
