"""Presentation-only energy unit regressions; run via the reviewed Windows BAT."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from typing import cast
from unittest.mock import patch

from app.energy_units import EnergyUnit, energy_to_display, energy_unit_label, format_energy
from app.ui.context import (
    ExerciseTypeDTO,
    FoodDTO,
    IntakeSourceDTO,
    NutritionDTO,
    RecipeDetailDTO,
    RecipeItemDTO,
    RecipeSummaryDTO,
    UIContext,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:
    HAS_QT = False


class _MemoryContext:
    """Canonical immutable records with observable writes and no database."""

    def __init__(self, unit: EnergyUnit = "kj") -> None:
        self.unit = unit
        self.food = FoodDTO(
            food_id=1,
            name="精确饮品",
            category="饮品",
            basis_unit="ml",
            kj=418.41234567890123,
            protein_g=3.12345678901234,
            fat_g=1.98765432101234,
            carb_g=5.12345678901234,
            fiber_g=0.12345678901234,
        )
        self.exercise = ExerciseTypeDTO(
            exercise_type_id=1,
            name="步行",
            default_active_kj=836.8123456789012,
            last_duration_min=45.0,
            last_active_kj=1255.2123456789012,
        )
        self.nutrition = NutritionDTO(
            total_volume_ml=200.0,
            normalization_unit="ml",
            kj=836.8246913578025,
            per_100g_kj=418.41234567890123,
            protein_g=6.0,
            per_100g_protein_g=3.0,
        )
        self.recipe = RecipeDetailDTO(
            recipe_id=1,
            name="饮品食谱",
            items=(RecipeItemDTO(1, self.food.name, 200.0, "ml"),),
            nutrition=self.nutrition,
        )
        self.writes: list[object] = []

    def get_energy_display_unit(self):
        return self.unit

    def list_foods(self, *_args, **_kwargs):
        return (self.food,)

    def list_intake_sources(self, *_args, **_kwargs):
        return (
            IntakeSourceDTO(
                source_type="food",
                source_id=1,
                name=self.food.name,
                detail=f"每 100ml · {format_energy(self.food.kj, self.unit)}",
                kj_reference=self.food.kj,
                protein_g=self.food.protein_g,
                allowed_units=("ml",),
            ),
        )

    def list_exercise_shortcuts(self, *_args, **_kwargs):
        return (self.exercise,)

    def list_exercise_types(self, *_args, **_kwargs):
        return (self.exercise,)

    def list_recipes(self, *_args, **_kwargs):
        return (
            RecipeSummaryDTO(
                recipe_id=1,
                name=self.recipe.name,
                total_volume_ml=200.0,
                normalization_unit="ml",
                total_kj=self.nutrition.kj,
            ),
        )

    def get_recipe(self, _recipe_id):
        return self.recipe

    def preview_recipe(self, _items):
        return self.nutrition

    def save_food(self, draft):
        self.writes.append(draft)
        return replace(self.food, kj=draft.kj)

    def save_exercise_type(self, draft):
        self.writes.append(draft)
        return replace(self.exercise, default_active_kj=draft.default_active_kj)

    def record_exercise(self, draft):
        self.writes.append(draft)

    def save_recipe(self, draft):
        self.writes.append(draft)
        return replace(self.recipe, name=draft.name, note=draft.note, items=draft.items)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class EnergyViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_food_editor_noop_saves_exact_canonical_energy_and_macros_in_both_units(self) -> None:
        from app.ui.food_library import FoodEditorDialog

        for unit in ("kj", "kcal"):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = FoodEditorDialog(cast(UIContext, context), context.food)
                self.assertEqual(dialog.energy_spin.suffix(), f" {energy_unit_label(unit)}")
                self.assertIn(energy_unit_label(unit), dialog.energy_spin.accessibleName())
                self.assertEqual(dialog.energy_spin.value(), energy_to_display(context.food.kj, unit))
                self.assertEqual(context.writes, [])
                dialog._save()
                draft = context.writes[-1]
                self.assertEqual(draft.kj, context.food.kj)
                for field in ("protein_g", "fat_g", "carb_g", "fiber_g"):
                    self.assertEqual(getattr(draft, field), getattr(context.food, field))
                dialog.close()

    def test_food_editor_selected_unit_input_becomes_canonical_kj(self) -> None:
        from app.ui.food_library import FoodEditorDialog

        for unit, expected_kj in (("kj", 100.0), ("kcal", 418.4)):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = FoodEditorDialog(cast(UIContext, context), context.food)
                dialog.energy_spin.setValue(100.0)
                dialog._save()
                self.assertAlmostEqual(context.writes[-1].kj, expected_kj)
                dialog.close()

    def test_exercise_editors_preserve_default_and_last_canonical_energy(self) -> None:
        from app.ui.exercise_dialog import ExerciseDialog
        from app.ui.exercise_library import ExerciseTypeEditorDialog

        for unit in ("kj", "kcal"):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                editor = ExerciseTypeEditorDialog(cast(UIContext, context), context.exercise)
                self.assertEqual(editor.energy_spin.suffix(), f" {energy_unit_label(unit)}")
                editor._save()
                self.assertEqual(context.writes[-1].default_active_kj, context.exercise.default_active_kj)
                editor.close()
                dialog = ExerciseDialog(cast(UIContext, context))
                self.assertEqual(dialog.energy_spin.energy_kj(), context.exercise.last_active_kj)
                self.assertIn(format_energy(context.exercise.last_active_kj, unit), dialog.lists[0].item(0).text())
                self.assertEqual(dialog.lists[0].item(0).toolTip(), dialog.lists[0].item(0).text())
                dialog._save()
                self.assertEqual(context.writes[-1].active_kj, context.exercise.last_active_kj)
                dialog.close()

    def test_food_intake_reference_and_tooltip_follow_selected_unit(self) -> None:
        from app.ui.food_dialog import FoodDialog

        for unit in ("kj", "kcal"):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = FoodDialog(cast(UIContext, context))
                expected = format_energy(context.food.kj, unit)
                self.assertIn(expected, dialog.nutrition_label.text())
                self.assertIn(expected, dialog.lists[0].item(0).toolTip())
                self.assertIn("蛋白 3.12 g", dialog.nutrition_label.text())
                self.assertEqual(context.writes, [])
                dialog.close()

    def test_exercise_input_conversion_and_error_message_follow_selected_unit(self) -> None:
        from app.ui.exercise_dialog import ExerciseDialog

        for unit, expected_kj in (("kj", 100.0), ("kcal", 418.4)):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = ExerciseDialog(cast(UIContext, context))
                dialog.energy_spin.setValue(0.0)
                with patch("app.ui.exercise_dialog.QMessageBox.warning") as warning:
                    dialog._save()
                self.assertIn(f"0 {energy_unit_label(unit)}", warning.call_args.args[2])
                self.assertEqual(context.writes, [])
                dialog.energy_spin.setValue(100.0)
                dialog._save()
                self.assertAlmostEqual(context.writes[-1].active_kj, expected_kj)
                dialog.close()

    def test_exercise_type_input_conversion_uses_selected_unit(self) -> None:
        from app.ui.exercise_library import ExerciseTypeEditorDialog

        for unit, expected_kj in (("kj", 100.0), ("kcal", 418.4)):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = ExerciseTypeEditorDialog(cast(UIContext, context), context.exercise)
                dialog.energy_spin.setValue(100.0)
                dialog._save()
                self.assertAlmostEqual(context.writes[-1].default_active_kj, expected_kj)
                self.assertEqual(
                    context.writes[-1].default_duration_min,
                    context.exercise.default_duration_min,
                )
                dialog.close()

    def test_dialogs_reopened_after_unit_switch_use_current_preference_without_writes(self) -> None:
        from app.ui.exercise_dialog import ExerciseDialog
        from app.ui.exercise_library import ExerciseTypeEditorDialog
        from app.ui.food_dialog import FoodDialog
        from app.ui.food_library import FoodEditorDialog
        from app.ui.recipe_editor import RecipeEditorDialog

        context = _MemoryContext()
        originals = (context.food, context.exercise, context.recipe, context.nutrition)
        # Dialogs are modal and constructed afresh by the pages. Unit changes in
        # settings must be picked up on every later open, including a switch back.
        for unit in ("kj", "kcal", "kj"):
            with self.subTest(unit=unit):
                context.unit = unit
                food = FoodEditorDialog(cast(UIContext, context), context.food)
                intake = FoodDialog(cast(UIContext, context))
                exercise = ExerciseDialog(cast(UIContext, context))
                exercise_type = ExerciseTypeEditorDialog(cast(UIContext, context), context.exercise)
                recipe = RecipeEditorDialog(cast(UIContext, context), context.recipe)
                for editor in (food, exercise, exercise_type):
                    self.assertEqual(editor.energy_spin.display_unit, unit)
                    self.assertIn(energy_unit_label(unit), editor.energy_spin.accessibleName())
                self.assertEqual(food.energy_spin.energy_kj(), context.food.kj)
                self.assertEqual(exercise.energy_spin.energy_kj(), context.exercise.last_active_kj)
                self.assertEqual(
                    exercise_type.energy_spin.energy_kj(), context.exercise.default_active_kj
                )
                self.assertIn(format_energy(context.food.kj, unit), intake.nutrition_label.text())
                self.assertEqual(
                    recipe.nutrition._labels["energy"].text(),
                    format_energy(context.nutrition.kj, unit),
                )
                for dialog in (food, intake, exercise, exercise_type, recipe):
                    dialog.reject()
                    dialog.close()
        self.assertEqual(context.writes, [])
        self.assertEqual(originals, (context.food, context.exercise, context.recipe, context.nutrition))

    def test_library_refresh_changes_headers_values_and_recipe_accessibility_without_writes(self) -> None:
        from app.ui.exercise_library import ExerciseLibraryPage
        from app.ui.food_library import FoodLibraryPage
        from app.ui.recipe_editor import RecipeLibraryPage

        context = _MemoryContext()
        records = (context.food, context.exercise, context.recipe)
        food_page = FoodLibraryPage(cast(UIContext, context))
        exercise_page = ExerciseLibraryPage(cast(UIContext, context))
        recipe_page = RecipeLibraryPage(cast(UIContext, context))
        for unit in ("kj", "kcal", "kj"):
            with self.subTest(unit=unit):
                context.unit = unit
                food_page.refresh()
                exercise_page.refresh()
                recipe_page.refresh()
                self.assertIn(energy_unit_label(unit), food_page.table.horizontalHeaderItem(4).text())
                self.assertEqual(food_page.table.item(0, 4).text(), f"{energy_to_display(context.food.kj, unit):.2f}")
                self.assertIn(energy_unit_label(unit), exercise_page.table.horizontalHeaderItem(4).text())
                self.assertEqual(exercise_page.table.item(0, 4).text(), format_energy(context.exercise.last_active_kj, unit))
                self.assertIn(format_energy(context.nutrition.kj, unit), recipe_page.recipe_list.item(0).text())
                self.assertEqual(recipe_page.nutrition._labels["energy"].text(), format_energy(context.nutrition.kj, unit))
                self.assertIn(format_energy(context.nutrition.kj, unit), recipe_page.nutrition.accessibleDescription())
                self.assertEqual(recipe_page.nutrition._captions["per100"].text(), "每 100ml 能量")
                self.assertEqual(recipe_page.nutrition._labels["per100"].text(), format_energy(context.nutrition.per_100g_kj, unit))
        self.assertEqual(context.writes, [])
        self.assertEqual(records, (context.food, context.exercise, context.recipe))
        for page in (food_page, exercise_page, recipe_page):
            page.close()

    def test_loaded_library_show_event_refreshes_changed_unit(self) -> None:
        from PySide6.QtGui import QShowEvent

        from app.ui.exercise_library import ExerciseLibraryPage
        from app.ui.food_library import FoodLibraryPage
        from app.ui.recipe_editor import RecipeLibraryPage

        context = _MemoryContext()
        for page_type in (FoodLibraryPage, ExerciseLibraryPage, RecipeLibraryPage):
            with self.subTest(page=page_type.__name__):
                context.unit = "kj"
                page = page_type(cast(UIContext, context))
                page.refresh()
                with patch.object(page, "refresh", wraps=page.refresh) as refresh:
                    page.showEvent(QShowEvent())
                    refresh.assert_not_called()
                    context.unit = "kcal"
                    page.showEvent(QShowEvent())
                    self.assertEqual(refresh.call_count, 1)
                    self.assertEqual(page._energy_unit, "kcal")
                    context.unit = "kj"
                    page.showEvent(QShowEvent())
                    self.assertEqual(refresh.call_count, 2)
                    self.assertEqual(page._energy_unit, "kj")
                page.close()
        self.assertEqual(context.writes, [])

    def test_recipe_preview_and_mixed_recipe_summary_keep_units_and_dimensions(self) -> None:
        from app.ui.recipe_editor import RecipeEditorDialog

        for unit in ("kj", "kcal"):
            with self.subTest(unit=unit):
                context = _MemoryContext(unit)
                dialog = RecipeEditorDialog(cast(UIContext, context), context.recipe)
                self.assertEqual(dialog.nutrition._labels["energy"].text(), format_energy(context.nutrition.kj, unit))
                mixed = replace(context.nutrition, normalization_unit=None, total_weight_g=50.0)
                dialog.nutrition.set_nutrition(mixed)
                self.assertEqual(dialog.nutrition._labels["per100"].text(), "不适用于 g/ml 混合食谱")
                self.assertEqual(dialog.nutrition._labels["weight"].text(), "50.00 g + 200.00 ml")
                self.assertEqual(dialog.nutrition._labels["energy"].text(), format_energy(mixed.kj, unit))
                self.assertEqual(context.writes, [])
                dialog.close()

    def test_recipe_noop_ingredient_edit_and_save_keep_full_precision_in_both_units(self) -> None:
        from app.ui.recipe_editor import RecipeEditorDialog

        context = _MemoryContext()
        item = replace(context.recipe.items[0], amount_g=200.12345678901235)
        context.recipe = replace(context.recipe, items=(item,))
        original_nutrition = context.nutrition
        for unit in ("kj", "kcal", "kj"):
            with self.subTest(unit=unit):
                context.unit = unit
                dialog = RecipeEditorDialog(cast(UIContext, context), context.recipe)
                dialog.ingredients.selectRow(0)
                self.assertEqual(dialog.amount_spin.value(), item.amount_g)
                dialog._update_selected_item()
                dialog._save()
                self.assertEqual(context.writes[-1].items, (item,))
                self.assertEqual(context.nutrition, original_nutrition)
                self.assertEqual(dialog.saved_recipe.nutrition, original_nutrition)
                dialog.close()


if __name__ == "__main__":
    unittest.main()
