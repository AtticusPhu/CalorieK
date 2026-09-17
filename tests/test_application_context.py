from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

from app.application import ApplicationContext
from app.energy_units import DEFAULT_KJ_PER_KG, kcal_to_kj
from app.ui.context import (
    ExerciseTypeDraft,
    IntakeDraft,
    ProfileDraft,
    RecipeDraft,
    RecipeItemDTO,
    SettingsDraft,
)


class ApplicationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.original = self.root / "original"
        self.context = ApplicationContext(self.original)
        self.context.save_initial_profile(
            ProfileDraft(
                sex="male",
                birth_date=date(1990, 1, 1),
                height_cm=175.0,
                current_weight_kg=70.0,
                wake_time=time(7, 0),
                sleep_time=time(23, 0),
                awake_multiplier=1.2,
                sleep_multiplier=0.95,
                note="relocation test",
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_data_directory_relocation_is_complete_and_rebinds_context(self) -> None:
        target = self.root / "relocated"
        current = self.context.get_settings()
        draft = SettingsDraft(
            sex=current.sex,
            birth_date=current.birth_date,
            height_cm=current.height_cm,
            wake_time=current.wake_time,
            sleep_time=current.sleep_time,
            awake_multiplier=current.awake_multiplier,
            sleep_multiplier=current.sleep_multiplier,
            candle_color_mode="international",
            treemap_color_mode="intake_green",
            kj_per_kg=kcal_to_kj(7500.0),
            data_directory=target,
        )

        with patch("app.application.save_data_dir_preference") as save_preference:
            self.context.save_settings(draft)

        save_preference.assert_called_once_with(target.resolve())
        self.assertEqual(self.context.data_dir, target.resolve())
        self.assertTrue((target / "caloriek.sqlite3").is_file())
        self.assertTrue((self.original / "caloriek.sqlite3").is_file())
        self.assertFalse((target / ".caloriek-move.sqlite3").exists())
        self.assertFalse((target / ".caloriek-move.sqlite3-wal").exists())
        self.assertFalse((target / ".caloriek-move.sqlite3-shm").exists())

        relocated = self.context.get_settings()
        self.assertEqual(relocated.candle_color_mode, "international")
        self.assertEqual(relocated.treemap_color_mode, "intake_green")
        self.assertEqual(relocated.kj_per_kg, kcal_to_kj(7500.0))
        self.assertTrue(self.context.has_profile())
        self.assertTrue(self.context.get_dashboard().candles)

    def test_exercise_shortcuts_can_be_created_edited_and_disabled(self) -> None:
        created = self.context.save_exercise_type(
            ExerciseTypeDraft(
                exercise_type_id=None,
                name="划船机",
                default_duration_min=35.0,
                default_active_kj=kcal_to_kj(280.0),
                favorite=True,
            )
        )
        self.assertEqual(created.name, "划船机")
        self.assertTrue(created.favorite)
        self.assertTrue(created.active)

        found = self.context.list_exercise_shortcuts(query="划船")
        self.assertEqual([item.exercise_type_id for item in found], [created.exercise_type_id])

        updated = self.context.save_exercise_type(
            ExerciseTypeDraft(
                exercise_type_id=created.exercise_type_id,
                name="划船机间歇",
                default_duration_min=40.0,
                default_active_kj=kcal_to_kj(320.0),
                favorite=False,
            )
        )
        self.assertEqual(updated.default_duration_min, 40.0)
        self.assertEqual(updated.default_active_kj, kcal_to_kj(320.0))
        self.assertFalse(updated.favorite)

        self.context.set_exercise_type_active(created.exercise_type_id, False)
        self.assertFalse(
            any(
                item.exercise_type_id == created.exercise_type_id
                for item in self.context.list_exercise_shortcuts()
            )
        )
        inactive = self.context.list_exercise_shortcuts(include_inactive=True)
        disabled = next(
            item for item in inactive if item.exercise_type_id == created.exercise_type_id
        )
        self.assertFalse(disabled.active)

    def test_named_food_serving_is_exposed_and_recorded_by_id(self) -> None:
        builtin_milk = next(
            item
            for item in self.context.list_intake_sources("search", "牛奶")
            if item.name == "牛奶"
        )
        self.assertTrue(builtin_milk.servings)
        self.assertEqual(builtin_milk.servings[0].name, "默认份")
        self.assertEqual(builtin_milk.servings[0].base_amount, 250.0)

        drink_id = self.context.foods.create_food(
            name="测试杏仁饮",
            category="蛋奶",
            basis_unit="ml",
            kj=kcal_to_kj(40.0),
            protein_g=1.0,
        )
        glass_id = self.context.foods.add_serving(
            drink_id,
            name="玻璃杯",
            serving_amount=1.0,
            serving_unit="serving",
            base_amount=250.0,
        )

        source = next(
            item
            for item in self.context.list_intake_sources("search", "测试杏仁饮")
            if item.source_id == drink_id
        )
        glass = next(item for item in source.servings if item.name == "玻璃杯")
        self.assertEqual(glass.serving_id, glass_id)
        self.assertEqual(glass.basis_unit, "ml")

        self.context.record_intake(
            IntakeDraft(
                occurred_at=datetime(2026, 9, 4, 8, 0),
                source_type="food",
                source_id=drink_id,
                amount=2.0,
                unit="serving",
                serving_id=glass_id,
                meal_category="早餐",
            )
        )
        event = self.context.foods.list_intake_events(local_date="2026-09-04")[-1]
        self.assertEqual(event["amount"], 2.0)
        self.assertEqual(event["unit"], "serving")
        self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(200.0))

    def test_mixed_unit_recipe_is_not_reported_as_a_weight(self) -> None:
        solid_id = self.context.foods.create_food(
            name="测试谷物",
            category="主食",
            basis_unit="g",
            kj=kcal_to_kj(100.0),
        )
        liquid_id = self.context.foods.create_food(
            name="测试汤底",
            category="其它",
            basis_unit="ml",
            kj=kcal_to_kj(20.0),
        )
        detail = self.context.save_recipe(
            RecipeDraft(
                recipe_id=None,
                name="测试混合粥",
                note="",
                items=(
                    RecipeItemDTO(solid_id, "测试谷物", 100.0, "g"),
                    RecipeItemDTO(liquid_id, "测试汤底", 250.0, "ml"),
                ),
            )
        )
        self.assertEqual(detail.nutrition.total_weight_g, 100.0)
        self.assertEqual(detail.nutrition.total_volume_ml, 250.0)
        self.assertIsNone(detail.nutrition.normalization_unit)
        source = next(
            item
            for item in self.context.list_intake_sources("recipes")
            if item.source_id == detail.recipe_id
        )
        self.assertEqual(source.allowed_units, ("ratio_percent",))
        self.assertIn("100.00 g + 250.00 ml", source.detail)

    def test_invalid_color_modes_are_rejected_before_settings_write(self) -> None:
        current = self.context.get_settings()
        invalid = SettingsDraft(
            sex=current.sex,
            birth_date=current.birth_date,
            height_cm=current.height_cm,
            wake_time=current.wake_time,
            sleep_time=current.sleep_time,
            awake_multiplier=current.awake_multiplier,
            sleep_multiplier=current.sleep_multiplier,
            candle_color_mode="western",  # type: ignore[arg-type]
            treemap_color_mode="intake_blue",  # type: ignore[arg-type]
            kj_per_kg=current.kj_per_kg,
            data_directory=current.data_directory,
        )
        with self.assertRaises(ValueError):
            self.context.save_settings(invalid)
        unchanged = self.context.get_settings()
        self.assertEqual(unchanged.candle_color_mode, current.candle_color_mode)
        self.assertEqual(unchanged.treemap_color_mode, current.treemap_color_mode)

    def test_invalid_stored_color_modes_fall_back_to_defaults(self) -> None:
        self.context.database.set_setting("candle_color_mode", "broken")
        self.context.database.set_setting("treemap_color_mode", "broken")
        settings = self.context.get_settings()
        self.assertEqual(settings.candle_color_mode, "china")
        self.assertEqual(settings.treemap_color_mode, "intake_red")

    def test_runtime_defaults_and_dtos_use_canonical_kj(self) -> None:
        self.assertEqual(self.context.get_settings().kj_per_kg, DEFAULT_KJ_PER_KG)
        running = next(item for item in self.context.list_exercise_shortcuts() if item.name == "跑步")
        self.assertEqual(running.default_active_kj, kcal_to_kj(300.0))
        food_id = self.context.foods.create_food(
            name="精度食品", category="其它", basis_unit="g", kj=123.12345678901234
        )
        food = next(item for item in self.context.list_foods() if item.food_id == food_id)
        self.assertEqual(food.kj, 123.12345678901234)
        source = next(item for item in self.context.list_intake_sources("search", "精度食品")
                      if item.source_id == food_id)
        self.assertEqual(source.kj_reference, food.kj)
        self.assertIn("kJ", source.detail)

    def test_nonfinite_stored_energy_equivalence_falls_back(self) -> None:
        for value in ("nan", "inf", "-1", "broken"):
            self.context.database.set_setting("kj_per_kg", value)
            self.assertEqual(self.context.get_settings().kj_per_kg, DEFAULT_KJ_PER_KG)


if __name__ == "__main__":
    unittest.main()
