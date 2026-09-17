"""Lossless v0.0.1 fixtures and failure injection; temporary databases only."""

from contextlib import closing
from datetime import date
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.db.database import Database
from app.db.migrations import (
    migrate_v1_to_v2, migrate_v2_to_v3, schema_signature, schema_sql_for_version, validate_schema,
)
from app.nutrition import FOOD_NUTRIENT_FIELDS, NUTRIENT_NAMES
from app.services.backup_service import BackupError, BackupService
from app.services.food_service import FoodService
from app.services.nutrition_service import NutritionService
from app.version import CALCULATION_VERSION, SCHEMA_VERSION
from tests.test_energy_migration import create_legacy_database


def create_v2_database(path: Path) -> None:
    """Build actual v2, without calling today's initialize or services."""
    create_legacy_database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("BEGIN IMMEDIATE")
        migrate_v1_to_v2(connection, "v2-old")
        connection.execute("INSERT INTO schema_version VALUES (2, 'v2-old')")
        connection.execute(
            "INSERT INTO calibration_runs(window_start, window_end, weight_sample_count, days_span, "
            "calibration_kj_day, model_version, created_at) VALUES ('2026-08-19', '2026-08-20', 2, 1, ?, ?, 'v2-old')",
            (123.4567890123, CALCULATION_VERSION),
        )
        connection.execute(
            "INSERT INTO daily_metrics_cache(date, open_kg, close_kg, predicted_close_kg, intake_kj, "
            "balance_kj, calculation_version, calculated_at) VALUES ('2026-08-20', 70.1234, 70.4567, 70.23456789, 1234.56789, -321.123456, ?, 'v2-old')",
            (CALCULATION_VERSION,),
        )
        connection.execute("INSERT INTO app_settings VALUES ('display_energy_unit', 'kcal', 'v2-old')")
        connection.execute("UPDATE recalculation_state SET dirty_from_date = NULL, updated_at = 'v2-old'")
        connection.execute("UPDATE foods SET default_serving = 37.123456789, active = 0 WHERE id = 1")
        connection.execute("UPDATE intake_events SET protein_snapshot = 17.123456789, fiber_snapshot = 5.4321")
        connection.commit()


def dump(path: Path) -> tuple[str, ...]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        return tuple(connection.iterdump())


