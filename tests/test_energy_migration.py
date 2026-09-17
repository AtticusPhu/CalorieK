"""Legacy storage migration regression cases; runtime values are canonical kJ."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from app.db.database import Database
from app.db.migrations import schema_sql_for_version
from app.energy_units import kcal_to_kj
from app.version import SCHEMA_VERSION


def create_legacy_database(path: Path, *, schema_sql: str | None = None) -> None:
    """Build a populated v1 fixture without going through current services."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(schema_sql if schema_sql is not None else schema_sql_for_version(1))
        connection.execute("INSERT INTO schema_version VALUES (1, 'legacy')")
        connection.execute(
            "INSERT INTO profile VALUES (1, 'female', '1990-01-02', '旧档案', 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO profile_revisions(profile_id, effective_from, gender, birth_date, "
            "height_cm, wake_time, sleep_time, awake_multiplier, sleep_multiplier, created_at) "
            "VALUES (1, '2026-08-19', 'female', '1990-01-02', 168.123, '07:30', '23:30', "
            "1.2345, 0.9876, 'old')"
        )
        connection.execute(
            "INSERT INTO foods(id, name, category, basis_unit, kcal, protein_g, "
            "is_favorite, user_modified, created_at, updated_at) "
            "VALUES (1, '高精度历史食品', '自定义', 'g', ?, 12.3, 1, 1, 'old', 'old')",
            (123.456789123,),
        )
        connection.execute(
            "INSERT INTO food_servings(food_id, name, base_amount, created_at, updated_at) "
            "VALUES (1, '原始份量', 35.5, 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO recipes(id, name, description, is_favorite, created_at, updated_at) "
            "VALUES (1, '历史菜谱', '原始说明', 1, 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO recipe_items(recipe_id, food_id, amount, unit, created_at, updated_at) "
            "VALUES (1, 1, 50.123456789, 'g', 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO intake_events(occurred_at, local_date, source_type, source_id, "
            "name_snapshot, amount, unit, kcal_snapshot, created_at, updated_at) "
            "VALUES ('2026-08-20T12:00:00', '2026-08-20', 'FOOD', 1, '历史快照', "
            "35.5, 'g', ?, 'old', 'old')",
            (43.827160138665,),
        )
        connection.execute(
            "INSERT INTO exercise_types(id, name, default_duration_min, default_active_kcal, "
            "created_at, updated_at) VALUES (1, '历史运动', 30, ?, 'old', 'old')",
            (210.123456789,),
        )
        connection.execute(
            "INSERT INTO exercise_types(id, name, created_at, updated_at) "
            "VALUES (2, '无默认热量', 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO exercise_events(occurred_at, local_date, exercise_type_id, "
            "name_snapshot, duration_min, active_kcal, created_at, updated_at) "
            "VALUES ('2026-08-20T13:00:00', '2026-08-20', 1, '历史运动快照', "
            "30, ?, 'old', 'old')",
            (205.987654321,),
        )
        connection.execute(
            "INSERT INTO weight_measurements(occurred_at, local_date, weight_kg, "
            "created_at, updated_at) "
            "VALUES ('2026-08-19T08:00:00', '2026-08-19', 70.123, 'old', 'old')"
        )
        connection.execute(
            "INSERT INTO calibration_runs(window_start, window_end, weight_sample_count, "
            "days_span, calibration_kcal_day, model_version, created_at) "
            "VALUES ('2026-08-19', '2026-08-20', 2, 1, -35.123, 'legacy', 'old')"
        )
        connection.execute(
            "INSERT INTO daily_metrics_cache(date, intake_kcal, baseline_kcal, exercise_kcal, "
            "total_burn_kcal, balance_kcal, calibration_kcal, calculation_version, calculated_at) "
            "VALUES ('2026-08-20', 100, 200, 50, 250, -150, -35.123, 'legacy', 'old')"
        )
        connection.execute(
            "INSERT INTO app_settings VALUES ('kcal_per_kg', '7700.123456789', 'old')"
        )
        connection.execute("INSERT INTO app_settings VALUES ('theme', 'light', 'old')")
        connection.execute("INSERT INTO recalculation_state VALUES (1, NULL, 'old')")
        connection.commit()


class EnergyMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "legacy.sqlite3"
        create_legacy_database(self.path)
        self.db = Database(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_migration_preserves_fact_precision_and_invalidates_derived_results(self) -> None:
        preserved_tables = (
            "profile", "profile_revisions", "weight_measurements", "food_servings",
            "recipes", "recipe_items",
        )
        with self.db.connection() as connection:
            original_facts = {
                table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id')]
                for table in preserved_tables
            }
        self.db.initialize(seed_foods=False)

        self.assertEqual(self.db.get_schema_version(), SCHEMA_VERSION)
        self.assertIsNone(self.db.get_setting("kcal_per_kg"))
        self.assertEqual(float(self.db.get_setting("kj_per_kg")), kcal_to_kj(7700.123456789))
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-19")
        self.assertEqual(self.db.get_setting("theme"), "light")
        with self.db.connection() as connection:
            for table, original_rows in original_facts.items():
                self.assertEqual(
                    [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY id')],
                    original_rows,
                    table,
                )
            food = connection.execute("SELECT * FROM foods WHERE id = 1").fetchone()
            self.assertEqual(food["kj"], kcal_to_kj(123.456789123))
            self.assertEqual((food["protein_g"], food["is_favorite"], food["user_modified"]), (12.3, 1, 1))
            self.assertEqual(food["updated_at"], "old")
            self.assertEqual(connection.execute("SELECT base_amount FROM food_servings").fetchone()[0], 35.5)
            intake = connection.execute("SELECT * FROM intake_events").fetchone()
            self.assertEqual(intake["kj_snapshot"], kcal_to_kj(43.827160138665))
            self.assertEqual(intake["name_snapshot"], "历史快照")
            self.assertEqual(intake["source_id"], 1)
            self.assertEqual(connection.execute("SELECT default_active_kj FROM exercise_types WHERE id = 1").fetchone()[0], kcal_to_kj(210.123456789))
            self.assertIsNone(connection.execute("SELECT default_active_kj FROM exercise_types WHERE id = 2").fetchone()[0])
            self.assertEqual(connection.execute("SELECT active_kj FROM exercise_events").fetchone()[0], kcal_to_kj(205.987654321))
            self.assertEqual(connection.execute("SELECT weight_kg FROM weight_measurements").fetchone()[0], 70.123)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_metrics_cache").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM calibration_runs").fetchone()[0], 0)
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            for table in ("foods", "intake_events", "exercise_types", "exercise_events", "calibration_runs", "daily_metrics_cache"):
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
                self.assertFalse(any("kcal" in column for column in columns))

    def test_second_initialize_does_not_convert_canonical_values_again(self) -> None:
        self.db.initialize(seed_foods=False)
        with self.db.connection() as connection:
            before = connection.execute("SELECT kj FROM foods WHERE id = 1").fetchone()[0]
        self.db.initialize(seed_foods=False)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("SELECT kj FROM foods WHERE id = 1").fetchone()[0], before)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM schema_version WHERE version = 2").fetchone()[0], 1)

    def test_failed_migration_rolls_back_values_columns_and_schema_version(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE app_settings SET value = 'invalid' WHERE key = 'kcal_per_kg'")
        with self.assertRaisesRegex(RuntimeError, "energy-to-weight setting is invalid"):
            self.db.initialize(seed_foods=False)

        self.assertEqual(self.db.get_schema_version(), 1)
        with self.db.connection() as connection:
            food = connection.execute("SELECT * FROM foods WHERE id = 1").fetchone()
            self.assertEqual(food["kcal"], 123.456789123)
            self.assertNotIn("kj", food.keys())
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM daily_metrics_cache").fetchone()[0], 1)
        self.assertEqual(self.db.get_setting("kcal_per_kg"), "invalid")
        self.assertIsNone(self.db.get_setting("kj_per_kg"))

    def test_legacy_database_with_canonical_setting_is_rejected_as_ambiguous(self) -> None:
        for keep_legacy_setting in (True, False):
            with self.subTest(keep_legacy_setting=keep_legacy_setting):
                with self.db.transaction() as connection:
                    connection.execute(
                        "INSERT OR REPLACE INTO app_settings VALUES ('kj_per_kg', '32216.8', 'old')"
                    )
                    if not keep_legacy_setting:
                        connection.execute("DELETE FROM app_settings WHERE key = 'kcal_per_kg'")
                with self.assertRaisesRegex(RuntimeError, "ambiguous energy-to-weight settings"):
                    self.db.initialize(seed_foods=False)
                self.assertEqual(self.db.get_schema_version(), 1)
                with self.db.connection() as connection:
                    self.assertEqual(connection.execute("SELECT kcal FROM foods WHERE id = 1").fetchone()[0], 123.456789123)

    def test_incomplete_legacy_database_is_rejected_without_silent_repair(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("DROP TABLE food_servings")
        with self.assertRaisesRegex(RuntimeError, "schema structure is invalid"):
            self.db.initialize(seed_foods=False)
        self.assertEqual(self.db.get_schema_version(), 1)
        with self.db.connection() as connection:
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'food_servings'").fetchone())
            self.assertEqual(connection.execute("SELECT kcal FROM foods WHERE id = 1").fetchone()[0], 123.456789123)

    def test_malformed_legacy_schema_is_rejected_before_journal_or_data_changes(self) -> None:
        legacy_sql = schema_sql_for_version(1)
        malformed_schemas = {
            "missing_check": legacy_sql.replace(" CHECK (kcal >= 0)", ""),
            "changed_check": legacy_sql.replace("CHECK (kcal >= 0)", "CHECK (kcal > 0)"),
            "wrong_column_type": legacy_sql.replace("kcal REAL NOT NULL", "kcal TEXT NOT NULL"),
            "missing_autoincrement": legacy_sql.replace(" PRIMARY KEY AUTOINCREMENT", " PRIMARY KEY"),
        }
        for name, sql in malformed_schemas.items():
            with self.subTest(name=name):
                path = Path(self.temporary.name) / f"{name}.sqlite3"
                create_legacy_database(path, schema_sql=sql)
                before = path.read_bytes()
                database = Database(path)
                with self.assertRaisesRegex(RuntimeError, "schema structure is invalid"):
                    database.initialize(seed_foods=False)
                self.assertEqual(path.read_bytes(), before)
                self.assertIsNone(database.last_migration_snapshot)
                with database.connection() as connection:
                    self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_mislabeled_legacy_schema_is_rejected_without_conversion(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE schema_version SET version = 2")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "schema structure is invalid"):
            self.db.initialize(seed_foods=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.db.last_migration_snapshot)

    def test_invalid_version_history_is_rejected_without_conversion(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO schema_version VALUES (-1, 'invalid')")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "invalid schema version history"):
            self.db.initialize(seed_foods=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.db.last_migration_snapshot)

    def test_newer_schema_is_rejected_without_journal_or_data_changes(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            self.db.initialize(seed_foods=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.db.last_migration_snapshot)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_invalid_foreign_keys_are_rejected_without_conversion(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("UPDATE food_servings SET food_id = 99999")
            connection.commit()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "invalid foreign keys"):
            self.db.initialize(seed_foods=False)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIsNone(self.db.last_migration_snapshot)

    def test_invalid_legacy_energy_is_not_silently_coerced_or_overflowed(self) -> None:
        for value in ("not-a-number", float("inf"), 1e308):
            with self.subTest(value=value):
                with self.db.transaction() as connection:
                    connection.execute("UPDATE foods SET kcal = ? WHERE id = 1", (value,))
                with self.assertRaisesRegex(RuntimeError, "legacy energy value is invalid"):
                    self.db.initialize(seed_foods=False)
                self.assertEqual(self.db.get_schema_version(), 1)
                with self.db.connection() as connection:
                    self.assertEqual(connection.execute("SELECT kcal FROM foods WHERE id = 1").fetchone()[0], value)


if __name__ == "__main__":
    unittest.main()
