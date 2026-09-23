"""CAL-16 OS ownership tests. Subprocesses only use temporary synthetic paths."""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import date
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.db.database import Database
from app.db.writer_guard import DatabaseInUseError, WriterLease
from app.services.backup_service import BackupService
from app.ui.context import SettingsDraft


ROOT = Path(__file__).resolve().parents[1]


class FixtureDate(date):
    @classmethod
    def today(cls):
        return date(2025, 12, 30)


# stdin/stdout handshake, no sleeps or timing-dependent races. EOF also exits.
HOLDER = """
import sys
from app.db.writer_guard import WriterLease
with WriterLease(sys.argv[1]):
    print('owned', flush=True)
    sys.stdin.readline()
"""


class WriterGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "data" / "caloriek.sqlite3"

    def run_child(self, code, path=None):
        return subprocess.run(
            [sys.executable, "-B", "-c", code, str(path or self.path)],
            cwd=ROOT, capture_output=True, text=True, timeout=20,
        )

    def assert_child_blocked(self, path=None):
        result = self.run_child("""
import sys
from app.db.database import Database
from app.db.writer_guard import DatabaseInUseError
try:
    Database(sys.argv[1])
except DatabaseInUseError:
    print('blocked')
else:
    raise SystemExit('unexpected ownership')
""", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "blocked")

    @contextmanager
    def child_owner(self, path=None):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", HOLDER, str(path or self.path)],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        # A watchdog prevents a failed child setup from hanging the test runner.
        watchdog = threading.Timer(20, process.kill)
        watchdog.start()
        try:
            self.assertEqual(process.stdout.readline().strip(), "owned")
            yield process
        finally:
            if process.poll() is None:
                process.communicate("release\n", timeout=10)
            else:
                process.communicate(timeout=10)
            watchdog.cancel()

    def test_same_process_adapters_share_ownership_until_last_close(self):
        first = Database(self.path)
        self.addCleanup(first.close)
        second = Database(self.path.parent / "." / self.path.name)
        self.addCleanup(second.close)
        first.initialize(seed_foods=False)
        second.set_setting("shared", "yes")
        self.assertEqual(first.get_setting("shared"), "yes")
        self.assert_child_blocked()
        first.close()
        self.assert_child_blocked()
        second.close()
        with self.child_owner():
            with self.assertRaises(DatabaseInUseError):
                Database(self.path)

    def test_owner_excludes_initialization_before_database_creation(self):
        with self.child_owner():
            with self.assertRaises(DatabaseInUseError):
                ApplicationContext(self.path.parent)
            self.assertFalse(self.path.exists())

    def test_different_database_paths_coexist_in_different_processes(self):
        with WriterLease(self.path), self.child_owner(self.root / "other" / "caloriek.sqlite3"):
            self.assert_child_blocked(self.path)

    def test_crash_releases_os_lock_and_stale_marker_does_not_block(self):
        with self.child_owner() as process:
            with self.assertRaises(DatabaseInUseError):
                WriterLease(self.path)
            process.kill()  # No Python finally/atexit cleanup in the owner.
            process.wait(timeout=10)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        Path(str(self.path) + ".writer.lock").touch(exist_ok=True)
        with WriterLease(self.path):
            self.assert_child_blocked()

    def test_cross_thread_database_access_and_acquisition_are_rejected(self):
        database = Database(self.path)
        self.addCleanup(database.close)
        errors = []

        def use():
            for action in (database.connect, lambda: Database(self.path)):
                try:
                    action()
                except DatabaseInUseError:
                    errors.append("blocked")

        thread = threading.Thread(target=use)
        thread.start()
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, ["blocked", "blocked"])
        self.assertFalse(self.path.exists())

    def test_restore_is_guarded_even_without_application_context(self):
        with self.child_owner():
            service = BackupService(self.path)
            with self.assertRaises(DatabaseInUseError), patch.object(service, "inspect_backup") as inspect:
                service.restore_backup(self.root / "not-opened.zip")
            inspect.assert_not_called()
            self.assertFalse(self.path.exists())

    def test_read_only_export_and_backup_work_while_another_process_owns_database(self):
        db = Database(self.path)
        db.initialize(seed_foods=False)
        db.set_setting("fixture", "only synthetic")
        db.close()
        with self.child_owner():
            service = BackupService(self.path)
            self.assertTrue(service.export_json(self.root / "export.json").is_file())
            self.assertTrue(service.create_backup(self.root / "backup.zip").is_file())

    def test_relocation_refuses_reserved_destination_without_source_or_config_changes(self):
        context = ApplicationContext(self.path.parent)
        self.addCleanup(context.database.close)
        destination = self.root / "destination"
        target = destination / self.path.name
        draft = replace(SettingsDraft(**asdict(context.get_settings())), data_directory=destination)
        before = context.database.path
        with (
            self.child_owner(target),
            patch("app.application.save_data_dir_preference") as preference,
            patch.object(context.backups, "create_backup") as backup,
            self.assertRaises(DatabaseInUseError),
        ):
            context.save_settings(draft)
        preference.assert_not_called()
        backup.assert_not_called()
        self.assertEqual(context.database.path, before)
        self.assertFalse(target.exists())

    def test_restore_keeps_database_lease_across_atomic_replace(self):
        db = Database(self.path)
        self.addCleanup(db.close)
        db.initialize(seed_foods=False)
        db.set_setting("marker", "before")
        service = BackupService(self.path)
        backup = service.create_backup(self.root / "source.zip")
        db.set_setting("marker", "after")
        replace_file = os.replace

        def replace_while_owned(source, target):
            if Path(target) == self.path:
                self.assert_child_blocked()
            return replace_file(source, target)

        with patch("app.services.backup_service.os.replace", side_effect=replace_while_owned):
            service.restore_backup(backup)
        self.assertEqual(db.get_setting("marker"), "before")
        self.assert_child_blocked()

    def test_relocation_holds_destination_through_publication_and_afterwards(self):
        context = ApplicationContext(self.path.parent)
        source = context.database
        self.addCleanup(source.close)
        context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=70, effective_from="2025-12-30",
            measured_at="2025-12-30T08:00:00",
        )
        destination = self.root / "destination"
        target = destination / self.path.name
        draft = replace(SettingsDraft(**asdict(context.get_settings())), data_directory=destination)

        def publish(_directory):
            self.assert_child_blocked(self.path)
            self.assert_child_blocked(target)

        with (
            patch("app.application.date", FixtureDate),
            patch("app.application.save_data_dir_preference", side_effect=publish),
        ):
            context.save_settings(draft)
        self.addCleanup(context.database.close)
        self.assertEqual(context.database.path, target)
        self.assert_child_blocked(target)

    def test_failed_preference_releases_destination_and_keeps_source_owned(self):
        context = ApplicationContext(self.path.parent)
        self.addCleanup(context.database.close)
        context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=70, effective_from="2025-12-30",
            measured_at="2025-12-30T08:00:00",
        )
        destination = self.root / "failed"
        target = destination / self.path.name
        draft = replace(SettingsDraft(**asdict(context.get_settings())), data_directory=destination)
        with (
            patch("app.application.date", FixtureDate),
            patch("app.application.save_data_dir_preference", side_effect=OSError("synthetic failure")),
            self.assertRaises(OSError),
        ):
            context.save_settings(draft)
        self.assertFalse(target.exists())
        self.assertEqual(context.database.path, self.path)
        self.assert_child_blocked()
        with self.child_owner(target):
            pass  # Last prepared-context reference did not retain ownership.

    def test_failed_context_initialization_releases_lease(self):
        with (
            patch.object(Database, "initialize", side_effect=RuntimeError("synthetic init failure")),
            self.assertRaises(RuntimeError),
        ):
            ApplicationContext(self.path.parent)
        with self.child_owner():
            pass
