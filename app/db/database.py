"""SQLite connection, migration, and cache-invalidation primitives.

The application keeps every write behind this module so that SQLite settings and
transaction semantics are consistent outside the GUI as well as inside it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any


LATEST_SCHEMA_VERSION = 1


def now_iso() -> str:
    """Return a sortable local timestamp including the UTC offset."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_date(value: date | datetime | str) -> str:
    """Normalize a date-like value to ``YYYY-MM-DD``.

    ISO datetimes are accepted to make service methods convenient for callers.
    """

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        raise ValueError("date must not be empty")
    try:
        if "T" in text or " " in text:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {value!r}") from exc


def normalize_datetime(value: datetime | date | str | None = None) -> tuple[str, str]:
    """Return a complete ISO datetime and its local calendar date."""

    if value is None:
        moment = datetime.now().astimezone()
    elif isinstance(value, datetime):
        moment = value
    elif isinstance(value, date):
        moment = datetime.combine(value, datetime.min.time())
    else:
        text = str(value).strip()
        if not text:
            raise ValueError("datetime must not be empty")
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"invalid ISO datetime: {value!r}") from exc
    return moment.isoformat(timespec="seconds"), moment.date().isoformat()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    """Own SQLite connections and schema lifecycle for one database file."""

    def __init__(self, path: str | Path, *, timeout: float = 10.0) -> None:
        self.path = Path(path).expanduser().resolve()
        self.timeout = timeout

    def connect(self) -> sqlite3.Connection:
        """Open a configured connection.

        The caller owns the returned connection. Service code should normally use
        :meth:`connection` or :meth:`transaction` so it is always closed.
        """

        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=self.timeout,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self.timeout * 1000)}")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write transaction with rollback on every exception."""

        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self, *, seed_foods: bool = True) -> None:
        """Create or migrate the database, safely repeatable on every startup."""

        schema_path = Path(__file__).with_name("schema.sql")
        schema_sql = schema_path.read_text(encoding="utf-8")
        with self.connection() as connection:
            # Journal mode is persistent and must be selected outside a transaction.
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_version "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version"
            ).fetchone()[0]
            if current > LATEST_SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {current} is newer than supported "
                    f"schema {LATEST_SCHEMA_VERSION}"
                )

            # Version 1 is a single idempotent migration. Re-running the CREATE
            # statements also repairs an interrupted first initialization.
            connection.executescript(schema_sql)
            timestamp = now_iso()
            connection.execute(
                "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (LATEST_SCHEMA_VERSION, timestamp),
            )
            connection.execute(
                "INSERT OR IGNORE INTO recalculation_state(id, dirty_from_date, updated_at) "
                "VALUES (1, NULL, ?)",
                (timestamp,),
            )
            if seed_foods:
                from app.db.seed_foods import seed_builtin_foods

                seed_builtin_foods(connection)

    def get_schema_version(self) -> int:
        with self.connection() as connection:
            try:
                row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM schema_version"
                ).fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row[0])

    def mark_dirty(
        self,
        value: date | datetime | str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        """Invalidate cached daily results from a date onward.

        If a connection is supplied this participates in the caller's transaction,
        so a failed fact write cannot leave an unrelated dirty marker behind.
        """

        dirty_date = normalize_date(value)

        def apply(conn: sqlite3.Connection) -> None:
            timestamp = now_iso()
            conn.execute(
                "INSERT OR IGNORE INTO recalculation_state(id, dirty_from_date, updated_at) "
                "VALUES (1, NULL, ?)",
                (timestamp,),
            )
            conn.execute(
                "UPDATE recalculation_state "
                "SET dirty_from_date = CASE "
                "  WHEN dirty_from_date IS NULL OR ? < dirty_from_date THEN ? "
                "  ELSE dirty_from_date END, updated_at = ? WHERE id = 1",
                (dirty_date, dirty_date, timestamp),
            )
            conn.execute(
                "UPDATE daily_metrics_cache SET is_dirty = 1 WHERE date >= ?",
                (dirty_date,),
            )

        if connection is not None:
            apply(connection)
        else:
            with self.transaction() as own_connection:
                apply(own_connection)
        return dirty_date

    invalidate_from = mark_dirty

    def get_dirty_from_date(self) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT dirty_from_date FROM recalculation_state WHERE id = 1"
            ).fetchone()
        return None if row is None else row[0]

    def clear_dirty(self) -> None:
        """Clear invalidation after the calculation layer completes its rebuild."""

        with self.transaction() as connection:
            connection.execute(
                "UPDATE recalculation_state SET dirty_from_date = NULL, updated_at = ? "
                "WHERE id = 1",
                (now_iso(),),
            )
            connection.execute("UPDATE daily_metrics_cache SET is_dirty = 0")

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return default if row is None else str(row[0])

    def set_setting(self, key: str, value: str) -> None:
        timestamp = now_iso()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, timestamp),
            )

