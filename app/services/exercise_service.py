"""Exercise shortcut catalogue and active-calorie event snapshots."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from app.db.database import Database, normalize_date, normalize_datetime, now_iso, row_to_dict


def _non_negative(value: float, label: str) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return normalized


class ExerciseService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def create_exercise_type(
        self,
        *,
        name: str,
        default_duration_min: float | None = None,
        default_active_kcal: float | None = None,
        favorite: bool = False,
    ) -> int:
        if not name.strip():
            raise ValueError("exercise name must not be empty")
        duration_value = (
            _non_negative(default_duration_min, "default_duration_min")
            if default_duration_min is not None
            else None
        )
        kcal_value = (
            _non_negative(default_active_kcal, "default_active_kcal")
            if default_active_kcal is not None
            else None
        )
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO exercise_types(
                    name, default_duration_min, default_active_kcal, favorite,
                    created_at, updated_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    name.strip(),
                    duration_value,
                    kcal_value,
                    int(favorite),
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def get_exercise_type(
        self, exercise_type_id: int, *, include_inactive: bool = True
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM exercise_types WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (exercise_type_id,)).fetchone()
        return row_to_dict(row)

    def list_exercise_types(
        self, *, include_inactive: bool = False, favorites_only: bool = False
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if not include_inactive:
            clauses.append("active = 1")
        if favorites_only:
            clauses.append("favorite = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM exercise_types" + where + " ORDER BY favorite DESC, name, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_exercise_types(self, *, limit: int = 10) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self.db.connection() as connection:
            rows = connection.execute(
                """
                SELECT t.*, MAX(e.occurred_at) AS last_used_at
                FROM exercise_types t
                JOIN exercise_events e ON e.exercise_type_id = t.id AND e.active = 1
                WHERE t.active = 1
                GROUP BY t.id
                ORDER BY last_used_at DESC, t.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_exercise_type(self, exercise_type_id: int, **changes: Any) -> None:
        allowed = {"name", "default_duration_min", "default_active_kcal", "favorite"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported exercise fields: {sorted(unknown)}")
        if not changes:
            return
        if "name" in changes and not str(changes["name"]).strip():
            raise ValueError("exercise name must not be empty")
        for field in ("default_duration_min", "default_active_kcal"):
            if field in changes and changes[field] is not None and float(changes[field]) < 0:
                raise ValueError(f"{field} must be non-negative")
        normalized = dict(changes)
        if "name" in normalized:
            normalized["name"] = str(normalized["name"]).strip()
        for field in ("default_duration_min", "default_active_kcal"):
            if field in normalized and normalized[field] is not None:
                normalized[field] = _non_negative(normalized[field], field)
        if "favorite" in normalized:
            normalized["favorite"] = int(bool(normalized["favorite"]))
        assignments = [f"{field} = ?" for field in normalized]
        values = list(normalized.values())
        assignments.append("updated_at = ?")
        values.extend((now_iso(), exercise_type_id))
        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE exercise_types SET {', '.join(assignments)} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise LookupError(f"exercise type {exercise_type_id} does not exist")

    def soft_delete_exercise_type(self, exercise_type_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE exercise_types SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), exercise_type_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"exercise type {exercise_type_id} does not exist")

    def restore_exercise_type(self, exercise_type_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE exercise_types SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), exercise_type_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"exercise type {exercise_type_id} does not exist")

    def get_last_values(self, exercise_type_id: int) -> dict[str, float | None]:
        exercise_type = self.get_exercise_type(exercise_type_id)
        if exercise_type is None:
            raise LookupError(f"exercise type {exercise_type_id} does not exist")
        with self.db.connection() as connection:
            row = connection.execute(
                """
                SELECT duration_min, active_kcal FROM exercise_events
                WHERE exercise_type_id = ? AND active = 1
                ORDER BY occurred_at DESC, id DESC LIMIT 1
                """,
                (exercise_type_id,),
            ).fetchone()
        if row is not None:
            return {
                "duration_min": float(row["duration_min"]),
                "active_kcal": float(row["active_kcal"]),
            }
        return {
            "duration_min": (
                None
                if exercise_type["default_duration_min"] is None
                else float(exercise_type["default_duration_min"])
            ),
            "active_kcal": (
                None
                if exercise_type["default_active_kcal"] is None
                else float(exercise_type["default_active_kcal"])
            ),
        }

    def record_exercise(
        self,
        exercise_type_id: int,
        *,
        duration_min: float | None = None,
        active_kcal: float | None = None,
        occurred_at: datetime | date | str | None = None,
        note: str | None = None,
    ) -> int:
        exercise_type = self.get_exercise_type(exercise_type_id, include_inactive=False)
        if exercise_type is None:
            raise LookupError(f"active exercise type {exercise_type_id} does not exist")
        carried = self.get_last_values(exercise_type_id)
        duration_value = carried["duration_min"] if duration_min is None else duration_min
        kcal_value = carried["active_kcal"] if active_kcal is None else active_kcal
        if duration_value is None or kcal_value is None:
            raise ValueError("duration and active kcal need values or defaults")
        duration_value = _non_negative(duration_value, "duration_min")
        kcal_value = _non_negative(kcal_value, "active_kcal")
        occurred_value, local_date = normalize_datetime(occurred_at)
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO exercise_events(
                    occurred_at, local_date, exercise_type_id, name_snapshot,
                    duration_min, active_kcal, note, created_at, updated_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    occurred_value,
                    local_date,
                    exercise_type_id,
                    exercise_type["name"],
                    duration_value,
                    kcal_value,
                    note,
                    timestamp,
                    timestamp,
                ),
            )
            self.db.mark_dirty(local_date, connection=connection)
            return int(cursor.lastrowid)

    add_exercise_event = record_exercise

    def get_exercise_event(self, event_id: int, *, include_inactive: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM exercise_events WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (event_id,)).fetchone()
        return row_to_dict(row)

    def list_exercise_events(
        self,
        *,
        local_date: date | str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if local_date is not None:
            clauses.append("local_date = ?")
            parameters.append(normalize_date(local_date))
        if not include_inactive:
            clauses.append("active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM exercise_events" + where + " ORDER BY occurred_at, id",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_exercise_event(
        self,
        event_id: int,
        *,
        exercise_type_id: int | None = None,
        duration_min: float | None = None,
        active_kcal: float | None = None,
        occurred_at: datetime | date | str | None = None,
        note: str | None = None,
        update_note: bool = False,
    ) -> None:
        event = self.get_exercise_event(event_id)
        if event is None:
            raise LookupError(f"exercise event {event_id} does not exist")
        if exercise_type_id is None:
            # A source can be soft deleted without invalidating or freezing its
            # historical facts. Preserve the old snapshot for ordinary corrections.
            type_id = event["exercise_type_id"]
            name_snapshot = event["name_snapshot"]
        else:
            type_id = exercise_type_id
            exercise_type = self.get_exercise_type(type_id, include_inactive=False)
            if exercise_type is None:
                raise LookupError(f"active exercise type {type_id} does not exist")
            name_snapshot = exercise_type["name"]
        duration_value = _non_negative(
            duration_min if duration_min is not None else event["duration_min"],
            "duration_min",
        )
        kcal_value = _non_negative(
            active_kcal if active_kcal is not None else event["active_kcal"],
            "active_kcal",
        )
        if occurred_at is None:
            occurred_value, local_date = event["occurred_at"], event["local_date"]
        else:
            occurred_value, local_date = normalize_datetime(occurred_at)
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE exercise_events SET occurred_at = ?, local_date = ?,
                    exercise_type_id = ?, name_snapshot = ?, duration_min = ?,
                    active_kcal = ?, note = ?, updated_at = ? WHERE id = ?
                """,
                (
                    occurred_value,
                    local_date,
                    type_id,
                    name_snapshot,
                    duration_value,
                    kcal_value,
                    note if update_note or note is not None else event["note"],
                    now_iso(),
                    event_id,
                ),
            )
            self.db.mark_dirty(min(event["local_date"], local_date), connection=connection)

    def soft_delete_exercise_event(self, event_id: int) -> None:
        event = self.get_exercise_event(event_id)
        if event is None:
            raise LookupError(f"exercise event {event_id} does not exist")
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE exercise_events SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), event_id),
            )
            self.db.mark_dirty(event["local_date"], connection=connection)

    def restore_exercise_event(self, event_id: int) -> None:
        event = self.get_exercise_event(event_id)
        if event is None:
            raise LookupError(f"exercise event {event_id} does not exist")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE exercise_events SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), event_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"exercise event {event_id} does not exist")
            self.db.mark_dirty(event["local_date"], connection=connection)
