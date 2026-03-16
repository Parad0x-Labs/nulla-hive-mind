from __future__ import annotations

import contextlib
import sqlite3
import threading
from pathlib import Path

from core.runtime_paths import data_path

DEFAULT_DB_PATH = data_path("nulla_web0_v2.db")

_thread_local = threading.local()


def _resolve_db_path(db_path: str | Path) -> str:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _make_connection(db_path: str | Path) -> sqlite3.Connection:
    """Create a fresh SQLite connection with WAL mode and safe defaults."""
    conn = sqlite3.connect(_resolve_db_path(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


class _PooledConnection:
    """Thin wrapper that makes close() a no-op so callers cannot kill the cached connection."""

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def close(self) -> None:
        # Keep pooled connections alive, but never return with an open tx.
        try:
            if self._conn.in_transaction:
                self._conn.rollback()
        except Exception:
            return

    def _real_close(self) -> None:
        self._conn.close()

    def __enter__(self) -> _PooledConnection:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None:
            try:
                if self._conn.in_transaction:
                    self._conn.rollback()
            except Exception:
                pass
            return False
        try:
            if self._conn.in_transaction:
                self._conn.commit()
        except Exception:
            with contextlib.suppress(Exception):
                self._conn.rollback()
        return False

    def __getattr__(self, name: str):
        return getattr(self._conn, name)


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Return a thread-local reusable SQLite connection.

    Connections are cached per-thread for the default DB path to avoid
    the overhead of re-opening and re-running PRAGMAs on every call.
    Non-default paths always get a fresh connection.
    """
    resolved = _resolve_db_path(db_path)
    default_resolved = _resolve_db_path(DEFAULT_DB_PATH)

    # Non-default path: always fresh (used for test isolation etc.)
    if resolved != default_resolved:
        return _make_connection(db_path)

    # Thread-local reuse for the default path
    cached: _PooledConnection | None = getattr(_thread_local, "default_conn", None)
    if cached is not None:
        try:
            cached._conn.execute("SELECT 1")
            return cached  # type: ignore[return-value]
        except Exception:
            with contextlib.suppress(Exception):
                cached._real_close()

    conn = _make_connection(db_path)
    pooled = _PooledConnection(conn)
    _thread_local.default_conn = pooled
    return pooled  # type: ignore[return-value]


def execute_query(query: str, params: tuple = (), db_path: str | Path = DEFAULT_DB_PATH) -> list:
    """Run a parameterized query and close the connection immediately after use."""
    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if query.strip().upper().startswith("SELECT"):
            return [dict(row) for row in cursor.fetchall()]
        conn.commit()
        return [{"status": "success", "lastrowid": cursor.lastrowid}]
    finally:
        conn.close()


def init_schema(db_path: str | Path = DEFAULT_DB_PATH):
    """
    Initialize the exact V2 SQLite schema defined in the Reference Architecture.
    """
    from storage.migrations import SCHEMA_SQL

    conn = get_connection(db_path)
    try:
        cursor = conn.cursor()
        cursor.executescript(SCHEMA_SQL)
        conn.commit()
        return
    finally:
        conn.close()


def healthcheck(db_path: str | Path = DEFAULT_DB_PATH) -> bool:
    try:
        conn = get_connection(db_path)
        try:
            conn.execute("SELECT 1")
            return True
        finally:
            conn.close()
    except Exception:
        return False
