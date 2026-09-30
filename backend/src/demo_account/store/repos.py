"""各表的读写。SQL 只出现在这里，其他模块不写 SQL。"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..core.models import (
    AccountStatus,
    Board,
    EventType,
    Exchange,
    FeeParams,
    Fill,
    FillKind,
    Instrument,
    OrderStatus,
    OrderType,
    Side,
)
from ..market.base import CorporateAction, DailyBar, Suspension
from ..timeutil import d2s, dec, dec0, iso, parse_iso, s2d, sdec
from .db import Executor
from .records import Account, Alert, Event, NavDaily, Order, Position, SettlementRun


def _sum_dec(rows: Sequence[sqlite3.Row], col: str) -> Decimal:
    total = Decimal("0")
    for r in rows:
        total += Decimal(r[col])
    return total


# ---------------------------------------------------------------- accounts


def _row_account(r: sqlite3.Row) -> Account:
    return Account(
        id=r["id"],
        name=r["name"],
        note=r["note"],
        initial_cash=Decimal(r["initial_cash"]),
        fee_params=FeeParams(
            commission_rate=Decimal(r["commission_rate"]),
            min_commission=Decimal(r["min_commission"]),
            slippage_rate=Decimal(r["slippage_rate"]),
        ),
        block_st=bool(r["block_st"]),
        status=AccountStatus(r["status"]),
        webhook_url=r["webhook_url"],
        webhook_secret=r["webhook_secret"],
        created_at=parse_iso(r["created_at"]),
        updated_at=parse_iso(r["updated_at"]),
    )


def insert_account(ex: Executor, a: Account) -> None:
    ex.execute(
        "INSERT INTO accounts (id, name, note, initial_cash, commission_rate, min_commission,"
        " slippage_rate, block_st, status, webhook_url, webhook_secret, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            a.id,
            a.name,
            a.note,
            str(a.initial_cash),
            str(a.fee_params.commission_rate),
            str(a.fee_params.min_commission),
            str(a.fee_params.slippage_rate),
            int(a.block_st),
            a.status.value,
            a.webhook_url,
            a.webhook_secret,
            iso(a.created_at),
            iso(a.updated_at),
        ),
    )


def update_account(ex: Executor, a: Account) -> None:
    ex.execute(
        "UPDATE accounts SET name=?, note=?, commission_rate=?, min_commission=?, slippage_rate=?,"
        " block_st=?, status=?, webhook_url=?, webhook_secret=?, updated_at=? WHERE id=?",
        (
            a.name,
            a.note,
            str(a.fee_params.commission_rate),
            str(a.fee_params.min_commission),
            str(a.fee_params.slippage_rate),
            int(a.block_st),
            a.status.value,
            a.webhook_url,
            a.webhook_secret,
            iso(a.updated_at),
            a.id,
        ),
    )


def get_account(ex: Executor, account_id: str) -> Account | None:
    r = ex.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    return None if r is None else _row_account(r)


def list_accounts(ex: Executor, include_archived: bool = False) -> list[Account]:
    sql = "SELECT * FROM accounts"
    if not include_archived:
        sql += " WHERE status != 'archived'"
    sql += " ORDER BY created_at"
    return [_row_account(r) for r in ex.execute(sql).fetchall()]


def account_cash(ex: Executor, account_id: str, initial_cash: Decimal) -> Decimal:
    """现金 = 初始资金 + 全部台账现金变动之和（Decimal 在 Python 中求和，不用 SQL 的浮点 SUM）。"""
    rows = ex.execute("SELECT cash_delta FROM fills WHERE account_id=?", (account_id,)).fetchall()
    return initial_cash + _sum_dec(rows, "cash_delta")


# ---------------------------------------------------------------- orders


def _row_order(r: sqlite3.Row) -> Order:
    return Order(
        id=r["id"],
        account_id=r["account_id"],
        symbol=r["symbol"],
        side=Side(r["side"]),
        order_type=OrderType(r["order_type"]),
        qty=int(r["qty"]),
        amount=dec(r["amount"]),
        limit_price=dec(r["limit_price"]),
        protect_price=dec(r["protect_price"]),
        idempotency_key=r["idempotency_key"],
        note=r["note"],
        tags=list(json.loads(r["tags"])),
        trade_date=s2d(r["trade_date"]),
        defer_count=int(r["defer_count"]),
        status=OrderStatus(r["status"]),
        reason_code=r["reason_code"],
        reason=r["reason"],
        frozen_cash=Decimal(r["frozen_cash"]),
        frozen_qty=int(r["frozen_qty"]),
        created_at=parse_iso(r["created_at"]),
        finished_at=parse_iso(r["finished_at"]) if r["finished_at"] else None,
    )


def insert_order(ex: Executor, o: Order) -> None:
    ex.execute(
        "INSERT INTO orders (id, account_id, symbol, side, order_type, qty, amount, limit_price,"
        " protect_price, idempotency_key, note, tags, trade_date, defer_count, status, reason_code,"
        " reason, frozen_cash, frozen_qty, created_at, finished_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            o.id,
            o.account_id,
            o.symbol,
            o.side.value,
            o.order_type.value,
            o.qty,
            sdec(o.amount),
            sdec(o.limit_price),
            sdec(o.protect_price),
            o.idempotency_key,
            o.note,
            json.dumps(o.tags, ensure_ascii=False),
            d2s(o.trade_date),
            o.defer_count,
            o.status.value,
            o.reason_code,
            o.reason,
            str(o.frozen_cash),
            o.frozen_qty,
            iso(o.created_at),
            iso(o.finished_at) if o.finished_at else None,
        ),
    )


def update_order(ex: Executor, o: Order) -> None:
    ex.execute(
        "UPDATE orders SET trade_date=?, defer_count=?, status=?, reason_code=?, reason=?,"
        " frozen_cash=?, frozen_qty=?, finished_at=? WHERE id=?",
        (
            d2s(o.trade_date),
            o.defer_count,
            o.status.value,
            o.reason_code,
            o.reason,
            str(o.frozen_cash),
            o.frozen_qty,
            iso(o.finished_at) if o.finished_at else None,
            o.id,
        ),
    )


def get_order(ex: Executor, account_id: str, order_id: str) -> Order | None:
    r = ex.execute(
        "SELECT * FROM orders WHERE id=? AND account_id=?", (order_id, account_id)
    ).fetchone()
    return None if r is None else _row_order(r)


def get_order_by_idem(ex: Executor, account_id: str, key: str) -> Order | None:
    r = ex.execute(
        "SELECT * FROM orders WHERE account_id=? AND idempotency_key=?", (account_id, key)
    ).fetchone()
    return None if r is None else _row_order(r)


def list_orders(
    ex: Executor,
    account_id: str,
    status: OrderStatus | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Order]:
    sql = "SELECT * FROM orders WHERE account_id=?"
    params: list[Any] = [account_id]
    if status is not None:
        sql += " AND status=?"
        params.append(status.value)
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol)
    if since is not None:
        sql += " AND created_at>=?"
        params.append(iso(since))
    if until is not None:
        sql += " AND created_at<=?"
        params.append(iso(until))
    sql += " ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_order(r) for r in ex.execute(sql, params).fetchall()]


def pending_orders(
    ex: Executor,
    order_types: Sequence[OrderType] | None = None,
    trade_date: date | None = None,
    trade_date_upto: date | None = None,
    account_id: str | None = None,
) -> list[Order]:
    sql = "SELECT * FROM orders WHERE status='pending'"
    params: list[Any] = []
    if order_types:
        marks = ",".join("?" for _ in order_types)
        sql += f" AND order_type IN ({marks})"
        params += [t.value for t in order_types]
    if trade_date is not None:
        sql += " AND trade_date=?"
        params.append(d2s(trade_date))
    if trade_date_upto is not None:
        sql += " AND trade_date<=?"
        params.append(d2s(trade_date_upto))
    if account_id is not None:
        sql += " AND account_id=?"
        params.append(account_id)
    sql += " ORDER BY created_at, id"
    return [_row_order(r) for r in ex.execute(sql, params).fetchall()]


def frozen_cash_total(
    ex: Executor, account_id: str, exclude_order_id: str | None = None
) -> Decimal:
    rows = ex.execute(
        "SELECT frozen_cash FROM orders WHERE account_id=? AND status='pending' AND id != ?",
        (account_id, exclude_order_id or ""),
    ).fetchall()
    return _sum_dec(rows, "frozen_cash")


def frozen_sell_qty(ex: Executor, account_id: str, symbol: str) -> int:
    r = ex.execute(
        "SELECT COALESCE(SUM(frozen_qty),0) AS q FROM orders WHERE account_id=? AND symbol=?"
        " AND status='pending' AND side='sell'",
        (account_id, symbol),
    ).fetchone()
    return int(r["q"])


def pending_buy_qty(ex: Executor, account_id: str, symbol: str) -> int:
    r = ex.execute(
        "SELECT COALESCE(SUM(qty),0) AS q FROM orders WHERE account_id=? AND symbol=?"
        " AND status='pending' AND side='buy'",
        (account_id, symbol),
    ).fetchone()
    return int(r["q"])


def pending_symbols(ex: Executor) -> list[str]:
    rows = ex.execute("SELECT DISTINCT symbol FROM orders WHERE status='pending'").fetchall()
    return [r["symbol"] for r in rows]


# ---------------------------------------------------------------- fills


def _row_fill(r: sqlite3.Row) -> Fill:
    return Fill(
        seq=int(r["seq"]),
        account_id=r["account_id"],
        order_id=r["order_id"],
        symbol=r["symbol"],
        kind=FillKind(r["kind"]),
        side=Side(r["side"]),
        qty=int(r["qty"]),
        price=Decimal(r["price"]),
        gross_amount=Decimal(r["gross_amount"]),
        commission=Decimal(r["commission"]),
        stamp_tax=Decimal(r["stamp_tax"]),
        transfer_fee=Decimal(r["transfer_fee"]),
        cash_delta=Decimal(r["cash_delta"]),
        trade_date=s2d(r["trade_date"]),
        occurred_at=parse_iso(r["occurred_at"]),
        note=r["note"],
    )


def fill_to_dict(f: Fill) -> dict[str, Any]:
    return {
        "seq": f.seq,
        "account_id": f.account_id,
        "order_id": f.order_id,
        "symbol": f.symbol,
        "kind": f.kind.value,
        "side": f.side.value,
        "qty": f.qty,
        "price": str(f.price),
        "gross_amount": str(f.gross_amount),
        "commission": str(f.commission),
        "stamp_tax": str(f.stamp_tax),
        "transfer_fee": str(f.transfer_fee),
        "cash_delta": str(f.cash_delta),
        "trade_date": d2s(f.trade_date),
        "occurred_at": iso(f.occurred_at),
        "note": f.note,
    }


def insert_fill(ex: Executor, f: Fill) -> int:
    cur = ex.execute(
        "INSERT INTO fills (account_id, order_id, symbol, kind, side, qty, price, gross_amount,"
        " commission, stamp_tax, transfer_fee, cash_delta, trade_date, occurred_at, note)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            f.account_id,
            f.order_id,
            f.symbol,
            f.kind.value,
            f.side.value,
            f.qty,
            str(f.price),
            str(f.gross_amount),
            str(f.commission),
            str(f.stamp_tax),
            str(f.transfer_fee),
            str(f.cash_delta),
            d2s(f.trade_date),
            iso(f.occurred_at),
            f.note,
        ),
    )
    return int(cur.lastrowid or 0)


def list_fills(
    ex: Executor,
    account_id: str,
    symbol: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Fill]:
    sql = "SELECT * FROM fills WHERE account_id=?"
    params: list[Any] = [account_id]
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol)
    if since is not None:
        sql += " AND occurred_at>=?"
        params.append(iso(since))
    if until is not None:
        sql += " AND occurred_at<=?"
        params.append(iso(until))
    sql += " ORDER BY seq DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    return [_row_fill(r) for r in ex.execute(sql, params).fetchall()]


def all_fills(ex: Executor, account_id: str, up_to: date | None = None) -> list[Fill]:
    sql = "SELECT * FROM fills WHERE account_id=?"
    params: list[Any] = [account_id]
    if up_to is not None:
        sql += " AND trade_date<=?"
        params.append(d2s(up_to))
    sql += " ORDER BY seq"
    return [_row_fill(r) for r in ex.execute(sql, params).fetchall()]


def bought_qty_on(ex: Executor, account_id: str, symbol: str, trade_date: date) -> int:
    r = ex.execute(
        "SELECT COALESCE(SUM(qty),0) AS q FROM fills WHERE account_id=? AND symbol=?"
        " AND trade_date=? AND kind='trade' AND side='buy'",
        (account_id, symbol, d2s(trade_date)),
    ).fetchone()
    return int(r["q"])


def corporate_fill_exists(ex: Executor, account_id: str, note: str) -> bool:
    r = ex.execute(
        "SELECT 1 FROM fills WHERE account_id=? AND kind='corporate_action' AND note=? LIMIT 1",
        (account_id, note),
    ).fetchone()
    return r is not None


# ---------------------------------------------------------------- positions


def _row_position(r: sqlite3.Row) -> Position:
    return Position(
        account_id=r["account_id"],
        symbol=r["symbol"],
        qty=int(r["qty"]),
        today_bought_qty=int(r["today_bought_qty"]),
        cost_total=Decimal(r["cost_total"]),
        updated_at=parse_iso(r["updated_at"]),
    )


def get_position(ex: Executor, account_id: str, symbol: str) -> Position | None:
    r = ex.execute(
        "SELECT * FROM positions WHERE account_id=? AND symbol=?", (account_id, symbol)
    ).fetchone()
    return None if r is None else _row_position(r)


def list_positions(ex: Executor, account_id: str) -> list[Position]:
    rows = ex.execute(
        "SELECT * FROM positions WHERE account_id=? ORDER BY symbol", (account_id,)
    ).fetchall()
    return [_row_position(r) for r in rows]


def upsert_position(ex: Executor, p: Position) -> None:
    ex.execute(
        "INSERT INTO positions (account_id, symbol, qty, today_bought_qty, cost_total, updated_at)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(account_id, symbol) DO UPDATE SET qty=excluded.qty,"
        " today_bought_qty=excluded.today_bought_qty, cost_total=excluded.cost_total,"
        " updated_at=excluded.updated_at",
        (p.account_id, p.symbol, p.qty, p.today_bought_qty, str(p.cost_total), iso(p.updated_at)),
    )


def delete_position(ex: Executor, account_id: str, symbol: str) -> None:
    ex.execute("DELETE FROM positions WHERE account_id=? AND symbol=?", (account_id, symbol))


def all_position_symbols(ex: Executor) -> list[str]:
    rows = ex.execute("SELECT DISTINCT symbol FROM positions WHERE qty>0").fetchall()
    return [r["symbol"] for r in rows]


def unlock_positions(ex: Executor, account_id: str, at: datetime) -> None:
    ex.execute(
        "UPDATE positions SET today_bought_qty=0, updated_at=? WHERE account_id=?",
        (iso(at), account_id),
    )


# ---------------------------------------------------------------- nav_daily


def _row_nav(r: sqlite3.Row) -> NavDaily:
    return NavDaily(
        account_id=r["account_id"],
        trade_date=s2d(r["trade_date"]),
        cash=Decimal(r["cash"]),
        market_value=Decimal(r["market_value"]),
        total_assets=Decimal(r["total_assets"]),
        nav=Decimal(r["nav"]),
        day_pnl=Decimal(r["day_pnl"]),
        benchmark_nav=dec(r["benchmark_nav"]),
        finalized_at=parse_iso(r["finalized_at"]),
    )


def upsert_nav(ex: Executor, n: NavDaily) -> None:
    ex.execute(
        "INSERT INTO nav_daily (account_id, trade_date, cash, market_value, total_assets, nav,"
        " day_pnl, benchmark_nav, finalized_at) VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(account_id, trade_date) DO UPDATE SET cash=excluded.cash,"
        " market_value=excluded.market_value, total_assets=excluded.total_assets, nav=excluded.nav,"
        " day_pnl=excluded.day_pnl, benchmark_nav=excluded.benchmark_nav,"
        " finalized_at=excluded.finalized_at",
        (
            n.account_id,
            d2s(n.trade_date),
            str(n.cash),
            str(n.market_value),
            str(n.total_assets),
            str(n.nav),
            str(n.day_pnl),
            sdec(n.benchmark_nav),
            iso(n.finalized_at),
        ),
    )


def list_nav(ex: Executor, account_id: str) -> list[NavDaily]:
    rows = ex.execute(
        "SELECT * FROM nav_daily WHERE account_id=? ORDER BY trade_date", (account_id,)
    ).fetchall()
    return [_row_nav(r) for r in rows]


def last_nav(ex: Executor, account_id: str, before: date | None = None) -> NavDaily | None:
    sql = "SELECT * FROM nav_daily WHERE account_id=?"
    params: list[Any] = [account_id]
    if before is not None:
        sql += " AND trade_date<?"
        params.append(d2s(before))
    sql += " ORDER BY trade_date DESC LIMIT 1"
    r = ex.execute(sql, params).fetchone()
    return None if r is None else _row_nav(r)


# ---------------------------------------------------------------- events


def _row_event(r: sqlite3.Row) -> Event:
    return Event(
        account_id=r["account_id"],
        seq=int(r["seq"]),
        type=EventType(r["type"]),
        occurred_at=parse_iso(r["occurred_at"]),
        order_id=r["order_id"],
        fill_seq=int(r["fill_seq"]) if r["fill_seq"] is not None else None,
        trade_date=s2d(r["trade_date"]) if r["trade_date"] else None,
        symbol=r["symbol"],
        basis=json.loads(r["basis"]),
        summary=r["summary"],
        payload=json.loads(r["payload"]),
        notify=bool(r["notify"]),
        delivered_at=parse_iso(r["delivered_at"]) if r["delivered_at"] else None,
        attempts=int(r["attempts"]),
        next_attempt_at=parse_iso(r["next_attempt_at"]) if r["next_attempt_at"] else None,
    )


def next_event_seq(ex: Executor, account_id: str) -> int:
    r = ex.execute(
        "SELECT COALESCE(MAX(seq),0) AS s FROM events WHERE account_id=?", (account_id,)
    ).fetchone()
    return int(r["s"]) + 1


def insert_event(ex: Executor, e: Event) -> None:
    ex.execute(
        "INSERT INTO events (account_id, seq, type, occurred_at, order_id, fill_seq, trade_date,"
        " symbol, basis, summary, payload, notify, delivered_at, attempts, next_attempt_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            e.account_id,
            e.seq,
            e.type.value,
            iso(e.occurred_at),
            e.order_id,
            e.fill_seq,
            d2s(e.trade_date) if e.trade_date else None,
            e.symbol,
            json.dumps(e.basis, ensure_ascii=False),
            e.summary,
            json.dumps(e.payload, ensure_ascii=False),
            int(e.notify),
            iso(e.delivered_at) if e.delivered_at else None,
            e.attempts,
            iso(e.next_attempt_at) if e.next_attempt_at else None,
        ),
    )


def list_events(
    ex: Executor,
    account_id: str,
    after: int = 0,
    types: Sequence[EventType] | None = None,
    symbol: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
) -> list[Event]:
    sql = "SELECT * FROM events WHERE account_id=? AND seq>?"
    params: list[Any] = [account_id, after]
    if types:
        marks = ",".join("?" for _ in types)
        sql += f" AND type IN ({marks})"
        params += [t.value for t in types]
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol)
    if since is not None:
        sql += " AND occurred_at>=?"
        params.append(iso(since))
    if until is not None:
        sql += " AND occurred_at<=?"
        params.append(iso(until))
    sql += " ORDER BY seq LIMIT ?"
    params.append(limit)
    return [_row_event(r) for r in ex.execute(sql, params).fetchall()]


def due_events(ex: Executor, now: datetime, max_attempts: int, limit: int = 50) -> list[Event]:
    rows = ex.execute(
        "SELECT * FROM events WHERE notify=1 AND delivered_at IS NULL AND attempts<?"
        " AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY occurred_at LIMIT ?",
        (max_attempts, iso(now), limit),
    ).fetchall()
    return [_row_event(r) for r in rows]


def mark_delivered(ex: Executor, account_id: str, seq: int, at: datetime) -> None:
    ex.execute(
        "UPDATE events SET delivered_at=?, attempts=attempts+1 WHERE account_id=? AND seq=?",
        (iso(at), account_id, seq),
    )


def mark_attempt(ex: Executor, account_id: str, seq: int, next_at: datetime | None) -> None:
    ex.execute(
        "UPDATE events SET attempts=attempts+1, next_attempt_at=? WHERE account_id=? AND seq=?",
        (iso(next_at) if next_at else None, account_id, seq),
    )


# ---------------------------------------------------------------- settlement_runs


def _row_run(r: sqlite3.Row) -> SettlementRun:
    return SettlementRun(
        trade_date=s2d(r["trade_date"]),
        status=r["status"],
        started_at=parse_iso(r["started_at"]),
        finished_at=parse_iso(r["finished_at"]) if r["finished_at"] else None,
        error=r["error"],
    )


def get_run(ex: Executor, trade_date: date) -> SettlementRun | None:
    r = ex.execute(
        "SELECT * FROM settlement_runs WHERE trade_date=?", (d2s(trade_date),)
    ).fetchone()
    return None if r is None else _row_run(r)


def upsert_run(ex: Executor, run: SettlementRun) -> None:
    ex.execute(
        "INSERT INTO settlement_runs (trade_date, status, started_at, finished_at, error)"
        " VALUES (?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET status=excluded.status,"
        " started_at=excluded.started_at, finished_at=excluded.finished_at, error=excluded.error",
        (
            d2s(run.trade_date),
            run.status,
            iso(run.started_at),
            iso(run.finished_at) if run.finished_at else None,
            run.error,
        ),
    )


def last_done_run(ex: Executor) -> date | None:
    r = ex.execute(
        "SELECT trade_date FROM settlement_runs WHERE status='done'"
        " ORDER BY trade_date DESC LIMIT 1"
    ).fetchone()
    return None if r is None else s2d(r["trade_date"])


def list_runs(ex: Executor, limit: int = 30) -> list[SettlementRun]:
    rows = ex.execute(
        "SELECT * FROM settlement_runs ORDER BY trade_date DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_run(r) for r in rows]


# ---------------------------------------------------------------- instruments


def _row_instrument(r: sqlite3.Row) -> Instrument:
    return Instrument(
        symbol=r["symbol"],
        name=r["name"],
        board=Board(r["board"]),
        exchange=Exchange(r["exchange"]),
        list_date=s2d(r["list_date"]) if r["list_date"] else None,
        is_st=bool(r["is_st"]),
    )


def get_instrument(ex: Executor, symbol: str) -> Instrument | None:
    r = ex.execute("SELECT * FROM instruments WHERE symbol=?", (symbol,)).fetchone()
    return None if r is None else _row_instrument(r)


def upsert_instrument(ex: Executor, inst: Instrument, at: datetime) -> None:
    ex.execute(
        "INSERT INTO instruments (symbol, name, board, exchange, list_date, is_st,"
        " updated_at) VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(symbol) DO UPDATE SET name=excluded.name,"
        " board=excluded.board, exchange=excluded.exchange,"
        " list_date=excluded.list_date, is_st=excluded.is_st, updated_at=excluded.updated_at",
        (
            inst.symbol,
            inst.name,
            inst.board.value,
            inst.exchange.value,
            d2s(inst.list_date) if inst.list_date else None,
            int(inst.is_st),
            iso(at),
        ),
    )


def search_instruments(ex: Executor, q: str, limit: int = 20) -> list[Instrument]:
    like = f"%{q}%"
    rows = ex.execute(
        "SELECT * FROM instruments WHERE symbol LIKE ? OR name LIKE ? ORDER BY symbol LIMIT ?",
        (like, like, limit),
    ).fetchall()
    return [_row_instrument(r) for r in rows]


# ---------------------------------------------------------------- trading_calendar


def upsert_calendar(ex: Executor, rows: Sequence[tuple[date, bool]], source: str) -> None:
    for d, is_open in rows:
        ex.execute(
            "INSERT INTO trading_calendar (cal_date, is_open, source) VALUES (?,?,?)"
            " ON CONFLICT(cal_date) DO UPDATE SET is_open=excluded.is_open, source=excluded.source",
            (d2s(d), int(is_open), source),
        )


def calendar_rows(ex: Executor) -> list[tuple[date, bool]]:
    rows = ex.execute("SELECT cal_date, is_open FROM trading_calendar ORDER BY cal_date").fetchall()
    return [(s2d(r["cal_date"]), bool(r["is_open"])) for r in rows]


def calendar_coverage(ex: Executor) -> tuple[date, date] | None:
    r = ex.execute(
        "SELECT MIN(cal_date) AS lo, MAX(cal_date) AS hi FROM trading_calendar"
    ).fetchone()
    if r is None or r["lo"] is None:
        return None
    return s2d(r["lo"]), s2d(r["hi"])


# ---------------------------------------------------------------- daily_bars


def _row_bar(r: sqlite3.Row) -> DailyBar:
    return DailyBar(
        symbol=r["symbol"],
        trade_date=s2d(r["trade_date"]),
        open=dec(r["open"]),
        high=dec(r["high"]),
        low=dec(r["low"]),
        close=Decimal(r["close"]),
        adj_close=dec(r["adj_close"]),
        prev_close=dec(r["prev_close"]),
        volume=int(r["volume"]) if r["volume"] is not None else None,
        up_limit=dec(r["up_limit"]),
        down_limit=dec(r["down_limit"]),
    )


def get_bar(ex: Executor, symbol: str, d: date) -> DailyBar | None:
    r = ex.execute(
        "SELECT * FROM daily_bars WHERE symbol=? AND trade_date=?", (symbol, d2s(d))
    ).fetchone()
    return None if r is None else _row_bar(r)


def upsert_bar(ex: Executor, b: DailyBar) -> None:
    ex.execute(
        "INSERT INTO daily_bars (symbol, trade_date, open, high, low, close, adj_close, prev_close,"
        " volume, up_limit, down_limit) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(symbol, trade_date) DO UPDATE SET open=excluded.open, high=excluded.high,"
        " low=excluded.low, close=excluded.close, adj_close=excluded.adj_close,"
        " prev_close=excluded.prev_close, volume=excluded.volume,"
        " up_limit=COALESCE(excluded.up_limit, daily_bars.up_limit),"
        " down_limit=COALESCE(excluded.down_limit, daily_bars.down_limit)",
        (
            b.symbol,
            d2s(b.trade_date),
            sdec(b.open),
            sdec(b.high),
            sdec(b.low),
            str(b.close),
            sdec(b.adj_close),
            sdec(b.prev_close),
            b.volume,
            sdec(b.up_limit),
            sdec(b.down_limit),
        ),
    )


def set_bar_limits(
    ex: Executor, symbol: str, d: date, up: Decimal | None, down: Decimal | None
) -> None:
    ex.execute(
        "UPDATE daily_bars SET up_limit=?, down_limit=? WHERE symbol=? AND trade_date=?",
        (sdec(up), sdec(down), symbol, d2s(d)),
    )


def latest_bar_on_or_before(ex: Executor, symbol: str, d: date) -> DailyBar | None:
    r = ex.execute(
        "SELECT * FROM daily_bars WHERE symbol=? AND trade_date<=?"
        " ORDER BY trade_date DESC LIMIT 1",
        (symbol, d2s(d)),
    ).fetchone()
    return None if r is None else _row_bar(r)


def bars_between(ex: Executor, symbol: str, start: date, end: date) -> list[DailyBar]:
    rows = ex.execute(
        "SELECT * FROM daily_bars WHERE symbol=? AND trade_date BETWEEN ? AND ?"
        " ORDER BY trade_date",
        (symbol, d2s(start), d2s(end)),
    ).fetchall()
    return [_row_bar(r) for r in rows]


# ---------------------------------------------------------------- suspensions


def replace_suspensions(ex: Executor, d: date, rows: Sequence[Suspension], source: str) -> None:
    ex.execute("DELETE FROM suspensions WHERE trade_date=?", (d2s(d),))
    for s in rows:
        ex.execute(
            "INSERT OR REPLACE INTO suspensions (symbol, trade_date, reason, source)"
            " VALUES (?,?,?,?)",
            (s.symbol, d2s(d), s.reason, source),
        )


def is_suspended(ex: Executor, symbol: str, d: date) -> bool:
    r = ex.execute(
        "SELECT 1 FROM suspensions WHERE symbol=? AND trade_date=?", (symbol, d2s(d))
    ).fetchone()
    return r is not None


def suspensions_on(ex: Executor, d: date) -> list[Suspension]:
    rows = ex.execute(
        "SELECT symbol, reason FROM suspensions WHERE trade_date=?", (d2s(d),)
    ).fetchall()
    return [Suspension(symbol=r["symbol"], reason=r["reason"]) for r in rows]


# ---------------------------------------------------------------- corporate_actions


def _row_action(r: sqlite3.Row) -> CorporateAction:
    return CorporateAction(
        symbol=r["symbol"],
        ex_date=s2d(r["ex_date"]),
        record_date=s2d(r["record_date"]) if r["record_date"] else None,
        pay_date=s2d(r["pay_date"]) if r["pay_date"] else None,
        bonus_per_share=dec0(r["bonus_per_share"]),
        transfer_per_share=dec0(r["transfer_per_share"]),
        cash_per_share=dec0(r["cash_per_share"]),
        source=r["source"],
    )


def upsert_corporate_action(ex: Executor, a: CorporateAction) -> None:
    ex.execute(
        "INSERT INTO corporate_actions (symbol, ex_date, record_date, pay_date, bonus_per_share,"
        " transfer_per_share, cash_per_share, source) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT(symbol, ex_date) DO UPDATE SET record_date=excluded.record_date,"
        " pay_date=excluded.pay_date, bonus_per_share=excluded.bonus_per_share,"
        " transfer_per_share=excluded.transfer_per_share, cash_per_share=excluded.cash_per_share,"
        " source=excluded.source",
        (
            a.symbol,
            d2s(a.ex_date),
            d2s(a.record_date) if a.record_date else None,
            d2s(a.pay_date) if a.pay_date else None,
            str(a.bonus_per_share),
            str(a.transfer_per_share),
            str(a.cash_per_share),
            a.source,
        ),
    )


def corporate_actions_for(ex: Executor, symbol: str) -> list[CorporateAction]:
    rows = ex.execute(
        "SELECT * FROM corporate_actions WHERE symbol=? ORDER BY ex_date", (symbol,)
    ).fetchall()
    return [_row_action(r) for r in rows]


def corporate_actions_on(ex: Executor, ex_date: date) -> list[CorporateAction]:
    rows = ex.execute("SELECT * FROM corporate_actions WHERE ex_date=?", (d2s(ex_date),)).fetchall()
    return [_row_action(r) for r in rows]


def corporate_actions_paying(ex: Executor, pay_date: date) -> list[CorporateAction]:
    rows = ex.execute(
        "SELECT * FROM corporate_actions WHERE pay_date=? OR (pay_date IS NULL AND ex_date=?)",
        (d2s(pay_date), d2s(pay_date)),
    ).fetchall()
    return [_row_action(r) for r in rows]


# ---------------------------------------------------------------- benchmark


def get_benchmark(ex: Executor, code: str, d: date) -> Decimal | None:
    r = ex.execute(
        "SELECT close FROM benchmark_daily WHERE index_code=? AND trade_date=?", (code, d2s(d))
    ).fetchone()
    return None if r is None else Decimal(r["close"])


def upsert_benchmark(ex: Executor, code: str, d: date, close: Decimal, source: str) -> None:
    ex.execute(
        "INSERT INTO benchmark_daily (index_code, trade_date, close, source) VALUES (?,?,?,?)"
        " ON CONFLICT(index_code, trade_date) DO UPDATE SET close=excluded.close,"
        " source=excluded.source",
        (code, d2s(d), str(close), source),
    )


def benchmark_on_or_before(ex: Executor, code: str, d: date) -> tuple[date, Decimal] | None:
    r = ex.execute(
        "SELECT trade_date, close FROM benchmark_daily WHERE index_code=? AND trade_date<=?"
        " ORDER BY trade_date DESC LIMIT 1",
        (code, d2s(d)),
    ).fetchone()
    return None if r is None else (s2d(r["trade_date"]), Decimal(r["close"]))


# ---------------------------------------------------------------- alerts


def _row_alert(r: sqlite3.Row) -> Alert:
    return Alert(
        id=int(r["id"]),
        level=r["level"],
        code=r["code"],
        message=r["message"],
        trade_date=s2d(r["trade_date"]) if r["trade_date"] else None,
        created_at=parse_iso(r["created_at"]),
        resolved_at=parse_iso(r["resolved_at"]) if r["resolved_at"] else None,
    )


def insert_alert(
    ex: Executor, level: str, code: str, message: str, trade_date: date | None, at: datetime
) -> int:
    cur = ex.execute(
        "INSERT INTO alerts (level, code, message, trade_date, created_at) VALUES (?,?,?,?,?)",
        (level, code, message, d2s(trade_date) if trade_date else None, iso(at)),
    )
    return int(cur.lastrowid or 0)


def list_alerts(ex: Executor, unresolved_only: bool = True, limit: int = 100) -> list[Alert]:
    sql = "SELECT * FROM alerts"
    if unresolved_only:
        sql += " WHERE resolved_at IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    return [_row_alert(r) for r in ex.execute(sql, (limit,)).fetchall()]


def has_unresolved_alert(ex: Executor, code: str, trade_date: date) -> bool:
    r = ex.execute(
        "SELECT 1 FROM alerts WHERE code=? AND trade_date=? AND resolved_at IS NULL LIMIT 1",
        (code, d2s(trade_date)),
    ).fetchone()
    return r is not None


def count_unresolved_alerts(ex: Executor) -> int:
    r = ex.execute("SELECT COUNT(*) AS n FROM alerts WHERE resolved_at IS NULL").fetchone()
    return int(r["n"])


def resolve_alert(ex: Executor, alert_id: int, at: datetime) -> None:
    ex.execute("UPDATE alerts SET resolved_at=? WHERE id=?", (iso(at), alert_id))
