"""CAL-8 nutrition semantics. Run only in the reviewed Windows workflow."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from app.db.database import Database
from app.nutrition import (
    FOOD_NUTRIENT_FIELDS, NUTRIENT_NAMES, NutrientValues, format_nutrient,
)
from app.services.food_service import FoodService
from app.services.nutrition_service import NutritionService
from app.services.recipe_service import RecipeService


class NutritionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Database(Path(self.temporary.name) / "caloriek.sqlite3")
        self.db.initialize(seed_foods=False)
        self.foods = FoodService(self.db)
        self.recipes = RecipeService(self.db)
        self.nutrition = NutritionService(self.db)

    def food(self, **changes) -> int:
        values = dict(
            name="已知食品", category="测试", basis_unit="g", kj=321.123456789,
            protein_g_per_100g=12.3456789012345, fiber_g_per_100g=0.0,
            fat_g_per_100g=3.4567891234567, carbs_g_per_100g=23.4567891234567,
        )
        values.update(changes)
        return self.foods.create_food(**values)

    def snapshot(self, event_id: int) -> tuple:
        row = self.foods.get_intake_event(event_id)
        return tuple(row[name] for name in NUTRIENT_NAMES) + (row["nutrition_complete"],)

    def test_unknown_and_known_zero_are_distinct_and_old_macros_are_not_inferred(self) -> None:
        unknown = self.foods.create_food(name="旧字段有值", category="测试", basis_unit="g", kj=100, protein_g=80)
        zero = self.food(**dict.fromkeys(FOOD_NUTRIENT_FIELDS, 0.0))
        unknown_event = self.foods.record_food_intake(unknown, amount=100)
        zero_event = self.foods.record_food_intake(zero, amount=100)
        self.assertEqual(self.snapshot(unknown_event), (None, None, None, None, 0))
        self.assertEqual(self.snapshot(zero_event), (0.0, 0.0, 0.0, 0.0, 1))
        self.assertEqual(self.foods.get_intake_event(unknown_event)["protein_snapshot"], 80.0)

    def test_per_100g_is_independent_of_energy_basis_and_not_rounded(self) -> None:
        food_id = self.food(basis_amount=50)
        event = self.foods.record_food_intake(food_id, amount=37.123456789)
        source = self.foods.get_food(food_id)
        expected = tuple(source[name] * (37.123456789 / 100) for name in FOOD_NUTRIENT_FIELDS)
        self.assertEqual(self.snapshot(event), (*expected, 1))
        self.assertEqual(self.foods.get_intake_event(event)["kj_snapshot"], source["kj"] * (37.123456789 / 50))
        self.assertNotEqual(expected[0], round(expected[0], 2))

    def test_named_serving_snapshots_actual_mass_and_preserves_event_unit(self) -> None:
        food_id = self.food()
        serving = self.foods.add_serving(food_id, name="两片", serving_amount=2, base_amount=80, serving_unit="片")
        event = self.foods.record_food_intake(food_id, amount=3, serving_id=serving)
        source = self.foods.get_food(food_id)
        self.assertEqual(self.snapshot(event), (*(source[name] * 1.2 for name in FOOD_NUTRIENT_FIELDS), 1))
        self.assertEqual(self.foods.get_intake_event(event)["unit"], "片")

    def test_ml_does_not_assume_density_but_energy_still_works(self) -> None:
        food_id = self.food(basis_unit="ml")
        direct = self.foods.record_food_intake(food_id, amount=200)
        serving = self.foods.list_servings(food_id)[0]["id"]
        by_serving = self.foods.record_food_intake(food_id, amount=2, serving_id=serving)
        self.assertEqual(self.snapshot(direct), (None, None, None, None, 0))
        self.assertEqual(self.snapshot(by_serving), self.snapshot(direct))
        self.assertEqual(self.foods.get_intake_event(direct)["kj_snapshot"], 321.123456789 * 2)

    def test_nullable_fields_reject_negative_nan_and_infinity_before_writes(self) -> None:
        food_id = self.food()
        original = self.foods.get_food(food_id)
        for field in FOOD_NUTRIENT_FIELDS:
            for invalid in (-0.01, float("nan"), float("inf")):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError):
                        self.food(**{field: invalid})
                    with self.assertRaises(ValueError):
                        self.foods.update_food(food_id, **{field: invalid})
                    self.assertEqual(self.foods.get_food(food_id), original)

    def test_food_edits_and_soft_delete_do_not_rewrite_intake_nutrition(self) -> None:
        food_id = self.food()
        old_event = self.foods.record_food_intake(food_id, amount=80, occurred_at="2026-09-01")
        original = self.foods.get_intake_event(old_event)
        self.foods.update_food(food_id, protein_g_per_100g=99.87654321, fiber_g_per_100g=None, kj=800)
        new_event = self.foods.record_food_intake(food_id, amount=80, occurred_at="2026-09-02")
        self.assertEqual(self.foods.get_intake_event(old_event), original)
        self.assertEqual(self.snapshot(new_event)[0], 99.87654321 * 0.8)
        self.assertIsNone(self.snapshot(new_event)[1])
        self.foods.soft_delete_food(food_id)
        self.assertEqual(self.foods.get_intake_event(old_event), original)
        self.assertEqual(self.nutrition.daily_totals("2026-09-01").nutrients.protein_g, original["protein_g"])

    def test_recipe_aggregates_known_parts_and_freezes_scaled_snapshots(self) -> None:
        known = self.food()
        partial = self.food(protein_g_per_100g=None, fiber_g_per_100g=2, fat_g_per_100g=None, carbs_g_per_100g=None)
        items = [(known, 50, "g"), (partial, 125, "g")]
        recipe = self.recipes.create_recipe(name="部分已知", items=items)
        total = self.recipes.calculate_nutrition(recipe)["composition"]
        self.assertFalse(total.complete)
        self.assertEqual(total.values.protein_g, 12.3456789012345 * 0.5)
        self.assertEqual(total.values.fiber_g, 2.5)
        self.assertEqual(self.recipes.calculate_items(items)["composition"], total)
        event = self.recipes.record_recipe_intake(recipe, fraction=0.4, occurred_at="2026-09-01")
        by_mass = self.recipes.record_recipe_intake(recipe, amount_g=70, occurred_at="2026-09-02")
        self.assertEqual(self.snapshot(event), total.scaled(0.4).snapshot_values())
        self.assertEqual(self.snapshot(by_mass), self.snapshot(event))
        # All four sums are numeric, but the missing ingredient data must still
        # make the day incomplete (NULL checks alone would be insufficient).
        self.assertTrue(all(value is not None for value in self.snapshot(event)[:4]))
        day = self.nutrition.daily_totals("2026-09-01")
        self.assertEqual(day.indication, "部分记录无营养数据")
        before = self.foods.get_intake_event(event)
        self.foods.update_food(known, protein_g_per_100g=88)
        self.recipes.update_recipe(recipe, items=[(known, 100, "g")])
        self.recipes.soft_delete_recipe(recipe)
        self.assertEqual(self.foods.get_intake_event(event), before)

    def test_complete_recipe_and_unknown_mixed_recipe(self) -> None:
        known = self.food()
        liquid = self.food(basis_unit="ml")
        complete = self.recipes.create_recipe(name="完整", items=[(known, 100)])
        event = self.recipes.record_recipe_intake(complete, fraction=0.25)
        self.assertEqual(self.snapshot(event)[-1], 1)
        mixed = self.recipes.create_recipe(name="混合", items=[(known, 100), (liquid, 50)])
        event = self.recipes.record_recipe_intake(mixed, fraction=0.5)
        self.assertEqual(self.snapshot(event)[0], 12.3456789012345 * 0.5)
        self.assertEqual(self.snapshot(event)[-1], 0)
        with self.assertRaises(ValueError):
            self.recipes.record_recipe_intake(mixed, amount_g=50)
        unknown = self.recipes.create_recipe(name="仅液体", items=[(liquid, 100)])
        event = self.recipes.record_recipe_intake(unknown, amount=50, unit="ml")
        self.assertEqual(self.snapshot(event), (None, None, None, None, 0))

    def test_editing_amount_scales_saved_values_not_current_library_and_keeps_null(self) -> None:
        food = self.food(fiber_g_per_100g=None)
        event = self.foods.record_food_intake(food, amount=100)
        before = self.snapshot(event)
        self.foods.update_food(food, **dict.fromkeys(FOOD_NUTRIENT_FIELDS, 999))
        self.foods.update_intake_event(event, amount=50, occurred_at="2026-09-01", note="改量")
        self.assertEqual(self.snapshot(event), (*(None if value is None else value * 0.5 for value in before[:4]), 0))
        self.assertEqual(self.nutrition.daily_totals("2026-09-01").incomplete_count, 1)

    def test_daily_totals_handle_empty_unknown_partial_deleted_restored_and_dates(self) -> None:
        day = "2026-09-01"
        empty = self.nutrition.daily_totals(day)
        self.assertEqual(empty.nutrients, NutrientValues(0, 0, 0, 0))
        self.assertEqual((empty.intake_count, empty.indication), (0, ""))
        unknown_food = self.food(**dict.fromkeys(FOOD_NUTRIENT_FIELDS, None))
        unknown = self.foods.record_food_intake(unknown_food, amount=50, occurred_at=day)
        self.assertEqual(self.nutrition.daily_totals(day).nutrients, NutrientValues())
        self.assertEqual(self.nutrition.daily_totals(day).indication, "部分记录无营养数据")
        known = self.foods.record_food_intake(self.food(), amount=100, occurred_at=day)
        self.foods.record_food_intake(self.food(), amount=100, occurred_at="2026-09-02")
        mixed = self.nutrition.daily_totals(date(2026, 9, 1))
        self.assertEqual((mixed.intake_count, mixed.incomplete_count), (2, 1))
        self.assertEqual(mixed.nutrients.protein_g, self.snapshot(known)[0])
        self.foods.soft_delete_intake_event(unknown)
        self.assertFalse(self.nutrition.daily_totals(day).incomplete)
        self.foods.restore_intake_event(unknown)
        self.assertTrue(self.nutrition.daily_totals(day).incomplete)
        self.foods.update_intake_event(unknown, occurred_at="2026-09-03")
        self.assertFalse(self.nutrition.daily_totals(day).incomplete)
        self.assertTrue(self.nutrition.daily_totals("2026-09-03").incomplete)

    def test_custom_intake_omitted_values_are_unknown_not_zero(self) -> None:
        unknown = self.foods.record_custom_intake(name="手动", amount=1, unit="份", kj=500)
        zero = self.foods.record_custom_intake(name="零", amount=1, unit="份", kj=500,
                                              protein_g=0, fiber_g=0, fat_g=0, carb_g=0)
        self.assertEqual(self.snapshot(unknown), (None, None, None, None, 0))
        self.assertEqual(self.snapshot(zero), (0.0, 0.0, 0.0, 0.0, 1))

    def test_nutrition_read_does_not_write_any_data_or_energy_cache(self) -> None:
        self.foods.record_food_intake(self.food(), amount=100, occurred_at="2026-09-01")
        with self.db.transaction() as connection:
            connection.execute("INSERT INTO daily_metrics_cache(date, calculation_version, calculated_at) VALUES ('2026-09-01', 'unchanged', 'old')")
        with self.db.connection() as connection:
            before = tuple(connection.iterdump())
        self.nutrition.daily_totals("2026-09-01")
        self.nutrition.daily_totals("2025-01-01")
        with self.db.connection() as connection:
            self.assertEqual(tuple(connection.iterdump()), before)

    def test_two_decimal_presentation_does_not_mutate_precision(self) -> None:
        values = NutrientValues(12.3456789012345, 0, None, 1.2345678912345)
        self.assertEqual(tuple(map(format_nutrient, values.as_tuple())), ("12.35 g", "0.00 g", "未知", "1.23 g"))
        self.assertEqual(values.protein_g, 12.3456789012345)
