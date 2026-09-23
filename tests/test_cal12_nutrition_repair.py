"""Approved CAL-12 repair regressions; synthetic temporary SQLite files only.

Authored for the reviewed Windows BAT workflow, not executed by implementation.
"""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.db.database import Database
from app.db.migrations import validate_schema
from app.db.migrations.snapshot import create_pre_cal12_nutrition_snapshot
from app.db.nutrition_repair import repair_cal12_nutrition
from app.nutrition import intake_contribution
from app.services.backup_service import BackupService
from app.services.nutrition_service import NutritionService
from app.version import APP_VERSION, CALCULATION_VERSION, SCHEMA_VERSION


class Cal12NutritionRepairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "caloriek.sqlite3"
        self.db = Database(self.path, timeout=0.2)
        self.db.initialize(seed_foods=False)

    def event(self, **changes):
        values = dict(
            source_type="FOOD", source_id=987, meal_type="DINNER", name_snapshot="Synthetic saved name",
            amount=73.123456789, unit="ml", kj_snapshot=321.987654321,
            protein_snapshot=12.3456789012345, fiber_snapshot=0.0,
            fat_snapshot=2.3456789012345, carb_snapshot=34.5678901234567,
            protein_g=None, fiber_g=None, fat_g=None, carbs_g=None, nutrition_complete=0,
            occurred_at="2026-09-18T19:12:34+08:00", local_date="2026-09-18",
            note="Original synthetic note", created_at="2026-09-18T19:13:34+08:00",
            updated_at="2026-09-18T20:14:35+08:00", active=1,
        )
        values.update(changes)
        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"INSERT INTO intake_events ({', '.join(values)}) VALUES ({', '.join('?' for _ in values)})",
                tuple(values.values()),
            )
            return cursor.lastrowid

    def rows(self):
        with self.db.connection() as connection:
            return {row["id"]: dict(row) for row in connection.execute("SELECT * FROM intake_events ORDER BY id")}

    def dump(self, path=None):
        with closing(sqlite3.connect((path or self.path).as_uri() + "?mode=ro", uri=True)) as connection:
            return tuple(connection.iterdump())

    def snapshots(self):
        return sorted((self.root / "backups").glob("pre_repair_cal12_nutrition_*.sqlite3"))

    def other_tables(self):
        with self.db.connection() as connection:
            names = [row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name != 'intake_events' ORDER BY name"
            )]
            return {name: [tuple(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                    for name in names}

    def assert_repaired(self, before, after):
        expected = dict(before)
        expected.update(
            protein_g=before["protein_snapshot"], fiber_g=before["fiber_snapshot"],
            fat_g=before["fat_snapshot"], carbs_g=before["carb_snapshot"], nutrition_complete=1,
        )
        # Includes IDs, both timestamps, old snapshots, active, date/time, energy,
        # source, amount/unit, meal and note, not just a selected subset.
        self.assertEqual(after, expected)

    def test_affected_food_repairs_exactly_once_without_changing_other_fields(self):
        event_id = self.event()
        before = self.rows()[event_id]
        self.db.initialize()
        self.assert_repaired(before, self.rows()[event_id])
        self.assertEqual(self.db.last_nutrition_repair_count, 1)
        original_snapshot_bytes = self.snapshots()[0].read_bytes()
        self.db.initialize()
        self.assertEqual(self.db.last_nutrition_repair_count, 0)
        self.assert_repaired(before, self.rows()[event_id])
        self.assertEqual(len(self.snapshots()), 1)
        self.assertEqual(self.snapshots()[0].read_bytes(), original_snapshot_bytes)

    def test_affected_recipe_repairs_exactly_once_using_saved_not_library_nutrients(self):
        event_id = self.event(source_type="RECIPE", unit="recipe", source_id=99999)
        before = self.rows()[event_id]
        self.db.initialize()
        self.assert_repaired(before, self.rows()[event_id])
        self.db.initialize()
        self.assert_repaired(before, self.rows()[event_id])
        self.assertEqual(len(self.snapshots()), 1)

    def test_each_strictly_positive_legacy_dimension_is_sufficient(self):
        fields = ("protein_snapshot", "fiber_snapshot", "fat_snapshot", "carb_snapshot")
        for field in fields:
            self.event(**(dict.fromkeys(fields, 0) | {field: 1.2345678912345}))
        before = self.rows()
        self.db.initialize()
        self.assertEqual(self.db.last_nutrition_repair_count, 4)
        for event_id, row in before.items():
            self.assert_repaired(row, self.rows()[event_id])

    def test_all_zero_legacy_snapshots_stay_incomplete_and_take_no_snapshot(self):
        for source_type in ("FOOD", "RECIPE"):
            self.event(source_type=source_type, protein_snapshot=0, fiber_snapshot=0, fat_snapshot=0, carb_snapshot=0)
        before = self.dump()
        self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.snapshots(), [])
        daily = NutritionService(self.db).daily_totals("2026-09-18")
        self.assertEqual(daily.indication, "部分记录无营养数据")
        self.assertEqual(daily.nutrients.as_tuple(), (None,) * 4)

    def test_custom_rows_are_never_repaired(self):
        self.event(source_type="CUSTOM", source_id=None)
        before = self.dump()
        self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(NutritionService(self.db).daily_totals("2026-09-18").incomplete)

    def test_any_non_null_v3_field_including_zero_excludes_the_entire_row(self):
        for field in ("protein_g", "fiber_g", "fat_g", "carbs_g"):
            for value in (0, 4.5678912345):
                self.event(**{field: value})
        # Even four populated v3 numbers must not weaken complete=0 semantics.
        self.event(protein_g=1, fiber_g=0, fat_g=2, carbs_g=3)
        before = self.dump()
        self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.snapshots(), [])
        self.assertTrue(all(not intake_contribution(row).complete for row in self.rows().values()))

    def test_null_and_one_completeness_rows_are_untouched(self):
        for state in (None, 1):
            for source_type in ("FOOD", "RECIPE"):
                self.event(source_type=source_type, nutrition_complete=state)
        before = self.dump()
        self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.snapshots(), [])

    def test_mixed_batch_repairs_only_predicate_matches_including_inactive_rows(self):
        affected = {self.event(), self.event(source_type="RECIPE", active=0)}
        self.event(source_type="CUSTOM")
        self.event(fiber_g=0)
        self.event(nutrition_complete=None)
        self.event(nutrition_complete=1)
        self.event(protein_snapshot=0, fiber_snapshot=0, fat_snapshot=0, carb_snapshot=0)
        before = self.rows()
        self.db.initialize()
        after = self.rows()
        self.assertEqual(self.db.last_nutrition_repair_count, len(affected))
        for event_id, original in before.items():
            if event_id in affected:
                self.assert_repaired(original, after[event_id])
            else:
                self.assertEqual(after[event_id], original)

    def test_snapshot_is_published_before_first_write_and_contains_original_v3(self):
        self.event()
        self.event(source_type="RECIPE")
        before = self.dump()

        def inspect_before_update(connection):
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.total_changes, 0)
            snapshot = self.db.last_nutrition_repair_snapshot
            self.assertEqual(self.snapshots(), [snapshot])
            self.assertEqual(self.dump(snapshot), before)
            self.assertEqual(self.dump(), before)
            with closing(sqlite3.connect(snapshot)) as original:
                validate_schema(original, 3)
                self.assertEqual(original.execute("PRAGMA journal_mode").fetchone()[0], "delete")
                self.assertEqual(original.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 3)
            return repair_cal12_nutrition(connection)

        with patch("app.db.database.repair_cal12_nutrition", side_effect=inspect_before_update):
            self.db.initialize()
        snapshot = self.db.last_nutrition_repair_snapshot
        self.assertIsNone(self.db.last_migration_snapshot)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(f"{snapshot}{suffix}").exists())
        self.assertEqual(self.dump(snapshot), before)
        self.assertEqual(list((self.root / "backups").glob("pre_migration_*.sqlite3")), [])

    def test_snapshot_includes_uncheckpointed_committed_wal_under_writer_lock(self):
        event_id = self.event()
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE intake_events SET protein_snapshot=? WHERE id=?", (98.7654321098765, event_id))
            writer.execute("INSERT INTO app_settings VALUES ('wal-marker', 'saved', 'old')")
            writer.commit()
            self.assertGreater(Path(f"{self.path}-wal").stat().st_size, 0)
            before = self.dump()

            def inspect_lock(path, *, timeout):
                with closing(sqlite3.connect(path, timeout=0)) as contender:
                    with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                        contender.execute("UPDATE intake_events SET protein_snapshot=1 WHERE id=?", (event_id,))
                return create_pre_cal12_nutrition_snapshot(path, timeout=timeout)

            with patch("app.db.database.create_pre_cal12_nutrition_snapshot", side_effect=inspect_lock):
                self.db.initialize()
            self.assertEqual(self.dump(self.snapshots()[0]), before)
        self.assertEqual(self.rows()[event_id]["protein_g"], 98.7654321098765)

    def test_snapshot_failures_leave_delete_mode_live_bytes_and_all_rows_unchanged(self):
        self.event()
        self.event(source_type="RECIPE")
        # A journal-mode transition before backup would change these bytes even
        # if the later UPDATE were never reached. Guard that boundary explicitly.
        with self.db.connection() as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
        before_bytes = self.path.read_bytes()
        before_dump = self.dump()
        failure_points = (
            "app.db.migrations.snapshot.tempfile.mkstemp",
            "app.db.migrations.snapshot.validate_schema",
            "app.db.migrations.snapshot.os.fsync",
            "app.db.migrations.snapshot.os.replace",
        )
        for target in failure_points:
            with self.subTest(target=target):
                with patch(target, side_effect=OSError("injected snapshot failure")):
                    with patch("app.db.database.repair_cal12_nutrition") as repair:
                        with self.assertRaisesRegex(RuntimeError, "pre-repair safety snapshot failed"):
                            self.db.initialize()
                        repair.assert_not_called()
                self.assertEqual(self.path.read_bytes(), before_bytes)
                self.assertEqual(self.dump(), before_dump)
                self.assertEqual(self.snapshots(), [])
                self.assertEqual(list((self.root / "backups").iterdir()), [])
                self.assertEqual(self.db.last_nutrition_repair_count, 0)

    def test_snapshot_read_failure_leaves_wal_rows_and_journal_mode_unchanged(self):
        self.event()
        before = self.dump()
        real_connect = sqlite3.connect

        def failed_reader(path, *args, **kwargs):
            if path == self.path.as_uri() + "?mode=ro":
                raise sqlite3.OperationalError("injected snapshot read failure")
            return real_connect(path, *args, **kwargs)

        with patch("app.db.migrations.snapshot.sqlite3.connect", side_effect=failed_reader):
            with patch("app.db.database.repair_cal12_nutrition") as repair:
                with self.assertRaisesRegex(RuntimeError, "pre-repair safety snapshot failed"):
                    self.db.initialize()
                repair.assert_not_called()
        self.assertEqual(self.dump(), before)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_failure_after_update_rolls_back_every_repaired_row_and_keeps_snapshot(self):
        self.event()
        self.event(source_type="RECIPE")
        self.event(source_type="CUSTOM")
        before = self.dump()

        def failed_repair(connection):
            self.assertEqual(repair_cal12_nutrition(connection), 2)
            raise RuntimeError("injected repair failure")

        with patch("app.db.database.repair_cal12_nutrition", side_effect=failed_repair):
            with self.assertRaisesRegex(RuntimeError, "repair failed and was rolled back") as failure:
                self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.dump(self.snapshots()[0]), before)
        self.assertIn(str(self.snapshots()[0]), str(failure.exception))
        self.assertEqual(self.db.last_nutrition_repair_count, 0)
        self.db.initialize()
        self.assertEqual(len(self.snapshots()), 2)
        self.assertEqual(self.db.last_nutrition_repair_count, 2)

    def test_commit_failure_rolls_back_repair(self):
        self.event()
        self.event(source_type="RECIPE")
        before = self.dump()

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("injected commit failure")

        connection = sqlite3.connect(self.path, isolation_level=None, factory=FailedCommit)
        connection.row_factory = sqlite3.Row
        with patch.object(self.db, "connect", return_value=connection):
            with self.assertRaisesRegex(RuntimeError, "injected commit failure"):
                self.db.initialize()
        self.assertEqual(self.dump(), before)
        self.assertEqual(self.dump(self.snapshots()[0]), before)
        self.assertEqual(self.db.last_nutrition_repair_count, 0)

    def test_repeated_initialize_without_candidates_executes_no_write_or_backup(self):
        self.event()
        self.db.initialize()
        with self.db.connection() as connection:
            connection.execute("PRAGMA journal_mode=DELETE")
        before_bytes = self.path.read_bytes()
        before = self.dump()
        snapshot_bytes = self.snapshots()[0].read_bytes()
        for _ in range(2):
            statements = []
            connection = self.db.connect()
            connection.set_trace_callback(statements.append)
            with patch.object(self.db, "connect", return_value=connection):
                with patch("app.db.database.create_pre_cal12_nutrition_snapshot") as snapshot:
                    with patch("app.db.database.repair_cal12_nutrition") as repair:
                        self.db.initialize()
                        snapshot.assert_not_called()
                        repair.assert_not_called()
            for statement in statements:
                command = statement.strip().upper()
                self.assertFalse(command.startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP")), command)
                self.assertFalse(command.startswith("PRAGMA JOURNAL_MODE ="), command)
            self.assertEqual(self.dump(), before)
            self.assertEqual(self.path.read_bytes(), before_bytes)
            self.assertEqual(self.db.last_nutrition_repair_count, 0)
        self.assertEqual(len(self.snapshots()), 1)
        self.assertEqual(self.snapshots()[0].read_bytes(), snapshot_bytes)

    def test_repaired_rows_produce_complete_daily_nutrition_without_energy_changes(self):
        self.event(protein_snapshot=10, fiber_snapshot=0, fat_snapshot=2, carb_snapshot=20)
        self.event(source_type="RECIPE", protein_snapshot=5, fiber_snapshot=1, fat_snapshot=0, carb_snapshot=10)
        self.assertTrue(NutritionService(self.db).daily_totals("2026-09-18").incomplete)
        self.db.initialize()
        total = NutritionService(self.db).daily_totals("2026-09-18")
        self.assertEqual(total.nutrients.as_tuple(), (15, 1, 2, 30))
        self.assertFalse(total.incomplete)
        self.assertEqual(total.indication, "")
        self.assertEqual(sum(row["kj_snapshot"] for row in self.rows().values()), 2 * 321.987654321)

    def test_other_tables_caches_timestamps_sequences_and_versions_are_unchanged(self):
        self.event()
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO app_settings VALUES ('preference', 'keep', 'old')")
            connection.execute("INSERT INTO daily_metrics_cache(date, calculation_version, calculated_at) VALUES ('2026-09-18', ?, 'old')", (CALCULATION_VERSION,))
            connection.execute("INSERT INTO calibration_runs(window_start, window_end, weight_sample_count, days_span, calibration_kj_day, model_version, created_at) VALUES ('2026-09-01', '2026-09-18', 2, 17, 1.23456789, ?, 'old')", (CALCULATION_VERSION,))
        before = self.other_tables()
        self.db.initialize()
        self.assertEqual(self.other_tables(), before)
        self.assertEqual((APP_VERSION, SCHEMA_VERSION, CALCULATION_VERSION), ("0.0.3", 3, "v2-simple-energy-kj-1"))

    def test_restoring_original_raw_snapshot_can_legitimately_trigger_repair_again(self):
        event_id = self.event()
        original = self.rows()[event_id]
        self.db.initialize()
        snapshot = self.snapshots()[0]
        with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:
                source.backup(target)
        self.assertEqual(self.rows()[event_id], original)
        self.db.initialize()
        self.assertEqual(len(self.snapshots()), 2)
        self.assert_repaired(original, self.rows()[event_id])

    def test_old_v3_zip_is_repaired_in_restore_staging_without_changing_archive(self):
        event_id = self.event()
        original = self.rows()[event_id]
        service = BackupService(self.path)
        archive = service.create_backup(self.root / "synthetic-broken-v002.zip")
        archive_bytes = archive.read_bytes()
        self.db.initialize()
        safety = service.restore_backup(archive)
        self.assertTrue(safety.exists())
        self.assert_repaired(original, self.rows()[event_id])
        self.assertEqual(archive.read_bytes(), archive_bytes)
        with patch("app.db.database.create_pre_cal12_nutrition_snapshot") as snapshot:
            self.db.initialize()
            snapshot.assert_not_called()
