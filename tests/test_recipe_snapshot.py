from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.energy_units import kcal_to_kj
from app.services.food_service import FoodService
from app.services.recipe_service import RecipeService


class RecipeSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._temporary_directory.name) / "caloriek.sqlite3")
        self.db.initialize(seed_foods=False)
        self.foods = FoodService(self.db)
        self.recipes = RecipeService(self.db)
        self.beef_id = self.foods.create_food(
            name="牛肉", category="肉类", basis_unit="g", kj=kcal_to_kj(250), protein_g=26
        )
        self.rice_id = self.foods.create_food(
            name="杂粮饭", category="主食", basis_unit="g", kj=kcal_to_kj(116), carb_g=25
        )

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_recipe_intake_snapshot_survives_ingredient_and_recipe_edits(self) -> None:
        preview = self.recipes.preview_recipe(
            [(self.beef_id, 100), (self.rice_id, 100)]
        )
        self.assertAlmostEqual(preview["kj"], kcal_to_kj(366.0))
        self.assertAlmostEqual(preview["per_100g_kj"], kcal_to_kj(183.0))
        recipe_id = self.recipes.create_recipe(
            name="牛肉饭",
            items=[(self.beef_id, 100), (self.rice_id, 100)],
        )
        totals = self.recipes.calculate_nutrition(recipe_id)
        self.assertAlmostEqual(totals["total_weight_g"], 200.0)
        self.assertAlmostEqual(totals["kj"], kcal_to_kj(366.0))
        self.assertAlmostEqual(totals["per_100g_kj"], kcal_to_kj(183.0))

        event_id = self.recipes.record_recipe_intake(
            recipe_id,
            fraction=1,
            occurred_at="2026-08-05T19:00:00+08:00",
            meal_type="晚餐",
        )
        original = self.foods.get_intake_event(event_id)
        self.assertAlmostEqual(float(original["kj_snapshot"]), kcal_to_kj(366.0))
        self.assertEqual(original["name_snapshot"], "牛肉饭")
        self.assertEqual(self.recipes.list_recent_recipes()[0]["id"], recipe_id)
        self.assertEqual(self.recipes.list_recipes(query="牛肉")[0]["id"], recipe_id)
        self.assertEqual(self.recipes.list_recipes(query="不存在"), [])

        self.foods.update_food(self.beef_id, kj=kcal_to_kj(230))
        self.recipes.update_recipe(
            recipe_id,
            name="加量牛肉饭",
            items=[(self.beef_id, 200), (self.rice_id, 100)],
        )
        historical = self.foods.get_intake_event(event_id)
        self.assertAlmostEqual(float(historical["kj_snapshot"]), kcal_to_kj(366.0))
        self.assertEqual(historical["name_snapshot"], "牛肉饭")

        new_event_id = self.recipes.record_recipe_intake(
            recipe_id,
            amount_g=150,
            occurred_at="2026-08-06T19:00:00",
        )
        new_event = self.foods.get_intake_event(new_event_id)
        # Current recipe is 300 g; 150 g records half its total energy.
        self.assertAlmostEqual(float(new_event["kj_snapshot"]), kcal_to_kj(288.0))
        self.assertEqual(new_event["name_snapshot"], "加量牛肉饭")
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-05")

    def test_soft_deleted_sources_do_not_invalidate_existing_recipe_event(self) -> None:
        recipe_id = self.recipes.create_recipe(
            name="简餐",
            items=[(self.rice_id, 180)],
        )
        event_id = self.recipes.record_recipe_intake(
            recipe_id,
            amount_g=90,
            occurred_at="2026-08-07T12:00:00",
        )
        expected_kj = float(self.foods.get_intake_event(event_id)["kj_snapshot"])

        self.recipes.soft_delete_recipe(recipe_id)
        self.foods.soft_delete_food(self.rice_id)
        event = self.foods.get_intake_event(event_id, include_inactive=False)
        self.assertIsNotNone(event)
        self.assertAlmostEqual(float(event["kj_snapshot"]), expected_kj)
        self.assertEqual(self.recipes.list_recipes(), [])
        self.assertIsNotNone(self.recipes.get_recipe(recipe_id))

        with self.assertRaises(ValueError):
            self.recipes.preview_recipe([(self.beef_id, float("nan"))])

    def test_mixed_mass_and_volume_are_kept_separate(self) -> None:
        broth_id = self.foods.create_food(
            name="汤底",
            category="其它",
            basis_unit="ml",
            kj=kcal_to_kj(20),
        )
        recipe_id = self.recipes.create_recipe(
            name="杂粮汤饭",
            items=[
                (self.rice_id, 100, "g"),
                (broth_id, 250, "ml"),
            ],
        )
        totals = self.recipes.calculate_nutrition(recipe_id)
        self.assertEqual(totals["total_weight_g"], 100.0)
        self.assertEqual(totals["total_volume_ml"], 250.0)
        self.assertIsNone(totals["normalization_unit"])
        self.assertEqual(totals["per_100g_kj"], 0.0)

        with self.assertRaisesRegex(ValueError, "recipe fraction"):
            self.recipes.record_recipe_intake(recipe_id, amount=100, unit="g")
        event_id = self.recipes.record_recipe_intake(recipe_id, fraction=0.5)
        event = self.foods.get_intake_event(event_id)
        self.assertEqual(event["unit"], "recipe")
        self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(83.0))

    def test_liquid_only_recipe_uses_ml_reference(self) -> None:
        milk_id = self.foods.create_food(
            name="牛奶",
            category="蛋奶",
            basis_unit="ml",
            kj=kcal_to_kj(60),
        )
        recipe_id = self.recipes.create_recipe(
            name="热牛奶",
            items=[(milk_id, 250, "ml")],
        )
        totals = self.recipes.calculate_nutrition(recipe_id)
        self.assertEqual(totals["total_weight_g"], 0.0)
        self.assertEqual(totals["total_volume_ml"], 250.0)
        self.assertEqual(totals["normalization_unit"], "ml")
        self.assertAlmostEqual(totals["per_100g_kj"], kcal_to_kj(60.0))
        event_id = self.recipes.record_recipe_intake(
            recipe_id, amount=125, unit="ml"
        )
        event = self.foods.get_intake_event(event_id)
        self.assertEqual(event["unit"], "ml")
        self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(75.0))


if __name__ == "__main__":
    unittest.main()
