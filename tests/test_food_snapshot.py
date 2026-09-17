from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.db import Database
from app.energy_units import kcal_to_kj
from app.services.food_service import FoodService


class FoodSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._temporary_directory.name) / "caloriek.sqlite3")
        self.db.initialize(seed_foods=False)
        self.foods = FoodService(self.db)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_food_edit_and_delete_do_not_change_historical_snapshot(self) -> None:
        beef_id = self.foods.create_food(
            name="牛肉",
            category="肉类",
            basis_unit="g",
            kj=kcal_to_kj(250),
            protein_g=26,
            fat_g=15,
        )
        event_id = self.foods.record_food_intake(
            beef_id,
            amount=200,
            occurred_at="2026-08-01T12:00:00+08:00",
            meal_type="午餐",
        )
        original = self.foods.get_intake_event(event_id)
        self.assertEqual(original["name_snapshot"], "牛肉")
        self.assertAlmostEqual(float(original["kj_snapshot"]), kcal_to_kj(500.0))
        self.assertAlmostEqual(float(original["protein_snapshot"]), 52.0)
        self.assertEqual(self.foods.list_recent_foods()[0]["id"], beef_id)

        self.foods.update_food(beef_id, name="瘦牛肉", kj=kcal_to_kj(230), protein_g=30)
        self.foods.soft_delete_food(beef_id)
        historical = self.foods.get_intake_event(event_id)
        self.assertAlmostEqual(float(historical["kj_snapshot"]), kcal_to_kj(500.0))
        self.assertAlmostEqual(float(historical["protein_snapshot"]), 52.0)
        self.assertEqual(historical["name_snapshot"], "牛肉")
        self.assertEqual(len(self.foods.list_intake_events(local_date="2026-08-01")), 1)

    def test_serving_snapshot_scales_without_using_future_food_values(self) -> None:
        milk_id = self.foods.create_food(
            name="牛奶",
            category="蛋奶",
            basis_unit="ml",
            kj=kcal_to_kj(60),
            protein_g=3,
        )
        cup_id = self.foods.add_serving(
            milk_id,
            name="杯",
            base_amount=250,
            serving_amount=1,
            serving_unit="serving",
        )
        event_id = self.foods.record_food_intake(
            milk_id,
            amount=2,
            serving_id=cup_id,
            occurred_at="2026-08-02T08:00:00",
            meal_type="BREAKFAST",
        )
        event = self.foods.get_intake_event(event_id)
        self.assertEqual(event["amount"], 2.0)
        self.assertEqual(event["unit"], "serving")
        self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(300.0))

        self.foods.update_serving(cup_id, name="马克杯", base_amount=300)
        self.foods.soft_delete_serving(cup_id)
        self.assertIsNone(self.foods.get_serving(cup_id, include_inactive=False))
        self.foods.restore_serving(cup_id)
        self.assertEqual(self.foods.get_serving(cup_id)["name"], "马克杯")
        self.foods.update_food(milk_id, kj=kcal_to_kj(10))
        self.foods.update_intake_event(event_id, amount=1)
        scaled = self.foods.get_intake_event(event_id)
        self.assertAlmostEqual(float(scaled["kj_snapshot"]), kcal_to_kj(150.0))
        self.assertEqual(self.db.get_dirty_from_date(), "2026-08-02")

        self.foods.soft_delete_intake_event(event_id)
        self.assertIsNone(self.foods.get_intake_event(event_id, include_inactive=False))
        self.foods.restore_intake_event(event_id)
        self.assertIsNotNone(self.foods.get_intake_event(event_id, include_inactive=False))

    def test_custom_intake_is_a_self_contained_snapshot(self) -> None:
        event_id = self.foods.record_custom_intake(
            name="自制点心",
            amount=1,
            unit="份",
            kj=kcal_to_kj(320),
            protein_g=8,
            occurred_at="2026-08-03T16:00:00",
            meal_type="零食",
        )
        event = self.foods.get_intake_event(event_id)
        self.assertEqual(event["source_type"], "CUSTOM")
        self.assertIsNone(event["source_id"])
        self.assertEqual(event["meal_type"], "SNACK")
        self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(320.0))

        with self.assertRaises(ValueError):
            self.foods.create_food(
                name="无效",
                category="其它",
                basis_unit="g",
                kj=float("inf"),
            )

    def test_energy_persistence_preserves_full_float_precision(self) -> None:
        energy_kj = 1234.56789012345
        food_id = self.foods.create_food(
            name="精确能量样本",
            category="其它",
            basis_unit="g",
            kj=energy_kj,
        )
        self.assertEqual(self.foods.get_food(food_id)["kj"], energy_kj)
        event_id = self.foods.record_food_intake(food_id, amount=100)
        self.assertEqual(self.foods.get_intake_event(event_id)["kj_snapshot"], energy_kj)

        updated_energy_kj = 987.654321234567
        self.foods.update_food(food_id, kj=updated_energy_kj)
        self.assertEqual(self.foods.get_food(food_id)["kj"], updated_energy_kj)
        self.assertEqual(self.foods.get_intake_event(event_id)["kj_snapshot"], energy_kj)
        custom_id = self.foods.record_custom_intake(
            name="精确自定义摄入", amount=1, unit="份", kj=updated_energy_kj
        )
        self.assertEqual(
            self.foods.get_intake_event(custom_id)["kj_snapshot"], updated_energy_kj
        )


if __name__ == "__main__":
    unittest.main()
