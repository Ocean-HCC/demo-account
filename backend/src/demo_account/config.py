"""运行配置（实现 8.2）。全部来自环境变量，未设置时用默认值。"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import time
from decimal import Decimal
from pathlib import Path


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    host: str
    port: int
    api_key: str | None
    market: str  # tsp 或 mock
    tsp_base_url: str
    tsp_password: str | None
    http_trust_env: bool
    snapshot_interval: int
    snapshot_max_age: int
    settle_time: time
    instant_order_timeout: int
    max_defer_days: int
    default_initial_cash: Decimal
    min_initial_cash: Decimal
    default_commission_rate: Decimal
    default_min_commission: Decimal
    default_slippage_rate: Decimal
    default_block_st: bool
    benchmark_code: str
    webhook_max_attempts: int
    scheduler_enabled: bool
    frontend_dist: Path | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = dict(os.environ if env is None else env)

        def get(name: str, default: str) -> str:
            v = e.get(name)
            return default if v is None or v == "" else v

        hh, mm = get("DEMO_ACCOUNT_SETTLE_TIME", "16:00").split(":")
        dist = e.get("DEMO_ACCOUNT_FRONTEND_DIST")
        return cls(
            db_path=Path(get("DEMO_ACCOUNT_DB_PATH", "data/demo_account.sqlite3")),
            host=get("DEMO_ACCOUNT_HOST", "127.0.0.1"),
            port=int(get("DEMO_ACCOUNT_PORT", "8770")),
            api_key=e.get("DEMO_ACCOUNT_API_KEY") or None,
            market=get("DEMO_ACCOUNT_MARKET", "tsp"),
            tsp_base_url=get("DEMO_ACCOUNT_TSP_BASE_URL", "http://127.0.0.1:3018"),
            tsp_password=e.get("DEMO_ACCOUNT_TSP_PASSWORD") or None,
            http_trust_env=_bool(e.get("DEMO_ACCOUNT_HTTP_TRUST_ENV"), False),
            snapshot_interval=int(get("DEMO_ACCOUNT_SNAPSHOT_INTERVAL", "60")),
            snapshot_max_age=int(get("DEMO_ACCOUNT_SNAPSHOT_MAX_AGE", "180")),
            settle_time=time(int(hh), int(mm)),
            instant_order_timeout=int(get("DEMO_ACCOUNT_INSTANT_ORDER_TIMEOUT", "180")),
            max_defer_days=int(get("DEMO_ACCOUNT_MAX_DEFER_DAYS", "3")),
            default_initial_cash=Decimal(get("DEMO_ACCOUNT_DEFAULT_INITIAL_CASH", "1000000")),
            min_initial_cash=Decimal(get("DEMO_ACCOUNT_MIN_INITIAL_CASH", "10000")),
            default_commission_rate=Decimal(get("DEMO_ACCOUNT_DEFAULT_COMMISSION_RATE", "0.00013")),
            default_min_commission=Decimal(get("DEMO_ACCOUNT_DEFAULT_MIN_COMMISSION", "5")),
            default_slippage_rate=Decimal(get("DEMO_ACCOUNT_DEFAULT_SLIPPAGE_RATE", "0.0005")),
            default_block_st=_bool(e.get("DEMO_ACCOUNT_DEFAULT_BLOCK_ST"), True),
            benchmark_code=get("DEMO_ACCOUNT_BENCHMARK", "000300.SH"),
            webhook_max_attempts=int(get("DEMO_ACCOUNT_WEBHOOK_MAX_ATTEMPTS", "5")),
            scheduler_enabled=_bool(e.get("DEMO_ACCOUNT_SCHEDULER"), True),
            frontend_dist=Path(dist) if dist else None,
        )
