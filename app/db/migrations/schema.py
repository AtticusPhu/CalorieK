"""Versioned schema contracts and the one-way kcal storage import boundary."""

from __future__ import annotations

import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from app.energy_units import KJ_PER_KCAL, kcal_to_kj


ENERGY_COLUMN_MIGRATIONS = {
    "foods": (("kcal", "kj"),),
    "intake_events": (("kcal_snapshot", "kj_snapshot"),),
    "exercise_types": (("default_active_kcal", "default_active_kj"),),
    "exercise_events": (("active_kcal", "active_kj"),),
    "calibration_runs": (("calibration_kcal_day", "calibration_kj_day"),),
    "daily_metrics_cache": (
        ("intake_kcal", "intake_kj"),
        ("baseline_kcal", "baseline_kj"),
        ("exercise_kcal", "exercise_kj"),
        ("total_burn_kcal", "total_burn_kj"),
        ("balance_kcal", "balance_kj"),
        ("calibration_kcal", "calibration_kj"),
    ),
}

# Add only nullable metadata. Never reinterpret the legacy non-null macro
# columns: their default zero cannot establish whether nutrition was known.
NUTRITION_COLUMN_MIGRATIONS = {
    "foods": tuple(
        (name, f"REAL CHECK ({name} IS NULL OR {name} >= 0)")
        for name in ("protein_g_per_100g", "fiber_g_per_100g", "fat_g_per_100g", "carbs_g_per_100g")
    ),
    "intake_events": tuple(
        (name, f"REAL CHECK ({name} IS NULL OR {name} >= 0)")
        for name in ("protein_g", "fiber_g", "fat_g", "carbs_g")
    ) + (("nutrition_complete", "INTEGER CHECK (nutrition_complete IS NULL OR nutrition_complete IN (0, 1))"),),
}


def _nutrition_ddl() -> str:
    return "\n".join(
        f'ALTER TABLE "{table}" ADD COLUMN "{name}" {definition};'
        for table, columns in NUTRITION_COLUMN_MIGRATIONS.items()
        for name, definition in columns
    )


def schema_sql_for_version(version: int) -> str:
    """Return frozen legacy contracts plus the additive v3 extension.

    Keep this explicit version boundary when introducing future migrations: the
    v1 contract must continue to describe legacy bytes before they are upgraded.
    """

    if version not in (1, 2, 3):
        raise RuntimeError(f"unsupported schema version: {version}")
    sql = (Path(__file__).parent.parent / "schema.sql").read_text(encoding="utf-8")
    if version == 1:
        for columns in ENERGY_COLUMN_MIGRATIONS.values():
            for old_name, new_name in columns:
                sql = re.sub(rf"\b{new_name}\b", old_name, sql)
    if version == 3:
        sql += "\n" + _nutrition_ddl() + "\n"
    return sql


def migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    """Only append unknown nutrition columns inside the caller's transaction.

    No UPDATE/DELETE, seeding, energy conversion or cache invalidation belongs
    here. The original column values, timestamps and sequence values survive.
    """

    validate_schema(connection, 2)
    execute_schema(connection, _nutrition_ddl())
    validate_schema(connection, 3)


def execute_schema(connection: sqlite3.Connection, sql: str) -> None:
    """Execute DDL without executescript's implicit pre-transaction commit."""

    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise RuntimeError("incomplete database schema statement")


def _definition_tokens(sql: str) -> tuple[str, ...]:
    """Compare DDL constraints without depending on SQLite's identifier quoting.

    PRAGMA table_info does not expose CHECK constraints or AUTOINCREMENT.
    SQLite adds quotes to identifiers during ALTER TABLE RENAME COLUMN, so raw
    sqlite_master SQL equality would incorrectly reject a legitimate upgrade.
    """

    tokens = re.findall(
        r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|`(?:[^`]|``)*`|\[[^\]]*\]|\w+|[^\s]",
        sql,
    )
    normalized = []
    for token in tokens:
        if token.startswith("'"):
            normalized.append(token)
        elif token.startswith(('"', '`')):
            normalized.append(token[1:-1].replace(token[0] * 2, token[0]).lower())
        elif token.startswith("["):
            normalized.append(token[1:-1].lower())
        else:
            normalized.append(token.lower())
    return tuple(normalized)


