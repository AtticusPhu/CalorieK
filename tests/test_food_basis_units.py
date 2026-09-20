"""CAL-13 dimensional invariants using synthetic temporary databases only."""

from contextlib import closing
from dataclasses import replace
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from app.application import ApplicationContext
from app.db import Database
from app.db.seed_foods import seed_builtin_foods
from app.nutrition import NUTRIENT_NAMES, legacy_food_available
from app.services.food_service import FoodService
from app.services.recipe_service import RecipeService
from app.ui.context import FoodDraft
from tests.test_energy_migration import create_legacy_database
from tests.test_nutrition_migration import create_v2_database


class FoodBasisUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db = Database(self.root / "native.sqlite3")
        with patch("app.db.database.now_iso", return_value="2026-09-01T12:00:00+08:00"):
            self.db.initialize(seed_foods=False)
        self.foods = FoodService(self.db)
        self.recipes = RecipeService(self.db)

    @staticmethod
    def _dump(db: Database) -> tuple[str, ...]:
        with db.connection() as connection:
            return tuple(connection.iterdump())

    def _create_food(self, unit: str) -> int:
        with patch("app.services.food_service.now_iso", return_value="2026-09-02T12:00:00+08:00"):
            return self.foods.create_food(
                name=f"Synthetic {unit}", category="Synthetic", basis_unit=unit,
                basis_amount=100, kj=100, protein_g=8,
                protein_g_per_100g=6, fiber_g_per_100g=0,
                fat_g_per_100g=2, carbs_g_per_100g=4,
            )

    def _assert_locked(self, foods: FoodService, food_id: int, unit: str) -> None:
        before = self._dump(foods.db)
        other = "ml" if unit == "g" else "g"
        with patch.object(foods.db, "transaction", wraps=foods.db.transaction) as transaction:
            with self.assertRaisesRegex(ValueError, "basis_unit.*create a new food"):
                foods.update_food(
                    food_id, basis_unit=other, name="Must not persist",
                    basis_amount=200, kj=999, protein_g_per_100g=99,
                    default_serving=75,
                )
            transaction.assert_not_called()
        self.assertEqual(self._dump(foods.db), before)

        # Exact same-unit submissions do not mark built-ins modified, touch
        # timestamps, or write any dependent rows.
        with patch.object(foods.db, "transaction", wraps=foods.db.transaction) as transaction:
            foods.update_food(food_id, basis_unit=unit)
            transaction.assert_not_called()
        self.assertEqual(self._dump(foods.db), before)

    def test_new_mass_and_volume_foods_are_allowed_but_units_then_lock(self) -> None:
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                food_id = self._create_food(unit)
                row = self.foods.get_food(food_id)
                self.assertEqual(row["basis_unit"], unit)
                self.assertFalse(legacy_food_available(row))
                # Creation normally makes a default serving. Remove it only in
                # this synthetic fixture to prove no dependencies are needed.
                with self.db.transaction() as connection:
                    connection.execute("DELETE FROM food_servings WHERE food_id = ?", (food_id,))
                self._assert_locked(self.foods, food_id, unit)

    def test_builtin_foods_lock_both_dimensions(self) -> None:
        seed_builtin_foods(self.db)
        for key in ("rice-cooked", "milk-whole"):
            with self.subTest(key=key):
                row = next(food for food in self.foods.list_foods() if food["builtin_key"] == key)
                self.assertEqual(row["is_builtin"], 1)
                self.assertEqual(row["user_modified"], 0)
                self._assert_locked(self.foods, row["id"], row["basis_unit"])

    def test_migrated_v1_and_v2_foods_lock_both_dimensions(self) -> None:
        for version, factory in ((1, create_legacy_database), (2, create_v2_database)):
            for unit in ("g", "ml"):
                with self.subTest(version=version, unit=unit):
                    path = self.root / f"legacy-v{version}-{unit}" / "caloriek.sqlite3"
                    factory(path)
                    # Define a coherent mass/volume fixture BEFORE migration;
                    # production must never rewrite these dependent rows.
                    with closing(sqlite3.connect(path)) as connection:
                        connection.execute(
                            "UPDATE foods SET basis_unit=?, active=1, "
                            "created_at='2026-08-01T12:00:00+08:00' WHERE id=1", (unit,),
                        )
                        connection.execute("UPDATE recipe_items SET unit=? WHERE food_id=1", (unit,))
                        connection.execute(
                            "UPDATE intake_events SET unit=? WHERE source_type='FOOD' AND source_id=1",
                            (unit,),
                        )
                        connection.commit()
                    db = Database(path)
                    with patch("app.db.database.now_iso", return_value="2026-09-01T12:00:00+08:00"):
                        db.initialize(seed_foods=False)
                    foods = FoodService(db)
                    self.assertTrue(legacy_food_available(foods.get_food(1)))
                    self._assert_locked(foods, 1, unit)

    def test_inactive_foods_cannot_change_dimension(self) -> None:
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                food_id = self._create_food(unit)
                self.foods.soft_delete_food(food_id)
                self._assert_locked(self.foods, food_id, unit)

    def test_same_unit_allows_ordinary_edits_without_rewriting_dependent_facts(self) -> None:
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                food_id = self._create_food(unit)
                serving_id = self.foods.add_serving(food_id, name="Half basis", base_amount=50)
                recipe_id = self.recipes.create_recipe(name="Synthetic recipe", items=[(food_id, 100, unit)])
                when = "2026-09-03T12:00:00+08:00"
                event_ids = [
                    self.foods.record_food_intake(food_id, amount=100, unit=unit, occurred_at=when),
                    self.foods.record_food_intake(food_id, amount=1, serving_id=serving_id, occurred_at=when),
                    self.recipes.record_recipe_intake(recipe_id, fraction=1, occurred_at=when),
                ]
                history = [self.foods.get_intake_event(event_id) for event_id in event_ids]
                self.assertEqual([row["kj_snapshot"] for row in history], [100, 50, 100])
                self.assertEqual(history[0]["nutrition_complete"], int(unit == "g"))
                self._assert_locked(self.foods, food_id, unit)
                tables = ("food_servings", "recipe_items", "intake_events")
                with self.db.connection() as connection:
                    facts = {
                        table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")]
                        for table in tables
                    }

                self.foods.update_food(
                    food_id, basis_unit=unit, basis_amount=200, name="Edited food",
                    kj=300, protein_g=16, protein_g_per_100g=12,
                )
                updated = self.foods.get_food(food_id)
                self.assertEqual((updated["basis_unit"], updated["basis_amount"]), (unit, 200))
                self.assertEqual(updated["name"], "Edited food")
                with self.db.connection() as connection:
                    for table in tables:
                        self.assertEqual(
                            [tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")],
                            facts[table], table,
                        )

                item = self.recipes.get_recipe(recipe_id)["items"][0]
                self.assertEqual((item["amount"], item["unit"], item["basis_unit"]), (100, unit, unit))
                totals = self.recipes.calculate_nutrition(recipe_id)
                self.assertEqual(totals["normalization_unit"], unit)
                self.assertEqual(totals["total_weight_g"], 100 if unit == "g" else 0)
                self.assertEqual(totals["total_volume_ml"], 100 if unit == "ml" else 0)
                self.assertEqual(totals["kj"], 150)
                self.assertEqual(totals["per_100g_kj"], 150)
                self.assertEqual(totals["composition"].complete, unit == "g")
                new_serving = self.foods.get_intake_event(self.foods.record_food_intake(
                    food_id, amount=1, serving_id=serving_id, occurred_at=when,
                ))
                new_recipe = self.foods.get_intake_event(self.recipes.record_recipe_intake(
                    recipe_id, fraction=1, occurred_at=when,
                ))
                self.assertEqual(new_serving["unit"], "serving")
                self.assertEqual(new_serving["kj_snapshot"], 75)
                self.assertEqual(new_serving["protein_snapshot"], 4)
                self.assertEqual(new_recipe["kj_snapshot"], 150)
                if unit == "g":
                    self.assertEqual(new_serving["protein_g"], 6)
                    self.assertEqual(new_recipe["protein_g"], 12)
                else:
                    # Per-100g metadata must not become volume nutrition.
                    for event in (new_serving, new_recipe):
                        self.assertEqual(event["nutrition_complete"], 0)
                        self.assertTrue(all(event[field] is None for field in NUTRIENT_NAMES))
                self.assertEqual([self.foods.get_intake_event(event_id) for event_id in event_ids], history)

    def test_update_without_basis_unit_still_allows_basis_amount_change(self) -> None:
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                food_id = self._create_food(unit)
                self.foods.update_food(food_id, basis_amount=200, kj=300)
                self.assertEqual(self.foods.get_food(food_id)["basis_unit"], unit)
                self.assertEqual(self.foods.calculate_nutrition(food_id, 100, unit=unit)["kj"], 150)

    def test_application_save_cannot_bypass_service_invariant(self) -> None:
        context = ApplicationContext(self.root / "application")
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                draft = FoodDraft(
                    food_id=None, name="Synthetic adapter food", category="Synthetic", brand="",
                    basis_amount=100, basis_unit=unit, kj=100, protein_g=0, fat_g=0,
                    carb_g=0, fiber_g=0, default_serving=50, is_favorite=False,
                )
                created = context.save_food(draft)
                self.assertEqual(created.basis_unit, unit)
                before = self._dump(context.database)
                with self.assertRaisesRegex(ValueError, "basis_unit.*create a new food"):
                    context.save_food(replace(
                        draft, food_id=created.food_id, basis_unit="ml" if unit == "g" else "g",
                        basis_amount=200, default_serving=75,
                    ))
                self.assertEqual(self._dump(context.database), before)
                saved = context.save_food(replace(draft, food_id=created.food_id, basis_amount=200))
                self.assertEqual((saved.basis_unit, saved.basis_amount), (unit, 200))


if __name__ == "__main__":
    unittest.main()
