"""CAL-12 synthetic-only compatibility contracts; run by the user, not Codex."""

from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.db.database import Database
from app.nutrition import food_contribution, intake_contribution, legacy_food_available, NUTRIENT_NAMES
from app.services.food_service import FoodService
from app.services.nutrition_service import NutritionService
from app.services.recipe_service import RecipeService
from tests.test_energy_migration import create_legacy_database
from tests.test_nutrition_migration import create_v2_database


class LegacyNutritionCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def migrated(self, version=2, unit="g"):
        path = self.root / f"v{version}-{unit}" / "caloriek.sqlite3"
        (create_legacy_database if version == 1 else create_v2_database)(path)
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(
                "UPDATE foods SET created_at='2026-08-01T12:00:00+08:00', active=1, "
                "basis_amount=200, basis_unit=?, protein_g=12.3456789, fiber_g=0, fat_g=4, carb_g=8 WHERE id=1",
                (unit,),
            )
            connection.execute("UPDATE recipe_items SET unit=? WHERE food_id=1", (unit,))
            connection.commit()
        db = Database(path)
        with patch("app.db.database.now_iso", return_value="2026-09-01T12:00:00+08:00"):
            db.initialize(seed_foods=False)
        return db

    def test_v1_and_v2_foods_fallback_and_new_intake_freezes_complete_snapshot(self):
        for version in (1, 2):
            with self.subTest(version=version):
                db = self.migrated(version)
                foods = FoodService(db)
                food = foods.get_food(1)
                self.assertTrue(legacy_food_available(food))
                contribution = food_contribution(food, 100)
                self.assertEqual(contribution.values.as_tuple(), (6.17283945, 0, 2, 4))
                self.assertTrue(contribution.complete)
                event_id = foods.record_food_intake(1, amount=100, occurred_at="2026-09-02T12:00:00")
                event = foods.get_intake_event(event_id)
                self.assertEqual(tuple(event[field] for field in NUTRIENT_NAMES), contribution.values.as_tuple())
                self.assertEqual(event["nutrition_complete"], 1)
                foods.update_food(1, protein_g=999)
                self.assertEqual(NutritionService(db).daily_totals("2026-09-02").nutrients, contribution.values)

    def test_volume_uses_legacy_basis_and_named_serving_without_density(self):
        db = self.migrated(unit="ml")
        foods = FoodService(db)
        event_id = foods.record_food_intake(1, amount=2, serving_id=1, occurred_at="2026-09-02")
        # Existing fixture serving is 35.5 ml, not grams or a per-100g reference.
        event = foods.get_intake_event(event_id)
        self.assertAlmostEqual(event["protein_g"], 12.3456789 * 71 / 200)
        self.assertEqual(event["nutrition_complete"], 1)
        foods.update_food(1, protein_g_per_100g=99)
        explicit_mass = food_contribution(foods.get_food(1), 200)
        self.assertIsNone(explicit_mass.values.protein_g)
        self.assertEqual(explicit_mass.values.fiber_g, 0)
        self.assertFalse(explicit_mass.complete)

    def test_per_nutrient_v3_precedence_including_explicit_zero(self):
        db = self.migrated()
        foods = FoodService(db)
        foods.update_food(1, protein_g_per_100g=0, fat_g_per_100g=9.123456789)
        value = food_contribution(foods.get_food(1), 200)
        self.assertEqual(value.values.as_tuple(), (0, 0, 18.246913578, 8))
        self.assertTrue(value.complete)

    def test_v3_native_defaults_do_not_become_known_zero_even_in_migrated_database(self):
        db = self.migrated()
        foods = FoodService(db)
        with patch("app.services.food_service.now_iso", return_value="2026-09-02T12:00:00+08:00"):
            food_id = foods.create_food(name="New", category="Synthetic", basis_unit="g", kj=100)
        self.assertFalse(legacy_food_available(foods.get_food(food_id)))
        event_id = foods.record_food_intake(food_id, amount=100, occurred_at="2026-09-02")
        self.assertEqual(foods.get_intake_event(event_id)["nutrition_complete"], 0)
        self.assertEqual(NutritionService(db).daily_totals("2026-09-02").indication, "部分记录无营养数据")

    def test_builtin_provenance_in_fresh_v3_database(self):
        db = Database(self.root / "fresh.sqlite3")
        db.initialize()
        milk = next(row for row in FoodService(db).list_foods() if row["builtin_key"] == "milk-whole")
        value = food_contribution(milk, 250)
        self.assertTrue(value.complete)
        self.assertAlmostEqual(value.values.protein_g, 8)
        self.assertEqual(value.values.fiber_g, 0)

    def test_provenance_boundary_invalid_and_mixed_clock_fail_closed(self):
        row = dict(created_at="2026-09-01T12:00:00+08:00", v3_applied_at="2026-09-01T12:00:00+08:00")
        self.assertFalse(legacy_food_available(row))
        for created in (None, "old", "2026-08-01T12:00:00", "2026-09-02T12:00:00+08:00"):
            self.assertFalse(legacy_food_available(dict(row, created_at=created)))
        self.assertTrue(legacy_food_available(dict(row, created_at="2026-09-01T03:00:00+00:00")))

    def test_intake_tristate_and_per_field_precedence(self):
        event = dict(protein_snapshot=11, fiber_snapshot=0, fat_snapshot=2, carb_snapshot=3,
                     protein_g=0, fiber_g=None, fat_g=None, carbs_g=None)
        legacy = intake_contribution(dict(event, nutrition_complete=None))
        self.assertEqual(legacy.values.as_tuple(), (0, 0, 2, 3))
        self.assertTrue(legacy.complete)
        for state in (0, 1):
            value = intake_contribution(dict(event, nutrition_complete=state))
            self.assertEqual(value.values.as_tuple(), (0, None, None, None))
            self.assertFalse(value.complete)
        filled = dict(event, fiber_g=0, fat_g=2, carbs_g=3)
        self.assertFalse(intake_contribution(dict(filled, nutrition_complete=0)).complete)
        self.assertTrue(intake_contribution(dict(filled, nutrition_complete=1)).complete)

    def test_history_uses_snapshot_not_library_and_read_is_non_mutating(self):
        db = self.migrated()
        with db.connection() as connection:
            before = tuple(connection.iterdump())
        result = NutritionService(db).daily_totals("2026-08-20")
        self.assertEqual(result.nutrients.as_tuple(), (17.123456789, 5.4321, 0, 0))
        self.assertFalse(result.incomplete)
        with db.connection() as connection:
            self.assertEqual(tuple(connection.iterdump()), before)
        FoodService(db).update_food(1, protein_g=999)
        self.assertEqual(NutritionService(db).daily_totals("2026-08-20"), result)

    def test_recipe_preview_and_snapshot_share_effective_compatibility(self):
        db = self.migrated(unit="ml")
        recipes = RecipeService(db)
        expected = recipes.calculate_nutrition(1)["composition"]
        self.assertTrue(expected.complete)
        preview = recipes.calculate_items([(1, 50.123456789, "ml")])["composition"]
        self.assertEqual(preview, expected)
        event_id = recipes.record_recipe_intake(1, fraction=0.5, occurred_at="2026-09-02")
        event = FoodService(db).get_intake_event(event_id)
        self.assertEqual(intake_contribution(event), expected.scaled(0.5))

    def test_explicitly_incomplete_snapshot_does_not_backfill_after_scaling(self):
        db = self.migrated()
        foods = FoodService(db)
        event_id = foods.record_custom_intake(name="Unknown", amount=10, unit="g", kj=30,
                                              protein_g=0, occurred_at="2026-09-02")
        foods.update_intake_event(event_id, amount=20)
        event = foods.get_intake_event(event_id)
        self.assertEqual(event["nutrition_complete"], 0)
        self.assertEqual(intake_contribution(event).values.as_tuple(), (0, None, None, None))
