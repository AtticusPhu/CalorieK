from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.db import LATEST_SCHEMA_VERSION, Database, normalize_date, normalize_datetime
from app.db.seed_foods import BUILTIN_FOODS, seed_builtin_foods


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self._temporary_directory.name) / "data" / "caloriek.sqlite3"
        self.db = Database(self.database_path)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_initialize_schema_wal_and_seed_are_idempotent(self) -> None:
        self.db.initialize()
        self.assertEqual(self.db.get_schema_version(), LATEST_SCHEMA_VERSION)

        expected_tables = {
            "profile",
            "profile_revisions",
            "weight_measurements",
            "foods",
            "food_servings",
            "recipes",
            "recipe_items",
            "intake_events",
            "exercise_types",
            "exercise_events",
            "calibration_runs",
            "daily_metrics_cache",
            "app_settings",
            "recalculation_state",
            "schema_version",
        }
        with self.db.connection() as connection:
            table_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            self.assertTrue(expected_tables <= table_names)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
            food_count = int(connection.execute("SELECT COUNT(*) FROM foods").fetchone()[0])
            self.assertEqual(food_count, len(BUILTIN_FOODS))
            connection.execute(
                "UPDATE foods SET kcal = 999, user_modified = 1 WHERE builtin_key = ?",
                ("beef-lean",),
            )

        self.db.initialize()
        self.assertEqual(seed_builtin_foods(self.db), 0)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM foods").fetchone()[0], food_count)
            edited = connection.execute(
                "SELECT kcal, user_modified FROM foods WHERE builtin_key = ?",
                ("beef-lean",),
            ).fetchone()
        self.assertEqual(float(edited["kcal"]), 999.0)
        self.assertEqual(int(edited["user_modified"]), 1)

    def test_transaction_rolls_back_all_changes(self) -> None:
        self.db.initialize(seed_foods=False)
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with self.db.transaction() as connection:
                connection.execute(
                    "INSERT INTO app_settings(key, value, updated_at) VALUES ('x', '1', 'now')"
                )
                raise RuntimeError("abort")
        self.assertIsNone(self.db.get_setting("x"))

    def test_newer_schema_is_refused(self) -> None:
        database = Database(Path(self._temporary_directory.name) / "future.sqlite3")
        with database.connection() as connection:
            connection.execute(
                "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, 'future')",
                (LATEST_SCHEMA_VERSION + 1,),
            )
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            database.initialize(seed_foods=False)

    def test_dirty_marker_keeps_earliest_date_and_marks_future_cache(self) -> None:
        self.db.initialize(seed_foods=False)
        with self.db.transaction() as connection:
            connection.executemany(
                "INSERT INTO daily_metrics_cache(date, calculation_version, calculated_at) "
                "VALUES (?, 'v1', 'now')",
                (("2026-08-10",), ("2026-08-20",), ("2026-08-30",)),
            )

        self.db.mark_dirty("2026-08-20")
        self.db.mark_dirty("2026-08-25")
        self.db.mark_dirty("2026-08-15T23:59:00+08:00")
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-15")
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT date, is_dirty FROM daily_metrics_cache ORDER BY date"
            ).fetchall()
        self.assertEqual([(row["date"], row["is_dirty"]) for row in rows], [
            ("2026-08-10", 0),
            ("2026-08-20", 1),
            ("2026-08-30", 1),
        ])

        self.db.clear_dirty()
        self.assertIsNone(self.db.get_dirty_from_date())
        with self.db.connection() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM daily_metrics_cache WHERE is_dirty = 1"
                ).fetchone()[0],
                0,
            )

    def test_date_normalization_rejects_invalid_input(self) -> None:
        self.assertEqual(normalize_date("2026-08-25T12:30:00+08:00"), "2026-08-25")
        self.assertEqual(
            normalize_datetime("2026-08-25 12:30:00")[1],
            "2026-08-25",
        )
        with self.assertRaises(ValueError):
            normalize_date("not-a-date")


if __name__ == "__main__":
    unittest.main()
