"""Persist an intentional source snapshot and its invalidation atomically."""

from collections.abc import Mapping
from datetime import date, datetime
import sqlite3

from app.db.database import Database, normalize_datetime, now_iso
from app.nutrition import NUTRIENT_NAMES, NutritionContribution


def save_source_snapshot(
    database: Database,
    connection: sqlite3.Connection,
    *,
    source_type: str,
    source_id: int,
    name: str,
    amount: float,
    unit: str,
    nutrients: Mapping[str, float],
    composition: NutritionContribution,
    occurred_at: datetime | date | str | None,
    meal_type: str,
    note: str | None,
    replace_event_id: int | None = None,
) -> int:
    occurred_value, local_date = normalize_datetime(occurred_at)
    values = dict(
        occurred_at=occurred_value, local_date=local_date, source_type=source_type,
        source_id=source_id, meal_type=meal_type, name_snapshot=name, amount=amount,
        unit=unit, kj_snapshot=nutrients["kj"], protein_snapshot=nutrients["protein"],
        fat_snapshot=nutrients["fat"], carb_snapshot=nutrients["carb"],
        fiber_snapshot=nutrients["fiber"], note=note, updated_at=now_iso(),
        **dict(zip(NUTRIENT_NAMES, composition.values.as_tuple(), strict=True)),
        nutrition_complete=int(composition.complete),
    )
    dirty_date = local_date
    if replace_event_id is None:
        values.update(created_at=values["updated_at"], active=1)
        cursor = connection.execute(
            f"INSERT INTO intake_events ({', '.join(values)}) "
            f"VALUES ({', '.join('?' for _ in values)})", tuple(values.values()),
        )
        event_id = int(cursor.lastrowid)
    else:
        old = connection.execute(
            "SELECT local_date FROM intake_events WHERE id = ? AND active = 1",
            (replace_event_id,),
        ).fetchone()
        if old is None:
            raise LookupError(f"active intake event {replace_event_id} does not exist")
        connection.execute(
            f"UPDATE intake_events SET {', '.join(f'{key} = ?' for key in values)} WHERE id = ?",
            (*values.values(), replace_event_id),
        )
        event_id = replace_event_id
        dirty_date = min(dirty_date, old["local_date"])
    database.mark_dirty(dirty_date, connection=connection)
    return event_id
