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
from app.db.migrations import validate_schema
from app.db.writer_guard import WriterLease
from app.timestamps import now_iso
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
        return now_iso()

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
        if not row or type(row[0]) is not int or row[0] < 1:
            raise BackupError("备份数据库缺少有效的整数 schema_version。")
        return row[0]

    @classmethod
    def _validate_database_structure(cls, path: Path) -> int:
        """Validate the declared schema before migrating the isolated candidate."""

        schema_version = cls._read_schema_version(path)
        if schema_version not in (1, 2, SCHEMA_VERSION):
            raise BackupError(f"schema_version 不兼容：{schema_version}")
        try:
            with closing(sqlite3.connect(path)) as candidate:
                validate_schema(candidate, schema_version)
        except (OSError, UnicodeError, sqlite3.Error, RuntimeError, ValueError) as exc:
            raise BackupError(f"备份数据库结构无效：{exc}") from exc

        try:
            # A legacy backup is converted only after its declared v1/v2 structure
            # passes validation. All migration/seed writes target this temporary
            # candidate; a failure can never modify the live database.
            Database(path).initialize(seed_foods=True)
            if cls._read_schema_version(path) != SCHEMA_VERSION:
                raise BackupError("迁移后的备份数据库不是当前 schema_version。")
            with closing(sqlite3.connect(path)) as candidate:
                validate_schema(candidate, SCHEMA_VERSION)
        except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
            raise BackupError(f"备份数据库无法完成启动验证：{exc}") from exc
        return schema_version

    @staticmethod
    def _checkpoint_for_replace(connection: sqlite3.Connection) -> None:
        """Never discard WAL frames when another connection blocks checkpointing."""

        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if not result or result[0] != 0 or result[1] != result[2]:
            raise BackupError("数据库正被其他连接使用，无法安全完成 WAL checkpoint；请关闭其他实例后重试。")

    def _prepare_live_for_replace(self) -> None:
        """Let SQLite remove its sidecars only after excluding existing WAL users.

        Merely checkpointing successfully is insufficient: idle/read connections
        may still own the WAL/SHM files. The journal-mode transition requires an
        exclusive SQLite lock and fails safely if such a connection remains.
        Never unlink another connection's WAL/SHM files ourselves.
        """

        with closing(sqlite3.connect(self.database_path, timeout=0)) as connection:
            self._checkpoint_for_replace(connection)
            mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if not mode or str(mode[0]).lower() != "delete":
                raise BackupError("数据库正被其他连接使用，无法安全替换。")

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
            if type(raw["schema_version"]) is not int:
                raise ValueError("schema_version must be an integer")
            manifest = BackupManifest(
                app_name=str(raw["app_name"]),
                app_version=str(raw["app_version"]),
                schema_version=raw["schema_version"],
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
        # Standalone restore tools must obey the same ownership policy as the
        # GUI. Keep the lease through validation, safety backup and replacement.
        with WriterLease(self.database_path):
            return self._restore_backup(backup_path)

    def _restore_backup(self, backup_path: str | Path) -> Path:
        """Validate and atomically replace the database.

        A backup of the current database is always created immediately before
        replacement. All archive extraction and SQLite validation happens before
        the live path changes.
        """

        source_zip = Path(backup_path).expanduser().resolve()
        manifest = self.inspect_backup(source_zip)
        if manifest.app_name != APP_NAME:
            raise BackupError("该备份不属于 CalorieK。")
        if manifest.schema_version not in (1, 2, SCHEMA_VERSION):
            raise BackupError(
                f"schema_version 不兼容：备份为 {manifest.schema_version}，"
                f"程序要求 {SCHEMA_VERSION}。"
            )
        with tempfile.TemporaryDirectory(dir=self.data_dir) as temporary:
            temporary_dir = Path(temporary)
            candidate = temporary_dir / "candidate.sqlite3"
            stage: Path | None = None
            safety_backup: Path | None = None
            live_prepared = False
            try:
                with zipfile.ZipFile(source_zip, "r") as archive:
                    with archive.open(manifest.database_file, "r") as source:
                        with candidate.open("wb") as target:
                            shutil.copyfileobj(source, target)
                actual_schema = self._read_schema_version(candidate)
                if actual_schema != manifest.schema_version:
                    raise BackupError("数据库 schema 与 manifest 不一致。")
                self._validate_database_structure(candidate)

                # WAL is a persistent database-header setting. Enable it on the
                # isolated candidate so every fallible SQLite operation finishes
                # before the live path is atomically replaced.
                with closing(sqlite3.connect(candidate)) as candidate_connection:
                    mode = candidate_connection.execute(
                        "PRAGMA journal_mode=WAL"
                    ).fetchone()
                    if not mode or str(mode[0]).lower() != "wal":
                        raise BackupError("无法为恢复数据库启用 WAL。")
                    self._checkpoint_for_replace(candidate_connection)

                stage = self.data_dir / f".restore_{self._file_timestamp()}.sqlite3"
                shutil.copy2(candidate, stage)

                # All validation and staging are complete.  Take the mandatory
                # safety backup immediately before touching the live database.
                safety_backup = self.create_backup(prefix="pre_restore")
                if self.database_path.exists():
                    self._prepare_live_for_replace()
                    live_prepared = True
                os.replace(stage, self.database_path)
                stage = None
                live_prepared = False
            except (BackupError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError) as exc:
                journal_warning = ""
                if live_prepared:
                    try:
                        # A failed rename still leaves the original main file.
                        # Restore its usual WAL mode without changing user rows.
                        with closing(sqlite3.connect(self.database_path, timeout=0)) as connection:
                            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()
                            if not mode or str(mode[0]).lower() != "wal":
                                raise BackupError("无法恢复 WAL 模式")
                    except (OSError, sqlite3.Error, BackupError) as journal_error:
                        journal_warning = f"；原库仍保留，但 WAL 模式恢复失败：{journal_error}"
                location = (
                    f"；安全备份位于 {safety_backup}"
                    if safety_backup is not None
                    else ""
                )
                raise BackupError(
                    f"恢复失败，当前数据库保持不变{location}{journal_warning}：{exc}"
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
            "schema_version": None,  # Filled from the same snapshot as the rows.
            "exported_at": self._timestamp(),
            "tables": {},
        }
        staged_export: Path | None = None
        try:
            with closing(sqlite3.connect(
                self.database_path.as_uri() + "?mode=ro", uri=True,
                isolation_level=None,
            )) as connection:
                connection.row_factory = sqlite3.Row
                # BEGIN is deferred/read-only, not BEGIN IMMEDIATE. The first
                # read pins one WAL snapshot for metadata, table list and rows.
                connection.execute("BEGIN")
                check = connection.execute("PRAGMA quick_check").fetchone()
                if not check or check[0] != "ok":
                    raise BackupError("数据库未通过 SQLite 完整性校验。")
                version = connection.execute("SELECT MAX(version) FROM schema_version").fetchone()
                if not version or type(version[0]) is not int or version[0] < 1:
                    raise BackupError("数据库缺少有效的整数 schema_version。")
                payload["schema_version"] = version[0]
                tables = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
                for table_row in tables:
                    table = str(table_row["name"])
                    payload["tables"][table] = self._export_rows(connection, table)
                connection.rollback()  # End read snapshot before file I/O.
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

    @staticmethod
    def _export_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
        quoted = table.replace('"', '""')
        return [dict(row) for row in connection.execute(f'SELECT * FROM "{quoted}"')]
