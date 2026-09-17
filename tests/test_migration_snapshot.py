"""Focused recovery-snapshot tests using disposable databases only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.db.database import Database
from app.db.migrations import migrate_v1_to_v2
from app.db.migrations.snapshot import create_pre_migration_snapshot
from tests.test_energy_migration import create_legacy_database


class MigrationSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "live.sqlite3"
        create_legacy_database(self.path)
        self.db = Database(self.path, timeout=0.2)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def _dump(path: Path) -> tuple[str, ...]:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            return tuple(connection.iterdump())

    def _snapshots(self) -> list[Path]:
        return sorted((self.root / "backups").glob("pre_migration_v1_to_v2_*.sqlite3"))

    def test_snapshot_is_original_standalone_v1_before_any_conversion(self) -> None:
        original = self._dump(self.path)

        def inspect_before_conversion(connection, timestamp):
            snapshots = self._snapshots()
            self.assertEqual(len(snapshots), 1)
            self.assertEqual(self._dump(snapshots[0]), original)
            self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], 1)
            migrate_v1_to_v2(connection, timestamp)

        with patch("app.db.database.migrate_v1_to_v2", side_effect=inspect_before_conversion):
            self.db.initialize(seed_foods=False)

        snapshot = self._snapshots()[0]
        self.assertEqual(self.db.last_migration_snapshot, snapshot)
        self.assertEqual(self._dump(snapshot), original)
        with closing(sqlite3.connect(snapshot)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT kcal FROM foods WHERE id=1").fetchone()[0], 123.456789123)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(f"{snapshot}{suffix}").exists())
        self.assertEqual(self.db.get_schema_version(), 2)
        with self.db.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    def test_snapshot_includes_committed_uncheckpointed_wal(self) -> None:
        with closing(sqlite3.connect(self.path)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("UPDATE foods SET kcal=987.123456789012 WHERE id=1")
            writer.execute("INSERT INTO app_settings VALUES ('wal_marker', 'committed', 'now')")
            writer.commit()
            self.assertGreater(Path(f"{self.path}-wal").stat().st_size, 0)
            original = self._dump(self.path)
            self.db.initialize(seed_foods=False)
            self.assertEqual(self._dump(self._snapshots()[0]), original)

    def test_writer_lock_covers_snapshot_and_conversion(self) -> None:
        def inspect_lock(path, *, timeout):
            with closing(sqlite3.connect(path, timeout=0)) as contender:
                with self.assertRaisesRegex(sqlite3.OperationalError, "locked"):
                    contender.execute("UPDATE foods SET kcal=1 WHERE id=1")
            return create_pre_migration_snapshot(path, timeout=timeout)

        with patch("app.db.database.create_pre_migration_snapshot", side_effect=inspect_lock):
            self.db.initialize(seed_foods=False)
        self.assertEqual(len(self._snapshots()), 1)

    def test_failure_after_conversion_rolls_back_and_retains_recovery_snapshot(self) -> None:
        original = self._dump(self.path)

        def fail_after_conversion(connection, timestamp):
            migrate_v1_to_v2(connection, timestamp)
            raise RuntimeError("injected migration failure")

        with patch("app.db.database.migrate_v1_to_v2", side_effect=fail_after_conversion):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure") as failure:
                self.db.initialize(seed_foods=False)
        snapshot = self._snapshots()[0]
        self.assertIn(str(snapshot), str(failure.exception))
        self.assertEqual(self._dump(self.path), original)
        self.assertEqual(self._dump(snapshot), original)

        # Recovery can be made from the raw SQLite file without the live DB/WAL.
        recovered = self.root / "recovered.sqlite3"
        with closing(sqlite3.connect(snapshot)) as source:
            with closing(sqlite3.connect(recovered)) as target:
                source.backup(target)
        self.assertEqual(self._dump(recovered), original)

        # A retry takes a new snapshot rather than overwriting the recovery copy.
        self.db.initialize(seed_foods=False)
        self.assertEqual(len(self._snapshots()), 2)
        self.assertEqual(self._dump(snapshot), original)
        self.assertEqual(self.db.get_schema_version(), 2)

    def test_failed_snapshot_publication_stops_before_migration(self) -> None:
        original = self._dump(self.path)
        with patch("app.db.migrations.snapshot.os.replace", side_effect=OSError("disk full")):
            with patch("app.db.database.migrate_v1_to_v2") as migrate:
                with self.assertRaisesRegex(RuntimeError, "safety snapshot failed"):
                    self.db.initialize(seed_foods=False)
                migrate.assert_not_called()
        self.assertEqual(self._dump(self.path), original)
        self.assertEqual(self._snapshots(), [])
        self.assertEqual(list((self.root / "backups").iterdir()), [])

    def test_rollback_error_still_reports_the_retained_snapshot(self) -> None:
        original = self._dump(self.path)

        class ReportRollbackFailure(sqlite3.Connection):
            def rollback(self):
                super().rollback()
                raise sqlite3.OperationalError("injected rollback error")

        connection = sqlite3.connect(self.path, isolation_level=None, factory=ReportRollbackFailure)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        with patch.object(self.db, "connect", return_value=connection):
            with patch("app.db.database.migrate_v1_to_v2", side_effect=RuntimeError("injected conversion error")):
                with self.assertRaisesRegex(RuntimeError, "rollback also failed") as failure:
                    self.db.initialize(seed_foods=False)
        snapshot = self._snapshots()[0]
        self.assertIn(str(snapshot), str(failure.exception))
        self.assertEqual(self._dump(snapshot), original)
        self.assertEqual(self._dump(self.path), original)

    def test_snapshot_failure_during_backup_stops_before_migration(self) -> None:
        original = self._dump(self.path)
        real_connect = sqlite3.connect

        def fail_snapshot_reader(path, *args, **kwargs):
            if path == self.path.as_uri() + "?mode=ro":
                raise sqlite3.OperationalError("read failed")
            return real_connect(path, *args, **kwargs)

        with patch("app.db.migrations.snapshot.sqlite3.connect", side_effect=fail_snapshot_reader):
            with patch("app.db.database.migrate_v1_to_v2") as migrate:
                with self.assertRaisesRegex(RuntimeError, "safety snapshot failed"):
                    self.db.initialize(seed_foods=False)
                migrate.assert_not_called()
        self.assertEqual(self._dump(self.path), original)
        self.assertEqual(self._snapshots(), [])

    def test_reinitializing_v2_does_not_make_or_modify_a_snapshot(self) -> None:
        self.db.initialize(seed_foods=False)
        snapshots = self._snapshots()
        saved_bytes = snapshots[0].read_bytes()
        with patch("app.db.database.create_pre_migration_snapshot") as create:
            self.db.initialize(seed_foods=False)
            create.assert_not_called()
        self.assertEqual(self._snapshots(), snapshots)
        self.assertEqual(snapshots[0].read_bytes(), saved_bytes)

    def test_fresh_database_is_v2_without_a_legacy_snapshot(self) -> None:
        fresh = Database(self.root / "fresh" / "caloriek.sqlite3")
        fresh.initialize()
        self.assertEqual(fresh.get_schema_version(), 2)
        self.assertIsNone(fresh.last_migration_snapshot)
        self.assertFalse((fresh.path.parent / "backups").exists())
        with fresh.connection() as connection:
            self.assertIn("kj", [row[1] for row in connection.execute("PRAGMA table_info(foods)")])
            self.assertNotIn("kcal", [row[1] for row in connection.execute("PRAGMA table_info(foods)")])


if __name__ == "__main__":
    unittest.main()
