"""Filesystem locations used by source and frozen Windows builds."""

from __future__ import annotations

import os
import sys
import json
from pathlib import Path


DATA_DIR_ENV = "CALORIEK_DATA_DIR"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def location_config_path() -> Path:
    """Location of the tiny bootstrap file that points at the external data dir."""

    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data)
            if local_app_data
            else Path.home() / "AppData" / "Local"
        )
        return base / "CalorieK" / "config.json"
    return project_root() / "data_location.json"


def _configured_data_dir() -> Path | None:
    config = location_config_path()
    if not config.exists():
        return None
    try:
        payload = json.loads(config.read_text(encoding="utf-8"))
        value = payload.get("data_directory")
        return Path(value).expanduser().resolve() if value else None
    except (OSError, ValueError, TypeError):
        return None


def default_data_dir() -> Path:
    """Return a writable external data directory.

    Source checkouts keep their database in ``./data``.  Frozen builds use the
    current Windows user's local application-data directory so that installing
    under Program Files never makes the database read-only.
    """

    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser().resolve()
    configured = _configured_data_dir()
    if configured is not None:
        return configured
    if getattr(sys, "frozen", False):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "CalorieK" / "data"
        return Path.home() / "AppData" / "Local" / "CalorieK" / "data"
    return project_root() / "data"


def save_data_dir_preference(path: str | Path) -> Path:
    """Persist a new data location atomically for the next application start."""

    config = location_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_suffix(config.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"data_directory": str(Path(path).expanduser().resolve())},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, config)
    return config


def ensure_data_dir(path: str | Path | None = None) -> Path:
    directory = Path(path).expanduser().resolve() if path else default_data_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def database_path(data_dir: str | Path | None = None) -> Path:
    return ensure_data_dir(data_dir) / "caloriek.sqlite3"
