"""Safe SQLite backup, restore, and JSON export operations."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db.database import Database
from app.version import APP_NAME, APP_VERSION, SCHEMA_VERSION


class BackupError(RuntimeError):
    """Raised when a backup cannot be validated or restored safely."""


@dataclass(frozen=True)
class BackupManifest:
    app_name: str
    app_version: str
    schema_version: int
    created_at: str
    database_file: str = "caloriek.sqlite3"

    def as_dict(self) -> dict[str, Any]:
        return {
            "app_name": self.app_name,
            "app_version": self.app_version,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "database_file": self.database_file,
        }


class BackupService:
    """Create portable ZIP backups without copying a live WAL file directly."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()
        self.data_dir = self.database_path.parent

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _file_timestamp() -> str:
        return datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    @staticmethod
    def _read_schema_version(path: Path) -> int:
        try:
            # sqlite3.Connection.__exit__ commits/rolls back but does not close;
            # closing() is required so Windows does not retain a file lock.
            with closing(sqlite3.connect(path)) as connection:
                row = connection.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()
                check = connection.execute("PRAGMA quick_check").fetchone()
        except sqlite3.Error as exc:
            raise BackupError(f"无法读取备份数据库：{exc}") from exc
        if not check or check[0] != "ok":
            raise BackupError("备份数据库未通过 SQLite 完整性校验。")
        if not row or row[0] is None:
            raise BackupError("备份数据库缺少 schema_version。")
        return int(row[0])

    @staticmethod
    def _schema_signature(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        """Return the structural contract needed by the current application."""

        tables = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        signature: dict[str, dict[str, Any]] = {}
        for (raw_name,) in tables:
            name = str(raw_name)
            quoted = name.replace('"', '""')
            columns = [
                tuple(row[1:6])
                for row in connection.execute(
                    f'PRAGMA table_info("{quoted}")'
                ).fetchall()
            ]
            foreign_keys = [
                tuple(row[2:8])
                for row in connection.execute(
                    f'PRAGMA foreign_key_list("{quoted}")'
                ).fetchall()
            ]
            signature[name] = {
                "columns": columns,
                "foreign_keys": foreign_keys,
            }
        return signature

    @classmethod
    def _validate_database_structure(cls, path: Path) -> int:
        """Validate integrity and the full V1 table/column contract in isolation."""

        schema_version = cls._read_schema_version(path)
        if schema_version != SCHEMA_VERSION:
            return schema_version
        try:
            schema_path = Path(__file__).parents[1] / "db" / "schema.sql"
            schema_sql = schema_path.read_text(encoding="utf-8")
            with closing(sqlite3.connect(":memory:")) as expected:
                expected.executescript(schema_sql)
                expected_signature = cls._schema_signature(expected)
            with closing(sqlite3.connect(path)) as candidate:
                integrity = candidate.execute("PRAGMA integrity_check").fetchone()
                if not integrity or integrity[0] != "ok":
                    raise BackupError("备份数据库未通过 SQLite 完整性校验。")
                foreign_key_errors = candidate.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if foreign_key_errors:
                    raise BackupError("备份数据库包含无效的外键引用。")
                actual_signature = cls._schema_signature(candidate)
                unexpected_programmable_objects = candidate.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type IN ('trigger', 'view') ORDER BY name"
                ).fetchall()
        except BackupError:
            raise
        except (OSError, UnicodeError, sqlite3.Error, RuntimeError) as exc:
            raise BackupError(f"备份数据库结构无效：{exc}") from exc

        missing_tables = sorted(set(expected_signature) - set(actual_signature))
        mismatched_tables = sorted(
            table
            for table, expected_table in expected_signature.items()
            if table in actual_signature
            and actual_signature[table] != expected_table
        )
        if missing_tables or mismatched_tables:
            details: list[str] = []
            if missing_tables:
                details.append("缺少表 " + ", ".join(missing_tables))
            if mismatched_tables:
                details.append("列或外键不匹配 " + ", ".join(mismatched_tables))
            raise BackupError("备份数据库结构无效：" + "；".join(details))
        if unexpected_programmable_objects:
            names = ", ".join(str(row[0]) for row in unexpected_programmable_objects)
            raise BackupError("备份数据库结构无效：包含未知触发器或视图 " + names)

        try:
            # Only after proving that no required table/column is missing, run
            # the exact idempotent startup path against the isolated candidate.
            # This validates indexes and the built-in seed operation without
            # allowing initialize() to silently recreate a missing backup table.
            Database(path).initialize(seed_foods=True)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            raise BackupError(f"备份数据库无法完成启动验证：{exc}") from exc
        return schema_version

    def create_backup(
        self, destination: str | Path | None = None, *, prefix: str = "caloriek"
    ) -> Path:
        if not self.database_path.exists():
            raise BackupError(f"数据库不存在：{self.database_path}")
        if destination is None:
            destination_dir = self.data_dir / "backups"
            destination_dir.mkdir(parents=True, exist_ok=True)
            destination_path = destination_dir / (
                f"{prefix}_{self._file_timestamp()}.zip"
            )
        else:
            destination_path = Path(destination).expanduser().resolve()
            if destination_path.suffix.lower() != ".zip":
                destination_path = destination_path.with_suffix(".zip")
            destination_path.parent.mkdir(parents=True, exist_ok=True)

        staged_archive: Path | None = None
        with tempfile.TemporaryDirectory(dir=self.data_dir) as temporary:
            snapshot = Path(temporary) / "caloriek.sqlite3"
            try:
                with closing(sqlite3.connect(self.database_path)) as source:
                    with closing(sqlite3.connect(snapshot)) as target:
                        source.backup(target)
                # Read the version from the snapshot rather than from the live
                # database.  The manifest therefore describes the exact bytes in
                # the archive even if another writer commits during the backup.
                manifest = BackupManifest(
                    app_name=APP_NAME,
                    app_version=APP_VERSION,
                    schema_version=self._read_schema_version(snapshot),
                    created_at=self._timestamp(),
                )
                config = {
                    "data_directory": str(self.data_dir),
                    "database_filename": self.database_path.name,
                }

                # Build beside the destination and replace only after ZipFile has
                # closed successfully.  A failed backup must not truncate a good
                # archive that already exists at the requested path.
                descriptor, staged_name = tempfile.mkstemp(
                    dir=destination_path.parent,
                    prefix=f".{destination_path.name}.",
                    suffix=".tmp",
                )
                os.close(descriptor)
                staged_archive = Path(staged_name)
                with zipfile.ZipFile(
                    staged_archive, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    archive.write(snapshot, manifest.database_file)
                    archive.writestr(
                        "manifest.json",
                        json.dumps(manifest.as_dict(), ensure_ascii=False, indent=2),
                    )
                    archive.writestr(
                        "config.json",
                        json.dumps(config, ensure_ascii=False, indent=2),
                    )
                os.replace(staged_archive, destination_path)
                staged_archive = None
            except (OSError, sqlite3.Error, zipfile.BadZipFile, BackupError) as exc:
                raise BackupError(f"创建备份失败：{exc}") from exc
            finally:
                if staged_archive is not None:
                    staged_archive.unlink(missing_ok=True)
        return destination_path

    @staticmethod
    def _safe_member_names(archive: zipfile.ZipFile) -> set[str]:
        names: set[str] = set()
        for info in archive.infolist():
            candidate = Path(info.filename)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise BackupError("备份包包含不安全的文件路径。")
            if info.filename in names:
                raise BackupError("备份包包含重复文件名。")
            names.add(info.filename)
        return names

    def inspect_backup(self, backup_path: str | Path) -> BackupManifest:
        path = Path(backup_path).expanduser().resolve()
        try:
            with zipfile.ZipFile(path, "r") as archive:
                names = self._safe_member_names(archive)
                if "manifest.json" not in names:
                    raise BackupError("备份包缺少 manifest.json。")
                raw = json.loads(archive.read("manifest.json").decode("utf-8"))
        except BackupError:
            raise
        except (OSError, zipfile.BadZipFile, KeyError, ValueError, UnicodeError) as exc:
            raise BackupError(f"无法读取备份包：{exc}") from exc
        try:
            manifest = BackupManifest(
                app_name=str(raw["app_name"]),
                app_version=str(raw["app_version"]),
                schema_version=int(raw["schema_version"]),
                created_at=str(raw["created_at"]),
                database_file=str(raw.get("database_file", "caloriek.sqlite3")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BackupError("manifest.json 格式无效。") from exc
        database_member = Path(manifest.database_file)
        if (
            not manifest.database_file
            or database_member.is_absolute()
            or ".." in database_member.parts
            or manifest.database_file not in names
        ):
            raise BackupError("备份包缺少 manifest 声明的数据库文件。")
        return manifest

    def restore_backup(self, backup_path: str | Path) -> Path:
        """Validate and atomically replace the database.

        A backup of the current database is always created immediately before
        replacement. All archive extraction and SQLite validation happens before
        the live path changes.
        """

        source_zip = Path(backup_path).expanduser().resolve()
        manifest = self.inspect_backup(source_zip)
        if manifest.app_name != APP_NAME:
            raise BackupError("该备份不属于 CalorieK。")
        if manifest.schema_version != SCHEMA_VERSION:
            raise BackupError(
                f"schema_version 不兼容：备份为 {manifest.schema_version}，"
                f"程序要求 {SCHEMA_VERSION}。"
            )
        with tempfile.TemporaryDirectory(dir=self.data_dir) as temporary:
            temporary_dir = Path(temporary)
            candidate = temporary_dir / "candidate.sqlite3"
            stage: Path | None = None
            safety_backup: Path | None = None
            try:
                with zipfile.ZipFile(source_zip, "r") as archive:
                    with archive.open(manifest.database_file, "r") as source:
                        with candidate.open("wb") as target:
                            shutil.copyfileobj(source, target)
                actual_schema = self._validate_database_structure(candidate)
                if actual_schema != manifest.schema_version:
                    raise BackupError("数据库 schema 与 manifest 不一致。")

                # WAL is a persistent database-header setting. Enable it on the
                # isolated candidate so every fallible SQLite operation finishes
                # before the live path is atomically replaced.
                with closing(sqlite3.connect(candidate)) as candidate_connection:
                    mode = candidate_connection.execute(
                        "PRAGMA journal_mode=WAL"
                    ).fetchone()
                    if not mode or str(mode[0]).lower() != "wal":
                        raise BackupError("无法为恢复数据库启用 WAL。")
                    candidate_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

                stage = self.data_dir / f".restore_{self._file_timestamp()}.sqlite3"
                shutil.copy2(candidate, stage)

                # All validation and staging are complete.  Take the mandatory
                # safety backup immediately before touching the live database.
                safety_backup = self.create_backup(prefix="pre_restore")
                if self.database_path.exists():
                    with closing(sqlite3.connect(self.database_path)) as connection:
                        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                for suffix in ("-wal", "-shm"):
                    Path(f"{self.database_path}{suffix}").unlink(missing_ok=True)
                os.replace(stage, self.database_path)
                stage = None
            except BackupError:
                raise
            except (OSError, sqlite3.Error, zipfile.BadZipFile, KeyError) as exc:
                location = (
                    f"；安全备份位于 {safety_backup}"
                    if safety_backup is not None
                    else ""
                )
                raise BackupError(
                    f"恢复失败，当前数据库保持不变{location}：{exc}"
                ) from exc
            finally:
                if stage is not None:
                    stage.unlink(missing_ok=True)
        if safety_backup is None:  # pragma: no cover - defensive invariant
            raise BackupError("恢复失败：未创建恢复前安全备份。")
        return safety_backup

    def export_json(self, destination: str | Path) -> Path:
        if not self.database_path.exists():
            raise BackupError(f"数据库不存在：{self.database_path}")
        destination_path = Path(destination).expanduser().resolve()
        if destination_path.suffix.lower() != ".json":
            destination_path = destination_path.with_suffix(".json")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "schema_version": self._read_schema_version(self.database_path),
            "exported_at": self._timestamp(),
            "tables": {},
        }
        staged_export: Path | None = None
        try:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                tables = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for table_row in tables:
                    table = str(table_row["name"])
                    quoted = table.replace('"', '""')
                    rows = connection.execute(f'SELECT * FROM "{quoted}"').fetchall()
                    payload["tables"][table] = [dict(row) for row in rows]
            descriptor, staged_name = tempfile.mkstemp(
                dir=destination_path.parent,
                prefix=f".{destination_path.name}.",
                suffix=".tmp",
            )
            os.close(descriptor)
            staged_export = Path(staged_name)
            staged_export.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(staged_export, destination_path)
            staged_export = None
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise BackupError(f"导出失败：{exc}") from exc
        finally:
            if staged_export is not None:
                staged_export.unlink(missing_ok=True)
        return destination_path
