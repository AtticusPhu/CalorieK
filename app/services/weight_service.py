"""Actual weight measurement persistence.

Predicted weights deliberately do not pass through this service.  Every row
managed here represents a user-entered, real measurement and keeps its original
timestamp for later OHLC reconstruction.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from app.timestamps import parse_local_datetime, preserve_or_normalize_event_update

from app.db.database import (
    Database,
    normalize_date,
    normalize_datetime,
    now_iso,
    row_to_dict,
)


ANCHORS = frozenset({"AUTO", "OPEN", "CLOSE"})


def normalize_anchor(value: str | None) -> str:
    """Normalize and validate the stored OHLC anchor override."""

    normalized = "AUTO" if value is None else str(value).strip().upper()
    if normalized not in ANCHORS:
        raise ValueError("anchor must be AUTO, OPEN, or CLOSE")
    return normalized


def _positive_weight(value: float) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized <= 0:
        raise ValueError("weight_kg must be a finite positive number")
    return normalized


class WeightService:
    """CRUD operations for immutable-origin actual weight facts.

    Editing and soft deletion are supported for data correction, and every such
    operation invalidates derived metrics from the earliest affected date.
    """

    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    def resolve_anchor(
        anchor: str | None,
        occurred_at: datetime | date | str,
    ) -> str:
        """Resolve ``AUTO`` using the V1 noon boundary without changing storage."""

        normalized = normalize_anchor(anchor)
        if normalized != "AUTO":
            return normalized
        occurred_value, _ = normalize_datetime(occurred_at)
        moment = parse_local_datetime(occurred_value)
        return "OPEN" if moment.hour < 12 else "CLOSE"

    def record_weight(
        self,
        weight_kg: float,
        *,
        occurred_at: datetime | date | str | None = None,
        anchor: str = "AUTO",
        note: str | None = None,
    ) -> int:
        """Persist one actual measurement and invalidate dependent calculations."""

        weight_value = _positive_weight(weight_kg)
        anchor_value = normalize_anchor(anchor)
        occurred_value, local_date = normalize_datetime(occurred_at)
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO weight_measurements(
                    occurred_at, local_date, weight_kg, anchor, note,
                    created_at, updated_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    occurred_value,
                    local_date,
                    weight_value,
                    anchor_value,
                    note,
                    timestamp,
                    timestamp,
                ),
            )
            self.db.mark_dirty(local_date, connection=connection)
            return int(cursor.lastrowid)

    # Compatibility aliases keep adapters concise while retaining one write path.
    add_measurement = record_weight
    add_weight = record_weight
    add_weight_measurement = record_weight
    create_measurement = record_weight
    record_measurement = record_weight
    create_weight_measurement = record_weight

    def get_measurement(
        self,
        measurement_id: int,
        *,
        include_inactive: bool = True,
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM weight_measurements WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (measurement_id,)).fetchone()
        return row_to_dict(row)

    get_weight = get_measurement
    get_weight_measurement = get_measurement

    def list_measurements(
        self,
        *,
        local_date: date | datetime | str | None = None,
        start_date: date | datetime | str | None = None,
        end_date: date | datetime | str | None = None,
        include_inactive: bool = False,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List actual measurements using inclusive natural-date filters."""

        if local_date is not None and (start_date is not None or end_date is not None):
            raise ValueError("local_date cannot be combined with a date range")
        if limit is not None and limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []

        clauses: list[str] = []
        parameters: list[Any] = []
        if local_date is not None:
            clauses.append("local_date = ?")
            parameters.append(normalize_date(local_date))
        if start_date is not None:
            clauses.append("local_date >= ?")
            parameters.append(normalize_date(start_date))
        if end_date is not None:
            clauses.append("local_date <= ?")
            parameters.append(normalize_date(end_date))
        if start_date is not None and end_date is not None:
            if normalize_date(start_date) > normalize_date(end_date):
                raise ValueError("start_date must not be after end_date")
        if not include_inactive:
            clauses.append("active = 1")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        direction = "DESC" if descending else "ASC"
        sql = (
            "SELECT * FROM weight_measurements"
            + where
            + f" ORDER BY occurred_at COLLATE CALORIEK_LOCAL {direction}, id {direction}"
        )
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(int(limit))
        with self.db.connection() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [dict(row) for row in rows]

    list_weights = list_measurements
    list_weight_measurements = list_measurements

    def latest_measurement(
        self,
        *,
        as_of: datetime | date | str | None = None,
        include_inactive: bool = False,
    ) -> dict[str, Any] | None:
        """Return the latest actual measurement, optionally at/before a point."""

        clauses: list[str] = []
        parameters: list[Any] = []
        if as_of is not None:
            if isinstance(as_of, date) and not isinstance(as_of, datetime):
                clauses.append("local_date <= ?")
                parameters.append(as_of.isoformat())
            elif isinstance(as_of, str) and "T" not in as_of and " " not in as_of:
                clauses.append("local_date <= ?")
                parameters.append(normalize_date(as_of))
            else:
                clauses.append("occurred_at COLLATE CALORIEK_LOCAL <= ?")
                # Comparisons retain historical fractional seconds even though
                # new fact writes intentionally keep existing second precision.
                parameters.append(parse_local_datetime(as_of).isoformat())
        if not include_inactive:
            clauses.append("active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            row = connection.execute(
                "SELECT * FROM weight_measurements"
                + where
                + " ORDER BY occurred_at COLLATE CALORIEK_LOCAL DESC, id DESC LIMIT 1",
                parameters,
            ).fetchone()
        return row_to_dict(row)

    get_latest_weight = latest_measurement
    get_latest_measurement = latest_measurement

    def update_measurement(
        self,
        measurement_id: int,
        *,
        weight_kg: float | None = None,
        occurred_at: datetime | date | str | None = None,
        anchor: str | None = None,
        note: str | None = None,
        update_note: bool = False,
    ) -> None:
        """Correct a measurement while preserving its creation audit timestamp."""

        current = self.get_measurement(measurement_id)
        if current is None:
            raise LookupError(f"weight measurement {measurement_id} does not exist")
        weight_value = (
            float(current["weight_kg"])
            if weight_kg is None
            else _positive_weight(weight_kg)
        )
        anchor_value = (
            str(current["anchor"]) if anchor is None else normalize_anchor(anchor)
        )
        occurred_value, local_date = preserve_or_normalize_event_update(
            str(current["occurred_at"]), str(current["local_date"]), occurred_at,
        )
        note_value = note if update_note or note is not None else current["note"]

        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE weight_measurements
                SET occurred_at = ?, local_date = ?, weight_kg = ?, anchor = ?,
                    note = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    occurred_value,
                    local_date,
                    weight_value,
                    anchor_value,
                    note_value,
                    now_iso(),
                    measurement_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"weight measurement {measurement_id} does not exist")
            self.db.mark_dirty(
                min(str(current["local_date"]), local_date), connection=connection
            )

    update_weight = update_measurement
    update_weight_measurement = update_measurement

    def soft_delete_measurement(self, measurement_id: int) -> None:
        current = self.get_measurement(measurement_id)
        if current is None:
            raise LookupError(f"weight measurement {measurement_id} does not exist")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE weight_measurements SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), measurement_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"weight measurement {measurement_id} does not exist")
            self.db.mark_dirty(str(current["local_date"]), connection=connection)

    delete_weight = soft_delete_measurement
    delete_measurement = soft_delete_measurement
    soft_delete_weight = soft_delete_measurement
    soft_delete_weight_measurement = soft_delete_measurement

    def restore_measurement(self, measurement_id: int) -> None:
        current = self.get_measurement(measurement_id)
        if current is None:
            raise LookupError(f"weight measurement {measurement_id} does not exist")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE weight_measurements SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), measurement_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"weight measurement {measurement_id} does not exist")
            self.db.mark_dirty(str(current["local_date"]), connection=connection)

    restore_weight = restore_measurement
    restore_weight_measurement = restore_measurement


__all__ = ["ANCHORS", "WeightService", "normalize_anchor"]
