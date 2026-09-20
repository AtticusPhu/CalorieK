"""Profile and effective-dated profile revision operations."""

from __future__ import annotations

import math
import sqlite3
from contextlib import nullcontext
from datetime import date, datetime, time
from typing import Any

from app.db.database import (
    Database,
    normalize_date,
    normalize_datetime,
    now_iso,
    row_to_dict,
)


def _gender(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {"m": "male", "男": "male", "f": "female", "女": "female"}
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"male", "female"}:
        raise ValueError("gender must be 'male' or 'female'")
    return normalized


def _clock(value: str) -> str:
    try:
        parsed = time.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid time: {value!r}") from exc
    return parsed.isoformat(timespec="minutes")


def _daily_schedule(wake_time: str, sleep_time: str) -> tuple[str, str]:
    """Normalize and validate the two boundaries of a daily schedule.

    A schedule with identical boundaries is ambiguous (zero awake hours or
    twenty-four awake hours).  Keep this validation in the persistence layer
    so an invalid revision is rejected before any profile data is written.
    """

    wake_value = _clock(wake_time)
    sleep_value = _clock(sleep_time)
    # Baseline integration treats these as local wall-clock boundaries and
    # deliberately ignores any offset attached to a ``time`` value.  Mirror
    # that rule here so values such as 07:00+08:00 and 07:00+09:00 cannot
    # evade persistence validation and fail only during later calculation.
    wake_clock = time.fromisoformat(wake_value).replace(tzinfo=None)
    sleep_clock = time.fromisoformat(sleep_value).replace(tzinfo=None)
    if wake_clock == sleep_clock:
        raise ValueError("wake_time and sleep_time must differ")
    return wake_value, sleep_value


