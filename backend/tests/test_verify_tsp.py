"""TSP 接入验证工具（实现 8.6 M5）：用模拟上游跑通各种情形，确保在真实机器上第一次就能跑完。"""

from __future__ import annotations

import json
from pathlib import Path

from demo_account.clock import FakeClock
from demo_account.verify_tsp import Check, Verifier
from fake_upstream import FakeUpstream, upstream_for_day
from support import THU, at, make_settings

SYMS = ["600000.SH"]
NOSLEEP = lambda s: None  # noqa: E731


def full_upstream(clock: FakeClock) -> FakeUpstream:
    up = upstream_for_day()
    up.sina_index = [{"day": "2026-09-23", "close": "4517.279"}]
    up.live_clock = clock
    return up


def after_close_upstream(clock: FakeClock, final_sync_done: bool = True) -> FakeUpstream:
    """收盘定版后的 TSP（与 TSP 所在机器 2026-09-28 18:06 的真实样本一致）。

    当日日线行仍带 is_live：已落盘的行被实时值覆盖后返回，所以带 raw_close；指数有当日行。
    """
    up = full_upstream(clock)
    up.daily["600000.SH"] = [r for r in up.daily["600000.SH"] if not r.get("is_live")] + [
        {
            "date": "2026-09-24",
            "open": 8.99,
            "high": 9.08,
            "low": 8.97,
            "close": 9.05,
            "raw_close": 9.05,
            "is_live": True,
        }
    ]
    up.index["000300.SH"].append({"date": "2026-09-24", "close": 4439.144})
    up.status.update(
        {
            "market_phase": "close_final",
            "is_polling_window": False,
            "final_sync_done": final_sync_done,
            "final_sync_failed": None,
        }
    )
    return up


def run(tmp_path: Path, clock: FakeClock, up: FakeUpstream, **env: str) -> dict[str, Check]:
    settings = make_settings(tmp_path, DEMO_ACCOUNT_MARKET="tsp", **env)
    v = Verifier(
        settings,
        tmp_path / "out",
        symbols=SYMS,
        transport=up.transport(),
        clock=clock,
        sleep=NOSLEEP,
        wait=lambda s: clock.advance(s),
    )
    return {c.id: c for c in v.run()}


def test_all_checks_pass_during_session(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 9, 35))
    checks = run(tmp_path, clock, full_upstream(clock))
    for cid in ("T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "P1", "P2", "P3", "P4", "F1", "F2"):
        assert checks[cid].status == "PASS", (cid, checks[cid].detail)
    assert checks["E1"].status == "INFO"
    assert checks["P5"].status == "INFO"  # 模拟上游不带 Date 头
    assert "9.00" in checks["F1"].detail and "9.03" in checks["F2"].detail
    out = tmp_path / "out"
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["FAIL"] == 0
    assert (
        (out / "report.md")
        .read_text(encoding="utf-8")
        .startswith("# demo-account TSP 接入验证报告")
    )
    for name in (
        "tsp_intraday_status",
        "tsp_latest_600000.SH",
        "tsp_daily_600000.SH",
        "flow_history",
        "flow_live",
    ):
        assert (out / "samples" / f"{name}.json").exists(), name


def test_after_close_settles_today_from_final_live_rows(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 20, 0))
    checks = run(tmp_path, clock, after_close_upstream(clock))
    assert checks["T4"].status == "INFO" and checks["T5"].status == "INFO"
    assert checks["T7"].status == "PASS", checks["T7"].detail
    assert "收盘定版" in checks["T7"].detail
    f1 = checks["F1"]
    assert f1.status == "PASS", f1.detail
    assert "用 2026-09-24 的真实数据" in f1.detail and "9.05" in f1.detail
    assert checks["F2"].status == "SKIP"


def test_after_close_without_final_sync_fails(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 20, 0))
    checks = run(tmp_path, clock, after_close_upstream(clock, final_sync_done=False))
    t7 = checks["T7"]
    assert t7.status == "FAIL" and "应有 2026-09-24" in t7.detail and "尚未收盘定版" in t7.detail
    assert checks["F1"].status == "FAIL" and "收盘价" in checks["F1"].detail


def test_non_stock_symbols_are_skipped(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 9, 35))
    settings = make_settings(tmp_path, DEMO_ACCOUNT_MARKET="tsp")
    v = Verifier(
        settings,
        tmp_path / "out",
        symbols=["600000.SH", "510300.SH"],
        transport=full_upstream(clock).transport(),
        clock=clock,
        sleep=NOSLEEP,
        wait=lambda s: clock.advance(s),
        run_e2e=False,
    )
    checks = {c.id: c for c in v.run()}
    assert v.symbols == ["600000.SH"] and "510300.SH" in checks["E1"].detail
    assert "T9" not in checks and checks["T6"].status == "PASS"


def test_stale_daily_bars_fail(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 20, 0))  # 收盘后应已有当日日线
    checks = run(tmp_path, clock, full_upstream(clock))
    assert checks["T7"].status == "FAIL" and "应有 2026-09-24" in checks["T7"].detail


def test_tsp_unreachable(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 9, 35))
    up = full_upstream(clock)
    up.fail_tsp = True
    checks = run(tmp_path, clock, up)
    assert checks["T1"].status == "FAIL"
    assert all(checks[c].status == "SKIP" for c in ("T3", "T5", "T7", "F1", "F2"))
    assert checks["P1"].status == "PASS" and checks["P2"].status == "PASS"


def test_password_login_and_no_secret_in_output(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 9, 35))
    up = full_upstream(clock)
    up.password = "s3cr3t-pw"
    checks = run(tmp_path, clock, up, DEMO_ACCOUNT_TSP_PASSWORD="s3cr3t-pw")
    assert checks["T2"].status == "PASS" and "已登录" in checks["T2"].detail
    assert checks["F2"].status == "PASS", checks["F2"].detail
    for f in (tmp_path / "out").rglob("*.json"):
        assert "s3cr3t-pw" not in f.read_text(encoding="utf-8"), f
    assert "s3cr3t-pw" not in (tmp_path / "out" / "report.md").read_text(encoding="utf-8")


def test_missing_password_is_explained(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 9, 35))
    up = full_upstream(clock)
    up.password = "s3cr3t-pw"
    checks = run(tmp_path, clock, up)
    assert checks["T2"].status == "FAIL" and "DEMO_ACCOUNT_TSP_PASSWORD" in checks["T2"].detail
    assert checks["F1"].status == "SKIP"