def schema_signature(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    signature: dict[str, Any] = {}
    for raw_name, definition in tables:
        name = str(raw_name)
        quoted = name.replace('"', '""')
        indexes = []
        for index in connection.execute(f'PRAGMA index_list("{quoted}")'):
            index_name = str(index[1]).replace('"', '""')
            columns = tuple(
                row[2] for row in connection.execute(f'PRAGMA index_info("{index_name}")')
            )
            indexes.append((index[2], columns, index[4]))
        signature[name] = {
            "definition": _definition_tokens(definition),
            "columns": [tuple(row[1:6]) for row in connection.execute(f'PRAGMA table_info("{quoted}")')],
            "foreign_keys": [tuple(row[2:8]) for row in connection.execute(f'PRAGMA foreign_key_list("{quoted}")')],
            "indexes": sorted(indexes, key=repr),
        }
    return signature


def validate_schema(connection: sqlite3.Connection, version: int) -> None:
    """Reject damaged or mislabeled databases before any migration/seed writes."""

    with closing(sqlite3.connect(":memory:")) as expected:
        expected.executescript(schema_sql_for_version(version))
        expected_signature = schema_signature(expected)
    actual_signature = schema_signature(connection)
    mismatches = sorted(
        name for name in expected_signature.keys() | actual_signature.keys()
        if actual_signature.get(name) != expected_signature.get(name)
    )
    if mismatches:
        raise RuntimeError("database schema structure is invalid: " + ", ".join(mismatches))
    if any(
        not isinstance(row[0], int) or not 1 <= row[0] <= version
        for row in connection.execute("SELECT version FROM schema_version")
    ):
        raise RuntimeError("database has an invalid schema version history")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if not integrity or integrity[0] != "ok":
        raise RuntimeError("database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("database contains invalid foreign keys")
    if connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('trigger', 'view')"
    ).fetchone() is not None:
        raise RuntimeError("database contains unsupported triggers or views")


def migrate_v1_to_v2(connection: sqlite3.Connection, timestamp: str) -> None:
    """Convert a validated v1 database inside the caller's atomic transaction."""

    validate_schema(connection, 1)
    # REAL affinity alone permits text and infinity. Refuse those values before
    # SQLite multiplication can silently coerce text to zero or overflow a REAL.
    for table, columns in ENERGY_COLUMN_MIGRATIONS.items():
        for old_name, _ in columns:
            for (value,) in connection.execute(f'SELECT "{old_name}" FROM "{table}"'):
                if value is not None and (
                    not isinstance(value, (int, float))
                    or not math.isfinite(kcal_to_kj(value))
                ):
                    raise RuntimeError(f"legacy energy value is invalid: {table}.{old_name}")
    for table, columns in ENERGY_COLUMN_MIGRATIONS.items():
        for old_name, new_name in columns:
            connection.execute(
                f'ALTER TABLE "{table}" RENAME COLUMN "{old_name}" TO "{new_name}"'
            )
            connection.execute(
                f'UPDATE "{table}" SET "{new_name}" = "{new_name}" * ?',
                (KJ_PER_KCAL,),
            )

    setting = connection.execute(
        "SELECT value FROM app_settings WHERE key = 'kcal_per_kg'"
    ).fetchone()
    if connection.execute(
        "SELECT 1 FROM app_settings WHERE key = 'kj_per_kg'"
    ).fetchone() is not None:
        # v1 never wrote this key. Even without the legacy key its provenance is
        # ambiguous, so do not guess a unit and silently reinterpret user data.
        raise RuntimeError("legacy database contains ambiguous energy-to-weight settings")
    if setting is not None:
        try:
            converted = kcal_to_kj(float(setting[0]))
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("legacy energy-to-weight setting is invalid") from exc
        if not math.isfinite(converted) or converted <= 0:
            raise RuntimeError("legacy energy-to-weight setting is invalid")
        connection.execute(
            "UPDATE app_settings SET key = 'kj_per_kg', value = ?, updated_at = ? "
            "WHERE key = 'kcal_per_kg'",
            (repr(converted), timestamp),
        )

    # Derived results are reproducible. Rebuild them under the new calculation
    # version rather than trusting old results after crossing the unit boundary.
    earliest = connection.execute(
        "SELECT MIN(day) FROM ("
        "SELECT local_date AS day FROM weight_measurements UNION ALL "
        "SELECT local_date FROM intake_events UNION ALL "
        "SELECT local_date FROM exercise_events UNION ALL "
        "SELECT effective_from FROM profile_revisions UNION ALL "
        "SELECT date FROM daily_metrics_cache UNION ALL "
        "SELECT dirty_from_date FROM recalculation_state)"
    ).fetchone()[0]
    connection.execute("DELETE FROM daily_metrics_cache")
    connection.execute("DELETE FROM calibration_runs")
    connection.execute(
        "INSERT INTO recalculation_state(id, dirty_from_date, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET dirty_from_date = excluded.dirty_from_date, "
        "updated_at = excluded.updated_at",
        (earliest, timestamp),
    )
    validate_schema(connection, 2)