def _positive_number(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError(f"{label} must be a finite positive number")
    return normalized


class ProfileService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def is_initialized(self) -> bool:
        with self.db.connection() as connection:
            return connection.execute("SELECT 1 FROM profile WHERE id = 1").fetchone() is not None

    def create_profile(
        self,
        *,
        gender: str,
        birth_date: date | str,
        height_cm: float,
        current_weight_kg: float,
        wake_time: str = "07:00",
        sleep_time: str = "23:00",
        awake_multiplier: float = 1.20,
        sleep_multiplier: float = 0.95,
        note: str | None = None,
        bmr_formula: str = "mifflin_st_jeor",
        effective_from: date | datetime | str | None = None,
        measured_at: datetime | date | str | None = None,
    ) -> int:
        """Create the singleton profile, its first revision, and a real weight fact."""

        gender_value = _gender(gender)
        birth_value = normalize_date(birth_date)
        height_value = _positive_number(height_cm, "height_cm")
        weight_value = _positive_number(current_weight_kg, "current_weight_kg")
        awake_value = _positive_number(awake_multiplier, "awake_multiplier")
        sleep_multiplier_value = _positive_number(
            sleep_multiplier, "sleep_multiplier"
        )
        wake_value, sleep_value = _daily_schedule(wake_time, sleep_time)
        occurred_at, measured_date = normalize_datetime(measured_at)
        revision_date = normalize_date(effective_from or measured_date)
        if birth_value > min(measured_date, revision_date):
            raise ValueError("birth_date cannot be after the initial effective date")
        formula_value = str(bmr_formula).strip()
        if not formula_value:
            raise ValueError("bmr_formula must not be empty")
        timestamp = now_iso()

        with self.db.transaction() as connection:
            if connection.execute("SELECT 1 FROM profile WHERE id = 1").fetchone():
                raise ValueError("profile is already initialized")
            connection.execute(
                "INSERT INTO profile(id, gender, birth_date, note, created_at, updated_at) "
                "VALUES (1, ?, ?, ?, ?, ?)",
                (gender_value, birth_value, note, timestamp, timestamp),
            )
            connection.execute(
                """
                INSERT INTO profile_revisions(
                    profile_id, effective_from, gender, birth_date, height_cm,
                    wake_time, sleep_time, awake_multiplier, sleep_multiplier,
                    bmr_formula, created_at
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_date,
                    gender_value,
                    birth_value,
                    height_value,
                    wake_value,
                    sleep_value,
                    awake_value,
                    sleep_multiplier_value,
                    formula_value,
                    timestamp,
                ),
            )
            cursor = connection.execute(
                """
                INSERT INTO weight_measurements(
                    occurred_at, local_date, weight_kg, anchor, note,
                    created_at, updated_at, active
                ) VALUES (?, ?, ?, 'AUTO', ?, ?, ?, 1)
                """,
                (
                    occurred_at,
                    measured_date,
                    weight_value,
                    "Initial profile weight",
                    timestamp,
                    timestamp,
                ),
            )
            self.db.mark_dirty(min(revision_date, measured_date), connection=connection)
            return int(cursor.lastrowid)

    def get_profile(
        self, *, connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with (nullcontext(connection) if connection is not None else self.db.connection()) as connection:
            row = connection.execute("SELECT * FROM profile WHERE id = 1").fetchone()
        return row_to_dict(row)

    def get_current_profile(
        self, *, connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        profile = self.get_profile(connection=connection)
        if profile is None:
            return None
        revision = (
            self.get_revision_for_date(date.today(), connection=connection)
            or self.get_latest_revision(connection=connection)
        )
        if revision:
            profile.update(revision)
            profile["profile_id"] = 1
        return profile

    def get_revision_for_date(
        self, value: date | datetime | str, *, connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        target = normalize_date(value)
        with (nullcontext(connection) if connection is not None else self.db.connection()) as connection:
            row = connection.execute(
                "SELECT * FROM profile_revisions "
                "WHERE profile_id = 1 AND effective_from <= ? "
                "ORDER BY effective_from DESC, id DESC LIMIT 1",
                (target,),
            ).fetchone()
        return row_to_dict(row)

    def get_latest_revision(
        self, *, connection: sqlite3.Connection | None = None,
    ) -> dict[str, Any] | None:
        with (nullcontext(connection) if connection is not None else self.db.connection()) as connection:
            row = connection.execute(
                "SELECT * FROM profile_revisions WHERE profile_id = 1 "
                "ORDER BY effective_from DESC, id DESC LIMIT 1"
            ).fetchone()
        return row_to_dict(row)

    def list_revisions(self) -> list[dict[str, Any]]:
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM profile_revisions WHERE profile_id = 1 "
                "ORDER BY effective_from, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_profile(
        self,
        *,
        effective_from: date | datetime | str | None = None,
        gender: str | None = None,
        birth_date: date | str | None = None,
        height_cm: float | None = None,
        wake_time: str | None = None,
        sleep_time: str | None = None,
        awake_multiplier: float | None = None,
        sleep_multiplier: float | None = None,
        bmr_formula: str | None = None,
        note: str | None = None,
        update_note: bool = False,
        connection: sqlite3.Connection | None = None,
    ) -> int | None:
        """Update current metadata and append/replace an effective-day revision.

        Repeated edits on the same effective date replace that day's revision;
        earlier effective dates remain immutable and continue to drive history.
        A supplied connection participates in the caller's transaction, including
        reads and dirty/cache writes; this method never commits or closes it.
        """

        target_date = normalize_date(effective_from or date.today())
        current = (
            self.get_revision_for_date(target_date, connection=connection)
            or self.get_latest_revision(connection=connection)
        )
        profile = self.get_profile(connection=connection)
        if profile is None or current is None:
            raise LookupError("profile is not initialized")

        gender_value = _gender(gender) if gender is not None else current["gender"]
        birth_value = normalize_date(birth_date) if birth_date is not None else current["birth_date"]
        height_value = _positive_number(
            height_cm if height_cm is not None else current["height_cm"],
            "height_cm",
        )
        wake_value, sleep_value = _daily_schedule(
            wake_time if wake_time is not None else current["wake_time"],
            sleep_time if sleep_time is not None else current["sleep_time"],
        )
        awake_value = _positive_number(
            awake_multiplier if awake_multiplier is not None else current["awake_multiplier"],
            "awake_multiplier",
        )
        sleep_multiplier_value = _positive_number(
            sleep_multiplier if sleep_multiplier is not None else current["sleep_multiplier"],
            "sleep_multiplier",
        )
        formula_value = str(
            bmr_formula if bmr_formula is not None else current["bmr_formula"]
        ).strip()
        if birth_value > target_date:
            raise ValueError("birth_date cannot be after effective_from")
        if not formula_value:
            raise ValueError("bmr_formula must not be empty")

        revision_requested = any(
            value is not None
            for value in (
                gender,
                birth_date,
                height_cm,
                wake_time,
                sleep_time,
                awake_multiplier,
                sleep_multiplier,
                bmr_formula,
            )
        )
        timestamp = now_iso()
        revision_id: int | None = None
        with (nullcontext(connection) if connection is not None else self.db.transaction()) as connection:
            connection.execute(
                "UPDATE profile SET gender = ?, birth_date = ?, note = ?, updated_at = ? "
                "WHERE id = 1",
                (
                    gender_value,
                    birth_value,
                    note if update_note or note is not None else profile["note"],
                    timestamp,
                ),
            )
            if revision_requested:
                connection.execute(
                    """
                    INSERT INTO profile_revisions(
                        profile_id, effective_from, gender, birth_date, height_cm,
                        wake_time, sleep_time, awake_multiplier, sleep_multiplier,
                        bmr_formula, created_at
                    ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(profile_id, effective_from) DO UPDATE SET
                        gender = excluded.gender,
                        birth_date = excluded.birth_date,
                        height_cm = excluded.height_cm,
                        wake_time = excluded.wake_time,
                        sleep_time = excluded.sleep_time,
                        awake_multiplier = excluded.awake_multiplier,
                        sleep_multiplier = excluded.sleep_multiplier,
                        bmr_formula = excluded.bmr_formula,
                        created_at = excluded.created_at
                    """,
                    (
                        target_date,
                        gender_value,
                        birth_value,
                        height_value,
                        wake_value,
                        sleep_value,
                        awake_value,
                        sleep_multiplier_value,
                        formula_value,
                        timestamp,
                    ),
                )
                revision_id = int(
                    connection.execute(
                        "SELECT id FROM profile_revisions "
                        "WHERE profile_id = 1 AND effective_from = ?",
                        (target_date,),
                    ).fetchone()[0]
                )
                self.db.mark_dirty(target_date, connection=connection)
        return revision_id

    def add_revision(self, **values: Any) -> int:
        result = self.update_profile(**values)
        if result is None:
            raise ValueError("at least one revision field must be provided")
        return result

    @staticmethod
    def age_on(birth_date: date | str, on_date: date | str | None = None) -> int:
        born = date.fromisoformat(normalize_date(birth_date))
        target = date.fromisoformat(normalize_date(on_date or date.today()))
        if born > target:
            raise ValueError("birth date cannot be after target date")
        return target.year - born.year - ((target.month, target.day) < (born.month, born.day))
