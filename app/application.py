"""Composition root and UI-facing adapter for the local desktop application."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime, time, timedelta
from math import isfinite
import os
from pathlib import Path
import sqlite3
from typing import Any, Sequence

from app.color_modes import candle_palette, treemap_palette
from app.db.database import Database
from app.energy_units import DEFAULT_KJ_PER_KG, EnergyUnit, format_energy, kcal_to_kj, normalize_energy_unit
from app.nutrition import DailyNutrition, NutritionContribution, aggregate_contributions, food_contribution, legacy_food_available
from app.paths import database_path, ensure_data_dir, save_data_dir_preference
from app.services.backup_service import BackupService
from app.services.daily_metrics_service import DailyMetricsService, parse_local_datetime
from app.services.exercise_service import ExerciseService
from app.services.food_service import FoodService
from app.services.nutrition_service import NutritionService
from app.services.profile_service import ProfileService
from app.services.recipe_service import RecipeService
from app.services.treemap_service import TreemapDataService
from app.ui.context import (
    CandleDTO,
    DashboardDTO,
    DailyRecordDTO,
    IntakeEditDraft,
    ExerciseEditDraft,
    ExerciseDraft,
    ExerciseSection,
    ExerciseTypeDTO,
    ExerciseTypeDraft,
    FoodDTO,
    FoodDraft,
    FoodServingDTO,
    IntakeDraft,
    IntakeSection,
    IntakeSourceDTO,
    NutritionDTO,
    ProfileDraft,
    RecipeDetailDTO,
    RecipeDraft,
    RecipeItemDTO,
    RecipeSummaryDTO,
    SettingsDTO,
    SettingsDraft,
    TreemapItemDTO,
    WeightDraft,
)


DEFAULT_EXERCISES: tuple[tuple[str, float, float], ...] = (
    ("跑步", 30.0, kcal_to_kj(300.0)),
    ("骑行", 45.0, kcal_to_kj(300.0)),
    ("快走", 45.0, kcal_to_kj(180.0)),
    ("力量训练", 45.0, kcal_to_kj(220.0)),
)


class ApplicationContext:
    """Concrete implementation of the presentation-layer ``UIContext``."""

    def __init__(self, data_dir: str | Path | None = None) -> None:
        self._bind_data_dir(ensure_data_dir(data_dir))
        if self.database.initial_schema_version in (0, 1):
            self._seed_exercise_shortcuts()

    def _bind_data_dir(self, data_dir: str | Path) -> None:
        self.data_dir = ensure_data_dir(data_dir)
        self.database = Database(database_path(self.data_dir))
        self.database.initialize()
        self.profile = ProfileService(self.database)
        self.foods = FoodService(self.database)
        if self.database.initial_schema_version in (0, 1):
            self.foods.ensure_default_servings()
        self.recipes = RecipeService(self.database)
        self.nutrition = NutritionService(self.database)
        self.exercises = ExerciseService(self.database)
        self.daily = DailyMetricsService(self.database)
        self.treemap = TreemapDataService(self.database)
        self.backups = BackupService(self.database.path)

    def _seed_exercise_shortcuts(self) -> None:
        if self.exercises.list_exercise_types(include_inactive=True):
            return
        for name, duration, kj in DEFAULT_EXERCISES:
            self.exercises.create_exercise_type(
                name=name,
                default_duration_min=duration,
                default_active_kj=kj,
            )

    def has_profile(self) -> bool:
        return self.profile.is_initialized()

    def save_initial_profile(self, profile: ProfileDraft) -> None:
        self.profile.create_profile(
            gender=profile.sex,
            birth_date=profile.birth_date,
            height_cm=profile.height_cm,
            current_weight_kg=profile.current_weight_kg,
            wake_time=profile.wake_time.isoformat(timespec="minutes"),
            sleep_time=profile.sleep_time.isoformat(timespec="minutes"),
            awake_multiplier=profile.awake_multiplier,
            sleep_multiplier=profile.sleep_multiplier,
            note=profile.note,
            measured_at=datetime.now(),
        )
        self.daily.ensure_calculated(date.today())

    @staticmethod
    def _food_dto(row: dict) -> FoodDTO:
        return FoodDTO(
            food_id=int(row["id"]),
            name=str(row["name"]),
            category=str(row["category"]),
            brand=str(row.get("brand") or ""),
            basis_amount=float(row["basis_amount"]),
            basis_unit=str(row["basis_unit"]),  # type: ignore[arg-type]
            kj=float(row["kj"]),
            protein_g=float(row["protein_g"]),
            fat_g=float(row["fat_g"]),
            carb_g=float(row["carb_g"]),
            fiber_g=float(row["fiber_g"]),
            default_serving=float(row.get("default_serving") or row["basis_amount"]),
            is_builtin=bool(row["is_builtin"]),
            is_favorite=bool(row["is_favorite"]),
            user_modified=bool(row["user_modified"]),
            data_source=str(row.get("data_source") or ""),
            source_version=str(row.get("source_version") or ""),
            active=bool(row["active"]),
            protein_g_per_100g=row["protein_g_per_100g"],
            fiber_g_per_100g=row["fiber_g_per_100g"],
            fat_g_per_100g=row["fat_g_per_100g"],
            carbs_g_per_100g=row["carbs_g_per_100g"],
            legacy_nutrition_available=legacy_food_available(row),
            basis_nutrition=food_contribution(row, float(row["basis_amount"])),
        )

    def _intake_source_from_food(self, row: dict) -> IntakeSourceDTO:
        amount = float(row.get("default_serving") or row["basis_amount"])
        factor = amount / float(row["basis_amount"])
        composition = food_contribution(row, amount).values
        servings = tuple(
            FoodServingDTO(
                serving_id=int(serving["id"]),
                name=str(serving["name"]),
                serving_amount=float(serving["serving_amount"]),
                serving_unit=str(serving["serving_unit"]),
                base_amount=float(serving["base_amount"]),
                basis_unit=str(row["basis_unit"]),  # type: ignore[arg-type]
            )
            for serving in self.foods.list_servings(int(row["id"]))
        )
        return IntakeSourceDTO(
            source_type="food",
            source_id=int(row["id"]),
            name=str(row["name"]),
            category=str(row["category"]),
            detail=f"每 {row['basis_amount']:.2f}{row['basis_unit']} · {format_energy(row['kj'], self.get_energy_display_unit())}",
            kj_reference=float(row["kj"]) * factor,
            protein_g=composition.protein_g,
            fat_g=composition.fat_g,
            carb_g=composition.carbs_g,
            fiber_g=composition.fiber_g,
            allowed_units=(str(row["basis_unit"]),),
            default_amount=amount,
            favorite=bool(row["is_favorite"]),
            servings=servings,
        )

    def _intake_source_from_recipe(self, row: dict) -> IntakeSourceDTO:
        nutrition = self.recipes.calculate_nutrition(int(row["id"]))
        normalization_unit = nutrition.get("normalization_unit")
        if normalization_unit == "g":
            amount_detail = f"总重 {nutrition['total_weight_g']:.2f} g"
            allowed_units = ("g", "ratio_percent")
        elif normalization_unit == "ml":
            amount_detail = f"总体积 {nutrition['total_volume_ml']:.2f} ml"
            allowed_units = ("ml", "ratio_percent")
        else:
            amount_detail = (
                f"合计 {nutrition['total_weight_g']:.2f} g + "
                f"{nutrition['total_volume_ml']:.2f} ml"
            )
            allowed_units = ("ratio_percent",)
        has_reference = normalization_unit in {"g", "ml"}
        composition = nutrition["composition"].scaled(
            100.0 / nutrition["total_amount"] if has_reference else 1.0
        ).values
        return IntakeSourceDTO(
            source_type="recipe",
            source_id=int(row["id"]),
            name=str(row["name"]),
            category="我的食谱",
            detail=f"{amount_detail} · 合计 {format_energy(nutrition['kj'], self.get_energy_display_unit())}",
            kj_reference=(float(nutrition["per_100g_kj"]) if has_reference else None),
            protein_g=composition.protein_g,
            fat_g=composition.fat_g,
            carb_g=composition.carbs_g,
            fiber_g=composition.fiber_g,
            allowed_units=allowed_units,
            default_amount=100.0,
            favorite=bool(row["is_favorite"]),
        )

    def list_intake_sources(
        self, section: IntakeSection, query: str = ""
    ) -> Sequence[IntakeSourceDTO]:
        if section == "recipes":
            return tuple(
                self._intake_source_from_recipe(row)
                for row in self.recipes.list_recipes()
                if not query or query.lower() in str(row["name"]).lower()
            )
        if section == "favorites":
            foods = self.foods.list_foods(favorites_only=True, query=query or None)
            recipes = self.recipes.list_recipes(favorites_only=True)
            return tuple(
                [self._intake_source_from_food(row) for row in foods]
                + [
                    self._intake_source_from_recipe(row)
                    for row in recipes
                    if not query or query.lower() in str(row["name"]).lower()
                ]
            )
        if section == "recent":
            with self.database.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT source_type, source_id, MAX(occurred_at) AS last_used
                    FROM intake_events
                    WHERE active = 1 AND source_id IS NOT NULL
                    GROUP BY source_type, source_id
                    ORDER BY last_used DESC LIMIT 20
                    """
                ).fetchall()
            result: list[IntakeSourceDTO] = []
            for row in rows:
                if row["source_type"] == "FOOD":
                    food = self.foods.get_food(int(row["source_id"]), include_inactive=False)
                    if food is not None:
                        result.append(self._intake_source_from_food(food))
                elif row["source_type"] == "RECIPE":
                    recipe = self.recipes.get_recipe(
                        int(row["source_id"]), include_inactive=False
                    )
                    if recipe is not None:
                        result.append(self._intake_source_from_recipe(recipe))
            if result:
                return tuple(result)
            return tuple(
                self._intake_source_from_food(row)
                for row in self.foods.list_foods()[:12]
            )
        return tuple(
            self._intake_source_from_food(row)
            for row in self.foods.list_foods(query=query or None)
        )

    def record_intake(self, event: IntakeDraft) -> None:
        self._save_intake_source(event)
        self.daily.ensure_calculated(max(event.occurred_at.date(), date.today()))

    def _save_intake_source(self, event: IntakeDraft, replace_event_id: int | None = None) -> None:
        if event.source_type == "recipe":
            if event.unit == "ratio_percent":
                self.recipes.record_recipe_intake(
                    event.source_id,
                    fraction=event.amount / 100.0,
                    replace_event_id=replace_event_id,
                    occurred_at=event.occurred_at,
                    meal_type=event.meal_category,
                    note=event.note,
                )
            else:
                self.recipes.record_recipe_intake(
                    event.source_id,
                    amount=event.amount,
                    replace_event_id=replace_event_id,
                    unit=event.unit,
                    occurred_at=event.occurred_at,
                    meal_type=event.meal_category,
                    note=event.note,
                )
        else:
            self.foods.record_food_intake(
                event.source_id,
                amount=event.amount,
                unit=event.unit,
                serving_id=event.serving_id,
                replace_event_id=replace_event_id,
                occurred_at=event.occurred_at,
                meal_type=event.meal_category,
                note=event.note,
            )

    def update_intake(self, record_id: int, edit: IntakeEditDraft) -> None:
        old = self.foods.get_intake_event(record_id, include_inactive=False)
        if old is None:
            raise LookupError(f"active intake event {record_id} does not exist")
        if edit.replacement is None:
            self.foods.update_intake_event(
                record_id, amount=edit.amount, occurred_at=edit.occurred_at,
                meal_type=edit.meal_category, note=edit.note, update_note=True,
            )
        else:
            source = edit.replacement
            self._save_intake_source(IntakeDraft(
                occurred_at=edit.occurred_at, source_type=source.source_type,
                source_id=source.source_id, amount=edit.amount, unit=source.unit,
                meal_category=edit.meal_category, note=edit.note, serving_id=source.serving_id,
            ), replace_event_id=record_id)
        self.daily.ensure_calculated(max(date.fromisoformat(old["local_date"]), edit.occurred_at.date(), date.today()))

    def update_exercise(self, record_id: int, edit: ExerciseEditDraft) -> None:
        old = self.exercises.get_exercise_event(record_id, include_inactive=False)
        if old is None:
            raise LookupError(f"active exercise event {record_id} does not exist")
        self.exercises.update_exercise_event(
            record_id, exercise_type_id=edit.replacement_type_id, occurred_at=edit.occurred_at,
            duration_min=edit.duration_min, active_kj=edit.active_kj, note=edit.note, update_note=True,
        )
        self.daily.ensure_calculated(max(date.fromisoformat(old["local_date"]), edit.occurred_at.date(), date.today()))

    def get_daily_records(self, day: date) -> Sequence[DailyRecordDTO]:
        from app.services.weight_service import WeightService

        records = []
        for row in self.foods.list_intake_events(local_date=day):
            records.append(DailyRecordDTO(
                "intake", int(row["id"]), datetime.fromisoformat(row["occurred_at"]),
                row["name_snapshot"], note=row["note"] or "", amount=float(row["amount"]),
                unit=row["unit"], meal_type=row["meal_type"], energy_kj=float(row["kj_snapshot"]),
            ))
        for row in self.exercises.list_exercise_events(local_date=day):
            records.append(DailyRecordDTO(
                "exercise", int(row["id"]), datetime.fromisoformat(row["occurred_at"]),
                row["name_snapshot"], note=row["note"] or "", duration_min=float(row["duration_min"]),
                energy_kj=float(row["active_kj"]), exercise_type_id=row["exercise_type_id"],
            ))
        for row in WeightService(self.database).list_measurements(local_date=day):
            records.append(DailyRecordDTO(
                "weight", int(row["id"]), datetime.fromisoformat(row["occurred_at"]),
                "体重", note=row["note"] or "", amount=float(row["weight_kg"]), unit="kg",
            ))
        # Match existing local-calendar calculations; retain original offsets in DTOs.
        return tuple(sorted(records, key=lambda item: (
            parse_local_datetime(item.occurred_at), item.kind, item.record_id,
        )))

    def list_exercise_types(
        self, section: ExerciseSection
    ) -> Sequence[ExerciseTypeDTO]:
        if section == "recent":
            rows = self.exercises.list_recent_exercise_types()
        elif section == "favorites":
            rows = self.exercises.list_exercise_types(favorites_only=True)
        else:
            rows = self.exercises.list_exercise_types()
        result: list[ExerciseTypeDTO] = []
        for row in rows:
            last = self.exercises.get_last_values(int(row["id"]))
            result.append(
                ExerciseTypeDTO(
                    exercise_type_id=int(row["id"]),
                    name=str(row["name"]),
                    default_duration_min=float(row["default_duration_min"] or 0),
                    default_active_kj=float(row["default_active_kj"] or 0),
                    last_duration_min=last["duration_min"],
                    last_active_kj=last["active_kj"],
                    favorite=bool(row["favorite"]),
                    active=bool(row["active"]),
                )
            )
        return tuple(result)

    def record_exercise(self, event: ExerciseDraft) -> None:
        self.exercises.record_exercise(
            event.exercise_type_id,
            duration_min=event.duration_min,
            active_kj=event.active_kj,
            occurred_at=event.occurred_at,
            note=event.note,
        )
        self.daily.ensure_calculated(max(event.occurred_at.date(), date.today()))

    @staticmethod
    def _exercise_type_dto(row: dict, last: dict[str, float | None] | None = None) -> ExerciseTypeDTO:
        last_values = last or {"duration_min": None, "active_kj": None}
        return ExerciseTypeDTO(
            exercise_type_id=int(row["id"]),
            name=str(row["name"]),
            default_duration_min=float(row["default_duration_min"] or 0),
            default_active_kj=float(row["default_active_kj"] or 0),
            last_duration_min=last_values.get("duration_min"),
            last_active_kj=last_values.get("active_kj"),
            favorite=bool(row["favorite"]),
            active=bool(row["active"]),
        )

    def list_exercise_shortcuts(
        self, query: str = "", include_inactive: bool = False
    ) -> Sequence[ExerciseTypeDTO]:
        query_value = query.strip().lower()
        rows = self.exercises.list_exercise_types(include_inactive=include_inactive)
        result: list[ExerciseTypeDTO] = []
        for row in rows:
            if query_value and query_value not in str(row["name"]).lower():
                continue
            last = self.exercises.get_last_values(int(row["id"]))
            result.append(self._exercise_type_dto(row, last))
        return tuple(result)

    def save_exercise_type(self, exercise: ExerciseTypeDraft) -> ExerciseTypeDTO:
        values = dict(
            name=exercise.name,
            default_duration_min=exercise.default_duration_min,
            default_active_kj=exercise.default_active_kj,
            favorite=exercise.favorite,
        )
        if exercise.exercise_type_id is None:
            exercise_type_id = self.exercises.create_exercise_type(**values)
        else:
            exercise_type_id = exercise.exercise_type_id
            self.exercises.update_exercise_type(exercise_type_id, **values)
        row = self.exercises.get_exercise_type(exercise_type_id)
        if row is None:
            raise RuntimeError("运动项目保存后无法读取")
        last = self.exercises.get_last_values(exercise_type_id)
        return self._exercise_type_dto(row, last)

    def set_exercise_type_active(self, exercise_type_id: int, active: bool) -> None:
        if active:
            self.exercises.restore_exercise_type(exercise_type_id)
        else:
            self.exercises.soft_delete_exercise_type(exercise_type_id)

    def record_weight(self, measurement: WeightDraft) -> None:
        # Import lazily so a partially migrated older source checkout can still
        # show a useful startup error before opening the GUI.
        from app.services.weight_service import WeightService

        WeightService(self.database).record_weight(
            weight_kg=measurement.weight_kg,
            occurred_at=measurement.occurred_at,
            anchor=measurement.anchor,
            note=measurement.note,
        )
        self.daily.ensure_calculated(max(measurement.occurred_at.date(), date.today()))

    def list_foods(
        self, query: str = "", include_inactive: bool = False
    ) -> Sequence[FoodDTO]:
        return tuple(
            self._food_dto(row)
            for row in self.foods.list_foods(
                query=query or None, include_inactive=include_inactive
            )
        )

    def save_food(self, food: FoodDraft) -> FoodDTO:
        values = dict(
            name=food.name,
            category=food.category,
            brand=food.brand or None,
            basis_amount=food.basis_amount,
            basis_unit=food.basis_unit,
            kj=food.kj,
            protein_g=food.protein_g,
            fat_g=food.fat_g,
            carb_g=food.carb_g,
            fiber_g=food.fiber_g,
            default_serving=food.default_serving,
            is_favorite=food.is_favorite,
            protein_g_per_100g=food.protein_g_per_100g,
            fiber_g_per_100g=food.fiber_g_per_100g,
            fat_g_per_100g=food.fat_g_per_100g,
            carbs_g_per_100g=food.carbs_g_per_100g,
        )
        if food.food_id is None:
            food_id = self.foods.create_food(**values)
        else:
            food_id = food.food_id
            self.foods.update_food(food_id, **values)
        result = self.foods.get_food(food_id)
        if result is None:
            raise RuntimeError("食品保存后无法读取")
        return self._food_dto(result)

    def set_food_active(self, food_id: int, active: bool) -> None:
        if active:
            self.foods.restore_food(food_id)
        else:
            self.foods.soft_delete_food(food_id)

    def _recipe_detail(self, recipe_id: int) -> RecipeDetailDTO:
        row = self.recipes.get_recipe(recipe_id)
        if row is None:
            raise LookupError(f"recipe {recipe_id} does not exist")
        nutrition = self.recipes.calculate_nutrition(recipe_id)
        return RecipeDetailDTO(
            recipe_id=recipe_id,
            name=str(row["name"]),
            note=str(row.get("description") or ""),
            items=tuple(
                RecipeItemDTO(
                    food_id=int(item["food_id"]),
                    food_name=str(item["food_name"]),
                    amount_g=float(item["amount"]),
                    unit=str(item["unit"]),  # type: ignore[arg-type]
                )
                for item in row["items"]
            ),
            nutrition=self._nutrition_dto(nutrition),
            active=bool(row["active"]),
        )

    @staticmethod
    def _nutrition_dto(values: dict[str, Any]) -> NutritionDTO:
        return NutritionDTO(
            total_weight_g=float(values.get("total_weight_g", values.get("total_amount", 0))),
            total_volume_ml=float(values.get("total_volume_ml", 0)),
            normalization_unit=values.get("normalization_unit", "g"),  # type: ignore[arg-type]
            kj=float(values.get("kj", 0)),
            protein_g=float(values.get("protein_g", 0)),
            fat_g=float(values.get("fat_g", 0)),
            carb_g=float(values.get("carb_g", 0)),
            fiber_g=float(values.get("fiber_g", 0)),
            per_100g_kj=float(values.get("per_100g_kj", 0)),
            per_100g_protein_g=float(values.get("per_100g_protein_g", 0)),
            per_100g_fat_g=float(values.get("per_100g_fat_g", 0)),
            per_100g_carb_g=float(values.get("per_100g_carb_g", 0)),
            per_100g_fiber_g=float(values.get("per_100g_fiber_g", 0)),
            composition=values.get("composition", NutritionContribution()),
        )

    def list_recipes(
        self, query: str = "", include_inactive: bool = False
    ) -> Sequence[RecipeSummaryDTO]:
        result: list[RecipeSummaryDTO] = []
        for row in self.recipes.list_recipes(include_inactive=include_inactive):
            if query and query.lower() not in str(row["name"]).lower():
                continue
            try:
                nutrition = self.recipes.calculate_nutrition(int(row["id"]))
            except ValueError:
                nutrition = {"total_weight_g": 0.0, "kj": 0.0}
            result.append(
                RecipeSummaryDTO(
                    recipe_id=int(row["id"]),
                    name=str(row["name"]),
                    total_weight_g=float(nutrition.get("total_weight_g", 0)),
                    total_volume_ml=float(nutrition.get("total_volume_ml", 0)),
                    normalization_unit=nutrition.get("normalization_unit", "g"),  # type: ignore[arg-type]
                    total_kj=float(nutrition.get("kj", 0)),
                    active=bool(row["active"]),
                )
            )
        return tuple(result)

    def get_recipe(self, recipe_id: int) -> RecipeDetailDTO:
        return self._recipe_detail(recipe_id)

    def preview_recipe(self, items: Sequence[RecipeItemDTO]) -> NutritionDTO:
        totals: dict[str, Any] = {
            "total_weight_g": 0.0,
            "total_volume_ml": 0.0,
            "kj": 0.0,
            "protein_g": 0.0,
            "fat_g": 0.0,
            "carb_g": 0.0,
            "fiber_g": 0.0,
        }
        contributions = []
        for item in items:
            food = self.foods.get_food(item.food_id, include_inactive=False)
            if food is None:
                raise LookupError(f"food {item.food_id} does not exist")
            unit = str(food["basis_unit"])
            if item.unit != unit:
                raise ValueError(
                    f"food {item.food_id} is measured in {unit}, not {item.unit}"
                )
            nutrition = self.foods.calculate_nutrition(item.food_id, item.amount_g, unit=unit)
            if unit == "g":
                totals["total_weight_g"] += item.amount_g
            else:
                totals["total_volume_ml"] += item.amount_g
            totals["kj"] += nutrition["kj"]
            totals["protein_g"] += nutrition["protein"]
            totals["fat_g"] += nutrition["fat"]
            totals["carb_g"] += nutrition["carb"]
            totals["fiber_g"] += nutrition["fiber"]
            contributions.append(food_contribution(food, item.amount_g))
        has_weight = totals["total_weight_g"] > 0
        has_volume = totals["total_volume_ml"] > 0
        if has_weight and not has_volume:
            normalization_amount = totals["total_weight_g"]
            totals["normalization_unit"] = "g"
        elif has_volume and not has_weight:
            normalization_amount = totals["total_volume_ml"]
            totals["normalization_unit"] = "ml"
        else:
            normalization_amount = 0.0
            totals["normalization_unit"] = None
        if normalization_amount > 0:
            totals.update(
                {
                    "per_100g_kj": totals["kj"] * 100 / normalization_amount,
                    "per_100g_protein_g": totals["protein_g"] * 100 / normalization_amount,
                    "per_100g_fat_g": totals["fat_g"] * 100 / normalization_amount,
                    "per_100g_carb_g": totals["carb_g"] * 100 / normalization_amount,
                    "per_100g_fiber_g": totals["fiber_g"] * 100 / normalization_amount,
                }
            )
        totals["composition"] = aggregate_contributions(contributions)
        return self._nutrition_dto(totals)

    def save_recipe(self, recipe: RecipeDraft) -> RecipeDetailDTO:
        items = [
            {"food_id": item.food_id, "amount": item.amount_g, "unit": item.unit}
            for item in recipe.items
        ]
        if recipe.recipe_id is None:
            recipe_id = self.recipes.create_recipe(
                name=recipe.name, description=recipe.note, items=items
            )
        else:
            recipe_id = recipe.recipe_id
            self.recipes.update_recipe(
                recipe_id,
                name=recipe.name,
                description=recipe.note,
                update_description=True,
                items=items,
            )
        return self._recipe_detail(recipe_id)

    def set_recipe_active(self, recipe_id: int, active: bool) -> None:
        if active:
            self.recipes.restore_recipe(recipe_id)
        else:
            self.recipes.soft_delete_recipe(recipe_id)

    def get_energy_display_unit(self) -> EnergyUnit:
        return normalize_energy_unit(self.database.get_setting("energy_display_unit"))

    def get_settings(self) -> SettingsDTO:
        profile = self.profile.get_current_profile() or {}
        candle_mode = str(self.database.get_setting("candle_color_mode", "china"))
        treemap_mode = str(
            self.database.get_setting("treemap_color_mode", "intake_red")
        )
        try:
            candle_palette(candle_mode)  # type: ignore[arg-type]
        except ValueError:
            candle_mode = "china"
        try:
            treemap_palette("intake", treemap_mode)  # type: ignore[arg-type]
        except ValueError:
            treemap_mode = "intake_red"
        try:
            kj_per_kg = float(
                self.database.get_setting("kj_per_kg", str(DEFAULT_KJ_PER_KG)) or DEFAULT_KJ_PER_KG
            )
        except (TypeError, ValueError):
            kj_per_kg = DEFAULT_KJ_PER_KG
        if not isfinite(kj_per_kg) or kj_per_kg <= 0:
            kj_per_kg = DEFAULT_KJ_PER_KG
        return SettingsDTO(
            sex=str(profile.get("gender", "male")),
            birth_date=date.fromisoformat(str(profile.get("birth_date", "1990-01-01"))),
            height_cm=float(profile.get("height_cm", 170.0)),
            wake_time=time.fromisoformat(str(profile.get("wake_time", "07:00"))),
            sleep_time=time.fromisoformat(str(profile.get("sleep_time", "23:00"))),
            awake_multiplier=float(profile.get("awake_multiplier", 1.20)),
            sleep_multiplier=float(profile.get("sleep_multiplier", 0.95)),
            candle_color_mode=candle_mode,  # type: ignore[arg-type]
            treemap_color_mode=treemap_mode,  # type: ignore[arg-type]
            kj_per_kg=kj_per_kg,
            data_directory=self.data_dir,
            energy_display_unit=self.get_energy_display_unit(),
        )

    @staticmethod
    def _validate_settings(settings: SettingsDraft) -> None:
        if settings.energy_display_unit not in ("kj", "kcal"):
            raise ValueError("能量显示单位必须为 kj 或 kcal")
        if not isfinite(settings.kj_per_kg) or settings.kj_per_kg <= 0:
            raise ValueError("kJ/kg 必须为大于 0 的有限值")
        candle_palette(settings.candle_color_mode)
        treemap_palette("intake", settings.treemap_color_mode)

    def save_settings(self, settings: SettingsDraft) -> bool:
        self._validate_settings(settings)
        requested_directory = settings.data_directory.expanduser().resolve()
        if requested_directory != self.data_dir:
            self._relocate_data_with_settings(requested_directory, settings)
            return True
        model_changed = self._apply_settings(self.database, settings)
        if model_changed:
            self.daily.refresh_model_settings()
            self.daily.ensure_calculated(date.today())
        return model_changed

    @staticmethod
    def _apply_settings(database: Database, settings: SettingsDraft) -> bool:
        """Commit settings and invalidation together, before any model refresh."""
        with database.transaction() as connection:
            profile = ProfileService(database)
            current = profile.get_current_profile(connection=connection) or {}
            # Presentation changes must not create a profile revision, invalidate
            # caches, or rewrite a high-precision energy-to-weight coefficient.
            profile_changed = (
                current.get("gender") != settings.sex
                or current.get("birth_date") != settings.birth_date.isoformat()
                or current.get("height_cm") != settings.height_cm
                or time.fromisoformat(str(current.get("wake_time", "00:00"))) != settings.wake_time
                or time.fromisoformat(str(current.get("sleep_time", "00:00"))) != settings.sleep_time
                or current.get("awake_multiplier") != settings.awake_multiplier
                or current.get("sleep_multiplier") != settings.sleep_multiplier
            )
            if profile_changed:
                profile.update_profile(
                    effective_from=date.today(),
                    gender=settings.sex,
                    birth_date=settings.birth_date,
                    height_cm=settings.height_cm,
                    wake_time=settings.wake_time.isoformat(),
                    sleep_time=settings.sleep_time.isoformat(),
                    awake_multiplier=settings.awake_multiplier,
                    sleep_multiplier=settings.sleep_multiplier,
                    connection=connection,
                )
            try:
                old_kj = float(database.get_setting(
                    "kj_per_kg", str(DEFAULT_KJ_PER_KG), connection=connection,
                ) or DEFAULT_KJ_PER_KG)
            except (TypeError, ValueError):
                old_kj = None
            for key, value in (
                ("candle_color_mode", settings.candle_color_mode),
                ("treemap_color_mode", settings.treemap_color_mode),
                ("energy_display_unit", settings.energy_display_unit),
            ):
                if database.get_setting(key, connection=connection) != value:
                    database.set_setting(key, value, connection=connection)
            if old_kj != settings.kj_per_kg:
                database.set_setting("kj_per_kg", str(settings.kj_per_kg), connection=connection)
                row = connection.execute(
                    "SELECT MIN(local_date) FROM weight_measurements WHERE active = 1"
                ).fetchone()
                if row and row[0]:
                    database.mark_dirty(str(row[0]), connection=connection)
        # Exiting the transaction must succeed before callers refresh/rebuild.
        return profile_changed or old_kj != settings.kj_per_kg

    def _relocate_data_with_settings(
        self, destination: Path, settings: SettingsDraft
    ) -> None:
        """Prepare a fully updated copy, then atomically switch directories.

        The original database is intentionally retained as a recovery copy.
        Existing target databases are never overwritten through this setting;
        users should use the explicit restore workflow for that operation.
        """

        destination.mkdir(parents=True, exist_ok=True)
        target = destination / self.database.path.name
        if target.exists():
            raise ValueError(
                "目标数据目录已经包含 caloriek.sqlite3；为避免覆盖数据，请选择空目录，"
                "或使用“从备份恢复”。"
            )
        self.backups.create_backup(prefix="before_data_directory_change")
        stage = destination / ".caloriek-move.sqlite3"
        try:
            stage.unlink(missing_ok=True)
            # sqlite3.Connection.__exit__ only commits or rolls back; it does not
            # close the handle.  Explicit closing is required before manipulating
            # WAL/SHM files on Windows.
            with closing(sqlite3.connect(self.database.path)) as source:
                with closing(sqlite3.connect(stage)) as copied:
                    source.backup(copied)
            staged_database = Database(stage)
            staged_database.initialize(seed_foods=False)
            self._apply_settings(staged_database, settings)
            staged_daily = DailyMetricsService(staged_database)
            staged_daily.ensure_calculated(date.today())
            with closing(sqlite3.connect(stage)) as staged_connection:
                staged_connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            for suffix in ("-wal", "-shm"):
                Path(f"{stage}{suffix}").unlink(missing_ok=True)
            os.replace(stage, target)
            # Prepare every destination service before publishing the preference.
            # A bind/initialization failure must leave the live context untouched.
            prepared = ApplicationContext(destination)
            prepared._seed_exercise_shortcuts()
            save_data_dir_preference(destination)
        except BaseException:
            # Cleanup must never hide the original relocation error (for example,
            # a transient antivirus/file-indexer lock on Windows).
            for artifact in (
                stage,
                Path(f"{stage}-wal"),
                Path(f"{stage}-shm"),
                target,
                Path(f"{target}-wal"),
                Path(f"{target}-shm"),
            ):
                try:
                    artifact.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        # No filesystem or database work remains after preference publication.
        self.__dict__.update(prepared.__dict__)

    def backup_data(self, destination: Path) -> Path:
        return self.backups.create_backup(destination)

    def restore_data(self, backup_file: Path) -> None:
        self.backups.restore_backup(backup_file)
        self.database.initialize(seed_foods=False)
        self.daily.refresh_model_settings()

    def export_data(self, destination: Path) -> Path:
        return self.backups.export_json(destination)

    def get_daily_nutrition(self, day: date) -> DailyNutrition:
        """Read snapshots only; selecting a nutrition date never recalculates energy."""
        return self.nutrition.daily_totals(day)

    def get_dashboard(self, on_date: date | None = None) -> DashboardDTO:
        target = on_date or date.today()
        self.daily.ensure_calculated(target)
        start = target - timedelta(days=89)
        metrics = self.daily.list_metrics(start_day=start, end_day=target)
        candles = tuple(
            CandleDTO(
                local_date=date.fromisoformat(row["date"]),
                open_kg=float(row["open_kg"]),
                high_kg=float(row["high_kg"]),
                low_kg=float(row["low_kg"]),
                close_kg=float(row["close_kg"]),
                intake_kj=float(row["intake_kj"]),
                burn_kj=float(row["total_burn_kj"]),
                balance_kj=float(row["balance_kj"]),
                actual_weight_count=int(row["actual_weight_count"]),
                open_source=str(row["open_source"]),
                close_source=str(row["close_source"]),
            )
            for row in metrics
        )
        current_row = metrics[-1] if metrics and metrics[-1]["date"] == target.isoformat() else None
        actual = self.daily._latest_actual_row(on_or_before=target)
        actual_weight = float(actual["weight_kg"]) if actual is not None else None
        actual_at = (
            parse_local_datetime(actual["occurred_at"]) if actual is not None else None
        )
        if target == date.today():
            projection = self.daily.calculate_today_projection()
        else:
            projection = None
        baseline = float(current_row["baseline_kj"]) if current_row else 0.0
        treemap_items = self.treemap.build_day(target, baseline_kj=baseline)
        return DashboardDTO(
            as_of=datetime.now(),
            nutrition=self.get_daily_nutrition(target),
            latest_actual_weight_kg=actual_weight,
            latest_actual_at=actual_at,
            predicted_weight_kg=(
                projection.predicted_close_kg
                if projection is not None
                else (float(current_row["predicted_close_kg"]) if current_row else None)
            ),
            predicted_change_kg=(
                projection.predicted_change_from_actual_kg
                if projection is not None
                else (
                    float(current_row["predicted_close_kg"]) - actual_weight
                    if current_row and actual_weight is not None
                    else None
                )
            ),
            intake_kj=(
                projection.today_intake_kj
                if projection is not None
                else (float(current_row["intake_kj"]) if current_row else 0.0)
            ),
            baseline_kj=(
                projection.today_baseline_projected_kj
                if projection is not None
                else baseline
            ),
            exercise_kj=(
                projection.today_exercise_kj
                if projection is not None
                else (float(current_row["exercise_kj"]) if current_row else 0.0)
            ),
            total_burn_kj=(
                projection.today_total_burn_projected_kj
                if projection is not None
                else (float(current_row["total_burn_kj"]) if current_row else 0.0)
            ),
            balance_kj=(
                projection.today_balance_kj
                if projection is not None
                else (float(current_row["balance_kj"]) if current_row else 0.0)
            ),
            candles=candles,
            treemap_items=tuple(
                TreemapItemDTO(
                    key=item.key,
                    name=item.name,
                    kj=item.kj,
                    side=item.side,
                    category=item.category,
                    protein_g=item.details.get("protein_g"),
                    fat_g=item.details.get("fat_g"),
                    carb_g=item.details.get("carb_g"),
                    fiber_g=item.details.get("fiber_g"),
                    duration_min=item.details.get("duration_min"),
                )
                for item in treemap_items
            ),
            calibration_kj_day=(
                projection.calibration_kj_day
                if projection is not None
                else (float(current_row["calibration_kj"]) if current_row else 0.0)
            ),
        )


__all__ = ["ApplicationContext", "DEFAULT_EXERCISES"]
