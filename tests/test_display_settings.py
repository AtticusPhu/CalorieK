"""CAL-3 settings regressions, authored for the reviewed Windows BAT workflow."""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import asdict, replace
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch

from app.application import ApplicationContext
from app.energy_units import kcal_to_kj
from app.ui.context import ProfileDraft, RecipeDraft, RecipeItemDTO, SettingsDraft
from tests.test_energy_migration import create_legacy_database


def populated_context(directory: Path) -> ApplicationContext:
    context = ApplicationContext(directory)
    context.save_initial_profile(ProfileDraft(
        sex="male", birth_date=date(1990, 1, 1), height_cm=175.12345678901234,
        current_weight_kg=70.12345678901234, wake_time=time(7), sleep_time=time(23),
        awake_multiplier=1.23456789012345, sleep_multiplier=0.98765432101234,
    ))
    context.database.set_setting("candle_color_mode", "china")
    context.database.set_setting("treemap_color_mode", "intake_red")
    # Keep the original text too, not only the equivalent parsed float.
    context.database.set_setting("kj_per_kg", "32216.812345678901234")
    context.daily.refresh_model_settings()
    food_id = context.foods.create_food(
        name="显示精度食品", category="其它", basis_unit="g", kj=418.41234567890123,
    )
    context.foods.record_food_intake(
        food_id, amount=75.12345678901234, occurred_at=datetime.combine(date.today(), time(8)),
    )
    context.save_recipe(RecipeDraft(
        recipe_id=None, name="显示精度食谱", note="",
        items=(RecipeItemDTO(food_id, "显示精度食品", 50.12345678901234, "g"),),
    ))
    exercise = context.list_exercise_shortcuts()[0]
    context.exercises.record_exercise(
        exercise.exercise_type_id, duration_min=30.12345678901234,
        active_kj=836.8123456789012, occurred_at=datetime.combine(date.today(), time(9)),
    )
    context.daily.ensure_calculated(date.today())
    context.daily.fit_calibration(date.today(), persist=True)
    return context


def draft_for(context: ApplicationContext, **changes) -> SettingsDraft:
    return replace(SettingsDraft(**asdict(context.get_settings())), **changes)


def database_rows(context: ApplicationContext, *, include_unit: bool = False) -> dict:
    """Include raw facts, revisions, caches, calibration, settings and timestamps."""
    result = {}
    with context.database.connection() as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
        for row in tables:
            name = str(row[0])
            quoted = '"' + name.replace('"', '""') + '"'
            condition = (
                " WHERE key != 'energy_display_unit'"
                if name == "app_settings" and not include_unit else ""
            )
            result[name] = tuple(tuple(record) for record in connection.execute(
                f"SELECT * FROM {quoted}{condition} ORDER BY rowid"
            ))
    return result


class DisplaySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = populated_context(self.root / "current")

    def test_missing_and_invalid_preferences_fall_back_without_repair_writes(self) -> None:
        self.assertIsNone(self.context.database.get_setting("energy_display_unit"))
        for raw in (None, "", "broken", "KCAL", "kJ", "kcal", "kj"):
            with self.subTest(raw=raw):
                if raw is not None:
                    self.context.database.set_setting("energy_display_unit", raw)
                before = database_rows(self.context, include_unit=True)
                expected = "kcal" if raw == "kcal" else "kj"
                self.assertEqual(self.context.get_energy_display_unit(), expected)
                self.assertEqual(self.context.get_settings().energy_display_unit, expected)
                self.assertEqual(database_rows(self.context, include_unit=True), before)

    def test_upgraded_v1_user_defaults_to_kj(self) -> None:
        directory = self.root / "legacy"
        create_legacy_database(directory / "caloriek.sqlite3")
        upgraded = ApplicationContext(directory)
        self.assertEqual(upgraded.database.get_schema_version(), 2)
        self.assertEqual(upgraded.get_settings().energy_display_unit, "kj")
        self.assertIsNone(upgraded.database.get_setting("energy_display_unit"))

    def test_display_only_save_preserves_all_non_display_rows_and_skips_model_work(self) -> None:
        before = database_rows(self.context)
        with (
            patch("app.application.ProfileService.update_profile") as profile_write,
            patch.object(self.context.database, "mark_dirty") as mark_dirty,
            patch.object(self.context.daily, "refresh_model_settings") as refresh_model,
            patch.object(self.context.daily, "ensure_calculated") as calculate,
        ):
            for unit in ("kcal", "kj", "kcal", "kcal", "kj"):
                with self.subTest(unit=unit):
                    self.assertFalse(self.context.save_settings(draft_for(
                        self.context, energy_display_unit=unit,
                    )))
                    self.assertEqual(self.context.get_energy_display_unit(), unit)
                    self.assertEqual(database_rows(self.context), before)
            profile_write.assert_not_called()
            mark_dirty.assert_not_called()
            refresh_model.assert_not_called()
            calculate.assert_not_called()

    def test_preference_survives_reopen_and_can_switch_back(self) -> None:
        self.context.save_settings(draft_for(self.context, energy_display_unit="kcal"))
        reopened = ApplicationContext(self.context.data_dir)
        self.assertEqual(reopened.get_settings().energy_display_unit, "kcal")
        before = database_rows(reopened)
        self.assertFalse(reopened.save_settings(draft_for(reopened, energy_display_unit="kj")))
        self.assertEqual(reopened.get_energy_display_unit(), "kj")
        self.assertEqual(database_rows(reopened), before)

    def test_invalid_requested_unit_rejected_before_any_writes(self) -> None:
        before = database_rows(self.context, include_unit=True)
        with self.assertRaises(ValueError):
            self.context.save_settings(draft_for(self.context, energy_display_unit="watts"))
        self.assertEqual(database_rows(self.context, include_unit=True), before)

    def test_food_and_recipe_detail_formatting_does_not_change_canonical_dtos_or_rows(self) -> None:
        before = database_rows(self.context)
        food_dtos = self.context.list_foods()
        recipe_dtos = self.context.list_recipes()
        for unit, label in (("kcal", "kcal"), ("kj", "kJ")):
            self.context.save_settings(draft_for(self.context, energy_display_unit=unit))
            sources = tuple(self.context.list_intake_sources("search", "显示精度食品"))
            recipes = tuple(self.context.list_intake_sources("recipes"))
            self.assertTrue(sources)
            self.assertTrue(recipes)
            for source in sources + recipes:
                self.assertIn(label, source.detail)
            self.assertEqual(self.context.list_foods(), food_dtos)
            self.assertEqual(self.context.list_recipes(), recipe_dtos)
            self.assertEqual(database_rows(self.context), before)

    def test_real_model_parameter_change_still_requests_recalculation(self) -> None:
        for changes in ({"kj_per_kg": 33000.123456789}, {"height_cm": 176.123456789}):
            with (
                self.subTest(changes=changes),
                patch.object(self.context.daily, "refresh_model_settings") as refresh_model,
                patch.object(self.context.daily, "ensure_calculated") as calculate,
            ):
                self.assertTrue(self.context.save_settings(draft_for(self.context, **changes)))
                refresh_model.assert_called_once_with()
                calculate.assert_called_once_with(date.today())


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DisplaySettingsEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        from app.ui.settings import SettingsPage

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.context = populated_context(Path(self.temporary.name))
        self.page = SettingsPage(self.context)
        self.addCleanup(self.page.close)
        self.page.load()

    def test_preview_cancel_and_save_unit_only_preserve_model_and_high_precision_values(self) -> None:
        before = database_rows(self.context)
        ratio = self.context.get_settings().kj_per_kg
        self.assertEqual(self.page.energy_per_kg_spin.suffix(), " kJ/kg")
        self.page.energy_unit_combo.setCurrentIndex(self.page.energy_unit_combo.findData("kcal"))
        self.assertEqual(self.page.energy_per_kg_spin.suffix(), " kcal/kg")
        self.assertEqual(self.page.energy_per_kg_spin.energy_kj(), ratio)
        self.assertEqual(self.context.get_energy_display_unit(), "kj")
        self.page.load()  # Discard preview.
        self.assertEqual(self.page.energy_unit_combo.currentData(), "kj")
        self.assertEqual(self.page.energy_per_kg_spin.energy_kj(), ratio)
        changes = []
        self.page.settings_saved.connect(changes.append)
        for unit in ("kcal", "kj"):
            self.page.energy_unit_combo.setCurrentIndex(self.page.energy_unit_combo.findData(unit))
            with patch.object(self.context.daily, "ensure_calculated") as calculate:
                self.page.save()
                calculate.assert_not_called()
            self.assertEqual(self.context.get_energy_display_unit(), unit)
            self.assertEqual(database_rows(self.context), before)
        self.assertEqual(changes, [False, False])

    def test_kcal_ratio_input_converts_once_before_saving(self) -> None:
        self.page.energy_unit_combo.setCurrentIndex(self.page.energy_unit_combo.findData("kcal"))
        self.page.energy_per_kg_spin.setValue(7750.123456789012)
        self.page.save()
        self.assertEqual(self.context.get_settings().kj_per_kg, kcal_to_kj(7750.123456789012))

    def test_hidden_schedule_seconds_are_preserved_by_unit_only_save(self) -> None:
        with self.context.database.transaction() as connection:
            connection.execute(
                "UPDATE profile_revisions SET wake_time = ?, sleep_time = ?",
                ("07:00:12.123456", "23:00:34.654321"),
            )
        self.page.load()
        before = database_rows(self.context)
        self.page.energy_unit_combo.setCurrentIndex(self.page.energy_unit_combo.findData("kcal"))
        with patch.object(self.context.daily, "ensure_calculated") as calculate:
            self.page.save()
            calculate.assert_not_called()
        self.assertEqual(database_rows(self.context), before)


if __name__ == "__main__":
    unittest.main()
