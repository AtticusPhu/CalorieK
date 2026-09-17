"""Raw, standalone SQLite recovery snapshots taken before live v1 migration."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path
from time import monotonic

from .schema import validate_schema


def create_pre_migration_snapshot(database_path: Path, *, timeout: float) -> Path:
    """Copy committed v1 data, including WAL pages, without changing its units.

    The caller must hold BEGIN IMMEDIATE on the live database, with no writes
    yet, until migration commits/rolls back. This prevents another writer from
    changing the facts between the snapshot and conversion. Use a separate
    read-only source: backing up the write-transaction connection can hang.
    """

    directory = database_path.parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    descriptor, name = tempfile.mkstemp(
        dir=directory, prefix=f"pre_migration_v1_to_v2_{stamp}_", suffix=".tmp"
    )
    os.close(descriptor)
    staged = Path(name)
    snapshot = staged.with_suffix(".sqlite3")
    deadline = monotonic() + max(timeout, 0.1)

    def progress(status: int, _remaining: int, _total: int) -> None:
        if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) and monotonic() >= deadline:
            raise TimeoutError("pre-migration snapshot could not acquire a read lock")

    try:
        with closing(sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True, timeout=timeout)) as source:
            with closing(sqlite3.connect(staged, timeout=timeout)) as target:
                source.backup(target, pages=256, progress=progress, sleep=0.01)
                # A raw .sqlite3 snapshot must be usable without WAL/SHM files.
                mode = target.execute("PRAGMA journal_mode = DELETE").fetchone()
                if not mode or str(mode[0]).lower() != "delete":
                    raise RuntimeError("pre-migration snapshot is not a standalone SQLite file")
                version = target.execute("SELECT MAX(version) FROM schema_version").fetchone()
                if not version or version[0] != 1:
                    raise RuntimeError("pre-migration snapshot must remain schema v1")
                validate_schema(target, 1)
        # Windows FlushFileBuffers (used by fsync) requires a writable handle.
        with staged.open("rb+") as handle:
            os.fsync(handle.fileno())
        # No final recovery filename is published until backup and validation pass.
        os.replace(staged, snapshot)
        return snapshot
    finally:
        # Only this attempt's incomplete staging files may be removed. A finished
        # snapshot is retained even if the following migration fails.
        staged.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(f"{staged}{suffix}").unlink(missing_ok=True)
