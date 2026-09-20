"""CAL-8 Qt input/display regressions; not executed by implementation tasks."""

from datetime import date, datetime, timedelta
import os
import tempfile
import unittest
from unittest.mock import Mock

from app.application import ApplicationContext
from app.nutrition import DailyNutrition, NutrientValues, NutritionContribution
from app.ui.context import DashboardDTO, FoodDTO, SettingsDTO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QDate
    from PySide6.QtWidgets import QApplication
    from app.ui.dashboard import DashboardPage
    from app.ui.food_library import FoodEditorDialog, FoodLibraryPage
    from app.ui.nutrition import DailyNutritionCard, OptionalNutrientEditor
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6/PyQtGraph is not installed")
class NutritionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_optional_editor_preserves_null_zero_and_untouched_precision(self) -> None:
        unknown = OptionalNutrientEditor("蛋白质")
        self.assertIsNone(unknown.value())
        self.assertFalse(unknown.spin.isEnabled())
        unknown.known.setChecked(True)
        self.assertEqual(unknown.value(), 0.0)
        self.assertEqual(unknown.spin.text(), "0.00 g")
        unknown.known.setChecked(False)
        self.assertIsNone(unknown.value())
        precise = OptionalNutrientEditor("蛋白质", 12.3456789012345)
        self.assertEqual(precise.spin.text(), "12.35 g")
        precise.spin.interpretText()
        self.assertEqual(precise.value(), 12.3456789012345)
        precise.known.setChecked(False)
        self.assertIsNone(precise.value())

    def test_food_editor_and_adapter_round_trip_preserve_old_and_new_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            context = ApplicationContext(directory)
            food_id = context.foods.create_food(
                name="高精度食品", category="测试", basis_unit="g", kj=321.123456789,
                protein_g=66.123456789, fat_g=77.123456789, carb_g=88.123456789, fiber_g=99.123456789,
                protein_g_per_100g=12.3456789012345, fiber_g_per_100g=0,
                fat_g_per_100g=None, carbs_g_per_100g=23.4567891234567,
            )
            original = context.foods.get_food(food_id)
            dto = next(food for food in context.list_foods() if food.food_id == food_id)
            dialog = FoodEditorDialog(context, dto)
            self.assertIsNone(dialog.nutrient_editors["fat_g_per_100g"].value())
            dialog._save()
            saved = context.foods.get_food(food_id)
            for field in ("kj", "protein_g", "fat_g", "carb_g", "fiber_g", "protein_g_per_100g",
                          "fiber_g_per_100g", "fat_g_per_100g", "carbs_g_per_100g"):
                self.assertEqual(saved[field], original[field], field)
            dialog.deleteLater()

    def test_new_food_defaults_to_unknown_and_library_distinguishes_zero(self) -> None:
        context = Mock()
        context.get_energy_display_unit.return_value = "kj"
        dialog = FoodEditorDialog(context)
        self.assertTrue(all(editor.value() is None for editor in dialog.nutrient_editors.values()))
        context.list_foods.return_value = [FoodDTO(
            food_id=1, name="营养食品", category="测试", protein_g_per_100g=12.3456789012345,
            fiber_g_per_100g=0, fat_g_per_100g=None, carbs_g_per_100g=5.4321,
            basis_nutrition=NutritionContribution(NutrientValues(12.3456789012345, 0, None, 5.4321), False),
        )]
        library = FoodLibraryPage(context)
        library.refresh()
        self.assertEqual([library.table.item(0, col).text() for col in range(5, 9)],
                         ["12.35 g", "0.00 g", "未知", "5.43 g"])
        dialog.deleteLater()
        library.deleteLater()

    def test_summary_exact_marker_unknown_values_partial_totals_and_empty_day(self) -> None:
        card = DailyNutritionCard(Mock())
        card.render(DailyNutrition(date.today(), NutrientValues(), 2, 2))
        self.assertEqual(card.indicator.text(), "部分记录无营养数据")
        self.assertTrue(all(label.text() == "未知" for label in card.values.values()))
        card.render(DailyNutrition(date.today(), NutrientValues(12.3456789, 0, 1.23456, 2.34567), 3, 1))
        self.assertEqual(card.indicator.text(), "部分记录无营养数据")
        self.assertIn("不是完整总量", card.detail.text())
        self.assertEqual(card.values["protein_g"].text(), "12.35 g")
        self.assertEqual(card.values["fiber_g"].text(), "0.00 g")
        card.render(DailyNutrition(date.today(), NutrientValues(0, 0, 0, 0), 0, 0))
        self.assertEqual(card.indicator.text(), "")
        self.assertIn("暂无饮食记录", card.detail.text())
        card.deleteLater()

    def test_history_date_and_energy_unit_changes_never_recalculate_or_write(self) -> None:
        context = Mock()
        context.get_energy_display_unit.return_value = "kj"
        context.get_settings.return_value = SettingsDTO()
        today = DailyNutrition(date.today(), NutrientValues(1.23456789, 0, 2.3456789, 3.456789), 1, 0)
        context.get_dashboard.return_value = DashboardDTO(as_of=datetime.now(), intake_kj=321.123456789, nutrition=today)
        yesterday = date.today() - timedelta(days=1)
        historical = DailyNutrition(yesterday, NutrientValues(9.87654321, None, None, None), 2, 2)
        context.get_daily_nutrition.return_value = historical
        page = DashboardPage(context)
        page.refresh()
        context.get_daily_nutrition.assert_not_called()
        page.nutrition_card.date_edit.setDate(QDate(yesterday.year, yesterday.month, yesterday.day))
        context.get_daily_nutrition.assert_called_once_with(yesterday)
        self.assertEqual(context.get_dashboard.call_count, 1)
        self.assertEqual(page.nutrition_card.indicator.text(), "部分记录无营养数据")
        before = [label.text() for label in page.nutrition_card.values.values()]
        context.get_energy_display_unit.return_value = "kcal"
        page.refresh_display_unit()
        self.assertEqual([label.text() for label in page.nutrition_card.values.values()], before)
        self.assertEqual(context.get_dashboard.call_count, 1)
        self.assertEqual(context.get_daily_nutrition.call_count, 1)
        context.save_food.assert_not_called()
        context.record_intake.assert_not_called()
        page.deleteLater()

    def test_summary_failure_clears_previous_date_values(self) -> None:
        context = Mock()
        context.get_daily_nutrition.side_effect = RuntimeError("read failed")
        card = DailyNutritionCard(context)
        card.render(DailyNutrition(date.today(), NutrientValues(1, 2, 3, 4), 1, 0))
        card.refresh()
        self.assertEqual(card.indicator.text(), "营养数据读取失败")
        self.assertTrue(all(label.text() == "—" for label in card.values.values()))
        card.deleteLater()
