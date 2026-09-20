"""SQLite connection, migration, and cache-invalidation primitives.

The application keeps every write behind this module so that SQLite settings and
transaction semantics are consistent outside the GUI as well as inside it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.db.migrations import execute_schema, migrate_v1_to_v2, migrate_v2_to_v3, schema_sql_for_version, validate_schema
from app.db.migrations.snapshot import create_pre_cal12_nutrition_snapshot, create_pre_migration_snapshot
from app.db.nutrition_repair import has_cal12_nutrition_repair_candidates, repair_cal12_nutrition
from app.version import SCHEMA_VERSION

LATEST_SCHEMA_VERSION = SCHEMA_VERSION


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
        self.last_migration_snapshot: Path | None = None
        self.last_migration_from_version: int | None = None
        self.initial_schema_version: int | None = None
        self.last_nutrition_repair_snapshot: Path | None = None
        self.last_nutrition_repair_count = 0

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

    @staticmethod
    def _initialization_version(connection: sqlite3.Connection) -> int:
        """Reject unknown schemas before any journal-mode or schema writes."""

        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'"
            )
        }
        current = (
            connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_version"
            ).fetchone()[0]
            if "schema_version" in tables else 0
        )
        if not isinstance(current, int) or current < 0:
            raise RuntimeError("database has an invalid schema version")
        if current > LATEST_SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema {current} is newer than supported "
                f"schema {LATEST_SCHEMA_VERSION}"
            )
        if current == 0 and tables - {"schema_version"}:
            raise RuntimeError("existing database has no valid schema version")
        return current

    def initialize(self, *, seed_foods: bool = True) -> None:
        """Create/migrate atomically; retain original data before any upgrade."""

        self.last_migration_snapshot = None
        self.last_migration_from_version = None
        self.initial_schema_version = None
        self.last_nutrition_repair_snapshot = None
        self.last_nutrition_repair_count = 0
        with self.connection() as connection:
            current = self._initialization_version(connection)
            if current:
                validate_schema(connection, current)
            # Existing v3 must have no live mutation before a repair snapshot:
            # even a DELETE -> WAL header change would violate that guarantee.
            # Preserve its current mode (normally WAL). New/legacy initialization
            # and the backup restore staging flow still explicitly enable WAL.
            if current != 3:
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()
                if not mode or str(mode[0]).lower() != "wal":
                    raise RuntimeError("could not enable WAL before database initialization")
            connection.execute("BEGIN IMMEDIATE")
            try:
                repaired_count = 0
                # Re-read under the writer lock: another process may have finished
                # its upgrade since preflight. Never apply an upgrade twice.
                current = self._initialization_version(connection)
                original_version = current
                self.initial_schema_version = current
                timestamp = now_iso()
                if current == 0:
                    execute_schema(connection, schema_sql_for_version(LATEST_SCHEMA_VERSION))
                elif current in (1, 2):
                    validate_schema(connection, current)
                    try:
                        if current == 1:
                            self.last_migration_snapshot = create_pre_migration_snapshot(
                                self.path, timeout=self.timeout
                            )
                        else:
                            self.last_migration_snapshot = create_pre_migration_snapshot(
                                self.path, timeout=self.timeout, from_version=2, to_version=3,
                            )
                    except Exception as exc:
                        raise RuntimeError(
                            f"pre-migration safety snapshot failed; migration was not started: {exc}"
                        ) from exc
                    self.last_migration_from_version = current
                    if current == 1:
                        migrate_v1_to_v2(connection, timestamp)
                        connection.execute(
                            "INSERT INTO schema_version(version, applied_at) VALUES (2, ?)",
                            (timestamp,),
                        )
                    migrate_v2_to_v3(connection)
                elif current == LATEST_SCHEMA_VERSION:
                    validate_schema(connection, current)
                    if current == 3 and has_cal12_nutrition_repair_candidates(connection):
                        try:
                            self.last_nutrition_repair_snapshot = create_pre_cal12_nutrition_snapshot(
                                self.path, timeout=self.timeout,
                            )
                        except Exception as exc:
                            raise RuntimeError(
                                f"CAL-12 pre-repair safety snapshot failed; repair was not started: {exc}"
                            ) from exc
                        repaired_count = repair_cal12_nutrition(connection)
                else:
                    raise RuntimeError(f"unsupported database schema {current}")
                if original_version != LATEST_SCHEMA_VERSION:
                    connection.execute(
                        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
                        (LATEST_SCHEMA_VERSION, timestamp),
                    )
                if original_version in (0, 1):
                    connection.execute(
                        "INSERT OR IGNORE INTO recalculation_state(id, dirty_from_date, updated_at) "
                        "VALUES (1, NULL, ?)",
                        (timestamp,),
                    )
                # A v0.0.1 upgrade is metadata-only, even when initialize uses
                # its default arguments. Seeding can insert rows/advance IDs.
                if seed_foods and original_version in (0, 1):
                    from app.db.seed_foods import seed_builtin_foods

                    seed_builtin_foods(connection)
                connection.commit()
                self.last_nutrition_repair_count = repaired_count
            except BaseException as exc:
                try:
                    connection.rollback()
                except Exception as rollback_error:
                    if self.last_nutrition_repair_snapshot is not None:
                        raise RuntimeError(
                            f"CAL-12 nutrition repair failed: {exc}; rollback also failed: {rollback_error}; "
                            f"recover from original schema-v3 safety snapshot: {self.last_nutrition_repair_snapshot}"
                        ) from exc
                    if self.last_migration_snapshot is not None:
                        raise RuntimeError(
                            f"database migration failed: {exc}; rollback also failed: {rollback_error}; "
                            f"recover from original schema-v{self.last_migration_from_version} safety snapshot: {self.last_migration_snapshot}"
                        ) from exc
                    raise
                if self.last_nutrition_repair_snapshot is not None and isinstance(exc, Exception):
                    raise RuntimeError(
                        f"CAL-12 nutrition repair failed and was rolled back: {exc}; "
                        f"original schema-v3 safety snapshot: {self.last_nutrition_repair_snapshot}"
                    ) from exc
                if self.last_migration_snapshot is not None and isinstance(exc, Exception):
                    raise RuntimeError(
                        f"database migration failed and was rolled back: {exc}; "
                        f"original schema-v{self.last_migration_from_version} safety snapshot: {self.last_migration_snapshot}"
                    ) from exc
                raise

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

    def get_setting(
        self, key: str, default: str | None = None, *,
        connection: sqlite3.Connection | None = None,
    ) -> str | None:
        with (nullcontext(connection) if connection is not None else self.connection()) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return default if row is None else str(row[0])

    def set_setting(
        self, key: str, value: str, *, connection: sqlite3.Connection | None = None,
    ) -> None:
        """Upsert a setting, optionally inside a caller-owned transaction.

        A supplied connection is never committed, rolled back, or closed here.
        Without one, retain the standalone atomic-write behavior.
        """
        timestamp = now_iso()
        with (nullcontext(connection) if connection is not None else self.transaction()) as connection:
            connection.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "updated_at = excluded.updated_at",
                (key, value, timestamp),
            )
