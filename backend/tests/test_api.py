"""HTTP 接口测试：成功路径与错误码（实现 8.1 api）。"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from demo_account.api.events import format_sse
from demo_account.main import create_app
from support import THU, Env, at, make_env

PF = "600000.SH"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[tuple[TestClient, Env]]:
    env = make_env(tmp_path, start=at(THU, 9, 35))
    with TestClient(create_app(container=env.c)) as cl:
        yield cl, env


def _account(cl: TestClient, name: str = "策略A") -> str:
    r = cl.post("/api/accounts", json={"name": name})
    assert r.status_code == 201
    return str(r.json()["id"])


def test_account_crud_and_overview(client: tuple[TestClient, Env]) -> None:
    cl, _ = client
    r = cl.post("/api/accounts", json={"name": "策略A", "note": "均线"})
    assert r.status_code == 201
    acc = r.json()
    assert acc["commission_rate"] == "0.00013" and acc["initial_cash"] == "1000000"
    aid = acc["id"]
    assert cl.get("/api/accounts").json()[0]["account"]["id"] == aid
    ov = cl.get(f"/api/accounts/{aid}").json()
    assert ov["cash"] == "1000000" and ov["available_cash"] == "1000000"
    assert (
        cl.patch(f"/api/accounts/{aid}", json={"slippage_rate": "0.001"}).json()["slippage_rate"]
        == "0.001"
    )
    types = [e["type"] for e in cl.get(f"/api/accounts/{aid}/events").json()]
    assert types == ["account_created", "fee_params_changed"]
    assert cl.post(f"/api/accounts/{aid}/freeze").json()["status"] == "frozen"
    assert cl.post(f"/api/accounts/{aid}/unfreeze").json()["status"] == "active"
    r = cl.get("/api/accounts/acc_missing")
    assert r.status_code == 404 and r.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"


def test_order_flow(client: tuple[TestClient, Env]) -> None:
    cl, env = client
    aid = _account(cl)
    env.mock.set_price(PF, env.prev_close(PF))
    body = {
        "symbol": "600000.sh",
        "side": "buy",
        "order_type": "market",
        "qty": 1000,
        "note": "金叉",
        "tags": ["ma"],
    }
    pv = cl.post(f"/api/accounts/{aid}/orders/preview", json=body).json()
    assert pv["ok"] is True and pv["qty"] == 1000 and Decimal(pv["frozen_cash"]) > 0
    r = cl.post(f"/api/accounts/{aid}/orders", json=body)
    assert r.status_code == 202
    o = r.json()
    assert o["status"] == "pending" and o["symbol"] == PF and o["tags"] == ["ma"]
    env.clock.advance(1)
    assert cl.post("/api/admin/match").json()["intraday"]["filled"] == 1
    assert cl.get(f"/api/accounts/{aid}/orders/{o['id']}").json()["status"] == "filled"
    pos = cl.get(f"/api/accounts/{aid}/positions").json()
    assert pos[0]["qty"] == 1000 and pos[0]["sellable_qty"] == 0
    fills = cl.get(f"/api/accounts/{aid}/fills").json()
    assert len(fills) == 1 and fills[0]["note"] == "金叉"
    r = cl.delete(f"/api/accounts/{aid}/orders/{o['id']}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "ORDER_NOT_PENDING"
    evs = cl.get(f"/api/accounts/{aid}/events?types=order_filled").json()
    assert len(evs) == 1 and evs[0]["basis"]["fill_price"]
    assert len(cl.get(f"/api/accounts/{aid}/orders?status=filled").json()) == 1


def test_rejected_order_returns_error_with_recorded_order(client: tuple[TestClient, Env]) -> None:
    cl, _ = client
    aid = _account(cl)
    r = cl.post(
        f"/api/accounts/{aid}/orders",
        json={"symbol": PF, "side": "sell", "order_type": "market", "qty": 100},
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "INSUFFICIENT_SELLABLE"
    oid = err["details"]["order"]["id"]
    assert cl.get(f"/api/accounts/{aid}/orders/{oid}").json()["status"] == "rejected"


def test_request_validation_errors(client: tuple[TestClient, Env]) -> None:
    cl, _ = client
    aid = _account(cl)
    url = f"/api/accounts/{aid}/orders"
    r = cl.post(url, json={"symbol": PF, "side": "buy", "order_type": "market"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = cl.post(url, json={"symbol": PF, "side": "hold", "order_type": "market", "qty": 100})
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_REQUEST"
    r = cl.post("/api/accounts", json={"name": ""})
    assert r.status_code == 400
    r = cl.post(
        url, json={"symbol": "430047.BJ", "side": "buy", "order_type": "market", "qty": 100}
    )
    assert r.status_code == 400 and r.json()["error"]["code"] == "SYMBOL_UNSUPPORTED"


def test_settle_nav_stats_and_reconcile_via_api(client: tuple[TestClient, Env]) -> None:
    cl, env = client
    aid = _account(cl)
    env.mock.set_price(PF, env.prev_close(PF))
    cl.post(
        f"/api/accounts/{aid}/orders",
        json={"symbol": PF, "side": "buy", "order_type": "market", "qty": 1000},
    )
    env.clock.advance(1)
    cl.post("/api/admin/match")
    env.go(THU, 16, 0)
    assert cl.post("/api/admin/settle").json()["status"] == "done"
    nav = cl.get(f"/api/accounts/{aid}/nav").json()
    assert len(nav) == 1 and nav[0]["benchmark_nav"] == "1.00000000"
    st = cl.get(f"/api/accounts/{aid}/stats").json()
    assert st["rounds"] == 0 and st["cumulative_return"] is not None
    assert cl.post(f"/api/admin/reconcile/{aid}").json()["ok"] is True
    assert cl.get("/api/admin/settlements").json()[0]["status"] == "done"


def test_market_endpoints_and_health(client: tuple[TestClient, Env]) -> None:
    cl, _ = client
    s = cl.get("/api/market/status").json()
    assert s["is_trading_day"] is True and s["session"] == "continuous"
    assert s["next_close"].startswith("2026-09-24")
    res = cl.get("/api/market/instruments?q=600000").json()
    assert any(i["symbol"] == PF for i in res)
    info = cl.get(f"/api/market/instruments/{PF}").json()
    assert info["up_limit"] and info["down_limit"] and info["suspended"] is False
    assert PF in cl.get(f"/api/market/quotes?symbols={PF}").json()
    h = cl.get("/api/health").json()
    assert h["status"] == "ok" and h["market"] == "mock"


def test_api_key_required_when_configured(tmp_path: Path) -> None:
    env = make_env(tmp_path, start=at(THU, 9, 35), DEMO_ACCOUNT_API_KEY="secret")
    with TestClient(create_app(container=env.c)) as cl:
        r = cl.get("/api/accounts")
        assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
        assert cl.get("/api/accounts", headers={"X-API-Key": "secret"}).status_code == 200


def test_format_sse() -> None:
    s = format_sse({"kind": "event", "event_id": "acc_1:3", "type": "order_filled", "x": 1})
    assert s.startswith("id: acc_1:3\nevent: order_filled\ndata: ") and s.endswith("\n\n")
    a = format_sse({"kind": "alert", "code": "X"})
    assert a.startswith("event: alert\n")
