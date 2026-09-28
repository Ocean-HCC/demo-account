"""用假时钟和 Mock 行情生成演示历史，供控制台体验（README 1.1）。

两个账户按固定规则买卖，每个交易日收盘结算。Mock 行情的种子与服务端 Mock 模式一致，
因此服务启动后看到的价格与这里生成的历史连续。默认生成到昨天为止，今天留给实时操作。

用法：
    uv run python scripts/seed_mock.py [--db data/mock.sqlite3] [--start YYYY-MM-DD]
        [--end YYYY-MM-DD] [--force]
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path

from demo_account.clock import FakeClock
from demo_account.config import Settings
from demo_account.core.models import OrderType, Side
from demo_account.core.rules import BEIJING
from demo_account.market.mock_source import MockMarket
from demo_account.services.container import build_container
from demo_account.services.orders import OrderRequest
from demo_account.store import repos
from demo_account.store.records import Account

REPO_ROOT = Path(__file__).resolve().parents[2]


def at(d: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=BEIJING)


def main() -> int:
    today = datetime.now(tz=BEIJING).date()
    p = argparse.ArgumentParser(description="生成 Mock 演示历史")
    p.add_argument("--db", default="data/mock.sqlite3", help="数据库文件，相对路径按仓库根目录解析")
    p.add_argument("--start", default=(today - timedelta(days=28)).isoformat())
    p.add_argument("--end", default=(today - timedelta(days=1)).isoformat())
    p.add_argument("--force", action="store_true", help="数据库已存在时覆盖")
    args = p.parse_args()

    db = Path(args.db)
    if not db.is_absolute():
        db = REPO_ROOT / db
    if db.exists() and not args.force:
        print(f"{db} 已存在，加 --force 覆盖")
        return 1
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db) + suffix)
        if f.exists():
            f.unlink()

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    clock = FakeClock(at(start, 8, 0))
    settings = Settings.from_env(
        {
            "DEMO_ACCOUNT_DB_PATH": str(db),
            "DEMO_ACCOUNT_MARKET": "mock",
            "DEMO_ACCOUNT_SCHEDULER": "false",
        }
    )
    c = build_container(settings, clock=clock, market=MockMarket(clock))
    c.reference.ensure_calendar(start)
    c.reference.ensure_calendar(end)
    a = c.accounts.create("均线策略 A", note="均线金叉买入、死叉卖出（Mock 演示）")
    b = c.accounts.create("动量策略 B", note="强势股轮动加沪深 300 ETF 底仓（Mock 演示）")

    def order(
        acc: Account, sym: str, side: Side, qty: int, ot: OrderType = OrderType.MARKET, **kw: object
    ) -> None:
        c.orders.submit(acc.id, OrderRequest(sym, side, ot, qty=qty, **kw))  # type: ignore[arg-type]

    def sellable(acc: Account, sym: str) -> int:
        pos = repos.get_position(c.db.read(), acc.id, sym)
        return pos.sellable_qty if pos else 0

    d, i = start, 0
    while d <= end:
        if not c.reference.calendar().is_trading_day(d):
            d += timedelta(days=1)
            continue
        clock.set(at(d, 9, 35))
        if i % 4 == 0:
            order(a, "600000.SH", Side.BUY, 2000, note="均线金叉", tags=["ma-cross"])
        elif i % 4 == 2 and sellable(a, "600000.SH"):
            order(
                a,
                "600000.SH",
                Side.SELL,
                sellable(a, "600000.SH"),
                note="均线死叉",
                tags=["ma-cross"],
            )
        if i == 0:
            order(b, "510300.SH", Side.BUY, 20000, note="ETF 底仓", tags=["base"])
        if i % 3 == 0:
            order(b, "300750.SZ", Side.BUY, 200, note="动量突破", tags=["momentum"])
        elif i % 3 == 2 and sellable(b, "300750.SZ"):
            order(
                b,
                "300750.SZ",
                Side.SELL,
                sellable(b, "300750.SZ"),
                note="动量衰减",
                tags=["momentum"],
            )
        clock.set(at(d, 9, 35, 2))
        c.engine.run_intraday_cycle()
        if i % 5 == 1:
            pc = c.reference.reference_prev_close("000001.SZ", d, d)
            order(
                b,
                "000001.SZ",
                Side.BUY,
                1000,
                OrderType.LIMIT,
                limit_price=pc,
                note="回踩前收挂单",
                tags=["limit"],
            )
        clock.set(at(d, 13, 30))
        c.engine.run_intraday_cycle()
        clock.set(at(d, 16, 0))
        run = c.settlement.settle(d)
        if run.status != "done":
            print(f"{d} 结算失败：{run.error}")
            return 1
        i += 1
        d += timedelta(days=1)

    for acc in (a, b):
        ov = c.portfolio.overview(acc)
        nav, total, n = ov["nav"], ov["total_assets"], ov["positions_count"]
        print(f"{acc.name}：净值 {nav}，总资产 {total}，持仓 {n} 只")
    print(f"共结算 {i} 个交易日，数据库 {db}")
    c.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
