"""Synthetic CAL-12 chart/timeline/edit UI coverage; not executed by Codex."""

from datetime import date, datetime, timezone, timedelta
import os
import unittest
from unittest.mock import Mock, patch

from app.nutrition import DailyNutrition, NutrientValues, NutritionContribution
from app.ui.context import DailyRecordDTO, FoodDTO, IntakeSourceDTO, ExerciseTypeDTO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QPushButton
    from app.charts.candlestick import CandlePoint
    from app.ui.daily_timeline import DailyTimelineDialog
    from app.ui.dashboard import DashboardPage
    from app.ui.food_dialog import FoodDialog
    from app.ui.exercise_dialog import ExerciseDialog
    from app.ui.food_library import FoodEditorDialog
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class DailyTimelineUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = Mock()
        self.context.has_profile.return_value = False
        self.context.get_energy_display_unit.return_value = "kj"
        self.context.list_intake_sources.return_value = []
        self.context.list_exercise_types.return_value = []
        self.day = date(2026, 9, 1)
        self.when = datetime(2026, 9, 1, 12, 30, 17, tzinfo=timezone(timedelta(hours=8)))
        self.record = DailyRecordDTO("intake", 4, self.when, "Saved food", note="original",
                                     amount=0.0123456789, unit="recipe", meal_type="LUNCH", energy_kj=100.12345)

    def keep(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def test_candle_click_opens_correct_date_without_old_wording(self):
        page = self.keep(DashboardPage(self.context))
        chart = page.candlestick_chart
        if chart._plot is None:
            self.skipTest("PyQtGraph is not installed")
        chart.set_data([CandlePoint(self.day, 70, 71, 69, 70.5)])
        page.resize(1000, 800)
        page.show()
        self.qt_app.processEvents()
        self.assertNotIn("自然日", chart.accessibleDescription())
        self.assertEqual(chart._axis.labelText, "日期")
        event = Mock()
        event.button.return_value = Qt.MouseButton.LeftButton
        event.scenePos.return_value = chart._plot.getViewBox().mapViewToScene(QPointF(0, 70))
        with patch("app.ui.daily_timeline.DailyTimelineDialog") as timeline:
            chart._mouse_clicked(event)
        timeline.assert_called_once_with(self.context, self.day, page)
        timeline.return_value.exec.assert_called_once()
        event.accept.assert_called_once()

    def test_timeline_edit_refreshes_records_nutrition_and_emits_change(self):
        self.context.get_daily_records.return_value = [self.record, DailyRecordDTO("weight", 1, self.when, "Weight", amount=70, unit="kg")]
        self.context.get_daily_nutrition.return_value = DailyNutrition(self.day, NutrientValues(1, 2, 3, 4), 1, 0)
        timeline = self.keep(DailyTimelineDialog(self.context, self.day))
        self.assertEqual(timeline.table.item(0, 2).text(), "Saved food")
        self.assertIsInstance(timeline.table.cellWidget(0, 6), QPushButton)
        self.assertIsNone(timeline.table.cellWidget(1, 6))
        changes = Mock()
        timeline.data_changed.connect(changes)
        with patch("app.ui.daily_timeline.FoodDialog") as editor:
            editor.return_value.exec.return_value = QDialog.DialogCode.Accepted
            timeline.table.cellWidget(0, 6).click()
        editor.assert_called_once_with(self.context, timeline, record=self.record)
        self.assertEqual(self.context.get_daily_records.call_count, 2)
        self.assertEqual(self.context.get_daily_nutrition.call_count, 2)
        changes.assert_called_once()

    def test_intake_ordinary_edit_keeps_snapshot_precision_and_time_offset(self):
        dialog = self.keep(FoodDialog(self.context, record=self.record))
        self.assertFalse(dialog.replace_source.isChecked())
        self.assertFalse(dialog.unit_combo.isEnabled())
        dialog._save()
        record_id, edit = self.context.update_intake.call_args.args
        self.assertEqual(record_id, self.record.record_id)
        self.assertEqual(edit.occurred_at, self.when)
        self.assertEqual(edit.amount, self.record.amount)
        self.assertEqual(edit.note, "original")
        self.assertIsNone(edit.replacement)
        self.context.record_intake.assert_not_called()

    def test_intake_replacement_requires_deliberate_selection(self):
        source = IntakeSourceDTO("food", 8, "New source", allowed_units=("ml",), default_amount=150)
        self.context.list_intake_sources.return_value = [source]
        dialog = self.keep(FoodDialog(self.context, record=self.record))
        dialog.replace_source.setChecked(True)
        self.assertIsNone(dialog._selected)
        self.assertFalse(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
        dialog.lists[0].setCurrentRow(0)
        dialog.amount_spin.setValue(250)
        dialog._save()
        edit = self.context.update_intake.call_args.args[1]
        self.assertEqual(edit.replacement.source_id, 8)
        self.assertEqual(edit.replacement.unit, "ml")
        self.assertEqual(edit.amount, 250)
        self.assertEqual(edit.occurred_at, self.when)

    def test_exercise_edit_keeps_saved_values_not_library_defaults(self):
        record = DailyRecordDTO("exercise", 3, self.when, "Saved exercise", note="saved", duration_min=0.25, energy_kj=0)
        self.context.list_exercise_types.return_value = [ExerciseTypeDTO(1, "Other", default_active_kj=999)]
        dialog = self.keep(ExerciseDialog(self.context, record=record))
        dialog._save()
        record_id, edit = self.context.update_exercise.call_args.args
        self.assertEqual(record_id, 3)
        self.assertEqual(edit.duration_min, 0.25)
        self.assertEqual(edit.active_kj, 0)
        self.assertEqual(edit.occurred_at, self.when)
        self.assertIsNone(edit.replacement_type_id)

    def test_legacy_editor_displays_fallback_without_untouched_backfill(self):
        food = FoodDTO(1, "Legacy", "Test", basis_amount=200, protein_g=10, fiber_g=0, fat_g=2, carb_g=8,
                       legacy_nutrition_available=True,
                       basis_nutrition=NutritionContribution(NutrientValues(10, 0, 2, 8), True))
        dialog = self.keep(FoodEditorDialog(self.context, food))
        self.assertEqual(dialog.nutrient_editors["protein_g_per_100g"].value(), 5)
        self.assertEqual(dialog.nutrient_editors["fiber_g_per_100g"].value(), 0)
        dialog._save()
        draft = self.context.save_food.call_args.args[0]
        self.assertIsNone(draft.protein_g_per_100g)
        self.assertEqual(draft.protein_g, 10)
        for label in dialog.findChildren(QLabel):
            self.assertNotIn("不参与每日营养汇总", label.text())

    def test_legacy_volume_editor_keeps_basis_values_not_per100g(self):
        food = FoodDTO(1, "Legacy milk", "Test", basis_amount=200, basis_unit="ml", protein_g=6,
                       legacy_nutrition_available=True,
                       basis_nutrition=NutritionContribution(NutrientValues(6, 0, 0, 0), True))
        dialog = self.keep(FoodEditorDialog(self.context, food))
        dialog.nutrient_editors["protein_g_per_100g"].spin.setValue(7.123456789)
        dialog._save()
        draft = self.context.save_food.call_args.args[0]
        self.assertEqual(draft.protein_g, 7.123456789)
        self.assertIsNone(draft.protein_g_per_100g)
        self.assertEqual(draft.basis_unit, "ml")
