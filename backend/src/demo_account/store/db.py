"""SQLite 连接、迁移与事务边界（实现 4.1、4.3、4.4）。

进程内单写者：所有写事务经同一把锁串行并以 BEGIN IMMEDIATE 开始；读用线程各自的连接，不加锁。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Executor(Protocol):
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor: ...


class Tx:
    """一次写事务。after_commit 注册的回调在提交并释放锁之后执行。"""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.after: list[Callable[[], None]] = []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def after_commit(self, fn: Callable[[], None]) -> None:
        self.after.append(fn)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._memory = str(path) == ":memory:"
        if not self._memory:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._write_conn = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False, timeout=5.0
        )
        conn.row_factory = sqlite3.Row
        if not self._memory:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def migrate(self) -> int:
        """按序号执行未应用的迁移脚本，PRAGMA user_version 记录版本。"""
        with self._lock:
            conn = self._write_conn
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                version = int(path.name.split("_", 1)[0])
                if version <= current:
                    continue
                conn.executescript(path.read_text(encoding="utf-8"))
                conn.execute(f"PRAGMA user_version={version}")
                current = version
            return current

    @contextmanager
    def write(self) -> Iterator[Tx]:
        with self._lock:
            conn = self._write_conn
            conn.execute("BEGIN IMMEDIATE")
            tx = Tx(conn)
            try:
                yield tx
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        for fn in tx.after:
            fn()

    def read(self) -> sqlite3.Connection:
        if self._memory:
            return self._write_conn
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock if self._memory else _NoLock():
            return self.read().execute(sql, params).fetchall()

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock if self._memory else _NoLock():
            return self.read().execute(sql, params).fetchone()

    def close(self) -> None:
        """关闭写连接与当前线程的读连接。Windows 不能删除仍被打开的数据库文件。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
        with self._lock:
            self._write_conn.close()


class _NoLock:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None
