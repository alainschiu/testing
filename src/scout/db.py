import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from scout.config import get_settings
from scout.logging import get_logger

log = get_logger("scout.db")


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    s = get_settings()
    conn = _connect(s.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations ("
        "name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    return {row["name"] for row in conn.execute("SELECT name FROM _migrations")}


def run_migrations() -> list[str]:
    """Apply any unapplied .sql files from migrations/ in lexical order. Idempotent.

    executescript() manages its own transaction in autocommit mode, so we don't
    wrap it. If the script raises, the _migrations row is not inserted and the
    next call retries from scratch.
    """
    s = get_settings()
    applied: list[str] = []
    with connection() as conn:
        done = _applied_migrations(conn)
        for path in sorted(s.migrations_dir.glob("*.sql")):
            if path.name in done:
                continue
            sql = path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
            applied.append(path.name)
            log.info("migration_applied", name=path.name)
    return applied


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Single-statement-batch transaction. Use sparingly; most code can use connection()."""
    with connection() as conn:
        try:
            conn.execute("BEGIN")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
