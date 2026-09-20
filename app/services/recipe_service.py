"""User recipes, current nutrition totals, and immutable intake snapshots."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from typing import Any

from app.db.database import Database, now_iso
from app.nutrition import aggregate_contributions, food_contribution
from app.services.food_service import normalize_meal_type
from app.services.intake_snapshot import save_source_snapshot


class RecipeService:
    def __init__(self, database: Database) -> None:
        self.db = database

    @staticmethod
    def _coerce_item(item: Mapping[str, Any] | tuple[Any, ...]) -> tuple[int, float, str | None]:
        if isinstance(item, Mapping):
            return int(item["food_id"]), float(item["amount"]), item.get("unit")
        if len(item) == 2:
            return int(item[0]), float(item[1]), None
        if len(item) == 3:
            return int(item[0]), float(item[1]), str(item[2])
        raise ValueError("recipe item must contain food_id, amount, and optional unit")

    def _validated_items(
        self,
        connection: sqlite3.Connection,
        items: Iterable[Mapping[str, Any] | tuple[Any, ...]],
    ) -> list[tuple[int, float, str]]:
        result: list[tuple[int, float, str]] = []
        for raw_item in items:
            food_id, amount, requested_unit = self._coerce_item(raw_item)
            if not math.isfinite(amount) or amount <= 0:
                raise ValueError("recipe item amount must be a finite positive number")
            food = connection.execute(
                "SELECT id, basis_unit FROM foods WHERE id = ? AND active = 1",
                (food_id,),
            ).fetchone()
            if food is None:
                raise LookupError(f"active food {food_id} does not exist")
            unit = (requested_unit or food["basis_unit"]).lower()
            if unit != food["basis_unit"]:
                raise ValueError(
                    f"food {food_id} is measured in {food['basis_unit']}, not {unit}"
                )
            result.append((food_id, amount, unit))
        if not result:
            raise ValueError("recipe must contain at least one item")
        return result

    def create_recipe(
        self,
        *,
        name: str,
        items: Iterable[Mapping[str, Any] | tuple[Any, ...]],
        description: str | None = None,
        is_favorite: bool = False,
    ) -> int:
        name_value = name.strip()
        if not name_value:
            raise ValueError("recipe name must not be empty")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            normalized_items = self._validated_items(connection, items)
            cursor = connection.execute(
                "INSERT INTO recipes(name, description, is_favorite, created_at, updated_at, active) "
                "VALUES (?, ?, ?, ?, ?, 1)",
                (name_value, description, int(is_favorite), timestamp, timestamp),
            )
            recipe_id = int(cursor.lastrowid)
            connection.executemany(
                """
                INSERT INTO recipe_items(
                    recipe_id, food_id, amount, unit, created_at, updated_at, active
                ) VALUES (?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    (recipe_id, food_id, amount, unit, timestamp, timestamp)
                    for food_id, amount, unit in normalized_items
                ),
            )
            return recipe_id

    def get_recipe(self, recipe_id: int, *, include_inactive: bool = True) -> dict[str, Any] | None:
        sql = "SELECT * FROM recipes WHERE id = ?"
        if not include_inactive:
            sql += " AND active = 1"
        with self.db.connection() as connection:
            row = connection.execute(sql, (recipe_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            item_rows = connection.execute(
                """
                SELECT ri.*, f.name AS food_name, f.basis_amount, f.basis_unit,
                       f.kj, f.protein_g, f.fat_g, f.carb_g, f.fiber_g
                FROM recipe_items ri JOIN foods f ON f.id = ri.food_id
                WHERE ri.recipe_id = ? AND ri.active = 1 ORDER BY ri.id
                """,
                (recipe_id,),
            ).fetchall()
        result["items"] = [dict(item) for item in item_rows]
        return result

    def list_recipes(
        self,
        *,
        include_inactive: bool = False,
        favorites_only: bool = False,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if not include_inactive:
            clauses.append("active = 1")
        if favorites_only:
            clauses.append("is_favorite = 1")
        if query:
            clauses.append("name LIKE ?")
            parameters.append(f"%{query.strip()}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM recipes" + where + " ORDER BY is_favorite DESC, name, id",
                parameters,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_recent_recipes(self, *, limit: int = 10) -> list[dict[str, Any]]:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        if limit == 0:
            return []
        with self.db.connection() as connection:
            rows = connection.execute(
                """
                SELECT r.*, MAX(e.occurred_at) AS last_used_at
                FROM recipes r
                JOIN intake_events e
                  ON e.source_type = 'RECIPE' AND e.source_id = r.id AND e.active = 1
                WHERE r.active = 1
                GROUP BY r.id
                ORDER BY last_used_at DESC, r.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_recipe(
        self,
        recipe_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
        update_description: bool = False,
        is_favorite: bool | None = None,
        items: Iterable[Mapping[str, Any] | tuple[Any, ...]] | None = None,
    ) -> None:
        current = self.get_recipe(recipe_id)
        if current is None:
            raise LookupError(f"recipe {recipe_id} does not exist")
        name_value = str(name if name is not None else current["name"]).strip()
        if not name_value:
            raise ValueError("recipe name must not be empty")
        timestamp = now_iso()
        with self.db.transaction() as connection:
            normalized_items = self._validated_items(connection, items) if items is not None else None
            connection.execute(
                "UPDATE recipes SET name = ?, description = ?, is_favorite = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    name_value,
                    (
                        description
                        if update_description or description is not None
                        else current["description"]
                    ),
                    int(is_favorite) if is_favorite is not None else current["is_favorite"],
                    timestamp,
                    recipe_id,
                ),
            )
            if normalized_items is not None:
                # Keep removed components for auditability while excluding them from
                # current recipe calculations.
                connection.execute(
                    "UPDATE recipe_items SET active = 0, updated_at = ? "
                    "WHERE recipe_id = ? AND active = 1",
                    (timestamp, recipe_id),
                )
                connection.executemany(
                    """
                    INSERT INTO recipe_items(
                        recipe_id, food_id, amount, unit, created_at, updated_at, active
                    ) VALUES (?, ?, ?, ?, ?, ?, 1)
                    """,
                    (
                        (recipe_id, food_id, amount, unit, timestamp, timestamp)
                        for food_id, amount, unit in normalized_items
                    ),
                )

    replace_recipe_items = update_recipe

    @staticmethod
    def _finalize_totals(
        totals: dict[str, Any], *, total_weight_g: float, total_volume_ml: float
    ) -> dict[str, Any]:
        """Attach dimensionally honest amount and per-100 reference values."""

        totals["total_weight_g"] = total_weight_g
        totals["total_volume_ml"] = total_volume_ml
        if total_weight_g > 0 and total_volume_ml == 0:
            normalization_amount = total_weight_g
            totals["normalization_unit"] = "g"
        elif total_volume_ml > 0 and total_weight_g == 0:
            normalization_amount = total_volume_ml
            totals["normalization_unit"] = "ml"
        else:
            # A density conversion is unknown, so g and ml must not be added and
            # presented as a weight or used for a per-100 normalization.
            normalization_amount = 0.0
            totals["normalization_unit"] = None
        totals["total_amount"] = (
            normalization_amount
            if normalization_amount > 0
            else total_weight_g + total_volume_ml
        )
        if normalization_amount > 0:
            per_factor = 100.0 / normalization_amount
            totals.update(
                {
                    "per_100g_kj": totals["kj"] * per_factor,
                    "per_100g_protein_g": totals["protein_g"] * per_factor,
                    "per_100g_fat_g": totals["fat_g"] * per_factor,
                    "per_100g_carb_g": totals["carb_g"] * per_factor,
                    "per_100g_fiber_g": totals["fiber_g"] * per_factor,
                }
            )
        else:
            totals.update(
                {
                    "per_100g_kj": 0.0,
                    "per_100g_protein_g": 0.0,
                    "per_100g_fat_g": 0.0,
                    "per_100g_carb_g": 0.0,
                    "per_100g_fiber_g": 0.0,
                }
            )
        return totals

    def soft_delete_recipe(self, recipe_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE recipes SET active = 0, updated_at = ? WHERE id = ?",
                (now_iso(), recipe_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"recipe {recipe_id} does not exist")

    def restore_recipe(self, recipe_id: int) -> None:
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE recipes SET active = 1, updated_at = ? WHERE id = ?",
                (now_iso(), recipe_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"recipe {recipe_id} does not exist")

    @staticmethod
    def _calculate_with_connection(
        connection: sqlite3.Connection, recipe_id: int
    ) -> dict[str, Any]:
        recipe = connection.execute(
            "SELECT id FROM recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if recipe is None:
            raise LookupError(f"recipe {recipe_id} does not exist")
        rows = connection.execute(
            """
            SELECT ri.amount, ri.unit, f.basis_amount, f.basis_unit,
                   f.kj, f.protein_g, f.fat_g, f.carb_g, f.fiber_g,
                   f.protein_g_per_100g, f.fiber_g_per_100g,
                   f.fat_g_per_100g, f.carbs_g_per_100g,
                   f.created_at, f.is_builtin, f.builtin_key,
                   (SELECT applied_at FROM schema_version WHERE version = 3) AS v3_applied_at
            FROM recipe_items ri JOIN foods f ON f.id = ri.food_id
            WHERE ri.recipe_id = ? AND ri.active = 1
            ORDER BY ri.id
            """,
            (recipe_id,),
        ).fetchall()
        if not rows:
            raise ValueError(f"recipe {recipe_id} has no active items")
        totals: dict[str, Any] = {
            "kj": 0.0,
            "protein_g": 0.0,
            "fat_g": 0.0,
            "carb_g": 0.0,
            "fiber_g": 0.0,
        }
        total_weight_g = 0.0
        total_volume_ml = 0.0
        for row in rows:
            factor = float(row["amount"]) / float(row["basis_amount"])
            if row["unit"] == "g":
                total_weight_g += float(row["amount"])
            else:
                total_volume_ml += float(row["amount"])
            totals["kj"] += float(row["kj"]) * factor
            totals["protein_g"] += float(row["protein_g"]) * factor
            totals["fat_g"] += float(row["fat_g"]) * factor
            totals["carb_g"] += float(row["carb_g"]) * factor
            totals["fiber_g"] += float(row["fiber_g"]) * factor
        totals["composition"] = aggregate_contributions(
            food_contribution(dict(row), float(row["amount"])) for row in rows
        )
        return RecipeService._finalize_totals(
            totals,
            total_weight_g=total_weight_g,
            total_volume_ml=total_volume_ml,
        )

    def calculate_nutrition(self, recipe_id: int) -> dict[str, Any]:
        with self.db.connection() as connection:
            return self._calculate_with_connection(connection, recipe_id)

    calculate_recipe = calculate_nutrition

    def calculate_items(
        self, items: Iterable[Mapping[str, Any] | tuple[Any, ...]]
    ) -> dict[str, Any]:
        """Preview totals for an unsaved recipe using current food values."""

        with self.db.connection() as connection:
            normalized_items = self._validated_items(connection, items)
            totals: dict[str, Any] = {
                "kj": 0.0,
                "protein_g": 0.0,
                "fat_g": 0.0,
                "carb_g": 0.0,
                "fiber_g": 0.0,
            }
            total_weight_g = 0.0
            total_volume_ml = 0.0
            contributions = []
            for food_id, amount, unit in normalized_items:
                food = connection.execute(
                    """
                    SELECT basis_amount, basis_unit, kj, protein_g, fat_g, carb_g, fiber_g,
                           protein_g_per_100g, fiber_g_per_100g, fat_g_per_100g, carbs_g_per_100g,
                           created_at, is_builtin, builtin_key,
                           (SELECT applied_at FROM schema_version WHERE version = 3) AS v3_applied_at
                    FROM foods WHERE id = ? AND active = 1
                    """,
                    (food_id,),
                ).fetchone()
                if food is None:  # Defensive against a concurrent catalogue edit.
                    raise LookupError(f"active food {food_id} does not exist")
                factor = amount / float(food["basis_amount"])
                if unit == "g":
                    total_weight_g += amount
                else:
                    total_volume_ml += amount
                totals["kj"] += float(food["kj"]) * factor
                totals["protein_g"] += float(food["protein_g"]) * factor
                totals["fat_g"] += float(food["fat_g"]) * factor
                totals["carb_g"] += float(food["carb_g"]) * factor
                totals["fiber_g"] += float(food["fiber_g"]) * factor
                contributions.append(food_contribution(dict(food), amount))
            totals["composition"] = aggregate_contributions(contributions)
        return self._finalize_totals(
            totals,
            total_weight_g=total_weight_g,
            total_volume_ml=total_volume_ml,
        )

    preview_recipe = calculate_items

    def record_recipe_intake(
        self,
        recipe_id: int,
        *,
        amount: float | None = None,
        amount_g: float | None = None,
        fraction: float | None = None,
        unit: str | None = None,
        occurred_at: datetime | date | str | None = None,
        meal_type: str = "OTHER",
        note: str | None = None,
        replace_event_id: int | None = None,
    ) -> int:
        supplied_amount = amount_g if amount_g is not None else amount
        supplied_unit = "g" if amount_g is not None else unit
        if supplied_amount is not None and fraction is not None:
            raise ValueError("specify consumed grams or recipe fraction, not both")
        with self.db.transaction() as connection:
            recipe_row = connection.execute(
                "SELECT * FROM recipes WHERE id = ? AND active = 1", (recipe_id,)
            ).fetchone()
            if recipe_row is None:
                raise LookupError(f"active recipe {recipe_id} does not exist")
            totals = self._calculate_with_connection(connection, recipe_id)
            recipe_name = str(recipe_row["name"])

            if supplied_amount is None and fraction is None:
                fraction = 1.0
            if fraction is not None:
                ratio = float(fraction)
                event_amount = ratio
                event_unit = "recipe"
            else:
                event_amount = float(supplied_amount)
                expected_unit = totals["normalization_unit"]
                if expected_unit is None:
                    raise ValueError("mixed g/ml recipes can only be recorded as a recipe fraction")
                event_unit = (supplied_unit or expected_unit).lower()
                if event_unit != expected_unit:
                    raise ValueError(f"recipe is measured in {expected_unit}, not {event_unit}")
                ratio = event_amount / totals["total_amount"]
            if not math.isfinite(ratio) or not math.isfinite(event_amount) or ratio <= 0 or event_amount <= 0:
                raise ValueError("consumed amount or fraction must be finite and positive")
            return save_source_snapshot(
                self.db, connection, source_type="RECIPE", source_id=recipe_id,
                name=recipe_name, amount=event_amount, unit=event_unit,
                nutrients=dict(kj=totals["kj"] * ratio, protein=totals["protein_g"] * ratio,
                               fat=totals["fat_g"] * ratio, carb=totals["carb_g"] * ratio,
                               fiber=totals["fiber_g"] * ratio),
                composition=totals["composition"].scaled(ratio), occurred_at=occurred_at,
                meal_type=normalize_meal_type(meal_type), note=note,
                replace_event_id=replace_event_id,
            )

    record_intake = record_recipe_intake