def legacy_rows(path: Path, columns: dict | None = None) -> tuple[dict, dict]:
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
        if columns is None:
            tables = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_version' ORDER BY name"
            )]
            columns = {table: [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')] for table in tables}
        rows = {}
        for table, names in columns.items():
            fields = ", ".join(f'"{name}"' for name in names)
            rows[table] = connection.execute(f'SELECT {fields} FROM "{table}" ORDER BY rowid').fetchall()
        return columns, rows


class NutritionMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "v001" / "caloriek.sqlite3"
        create_v2_database(self.path)
        self.db = Database(self.path, timeout=0.2)

    def test_upgrade_preserves_every_old_column_row_sequence_and_cache(self) -> None:
        columns, before = legacy_rows(self.path)
        original = dump(self.path)

        def inspect_before_upgrade(connection):
            snapshot = self.db.last_migration_snapshot
            self.assertIsNotNone(snapshot)
            self.assertEqual(dump(snapshot), original)
            self.assertIn("pre_migration_v2_to_v3_", snapshot.name)
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 2)
            migrate_v2_to_v3(connection)

        with patch("app.db.database.migrate_v2_to_v3", side_effect=inspect_before_upgrade):
            self.db.initialize()  # Default seeding must NOT change the legacy catalogue.
        self.assertEqual(self.db.get_schema_version(), 3)
        self.assertEqual(legacy_rows(self.path, columns)[1], before)
        with self.db.connection() as connection:
            validate_schema(connection, 3)
            self.assertEqual([row[0] for row in connection.execute("SELECT version FROM schema_version ORDER BY version")], [1, 2, 3])
            for table, fields in (("foods", FOOD_NUTRIENT_FIELDS), ("intake_events", (*NUTRIENT_NAMES, "nutrition_complete"))):
                self.assertTrue(all(value is None for row in connection.execute(f'SELECT {", ".join(fields)} FROM {table}') for value in row))
        day = NutritionService(self.db).daily_totals("2026-08-20")
        self.assertEqual(day.indication, "部分记录无营养数据")
        self.assertEqual(day.nutrients.as_tuple(), (None,) * 4)
        # Historical edits scale NULL snapshots, never backfill from old macros.
        FoodService(self.db).update_intake_event(1, amount=71)
        self.assertEqual(NutritionService(self.db).daily_totals("2026-08-20").nutrients.as_tuple(), (None,) * 4)

    def test_repeated_initialize_is_logically_identical_and_takes_no_new_snapshot(self) -> None:
        self.db.initialize()
        before = dump(self.path)
        with patch("app.db.database.create_pre_migration_snapshot") as snapshot:
            self.db.initialize()
            self.db.initialize()
            snapshot.assert_not_called()
        self.assertEqual(dump(self.path), before)

    def test_valid_v2_without_optional_rows_is_not_silently_filled(self) -> None:
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute("DELETE FROM recalculation_state")
            connection.execute("DELETE FROM food_servings")
            connection.commit()
        columns, before = legacy_rows(self.path)
        ApplicationContext(self.path.parent)
        ApplicationContext(self.path.parent)
        self.assertEqual(legacy_rows(self.path, columns)[1], before)

    def test_application_startup_and_reopen_do_not_seed_or_recalculate_v2_data(self) -> None:
        columns, before = legacy_rows(self.path)
        with patch("app.services.daily_metrics_service.DailyMetricsService.ensure_calculated") as calculate:
            context = ApplicationContext(self.path.parent)
            context.get_daily_nutrition(date(2026, 8, 20))
            ApplicationContext(self.path.parent)
            calculate.assert_not_called()
        self.assertEqual(legacy_rows(self.path, columns)[1], before)

    def test_fresh_and_migrated_schema_have_identical_contract(self) -> None:
        self.db.initialize()
        fresh = Database(self.root / "fresh.sqlite3")
        fresh.initialize()
        with self.db.connection() as migrated, fresh.connection() as new:
            self.assertEqual(schema_signature(migrated), schema_signature(new))
        self.assertIsNone(fresh.last_migration_snapshot)

    def test_v2_snapshot_includes_committed_wal_and_is_standalone(self) -> None:
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA journal_mode = WAL")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("INSERT INTO app_settings VALUES ('wal-only', 'committed', 'old')")
            writer.commit()
            self.assertTrue(Path(f"{self.path}-wal").exists())
            before = dump(self.path)
            self.db.initialize()
        snapshot = self.db.last_migration_snapshot
        self.assertEqual(dump(snapshot), before)
        with closing(sqlite3.connect(snapshot)) as connection:
            validate_schema(connection, 2)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
        self.assertFalse(Path(f"{snapshot}-wal").exists())
        self.assertFalse(Path(f"{snapshot}-shm").exists())

    def test_failure_after_partial_ddl_rolls_back_and_retains_original_snapshot(self) -> None:
        original = dump(self.path)

        def partial_failure(connection):
            connection.execute("ALTER TABLE foods ADD COLUMN protein_g_per_100g REAL")
            raise RuntimeError("injected additive migration failure")

        with patch("app.db.database.migrate_v2_to_v3", side_effect=partial_failure):
            with self.assertRaisesRegex(RuntimeError, "injected additive migration failure"):
                self.db.initialize()
        self.assertEqual(dump(self.path), original)
        snapshot = self.db.last_migration_snapshot
        self.assertEqual(dump(snapshot), original)
        # A standalone copy can be opened and retried without any original WAL.
        recovered = self.root / "recovered.sqlite3"
        recovered.write_bytes(snapshot.read_bytes())
        restored = Database(recovered)
        restored.initialize()
        self.assertEqual(restored.get_schema_version(), 3)
        self.assertEqual(dump(snapshot), original)

    def test_snapshot_failure_prevents_any_migration(self) -> None:
        original = dump(self.path)
        with patch("app.db.migrations.snapshot.os.replace", side_effect=OSError("disk full")):
            with patch("app.db.database.migrate_v2_to_v3") as migrate:
                with self.assertRaisesRegex(RuntimeError, "safety snapshot failed"):
                    self.db.initialize()
                migrate.assert_not_called()
        self.assertEqual(dump(self.path), original)
        self.assertEqual(list((self.path.parent / "backups").iterdir()), [])

    def test_malformed_incomplete_mislabeled_and_newer_schema_are_refused(self) -> None:
        for case in ("missing_table", "missing_check", "mislabeled", "newer"):
            with self.subTest(case=case):
                path = self.root / case / "caloriek.sqlite3"
                path.parent.mkdir()
                sql = schema_sql_for_version(2)
                if case == "missing_check":
                    sql = sql.replace("CHECK (kj >= 0)", "CHECK (kj > 0)")
                with closing(sqlite3.connect(path)) as connection:
                    connection.executescript(sql)
                    version = SCHEMA_VERSION + 1 if case == "newer" else 3 if case == "mislabeled" else 2
                    connection.execute("INSERT INTO schema_version VALUES (?, 'old')", (version,))
                    if case == "missing_table":
                        connection.execute("DROP TABLE food_servings")
                    connection.commit()
                before = path.read_bytes()
                database = Database(path)
                with self.assertRaises(RuntimeError):
                    database.initialize()
                self.assertEqual(path.read_bytes(), before)
                self.assertIsNone(database.last_migration_snapshot)

    def test_v2_backup_restore_migrates_in_staging_and_preserves_source_and_old_values(self) -> None:
        columns, old_values = legacy_rows(self.path)
        original = dump(self.path)
        backup = BackupService(self.path).create_backup(self.root / "v2.zip")
        archive_bytes = backup.read_bytes()
        target = Database(self.root / "live" / "caloriek.sqlite3")
        target.initialize()
        target.set_setting("marker", "live before restore")
        live_service = BackupService(target.path)
        safety = live_service.restore_backup(backup)
        self.assertTrue(safety.exists())
        self.assertEqual(target.get_schema_version(), 3)
        self.assertEqual(legacy_rows(target.path, columns)[1], old_values)
        self.assertEqual(dump(self.path), original)
        self.assertEqual(backup.read_bytes(), archive_bytes)
        self.assertEqual(NutritionService(target).daily_totals("2026-08-20").indication, "部分记录无营养数据")

    def test_v2_restore_failure_after_migration_keeps_live_and_archive_unchanged(self) -> None:
        backup = BackupService(self.path).create_backup(self.root / "v2.zip")
        archive_bytes = backup.read_bytes()
        target = Database(self.root / "live" / "caloriek.sqlite3")
        target.initialize()
        original = dump(target.path)
        versions = []

        def reject_final(connection, version):
            versions.append(version)
            validate_schema(connection, version)
            if version == 3:
                raise RuntimeError("injected final validation failure")

        with patch("app.services.backup_service.validate_schema", side_effect=reject_final):
            with self.assertRaisesRegex(BackupError, "injected final validation failure"):
                BackupService(target.path).restore_backup(backup)
        self.assertEqual(versions, [2, 3])
        self.assertEqual(dump(target.path), original)
        self.assertEqual(backup.read_bytes(), archive_bytes)
        self.assertEqual(list(target.path.parent.glob(".restore_*.sqlite3")), [])

    def test_v3_backup_round_trip_keeps_null_zero_and_precision(self) -> None:
        self.db.initialize()
        foods = FoodService(self.db)
        food = foods.create_food(name="新营养", category="测试", basis_unit="g", kj=123.123456789,
                                 protein_g_per_100g=1.2345678912345, fiber_g_per_100g=0,
                                 fat_g_per_100g=None, carbs_g_per_100g=9.8765432198765)
        event = foods.record_food_intake(food, amount=76.54321, occurred_at="2026-09-01")
        expected_food, expected_event = foods.get_food(food), foods.get_intake_event(event)
        backup = BackupService(self.path).create_backup(self.root / "v3.zip")
        foods.update_food(food, protein_g_per_100g=99)
        BackupService(self.path).restore_backup(backup)
        self.assertEqual(foods.get_food(food), expected_food)
        self.assertEqual(foods.get_intake_event(event), expected_event)
