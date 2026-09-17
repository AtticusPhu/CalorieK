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
from app.db.migrations import validate_schema
from app.energy_units import kcal_to_kj
from app.services.backup_service import BackupError, BackupService
from app.version import APP_NAME, APP_VERSION, SCHEMA_VERSION
from tests.test_energy_migration import create_legacy_database


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

    def _archive_database(self, path: Path, schema_version: object) -> Path:
        backup = self.root / f"{path.stem}.zip"
        manifest = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "schema_version": schema_version,
            "created_at": "2026-09-15T12:00:00+08:00",
            "database_file": "caloriek.sqlite3",
        }
        with zipfile.ZipFile(backup, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            archive.write(path, "caloriek.sqlite3")
        return backup

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

    def test_restore_rejects_malformed_schema_before_touching_live_data(self) -> None:
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

    def test_restore_legacy_backup_converts_candidate_to_canonical_energy(self) -> None:
        self.database.set_setting("marker", "恢复前现库")
        legacy_path = self.root / "legacy" / "caloriek.sqlite3"
        create_legacy_database(legacy_path)
        legacy_service = BackupService(legacy_path)
        backup = legacy_service.create_backup(self.root / "legacy.zip")
        original_archive = backup.read_bytes()
        self.assertEqual(legacy_service.inspect_backup(backup).schema_version, 1)

        safety_backup = self.service.restore_backup(backup)

        self.assertEqual(self.database.get_schema_version(), SCHEMA_VERSION)
        self.assertEqual(self._setting_in_backup(safety_backup), "恢复前现库")
        self.assertEqual(float(self.database.get_setting("kj_per_kg")), kcal_to_kj(7700.123456789))
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT kj FROM foods WHERE id = 1").fetchone()[0], kcal_to_kj(123.456789123))
        # Restoring does not rewrite the source backup or original legacy DB.
        self.assertEqual(legacy_service.inspect_backup(backup).schema_version, 1)
        self.assertEqual(Database(legacy_path).get_schema_version(), 1)
        self.assertEqual(backup.read_bytes(), original_archive)
        with self.database.connection() as connection:
            validate_schema(connection, SCHEMA_VERSION)
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

        # Importing the same original archive again starts from its v1 bytes;
        # it must not multiply already canonical live values a second time.
        self.service.restore_backup(backup)
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("SELECT kj FROM foods WHERE id = 1").fetchone()[0], kcal_to_kj(123.456789123))
        self.assertEqual(backup.read_bytes(), original_archive)

    def test_restore_rejects_newer_database_and_manifest_without_live_writes(self) -> None:
        self.database.set_setting("marker", "未来版本不可覆盖")
        newer_path = self.root / "newer.sqlite3"
        create_legacy_database(newer_path)
        with closing(sqlite3.connect(newer_path)) as connection:
            connection.execute("UPDATE schema_version SET version = ?", (SCHEMA_VERSION + 1,))
            connection.commit()
        backup = self._archive_database(newer_path, SCHEMA_VERSION + 1)

        with patch.object(self.service, "create_backup") as safety_backup:
            with self.assertRaisesRegex(BackupError, "schema_version 不兼容"):
                self.service.restore_backup(backup)
            safety_backup.assert_not_called()
        self.assertEqual(self._setting(self.database_path), "未来版本不可覆盖")

    def test_restore_rejects_non_integer_manifest_schema_versions(self) -> None:
        legacy_path = self.root / "legacy.sqlite3"
        create_legacy_database(legacy_path)
        for invalid_version in (True, 1.5, "1"):
            with self.subTest(version=invalid_version):
                backup = self._archive_database(legacy_path, invalid_version)
                with self.assertRaisesRegex(BackupError, "manifest.json 格式无效"):
                    self.service.restore_backup(backup)

    def test_restore_rejects_legacy_missing_energy_column_or_broken_foreign_key(self) -> None:
        self.database.set_setting("marker", "损坏备份不可覆盖")
        for corruption in ("missing_column", "broken_foreign_key"):
            with self.subTest(corruption=corruption):
                legacy_path = self.root / f"{corruption}.sqlite3"
                create_legacy_database(legacy_path)
                with closing(sqlite3.connect(legacy_path)) as connection:
                    if corruption == "missing_column":
                        connection.execute("ALTER TABLE foods DROP COLUMN kcal")
                    else:
                        connection.execute("UPDATE food_servings SET food_id = 9999")
                    connection.commit()
                backup = self._archive_database(legacy_path, 1)
                with patch.object(self.service, "create_backup") as safety_backup:
                    with self.assertRaisesRegex(BackupError, "结构无效"):
                        self.service.restore_backup(backup)
                    safety_backup.assert_not_called()
                self.assertEqual(self._setting(self.database_path), "损坏备份不可覆盖")

    def test_partial_staged_migration_failure_never_changes_live_or_source_archive(self) -> None:
        self.database.set_setting("marker", "部分迁移不可覆盖")
        legacy_path = self.root / "legacy.sqlite3"
        create_legacy_database(legacy_path)
        backup = self._archive_database(legacy_path, 1)
        archive_bytes = backup.read_bytes()
        staged_paths = []

        def fail_after_candidate_write(candidate_database, *, seed_foods=True):
            staged_paths.append(candidate_database.path)
            self.assertNotEqual(candidate_database.path, self.database_path)
            self.assertNotEqual(candidate_database.path, legacy_path)
            with closing(sqlite3.connect(candidate_database.path)) as connection:
                connection.execute("UPDATE foods SET kcal = 99999")
                connection.commit()
            raise RuntimeError("injected migration failure")

        with patch("app.services.backup_service.Database.initialize", new=fail_after_candidate_write):
            with self.assertRaisesRegex(BackupError, "injected migration failure"):
                self.service.restore_backup(backup)

        self.assertEqual(self._setting(self.database_path), "部分迁移不可覆盖")
        self.assertEqual(backup.read_bytes(), archive_bytes)
        self.assertTrue(staged_paths)
        self.assertTrue(all(not path.exists() for path in staged_paths))
        self.assertEqual(list(self.database_path.parent.glob(".restore_*.sqlite3")), [])
        self.assertEqual(list((self.database_path.parent / "backups").glob("pre_restore_*.zip")), [])

    def test_restore_validates_migrated_v2_before_live_backup_or_replace(self) -> None:
        self.database.set_setting("marker", "最终校验失败不可覆盖")
        legacy_path = self.root / "legacy.sqlite3"
        create_legacy_database(legacy_path)
        backup = self._archive_database(legacy_path, 1)
        archive_bytes = backup.read_bytes()
        validated_versions = []

        def reject_final_validation(connection, version):
            validated_versions.append(version)
            if version == SCHEMA_VERSION:
                self.assertEqual(connection.execute("SELECT MAX(version) FROM schema_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(connection.execute("SELECT kj FROM foods WHERE id = 1").fetchone()[0], kcal_to_kj(123.456789123))
                raise RuntimeError("injected final validation failure")
            validate_schema(connection, version)

        with patch("app.services.backup_service.validate_schema", side_effect=reject_final_validation):
            with patch.object(self.service, "create_backup") as safety_backup:
                with self.assertRaisesRegex(BackupError, "injected final validation failure"):
                    self.service.restore_backup(backup)
                safety_backup.assert_not_called()

        self.assertEqual(validated_versions, [1, SCHEMA_VERSION])
        self.assertEqual(self._setting(self.database_path), "最终校验失败不可覆盖")
        self.assertEqual(backup.read_bytes(), archive_bytes)

    def test_restore_rejects_incomplete_legacy_backup_before_migration(self) -> None:
        self.database.set_setting("marker", "保持现库")
        legacy_path = self.root / "legacy" / "caloriek.sqlite3"
        create_legacy_database(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute("DROP TABLE food_servings")
            connection.commit()
        backup = BackupService(legacy_path).create_backup(self.root / "incomplete-legacy.zip")

        with self.assertRaisesRegex(BackupError, "结构无效"):
            self.service.restore_backup(backup)

        self.assertEqual(self._setting(self.database_path), "保持现库")
        self.assertEqual(list((self.database_path.parent / "backups").glob("pre_restore_*.zip")), [])

    def test_failed_candidate_migration_preserves_live_database(self) -> None:
        self.database.set_setting("marker", "迁移失败保持现库")
        legacy_path = self.root / "legacy" / "caloriek.sqlite3"
        create_legacy_database(legacy_path)
        with closing(sqlite3.connect(legacy_path)) as connection:
            connection.execute("UPDATE app_settings SET value = 'invalid' WHERE key = 'kcal_per_kg'")
            connection.commit()
        backup = BackupService(legacy_path).create_backup(self.root / "invalid-setting.zip")

        with self.assertRaisesRegex(BackupError, "无法完成启动验证"):
            self.service.restore_backup(backup)

        self.assertEqual(self._setting(self.database_path), "迁移失败保持现库")
        self.assertEqual(self.database.get_schema_version(), SCHEMA_VERSION)
        self.assertEqual(list((self.database_path.parent / "backups").glob("pre_restore_*.zip")), [])

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
        with self.database.connection() as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    def test_busy_live_checkpoint_preserves_wal_and_current_commits(self) -> None:
        self.database.set_setting("marker", "旧备份")
        backup = self.service.create_backup(self.root / "source.zip")
        reader = sqlite3.connect(self.database_path, timeout=0)
        writer = sqlite3.connect(self.database_path, timeout=0)
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT value FROM app_settings WHERE key = 'marker'").fetchone()
            writer.execute("UPDATE app_settings SET value = 'WAL 中的新提交' WHERE key = 'marker'")
            writer.commit()
            wal = Path(f"{self.database_path}-wal")
            self.assertTrue(wal.exists())

            with self.assertRaisesRegex(BackupError, "WAL checkpoint"):
                self.service.restore_backup(backup)

            self.assertTrue(wal.exists())
            self.assertEqual(self._setting(self.database_path), "WAL 中的新提交")
            writer.execute("UPDATE app_settings SET value = '拒绝恢复后仍可写入' WHERE key = 'marker'")
            writer.commit()
        finally:
            reader.close()
            writer.close()

        self.assertEqual(self._setting(self.database_path), "拒绝恢复后仍可写入")
        safety_backups = list((self.database_path.parent / "backups").glob("pre_restore_*.zip"))
        self.assertEqual(len(safety_backups), 1)
        self.assertEqual(self._setting_in_backup(safety_backups[0]), "WAL 中的新提交")
        self.assertEqual(list(self.database_path.parent.glob(".restore_*.sqlite3")), [])

    def test_idle_live_wal_connection_is_not_replaced_after_checkpoint(self) -> None:
        self.database.set_setting("marker", "旧备份")
        backup = self.service.create_backup(self.root / "source.zip")
        self.database.set_setting("marker", "现库不可覆盖")
        with closing(sqlite3.connect(self.database_path, timeout=0)) as observer:
            observer.execute("SELECT value FROM app_settings WHERE key = 'marker'").fetchone()
            with self.assertRaises(BackupError):
                self.service.restore_backup(backup)
            self.assertEqual(observer.execute("SELECT value FROM app_settings WHERE key = 'marker'").fetchone()[0], "现库不可覆盖")
            self.assertEqual(observer.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(self._setting(self.database_path), "现库不可覆盖")

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
