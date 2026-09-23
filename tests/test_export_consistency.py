"""CAL-16: real WAL writers between export reads, and atomic output failures."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.db.database import Database
from app.services.backup_service import BackupError, BackupService
from app.services.food_service import FoodService
from app.version import APP_NAME, APP_VERSION, SCHEMA_VERSION


class ExportConsistencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database(self.root / "caloriek.sqlite3")
        self.addCleanup(self.db.close)
        self.db.initialize(seed_foods=False)
        self.db.set_setting("generation", "before")
        self.precise = 1234.5678901234567
        self.food = FoodService(self.db).create_food(
            name="before", category="synthetic", basis_unit="g", basis_amount=100,
            kj=self.precise, protein_g_per_100g=12.3456789012345,
        )
        self.service = BackupService(self.db.path)
        self.output = self.root / "export.json"

    def test_all_tables_and_version_share_one_read_snapshot_without_blocking_writer(self):
        other = Database(self.db.path, timeout=0)
        self.addCleanup(other.close)
        read_rows = self.service._export_rows
        commits = []

        def read_and_commit(connection, table):
            rows = read_rows(connection, table)
            self.assertTrue(connection.in_transaction)
            if table == "app_settings":
                # app_settings sorts before foods and schema_version. This is a
                # separate real connection/transaction, not simulated ordering.
                with other.transaction() as writer:
                    self.assertEqual(writer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                    writer.execute("UPDATE app_settings SET value='after' WHERE key='generation'")
                    writer.execute("UPDATE foods SET name='after', kj=999 WHERE id=?", (self.food,))
                    writer.execute(
                        "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                        (SCHEMA_VERSION + 1, "2026-09-20T12:00:00"),
                    )
                commits.append(table)  # Commit succeeded while reader was open.
            return rows

        with patch.object(self.service, "_export_rows", side_effect=read_and_commit):
            self.service.export_json(self.output)
        payload = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(commits, ["app_settings"])
        self.assertEqual(set(payload), {"app_name", "app_version", "schema_version", "exported_at", "tables"})
        self.assertEqual(payload["app_name"], APP_NAME)
        self.assertEqual(payload["app_version"], APP_VERSION)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertEqual(max(r["version"] for r in payload["tables"]["schema_version"]), SCHEMA_VERSION)
        self.assertEqual(payload["tables"]["app_settings"][0]["value"], "before")
        exported = payload["tables"]["foods"][0]
        self.assertEqual(exported["name"], "before")
        self.assertEqual(exported["kj"], self.precise)
        self.assertEqual(exported["protein_g_per_100g"], 12.3456789012345)
        self.assertEqual(other.get_setting("generation"), "after")
        self.assertEqual(FoodService(other).get_food(self.food)["name"], "after")

    def test_export_connection_cannot_write_and_snapshot_is_released(self):
        read_rows = self.service._export_rows

        def inspect(connection, table):
            if table == "app_settings":
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("UPDATE app_settings SET value='forbidden'")
            return read_rows(connection, table)

        with patch.object(self.service, "_export_rows", side_effect=inspect):
            self.service.export_json(self.output)
        with closing(sqlite3.connect(self.db.path, timeout=0)) as observer:
            self.assertEqual(observer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0], 0)
        self.assertEqual(self.db.get_setting("generation"), "before")

    def test_failures_preserve_previous_output_and_remove_partial_stage(self):
        original_write = Path.write_text

        def partial_write(path, content, *args, **kwargs):
            original_write(path, content[:10], *args, **kwargs)
            raise OSError("synthetic disk failure")

        for existing in (False, True):
            for failure in ("read", "serialize", "write", "replace"):
                with self.subTest(existing=existing, failure=failure):
                    self.output.unlink(missing_ok=True)
                    if existing:
                        self.output.write_text("previous successful export", encoding="utf-8")
                    targets = {
                        "read": patch.object(self.service, "_export_rows", side_effect=sqlite3.OperationalError("read failure")),
                        "serialize": patch("app.services.backup_service.json.dumps", side_effect=ValueError("encode failure")),
                        "write": patch.object(Path, "write_text", new=partial_write),
                        "replace": patch("app.services.backup_service.os.replace", side_effect=OSError("rename failure")),
                    }
                    with targets[failure], self.assertRaises(BackupError):
                        self.service.export_json(self.output)
                    if existing:
                        self.assertEqual(self.output.read_text(encoding="utf-8"), "previous successful export")
                    else:
                        self.assertFalse(self.output.exists())
                    self.assertEqual(list(self.root.glob(".export.json.*.tmp")), [])
