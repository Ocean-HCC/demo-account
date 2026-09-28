"""TSP 接入验证工具（实现 8.6 M5）：用模拟上游跑通各种情形，确保在真实机器上第一次就能跑完。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from demo_account.clock import FakeClock
from demo_account.verify_tsp import Check, Verifier
from fake_upstream import FakeUpstream, trading_days, upstream_for_day
from support import THU, at, make_settings

SYMS = ["600000.SH", "510300.SH"]
NOSLEEP = lambda s: None  # noqa: E731


def full_upstream(clock: FakeClock) -> FakeUpstream:
    up = upstream_for_day()
    up.daily["510300.SH"] = [
        {
            "date": d.isoformat(),
            "open": 4.5,
            "high": 4.52,
            "low": 4.49,
            "close": 4.515,
            "raw_close": 4.515,
        }
        for d in trading_days(date(2026, 8, 1), date(2026, 9, 23))
    ]
    up.latest["510300.SH"] = {
        "date": "2026-09-24",
        "open": 4.5,
        "high": 4.52,
        "low": 4.49,
        "close": 4.52,
        "change_pct": 0.001,
        "is_live": True,
    }
    up.sina_index = [{"day": "2026-09-23", "close": "4517.279"}]
    up.live_clock = clock
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
        "tsp_daily_510300.SH",
        "flow_history",
        "flow_live",
    ):
        assert (out / "samples" / f"{name}.json").exists(), name


def test_after_close_skips_live_parts(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 20, 0))
    up = full_upstream(clock)
    up.daily["600000.SH"] = [r for r in up.daily["600000.SH"] if not r.get("is_live")] + [
        {
            "date": "2026-09-24",
            "open": 8.99,
            "high": 9.08,
            "low": 8.97,
            "close": 9.05,
            "raw_close": 9.05,
        }
    ]
    up.daily["510300.SH"].append(
        {
            "date": "2026-09-24",
            "open": 4.5,
            "high": 4.53,
            "low": 4.49,
            "close": 4.52,
            "raw_close": 4.52,
        }
    )
    checks = run(tmp_path, clock, up)
    assert checks["T4"].status == "INFO" and checks["T5"].status == "INFO"
    assert checks["T7"].status == "PASS", checks["T7"].detail
    assert checks["F1"].status == "PASS", checks["F1"].detail
    assert checks["F2"].status == "SKIP"


def test_stale_daily_bars_are_flagged(tmp_path: Path) -> None:
    clock = FakeClock(at(THU, 20, 0))  # 收盘后应已有当日日线
    checks = run(tmp_path, clock, full_upstream(clock))
    assert checks["T7"].status == "WARN" and "应有 2026-09-24" in checks["T7"].detail


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
