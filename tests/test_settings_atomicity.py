"""CAL-14 real SQLite rollback coverage; only temporary synthetic user data."""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.db import Database
from app.paths import save_data_dir_preference
from app.ui.context import SettingsDraft
from app.version import CALCULATION_VERSION


TODAY = date(2026, 9, 20)
FIRST = TODAY - timedelta(days=5)


class SettingsDate(date):
    @classmethod
    def today(cls):
        return TODAY


class SettingsAtomicityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / "data_location.json"
        for name, value in (
            ("app.paths.location_config_path", lambda: self.config),
            ("app.application.date", SettingsDate),
            ("app.services.profile_service.date", SettingsDate),
        ):
            patcher = patch(name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = ApplicationContext(self.root / "source")
        self.db = self.context.database
        self.context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=70, effective_from=FIRST.isoformat(),
            measured_at=f"{FIRST.isoformat()}T07:00:00+08:00",
        )
        for key, value in (
            ("candle_color_mode", "china"), ("treemap_color_mode", "intake_red"),
            ("energy_display_unit", "kj"), ("kj_per_kg", "32216.812345678901234"),
        ):
            self.db.set_setting(key, value)
        # Deterministic cache sentinels, including a row before the first anchor.
        with self.db.transaction() as connection:
            for offset in range(-1, 6):
                connection.execute(
                    "INSERT INTO daily_metrics_cache(date, open_kg, close_kg, "
                    "calculation_version, calculated_at, is_dirty) VALUES (?, 70, 70, ?, 'fixture', 0)",
                    ((FIRST + timedelta(days=offset)).isoformat(), CALCULATION_VERSION),
                )
            connection.execute(
                "INSERT INTO calibration_runs(window_start, window_end, weight_sample_count, "
                "days_span, calibration_kj_day, model_version, created_at) "
                "VALUES (?, ?, 1, 5, 12.3456789, ?, 'fixture')",
                (FIRST.isoformat(), TODAY.isoformat(), CALCULATION_VERSION),
            )
        self.db.clear_dirty()
        self.context.daily.refresh_model_settings()
        save_data_dir_preference(self.context.data_dir)

    def draft(self, **changes) -> SettingsDraft:
        return replace(SettingsDraft(**asdict(self.context.get_settings())), **changes)

    def mixed_draft(self, **changes) -> SettingsDraft:
        return replace(self.draft(
            sex="female", height_cm=176, candle_color_mode="international",
            treemap_color_mode="intake_green", energy_display_unit="kcal", kj_per_kg=33000,
        ), **changes)

    @staticmethod
    def dump(db: Database) -> tuple[str, ...]:
        with db.connection() as connection:
            return tuple(connection.iterdump())

    def rows(self, table: str) -> tuple:
        with self.db.connection() as connection:
            return tuple(tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid'))

    def dirty_flags(self) -> dict[str, int]:
        with self.db.connection() as connection:
            return dict(connection.execute("SELECT date, is_dirty FROM daily_metrics_cache ORDER BY date"))

    @contextmanager
    def failing_trigger(self, event: str):
        # The production schema intentionally disallows triggers at startup.
        # Install only AFTER fixture initialization, and remove before reopening.
        with self.db.transaction() as connection:
            connection.execute(
                f"CREATE TRIGGER cal14_failure {event} "
                "BEGIN SELECT RAISE(ABORT, 'CAL14 injected write failure'); END"
            )
        try:
            yield
        finally:
            with self.db.transaction() as connection:
                connection.execute("DROP TRIGGER cal14_failure")

    def assert_failed_save_unchanged(self, draft: SettingsDraft) -> None:
        before = self.dump(self.db)
        preference = self.config.read_bytes()
        model = self.context.daily.weight_model
        with (
            patch.object(self.context.daily, "refresh_model_settings") as refresh,
            patch.object(self.context.daily, "ensure_calculated") as calculate,
        ):
            with self.assertRaises(sqlite3.IntegrityError):
                self.context.save_settings(draft)
            refresh.assert_not_called()
            calculate.assert_not_called()
        # Includes profile/revisions, settings, dirty flags, all cache and
        # calibration rows, timestamps, raw facts and sqlite_sequence.
        self.assertEqual(self.dump(self.db), before)
        self.assertEqual(self.config.read_bytes(), preference)
        self.assertIs(self.context.daily.weight_model, model)

    def test_each_persistent_write_stage_rolls_back_the_entire_save(self) -> None:
        events = (
            "AFTER UPDATE ON profile",
            "AFTER INSERT ON profile_revisions",
            "AFTER UPDATE ON app_settings WHEN NEW.key='candle_color_mode'",
            "AFTER UPDATE ON app_settings WHEN NEW.key='treemap_color_mode'",
            "AFTER UPDATE ON app_settings WHEN NEW.key='energy_display_unit'",
            "AFTER UPDATE ON app_settings WHEN NEW.key='kj_per_kg'",
            f"AFTER UPDATE ON recalculation_state WHEN NEW.dirty_from_date='{TODAY.isoformat()}'",
            f"AFTER UPDATE ON recalculation_state WHEN NEW.dirty_from_date='{FIRST.isoformat()}'",
            f"AFTER UPDATE OF is_dirty ON daily_metrics_cache WHEN NEW.date='{TODAY.isoformat()}'",
            f"AFTER UPDATE OF is_dirty ON daily_metrics_cache WHEN NEW.date='{FIRST.isoformat()}'",
        )
        for event in events:
            with self.subTest(event=event), self.failing_trigger(event):
                self.assert_failed_save_unchanged(self.mixed_draft())

    def test_new_setting_and_missing_dirty_row_insert_failures_roll_back(self) -> None:
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM app_settings WHERE key='energy_display_unit'")
        with self.failing_trigger("AFTER INSERT ON app_settings WHEN NEW.key='energy_display_unit'"):
            self.assert_failed_save_unchanged(self.mixed_draft())
        with self.db.transaction() as connection:
            connection.execute("DELETE FROM recalculation_state")
        with self.failing_trigger("AFTER INSERT ON recalculation_state"):
            self.assert_failed_save_unchanged(self.mixed_draft())

    def test_existing_today_revision_and_existing_dirty_state_survive_failed_save(self) -> None:
        self.context.profile.update_profile(height_cm=175)
        self.db.mark_dirty((FIRST - timedelta(days=1)).isoformat())
        with self.failing_trigger("AFTER UPDATE ON profile_revisions"):
            self.assert_failed_save_unchanged(self.mixed_draft())
        with self.failing_trigger("AFTER UPDATE ON app_settings WHEN NEW.key='kj_per_kg'"):
            self.assert_failed_save_unchanged(self.mixed_draft())

    def test_commit_failure_rolls_back_and_does_not_refresh(self) -> None:
        # A real deferred foreign-key violation fails at COMMIT, not execute().
        with self.db.transaction() as connection:
            connection.execute(
                "CREATE TABLE cal14_commit_guard(profile_id INTEGER REFERENCES profile(id) "
                "DEFERRABLE INITIALLY DEFERRED)"
            )
            connection.execute(
                "CREATE TRIGGER cal14_commit_failure AFTER UPDATE ON app_settings "
                "WHEN NEW.key='kj_per_kg' BEGIN INSERT INTO cal14_commit_guard VALUES (-1); END"
            )
        self.assert_failed_save_unchanged(self.mixed_draft())

    def test_mixed_persistence_uses_one_transaction(self) -> None:
        with patch.object(self.db, "transaction", wraps=self.db.transaction) as transaction:
            self.assertTrue(ApplicationContext._apply_settings(self.db, self.mixed_draft()))
            transaction.assert_called_once_with()
        self.assertEqual(self.db.get_dirty_from_date(), FIRST.isoformat())
        self.assertEqual(self.context.get_settings().height_cm, 176)
        self.assertEqual(self.db.get_setting("energy_display_unit"), "kcal")

    def test_successful_mixed_save_is_committed_before_refresh_and_real_rebuild(self) -> None:
        requested = self.mixed_draft()
        old_revision = self.context.profile.list_revisions()[0]
        real_refresh = self.context.daily.refresh_model_settings
        real_calculate = self.context.daily.ensure_calculated
        order = []

        def refresh():
            # These reads use independent connections and cannot see uncommitted
            # values from the settings transaction.
            self.assertEqual(asdict(self.context.get_settings()), asdict(requested))
            self.assertEqual(self.db.get_dirty_from_date(), FIRST.isoformat())
            self.assertEqual(self.dirty_flags(), {
                (FIRST + timedelta(days=offset)).isoformat(): int(offset >= 0)
                for offset in range(-1, 6)
            })
            order.append("refresh")
            real_refresh()

        def calculate(day):
            self.assertEqual(order, ["refresh"])
            self.assertEqual(day, TODAY)
            self.assertEqual(self.context.daily.weight_model.kj_per_kg, 33000)
            real_calculate(day)
            order.append("calculate")

        with (
            patch.object(self.context.daily, "refresh_model_settings", side_effect=refresh),
            patch.object(self.context.daily, "ensure_calculated", side_effect=calculate),
        ):
            self.assertTrue(self.context.save_settings(requested))
        self.assertEqual(order, ["refresh", "calculate"])
        self.assertIsNone(self.db.get_dirty_from_date())
        self.assertTrue(all(flag == 0 for flag in self.dirty_flags().values()))
        self.assertEqual(self.context.profile.list_revisions()[0], old_revision)
        self.assertEqual(len(self.context.profile.list_revisions()), 2)

    def test_presentation_only_save_preserves_clean_or_already_dirty_model_state(self) -> None:
        tables = ("profile", "profile_revisions", "recalculation_state", "daily_metrics_cache", "calibration_runs")
        for already_dirty, unit in ((False, "kcal"), (True, "kj")):
            with self.subTest(already_dirty=already_dirty):
                if already_dirty:
                    self.db.mark_dirty(FIRST.isoformat())
                before = {table: self.rows(table) for table in tables}
                ratio = next(row for row in self.rows("app_settings") if row[0] == "kj_per_kg")
                with (
                    patch.object(self.context.daily, "refresh_model_settings") as refresh,
                    patch.object(self.context.daily, "ensure_calculated") as calculate,
                ):
                    self.assertFalse(self.context.save_settings(self.draft(energy_display_unit=unit)))
                    refresh.assert_not_called()
                    calculate.assert_not_called()
                self.assertEqual(self.db.get_setting("energy_display_unit"), unit)
                self.assertEqual({table: self.rows(table) for table in tables}, before)
                self.assertIn(ratio, self.rows("app_settings"))

    def test_profile_effective_date_replaces_today_only_and_dirties_today(self) -> None:
        old_revision = self.context.profile.list_revisions()[0]
        revision_id = None
        for height in (176, 177):
            with self.subTest(height=height):
                self.db.clear_dirty()
                with patch.object(self.context.daily, "ensure_calculated"):
                    self.assertTrue(self.context.save_settings(self.draft(height_cm=height)))
                revisions = self.context.profile.list_revisions()
                self.assertEqual(len(revisions), 2)
                self.assertEqual(revisions[0], old_revision)
                self.assertEqual(revisions[1]["effective_from"], TODAY.isoformat())
                self.assertEqual(revisions[1]["height_cm"], height)
                if revision_id is not None:
                    self.assertEqual(revisions[1]["id"], revision_id)
                revision_id = revisions[1]["id"]
                self.assertEqual(self.db.get_dirty_from_date(), TODAY.isoformat())
                self.assertEqual(self.dirty_flags(), {
                    (FIRST + timedelta(days=offset)).isoformat(): int(offset == 5)
                    for offset in range(-1, 6)
                })

    def test_model_only_save_keeps_earlier_existing_dirty_date(self) -> None:
        earlier = (FIRST - timedelta(days=1)).isoformat()
        self.db.mark_dirty(earlier)
        revisions = self.rows("profile_revisions")
        with patch.object(self.context.daily, "ensure_calculated"):
            self.assertTrue(self.context.save_settings(self.draft(kj_per_kg=33000)))
        self.assertEqual(self.db.get_dirty_from_date(), earlier)
        self.assertEqual(self.rows("profile_revisions"), revisions)

    def test_borrowed_profile_and_setting_operations_see_uncommitted_values_and_do_not_commit(self) -> None:
        before = self.dump(self.db)
        with self.assertRaisesRegex(RuntimeError, "caller rollback"):
            with self.db.transaction() as connection:
                first_id = self.context.profile.update_profile(height_cm=176, connection=connection)
                second_id = self.context.profile.update_profile(awake_multiplier=1.3, connection=connection)
                self.assertEqual(first_id, second_id)
                current = self.context.profile.get_current_profile(connection=connection)
                self.assertEqual((current["height_cm"], current["awake_multiplier"]), (176, 1.3))
                self.db.set_setting("energy_display_unit", "kcal", connection=connection)
                self.assertEqual(self.db.get_setting("energy_display_unit", connection=connection), "kcal")
                self.assertTrue(connection.in_transaction)
                self.assertEqual(self.dump(self.db), before)
                raise RuntimeError("caller rollback")
        self.assertEqual(self.dump(self.db), before)
        # Existing independent callers still own and commit their transaction.
        self.context.profile.update_profile(height_cm=176)
        self.db.set_setting("energy_display_unit", "kcal")
        self.assertEqual(self.context.get_settings().height_cm, 176)
        self.assertEqual(self.db.get_setting("energy_display_unit"), "kcal")

    def test_post_commit_rebuild_failure_propagates_and_leaves_dirty_state_for_retry(self) -> None:
        with patch.object(self.context.daily, "ensure_calculated", side_effect=RuntimeError("rebuild failed")):
            with self.assertRaisesRegex(RuntimeError, "rebuild failed"):
                self.context.save_settings(self.mixed_draft())
        # Rebuild is deliberately after commit, not a compensating settings undo.
        self.assertEqual(self.context.get_settings().height_cm, 176)
        self.assertEqual(self.db.get_setting("kj_per_kg"), "33000")
        self.assertEqual(self.db.get_dirty_from_date(), FIRST.isoformat())

    def assert_relocation_failed_safely(self, destination: Path, before: tuple, preference: bytes) -> None:
        self.assertIs(self.context.database, self.db)
        self.assertEqual(self.context.data_dir, self.db.path.parent)
        self.assertEqual(self.dump(self.db), before)
        self.assertEqual(self.config.read_bytes(), preference)
        for name in ("caloriek.sqlite3", ".caloriek-move.sqlite3"):
            for suffix in ("", "-wal", "-shm"):
                self.assertFalse((destination / f"{name}{suffix}").exists())

    def test_staged_settings_failure_rolls_back_before_cleanup_without_touching_source(self) -> None:
        destination = self.root / "failed-stage"
        before, preference = self.dump(self.db), self.config.read_bytes()
        original_set = Database.set_setting
        original_apply = ApplicationContext._apply_settings
        inspected = []

        def failing_set(database, key, value, *, connection=None):
            original_set(database, key, value, connection=connection)
            if database.path.name == ".caloriek-move.sqlite3" and key == "treemap_color_mode":
                raise RuntimeError("staged settings failure")

        def apply(database, settings):
            staged_before = self.dump(database)
            try:
                return original_apply(database, settings)
            except RuntimeError:
                self.assertEqual(self.dump(database), staged_before)
                inspected.append(database.path)
                raise

        with (
            patch.object(Database, "set_setting", new=failing_set),
            patch.object(ApplicationContext, "_apply_settings", side_effect=apply),
        ):
            with self.assertRaisesRegex(RuntimeError, "staged settings failure"):
                self.context.save_settings(self.mixed_draft(data_directory=destination))
        self.assertEqual(inspected, [destination / ".caloriek-move.sqlite3"])
        self.assert_relocation_failed_safely(destination, before, preference)

    def test_destination_binding_failure_does_not_publish_directory_preference(self) -> None:
        destination = self.root / "failed-binding"
        before, preference = self.dump(self.db), self.config.read_bytes()
        original_bind = ApplicationContext._bind_data_dir

        def bind(context, directory):
            original_bind(context, directory)
            if Path(directory) == destination:
                raise RuntimeError("destination binding failure")

        with patch.object(ApplicationContext, "_bind_data_dir", new=bind):
            with self.assertRaisesRegex(RuntimeError, "destination binding failure"):
                self.context.save_settings(self.mixed_draft(data_directory=destination))
        self.assert_relocation_failed_safely(destination, before, preference)

    def test_preference_publication_failure_removes_prepared_destination(self) -> None:
        import os

        destination = self.root / "failed-preference"
        before, preference = self.dump(self.db), self.config.read_bytes()
        real_replace = os.replace

        def fail_preference(source, target):
            if Path(target) == self.config:
                raise OSError("preference publication failure")
            return real_replace(source, target)

        with patch("app.paths.os.replace", side_effect=fail_preference):
            with self.assertRaisesRegex(OSError, "preference publication failure"):
                self.context.save_settings(self.mixed_draft(data_directory=destination))
        self.assert_relocation_failed_safely(destination, before, preference)

    def test_successful_relocation_uses_atomic_settings_path_and_preserves_source(self) -> None:
        destination = self.root / "relocated"
        before = self.dump(self.db)
        original_apply = ApplicationContext._apply_settings
        with patch.object(ApplicationContext, "_apply_settings", wraps=original_apply) as apply:
            self.assertTrue(self.context.save_settings(self.mixed_draft(data_directory=destination)))
            apply.assert_called_once()
        self.assertEqual(apply.call_args.args[0].path, destination / ".caloriek-move.sqlite3")
        self.assertEqual(self.dump(self.db), before)
        self.assertEqual(self.context.data_dir, destination)
        self.assertEqual(self.context.get_settings().height_cm, 176)
        self.assertEqual(self.context.database.get_setting("energy_display_unit"), "kcal")
        self.assertEqual(self.context.database.get_setting("kj_per_kg"), "33000")
        self.assertIsNone(self.context.database.get_dirty_from_date())
        self.assertEqual(ApplicationContext(destination).get_settings(), self.context.get_settings())
        from app.paths import _configured_data_dir
        self.assertEqual(_configured_data_dir(), destination)


if __name__ == "__main__":
    unittest.main()
