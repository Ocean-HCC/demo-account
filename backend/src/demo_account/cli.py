"""命令行入口：demo-account serve / settle / reconcile。"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
from datetime import date
from pathlib import Path

from .config import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "module": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False)


def _setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)


def _load_env() -> None:
    from dotenv import load_dotenv

    for candidate in (Path.cwd() / ".env", REPO_ROOT / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)


def _safe_console() -> None:
    """Windows 终端的代码页可能写不出个别字符，替换掉而不是报错退出。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    _load_env()
    parser = argparse.ArgumentParser(prog="demo-account", description="A 股模拟券商柜台")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="启动 HTTP 服务、后台撮合与结算")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    settle = sub.add_parser("settle", help="手动执行某个交易日的日终结算")
    settle.add_argument("--date", help="YYYY-MM-DD，默认今天")
    rec = sub.add_parser("reconcile", help="用台账重放核对某个账户的持仓")
    rec.add_argument("account_id")
    ver = sub.add_parser("verify-tsp", help="在 TSP 所在机器上验证接入，生成报告与原始样本")
    ver.add_argument(
        "--out", default="verify-output", help="输出目录，相对当前目录，默认 verify-output"
    )
    ver.add_argument(
        "--symbols",
        help="逗号分隔的验证标的，默认 600000.SH,000001.SZ,300750.SZ,688981.SH",
    )
    ver.add_argument("--no-e2e", action="store_true", help="只检查接口与数据，不跑完整流程")
    ver.add_argument("--keep-db", action="store_true", help="保留流程验证用的临时数据库")
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    if not settings.db_path.is_absolute():  # 相对路径按仓库根目录解析
        settings = dataclasses.replace(settings, db_path=REPO_ROOT / settings.db_path)
    _setup_logging()

    if args.cmd == "serve":
        import uvicorn

        from .main import create_app

        app = create_app(settings)
        uvicorn.run(
            app, host=args.host or settings.host, port=args.port or settings.port, log_config=None
        )
        return 0

    if args.cmd == "verify-tsp":
        return _verify_tsp(settings, args)

    from .services.container import build_container
    from .services.errors import ServiceError

    c = build_container(settings)
    if args.cmd == "settle":
        d = date.fromisoformat(args.date) if args.date else c.clock.now().date()
        try:
            run = c.settlement.settle(d)
        except ServiceError as e:  # 例如更早的交易日还没结算（方案 4.4 顺序）
            print(json.dumps({"error": {"code": e.code, "message": e.message}}, ensure_ascii=False))
            return 1
        print(json.dumps(run.to_dict(), ensure_ascii=False))
        return 0 if run.status == "done" else 1
    if args.cmd == "reconcile":
        print(json.dumps(c.settlement.reconcile(args.account_id), ensure_ascii=False, indent=2))
        return 0
    return 2


def _verify_tsp(settings: Settings, args: argparse.Namespace) -> int:
    from .verify_tsp import LABEL, Verifier

    logging.getLogger().setLevel(logging.ERROR)  # 过程细节写进报告，控制台只打结果
    out = Path(args.out).resolve()
    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if args.symbols else None
    )
    print(f"开始验证：TSP {settings.tsp_base_url}，输出目录 {out}")
    checks = Verifier(
        settings, out, symbols=symbols, run_e2e=not args.no_e2e, keep_db=args.keep_db
    ).run()
    for c in checks:
        print(f"[{LABEL[c.status]}] {c.id} {c.title}：{c.detail}")
    failed = sum(1 for c in checks if c.status == "FAIL")
    print(f"完成：失败 {failed} 项。报告 {out / 'report.md'}，原始响应样本在 {out / 'samples'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
