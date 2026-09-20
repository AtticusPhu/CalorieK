"""Food catalogue and immutable-nutrition intake snapshots."""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from app.db.database import Database, normalize_datetime, now_iso, row_to_dict
from app.nutrition import (
    FOOD_NUTRIENT_FIELDS, FOOD_PROVENANCE_SQL, NutrientValues, NutritionContribution, food_contribution, optional_nutrient,
)
from app.services.intake_snapshot import save_source_snapshot


MEAL_TYPES = frozenset({"BREAKFAST", "LUNCH", "DINNER", "SNACK", "OTHER"})


def _finite_number(value: float, label: str, *, positive: bool = False) -> float:
    normalized = float(value)
    if not math.isfinite(normalized) or (normalized <= 0 if positive else normalized < 0):
        condition = "positive" if positive else "non-negative"
        raise ValueError(f"{label} must be a finite {condition} number")
    return normalized


def normalize_meal_type(value: str | None) -> str:
    if value is None:
        return "OTHER"
    normalized = value.strip().upper()
    aliases = {
        "早餐": "BREAKFAST",
        "午餐": "LUNCH",
        "晚餐": "DINNER",
        "零食": "SNACK",
        "其他": "OTHER",
        "其它": "OTHER",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in MEAL_TYPES:
        raise ValueError(f"invalid meal type: {value!r}")
    return normalized


class FoodService:
    def __init__(self, database: Database) -> None:
        self.db = database

    def ensure_default_servings(self) -> int:
        """Backfill named servings for databases created before the UI exposed them."""

        timestamp = now_iso()
        with self.db.transaction() as connection:
            before = connection.total_changes
            connection.execute(
                """
                INSERT OR IGNORE INTO food_servings(
                    food_id, name, serving_amount, serving_unit, base_amount,
                    created_at, updated_at, active
                )
                SELECT id, '默认份', 1, 'serving', default_serving, ?, ?, 1
                FROM foods
                WHERE default_serving IS NOT NULL
                """,
                (timestamp, timestamp),
            )
            return connection.total_changes - before

    def create_food(
        self,
        *,
        name: str,
        category: str,
        basis_unit: str,
        kj: float,
        protein_g: float = 0,
        fat_g: float = 0,
        carb_g: float = 0,
        fiber_g: float = 0,
        basis_amount: float = 100,
        brand: str | None = None,
        default_serving: float | None = None,
        is_favorite: bool = False,
        data_source: str = "user",
        source_version: str = "user-v1",
        protein_g_per_100g: float | None = None,
        fiber_g_per_100g: float | None = None,
        fat_g_per_100g: float | None = None,
        carbs_g_per_100g: float | None = None,
    ) -> int:
        name_value = name.strip()
        category_value = category.strip()
        unit_value = basis_unit.strip().lower()
        if not name_value or not category_value:
            raise ValueError("food name and category must not be empty")
        if unit_value not in {"g", "ml"}:
            raise ValueError("basis_unit must be 'g' or 'ml'")
        basis_value = _finite_number(basis_amount, "basis_amount", positive=True)
        nutrients = tuple(
            _finite_number(value, label)
            for value, label in zip(
                (kj, protein_g, fat_g, carb_g, fiber_g),
                ("kj", "protein_g", "fat_g", "carb_g", "fiber_g"),
                strict=True,
            )
        )
        serving_value = (
            _finite_number(default_serving, "default_serving", positive=True)
            if default_serving is not None
            else basis_value
        )
        composition = NutrientValues(protein_g_per_100g, fiber_g_per_100g, fat_g_per_100g, carbs_g_per_100g)
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO foods(
                    builtin_key, name, category, brand, basis_amount, basis_unit,
                    kj, protein_g, fat_g, carb_g, fiber_g, default_serving,
                    is_builtin, is_favorite, user_modified, data_source,
                    source_version, created_at, updated_at, active,
                    protein_g_per_100g, fiber_g_per_100g, fat_g_per_100g, carbs_g_per_100g
                ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 1, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    name_value,
                    category_value,
                    brand.strip() if brand else None,
                    basis_value,
                    unit_value,
                    *nutrients,
                    serving_value,
                    int(is_favorite),
                    data_source,
                    source_version,
                    timestamp,
                    timestamp,
                    *composition.as_tuple(),
                ),
            )
            food_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO food_servings(
                    food_id, name, serving_amount, serving_unit, base_amount,
                    created_at, updated_at, active
                ) VALUES (?, '默认份', 1, 'serving', ?, ?, ?, 1)
                """,
                (food_id, serving_value, timestamp, timestamp),
            )
            return food_id

    def get_food(self, food_id: int, *, include_inactive: bool = True) -> dict[str, Any] | None:
        sql = f"SELECT *, {FOOD_PROVENANCE_SQL} FROM foods WHERE id = ?"
        parameters: tuple[Any, ...] = (food_id,)
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, parameters).fetchone()
        return row_to_dict(row)

    def list_foods(
        self,
        *,
        include_inactive: bool = False,
        query: str | None = None,
        favorites_only: bool = False,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_inactive:
            clauses.append("active = 1")
        if query:
            clauses.append("(name LIKE ? OR COALESCE(brand, '') LIKE ?)")
            pattern = f"%{query.strip()}%"
            parameters.extend((pattern, pattern))
        if favorites_only:
            clauses.append("is_favorite = 1")
        if category:
            clauses.append("category = ?")
            parameters.append(category)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                f"SELECT *, {FOOD_PROVENANCE_SQL} FROM foods" + where + " ORDER BY is_favorite DESC, name, id",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_foods(self, *, limit: int = 10) -> list[dict[str, Any]]:
        """Return active foods ordered by their latest active intake event."""

        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        with self.db.connection() as connection:
            rows = connection.execute(
                """
                SELECT f.*, MAX(e.occurred_at) AS last_used_at,
                       (SELECT applied_at FROM schema_version WHERE version = 3) AS v3_applied_at
                FROM foods f
                JOIN intake_events e
                  ON e.source_type = 'FOOD' AND e.source_id = f.id AND e.active = 1
                WHERE f.active = 1
                GROUP BY f.id
                ORDER BY last_used_at DESC, f.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_food(self, food_id: int, **changes: Any) -> None:
        """Update food data without changing its persisted physical dimension."""
        allowed = {
            "name",
            "category",
            "brand",
            "basis_amount",
            "basis_unit",
            "kj",
            "protein_g",
            "fat_g",
            "carb_g",
            "fiber_g",
            "default_serving",
            "is_favorite",
            "data_source",
            "source_version",
        }
        allowed.update(FOOD_NUTRIENT_FIELDS)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported food fields: {sorted(unknown)}")
        if not changes:
            return
        current = self.get_food(food_id)
        if current is None:
            raise LookupError(f"food {food_id} does not exist")
        normalized: dict[str, Any] = dict(changes)
        for name in FOOD_NUTRIENT_FIELDS:
            if name in normalized:
                normalized[name] = optional_nutrient(normalized[name])
        for field in ("name", "category"):
            if field in normalized:
                normalized[field] = str(normalized[field]).strip()
                if not normalized[field]:
                    raise ValueError(f"food {field} must not be empty")
        if "brand" in normalized:
            normalized["brand"] = str(normalized["brand"]).strip() or None
        if "basis_unit" in normalized:
            basis_unit = str(normalized.pop("basis_unit")).lower()
            if basis_unit not in {"g", "ml"}:
                raise ValueError("basis_unit must be 'g' or 'ml'")
            if basis_unit != current["basis_unit"]:
                raise ValueError(
                    "basis_unit cannot be changed for an existing food; "
                    "create a new food to use a different unit"
                )
            # Same-unit submissions are valid, but never write the immutable
            # field. This protects servings and recipe items for every food.
        if "basis_amount" in normalized:
            normalized["basis_amount"] = _finite_number(
                normalized["basis_amount"], "basis_amount", positive=True
            )
        for field in ("kj", "protein_g", "fat_g", "carb_g", "fiber_g"):
            if field in normalized:
                normalized[field] = _finite_number(normalized[field], field)
        if "default_serving" in normalized and normalized["default_serving"] is not None:
            normalized["default_serving"] = _finite_number(
                normalized["default_serving"], "default_serving", positive=True
            )
        if "is_favorite" in normalized:
            normalized["is_favorite"] = int(bool(normalized["is_favorite"]))
        if not normalized:
            return
        assignments = [f"{field} = ?" for field in normalized]
        values = list(normalized.values())
        assignments.extend(("user_modified = 1", "updated_at = ?"))
        values.extend((now_iso(), food_id))
        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE foods SET {', '.join(assignments)} WHERE id = ?", values
            )
            if cursor.rowcount != 1:
                raise LookupError(f"food {food_id} does not exist")
            if "default_serving" in normalized:
                default_value = normalized["default_serving"]
                if default_value is None:
                    connection.execute(
                        """
                        UPDATE food_servings
                        SET active = 0, updated_at = ?
                        WHERE food_id = ? AND name = '默认份'
                        """,
                        (now_iso(), food_id),
                    )
                else:
                    timestamp = now_iso()
                    connection.execute(
                        """
                        INSERT INTO food_servings(
                            food_id, name, serving_amount, serving_unit,
                            base_amount, created_at, updated_at, active
                        ) VALUES (?, '默认份', 1, 'serving', ?, ?, ?, 1)
                        ON CONFLICT(food_id, name) DO UPDATE SET
                            serving_amount = 1,
                            serving_unit = 'serving',
                            base_amount = excluded.base_amount,
                            updated_at = excluded.updated_at,
                            active = 1
                        """,
                        (food_id, default_value, timestamp, timestamp),
                    )

    def soft_delete_food(self, food_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE foods SET active = 0, user_modified = 1, updated_at = ? "
                "WHERE id = ?",
                (now_iso(), food_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"food {food_id} does not exist")

    def restore_food(self, food_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE foods SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), food_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"food {food_id} does not exist")

    def add_serving(
        self,
        food_id: int,
        *,
        name: str,
        base_amount: float,
        serving_amount: float = 1,
        serving_unit: str = "serving",
    ) -> int:
        if self.get_food(food_id, include_inactive=False) is None:
            raise LookupError(f"active food {food_id} does not exist")
        name_value = name.strip()
        unit_value = serving_unit.strip()
        if not name_value or not unit_value:
            raise ValueError("serving name and unit must not be empty")
        base_value = _finite_number(base_amount, "base_amount", positive=True)
        serving_value = _finite_number(
            serving_amount, "serving_amount", positive=True
        )
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO food_servings(
                    food_id, name, serving_amount, serving_unit, base_amount,
                    created_at, updated_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    food_id,
                    name_value,
                    serving_value,
                    unit_value,
                    base_value,
                    timestamp,
                    timestamp,
                ),
            )
            return int(cursor.lastrowid)

    def get_serving(
        self, serving_id: int, *, include_inactive: bool = True
    ) -> dict[str, Any] | None:
        sql = "SELECT * FROM food_servings WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (serving_id,)).fetchone()
        return row_to_dict(row)

    def list_servings(self, food_id: int, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM food_servings WHERE food_id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        sql += " ORDER BY id"
        with self.db.connection() as connection:
            rows = connection.execute(sql, (food_id,)).fetchall()
        return [dict(row) for row in rows]

    def update_serving(self, serving_id: int, **changes: Any) -> None:
        allowed = {"name", "base_amount", "serving_amount", "serving_unit"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"unsupported serving fields: {sorted(unknown)}")
        if not changes:
            return
        if self.get_serving(serving_id) is None:
            raise LookupError(f"serving {serving_id} does not exist")
        normalized = dict(changes)
        if "name" in normalized:
            normalized["name"] = str(normalized["name"]).strip()
            if not normalized["name"]:
                raise ValueError("serving name must not be empty")
        if "serving_unit" in normalized:
            normalized["serving_unit"] = str(normalized["serving_unit"]).strip()
            if not normalized["serving_unit"]:
                raise ValueError("serving_unit must not be empty")
        for field in ("base_amount", "serving_amount"):
            if field in normalized:
                normalized[field] = _finite_number(
                    normalized[field], field, positive=True
                )
        assignments = [f"{field} = ?" for field in normalized]
        values = list(normalized.values())
        assignments.append("updated_at = ?")
        values.extend((now_iso(), serving_id))
        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE food_servings SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise LookupError(f"serving {serving_id} does not exist")

    def soft_delete_serving(self, serving_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE food_servings SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), serving_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"serving {serving_id} does not exist")

    def restore_serving(self, serving_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE food_servings SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), serving_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"serving {serving_id} does not exist")

    @staticmethod
    def _nutrition_for_base_amount(food: dict[str, Any], base_amount: float) -> dict[str, float]:
        factor = float(base_amount) / float(food["basis_amount"])
        return {
            "kj": float(food["kj"]) * factor,
            "protein": float(food["protein_g"]) * factor,
            "fat": float(food["fat_g"]) * factor,
            "carb": float(food["carb_g"]) * factor,
            "fiber": float(food["fiber_g"]) * factor,
        }

    def calculate_nutrition(self, food_id: int, amount: float, *, unit: str | None = None) -> dict[str, float]:
        food = self.get_food(food_id, include_inactive=False)
        if food is None:
            raise LookupError(f"active food {food_id} does not exist")
        amount_value = _finite_number(amount, "amount", positive=True)
        unit_value = (unit or food["basis_unit"]).lower()
        if unit_value != food["basis_unit"]:
            raise ValueError(f"food {food_id} is measured in {food['basis_unit']}, not {unit_value}")
        return self._nutrition_for_base_amount(food, amount_value)

    def record_food_intake(
        self,
        food_id: int,
        *,
        amount: float,
        occurred_at: datetime | date | str | None = None,
        unit: str | None = None,
        serving_id: int | None = None,
        meal_type: str = "OTHER",
        note: str | None = None,
        replace_event_id: int | None = None,
    ) -> int:
        amount_value = _finite_number(amount, "amount", positive=True)
        with self.db.transaction() as connection:
            row = connection.execute(
                f"SELECT *, {FOOD_PROVENANCE_SQL} FROM foods WHERE id = ? AND active = 1",
                (food_id,),
            ).fetchone()
            if row is None:
                raise LookupError(f"active food {food_id} does not exist")
            food = dict(row)
            if serving_id is not None:
                serving_row = connection.execute(
                    "SELECT * FROM food_servings WHERE id = ? AND food_id = ? AND active = 1",
                    (serving_id, food_id),
                ).fetchone()
                if serving_row is None:
                    raise LookupError(f"serving {serving_id} does not exist for food {food_id}")
                base_amount = amount_value * float(serving_row["base_amount"]) / float(
                    serving_row["serving_amount"]
                )
                unit_value = str(serving_row["serving_unit"])
            else:
                unit_value = (unit or food["basis_unit"]).lower()
                if unit_value != food["basis_unit"]:
                    raise ValueError(f"food {food_id} is measured in {food['basis_unit']}, not {unit_value}")
                base_amount = amount_value
            return save_source_snapshot(
                self.db, connection, source_type="FOOD", source_id=food_id,
                name=food["name"], amount=amount_value, unit=unit_value,
                nutrients=self._nutrition_for_base_amount(food, base_amount),
                composition=food_contribution(food, base_amount), occurred_at=occurred_at,
                meal_type=normalize_meal_type(meal_type), note=note,
                replace_event_id=replace_event_id,
            )

    # Concise aliases are useful to UI call sites and tests.
    add_intake_event = record_food_intake
    record_intake = record_food_intake

    def record_custom_intake(
        self,
        *,
        name: str,
        amount: float,
        unit: str,
        kj: float,
        protein_g: float | None = None,
        fat_g: float | None = None,
        carb_g: float | None = None,
        fiber_g: float | None = None,
        occurred_at: datetime | date | str | None = None,
        meal_type: str = "OTHER",
        note: str | None = None,
    ) -> int:
        name_value = name.strip()
        unit_value = unit.strip()
        amount_value = _finite_number(amount, "amount", positive=True)
        values = [
            _finite_number(value, label)
            for value, label in zip(
                (kj, protein_g or 0, fat_g or 0, carb_g or 0, fiber_g or 0),
                ("kj", "protein_g", "fat_g", "carb_g", "fiber_g"),
                strict=True,
            )
        ]
        if not name_value or not unit_value:
            raise ValueError("custom intake name and unit must not be empty")
        composition_values = NutrientValues(protein_g, fiber_g, fat_g, carb_g)
        composition = NutritionContribution(
            composition_values, all(value is not None for value in composition_values.as_tuple()),
        )
        occurred_value, local_date = normalize_datetime(occurred_at)
        timestamp = now_iso()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT INTO intake_events(
                    occurred_at, local_date, source_type, source_id, meal_type,
                    name_snapshot, amount, unit, kj_snapshot,
                    protein_snapshot, fat_snapshot, carb_snapshot, fiber_snapshot,
                    note, created_at, updated_at, active,
                    protein_g, fiber_g, fat_g, carbs_g, nutrition_complete
                ) VALUES (?, ?, 'CUSTOM', NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                """,
                (
                    occurred_value,
                    local_date,
                    normalize_meal_type(meal_type),
                    name_value,
                    amount_value,
                    unit_value,
                    *values,
                    note,
                    timestamp,
                    timestamp,
                    *composition.snapshot_values(),
                ),
            )
            self.db.mark_dirty(local_date, connection=connection)
            return int(cursor.lastrowid)

    def get_intake_event(self, event_id: int, *, include_inactive: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM intake_events WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (event_id,)).fetchone()
        return row_to_dict(row)

    def list_intake_events(
        self,
        *,
        local_date: date | str | None = None,
        include_inactive: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if local_date is not None:
            from app.db.database import normalize_date

            clauses.append("local_date = ?")
            parameters.append(normalize_date(local_date))
        if not include_inactive:
            clauses.append("active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM intake_events" + where + " ORDER BY occurred_at, id",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def update_intake_event(
        self,
        event_id: int,
        *,
        amount: float | None = None,
        occurred_at: datetime | date | str | None = None,
        meal_type: str | None = None,
        note: str | None = None,
        update_note: bool = False,
    ) -> None:
        event = self.get_intake_event(event_id)
        if event is None:
            raise LookupError(f"intake event {event_id} does not exist")
        amount_value = _finite_number(
            amount if amount is not None else event["amount"],
            "amount",
            positive=True,
        )
        scale = amount_value / float(event["amount"])
        composition = NutrientValues(
            event["protein_g"], event["fiber_g"], event["fat_g"], event["carbs_g"],
        ).scaled(scale)
        if occurred_at is None:
            occurred_value, local_date = event["occurred_at"], event["local_date"]
        else:
            occurred_value, local_date = normalize_datetime(occurred_at)
        meal_value = normalize_meal_type(meal_type or event["meal_type"])
        timestamp = now_iso()
        with self.db.transaction() as connection:
            connection.execute(
                """
                UPDATE intake_events SET occurred_at = ?, local_date = ?, meal_type = ?,
                    amount = ?, kj_snapshot = ?, protein_snapshot = ?,
                    fat_snapshot = ?, carb_snapshot = ?, fiber_snapshot = ?,
                    note = ?, updated_at = ?,
                    protein_g = ?, fiber_g = ?, fat_g = ?, carbs_g = ? WHERE id = ?
                """,
                (
                    occurred_value,
                    local_date,
                    meal_value,
                    amount_value,
                    float(event["kj_snapshot"]) * scale,
                    float(event["protein_snapshot"]) * scale,
                    float(event["fat_snapshot"]) * scale,
                    float(event["carb_snapshot"]) * scale,
                    float(event["fiber_snapshot"]) * scale,
                    note if update_note or note is not None else event["note"],
                    timestamp,
                    *composition.as_tuple(),
                    event_id,
                ),
            )
            self.db.mark_dirty(min(event["local_date"], local_date), connection=connection)

    def soft_delete_intake_event(self, event_id: int) -> None:
        event = self.get_intake_event(event_id)
        if event is None:
            raise LookupError(f"intake event {event_id} does not exist")
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE intake_events SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), event_id),
            )
            self.db.mark_dirty(event["local_date"], connection=connection)

    def restore_intake_event(self, event_id: int) -> None:
        event = self.get_intake_event(event_id)
        if event is None:
            raise LookupError(f"intake event {event_id} does not exist")
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE intake_events SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), event_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"intake event {event_id} does not exist")
            self.db.mark_dirty(event["local_date"], connection=connection)
