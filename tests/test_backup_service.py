import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from app.db.database import Database
from app.services.backup_service import BackupError, BackupService
from app.version import APP_NAME, APP_VERSION, SCHEMA_VERSION


class BackupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "data" / "caloriek.sqlite3"
        self.database = Database(self.database_path)
        self.database.initialize(seed_foods=False)
        self.service = BackupService(self.database_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _setting(self, database_path: Path, key: str = "marker") -> str | None:
        with closing(sqlite3.connect(database_path)) as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else str(row[0])

    def _setting_in_backup(self, backup_path: Path, key: str = "marker") -> str | None:
        extracted = self.root / f"extracted_{backup_path.stem}.sqlite3"
        with zipfile.ZipFile(backup_path, "r") as archive:
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            extracted.write_bytes(archive.read(manifest["database_file"]))
        return self._setting(extracted, key)

    def test_create_backup_uses_a_consistent_sqlite_snapshot_including_wal(self) -> None:
        writer = sqlite3.connect(self.database_path)
        try:
            writer.execute(
                "INSERT INTO app_settings(key, value, updated_at) VALUES (?, ?, ?)",
                ("marker", "只在 WAL 中的提交", "2026-08-25T12:00:00+08:00"),
            )
            writer.commit()
            self.assertTrue(Path(f"{self.database_path}-wal").exists())

            backup = self.service.create_backup(self.root / "manual-backup")
        finally:
            writer.close()

        self.assertEqual(backup.suffix, ".zip")
        with zipfile.ZipFile(backup, "r") as archive:
            self.assertEqual(
                set(archive.namelist()),
                {"caloriek.sqlite3", "manifest.json", "config.json"},
            )
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
            config = json.loads(archive.read("config.json").decode("utf-8"))
        self.assertEqual(manifest["app_name"], APP_NAME)
        self.assertEqual(manifest["app_version"], APP_VERSION)
        self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
        self.assertEqual(config["database_filename"], self.database_path.name)
        self.assertEqual(self._setting_in_backup(backup), "只在 WAL 中的提交")

    def test_restore_round_trip_and_safety_backup_contains_previous_live_data(self) -> None:
        self.database.set_setting("marker", "来自备份")
        source_backup = self.service.create_backup(self.root / "source.zip")
        self.database.set_setting("marker", "恢复前现库")

        safety_backup = self.service.restore_backup(source_backup)

        self.assertTrue(safety_backup.exists())
        self.assertTrue(safety_backup.name.startswith("pre_restore_"))
        self.assertEqual(self._setting(self.database_path), "来自备份")
        self.assertEqual(self._setting_in_backup(safety_backup), "恢复前现库")
        self.assertEqual(self.database.get_schema_version(), SCHEMA_VERSION)

    def test_restore_rejects_incompatible_manifest_without_touching_live_data(self) -> None:
        self.database.set_setting("marker", "备份内容")
        source_backup = self.service.create_backup(self.root / "source.zip")
        self.database.set_setting("marker", "必须保留的现库")

        incompatible = self.root / "incompatible.zip"
        with zipfile.ZipFile(source_backup, "r") as source:
            manifest = json.loads(source.read("manifest.json").decode("utf-8"))
            manifest["schema_version"] = SCHEMA_VERSION + 1
            database_bytes = source.read(manifest["database_file"])
            config_bytes = source.read("config.json")
        with zipfile.ZipFile(
            incompatible, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.writestr(manifest["database_file"], database_bytes)
            archive.writestr("config.json", config_bytes)

        with self.assertRaisesRegex(BackupError, "schema_version"):
            self.service.restore_backup(incompatible)

        self.assertEqual(self._setting(self.database_path), "必须保留的现库")

    def test_restore_rejects_database_schema_that_disagrees_with_manifest(self) -> None:
        self.database.set_setting("marker", "备份内容")
        source_backup = self.service.create_backup(self.root / "source.zip")
        self.database.set_setting("marker", "校验失败时保留")

        tampered_database = self.root / "tampered.sqlite3"
        with zipfile.ZipFile(source_backup, "r") as source:
            manifest_bytes = source.read("manifest.json")
            config_bytes = source.read("config.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            tampered_database.write_bytes(source.read(manifest["database_file"]))
        with closing(sqlite3.connect(tampered_database)) as connection:
            connection.execute(
                "UPDATE schema_version SET version = ?",
                (SCHEMA_VERSION + 1,),
            )
            connection.commit()

        inconsistent = self.root / "inconsistent.zip"
        with zipfile.ZipFile(
            inconsistent, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("manifest.json", manifest_bytes)
            archive.write(tampered_database, manifest["database_file"])
            archive.writestr("config.json", config_bytes)

        with self.assertRaisesRegex(BackupError, "manifest 不一致"):
            self.service.restore_backup(inconsistent)

        self.assertEqual(self._setting(self.database_path), "校验失败时保留")

    def test_restore_rejects_malformed_v1_schema_before_touching_live_data(self) -> None:
        self.database.set_setting("marker", "必须保持的现库")
        malformed_database = self.root / "malformed.sqlite3"
        with closing(sqlite3.connect(malformed_database)) as connection:
            connection.execute(
                "CREATE TABLE schema_version(version INTEGER PRIMARY KEY, applied_at TEXT)"
            )
            connection.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, "2026-08-25T12:00:00+08:00"),
            )
            connection.execute("CREATE TABLE foods(x TEXT)")
            connection.commit()

        malformed_backup = self.root / "malformed.zip"
        manifest = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "schema_version": SCHEMA_VERSION,
            "created_at": "2026-08-25T12:00:00+08:00",
            "database_file": "caloriek.sqlite3",
        }
        with zipfile.ZipFile(
            malformed_backup, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.write(malformed_database, "caloriek.sqlite3")

        with self.assertRaisesRegex(BackupError, "结构无效"):
            self.service.restore_backup(malformed_backup)

        self.assertEqual(self._setting(self.database_path), "必须保持的现库")
        with closing(sqlite3.connect(self.database_path)) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(foods)")
            }
        self.assertIn("active", columns)
        self.assertNotEqual(columns, {"x"})
        self.assertEqual(
            list((self.database_path.parent / "backups").glob("pre_restore_*.zip")),
            [],
        )

    def test_failed_atomic_replace_keeps_live_database_and_safety_backup(self) -> None:
        self.database.set_setting("marker", "待恢复内容")
        source_backup = self.service.create_backup(self.root / "source.zip")
        self.database.set_setting("marker", "替换失败时必须保留")
        real_replace = os.replace

        def fail_only_live_replace(source: str | Path, destination: str | Path) -> None:
            if Path(destination).resolve() == self.database_path:
                raise OSError("simulated replace failure")
            real_replace(source, destination)

        with patch(
            "app.services.backup_service.os.replace",
            side_effect=fail_only_live_replace,
        ):
            with self.assertRaisesRegex(BackupError, "当前数据库保持不变"):
                self.service.restore_backup(source_backup)

        self.assertEqual(self._setting(self.database_path), "替换失败时必须保留")
        safety_backups = list(
            (self.database_path.parent / "backups").glob("pre_restore_*.zip")
        )
        self.assertEqual(len(safety_backups), 1)
        self.assertEqual(self._setting_in_backup(safety_backups[0]), "替换失败时必须保留")
        self.assertEqual(list(self.database_path.parent.glob(".restore_*.sqlite3")), [])

    def test_export_json_contains_version_and_all_user_tables(self) -> None:
        self.database.set_setting("问候", "你好，CalorieK")

        exported = self.service.export_json(self.root / "caloriek-export")
        payload = json.loads(exported.read_text(encoding="utf-8"))

        self.assertEqual(exported.suffix, ".json")
        self.assertEqual(payload["app_name"], APP_NAME)
        self.assertEqual(payload["app_version"], APP_VERSION)
        self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
        self.assertIn("schema_version", payload["tables"])
        self.assertIn("app_settings", payload["tables"])
        exported_settings = {
            row["key"]: row["value"] for row in payload["tables"]["app_settings"]
        }
        self.assertEqual(exported_settings["问候"], "你好，CalorieK")


if __name__ == "__main__":
    unittest.main()
