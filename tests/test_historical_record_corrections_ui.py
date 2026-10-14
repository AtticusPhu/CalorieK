"""CAL-38 synthetic Qt edit regressions; execution belongs to the Windows BAT."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.application import ApplicationContext
from app.energy_units import kcal_to_kj
from app.nutrition import DailyNutrition, NUTRIENT_NAMES, NutrientValues, NutritionContribution
from app.ui.context import DailyRecordDTO, ExerciseTypeDTO, IntakeSourceDTO, RecordRecalculationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QDateTime
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox
    from app.ui.daily_timeline import DailyTimelineDialog
    from app.ui.exercise_dialog import ExerciseDialog
    from app.ui.food_dialog import FoodDialog
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class HistoricalCorrectionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = Mock()
        self.context.has_profile.return_value = False
        self.context.get_energy_display_unit.return_value = "kj"
        self.context.list_intake_sources.return_value = []
        self.context.list_exercise_types.return_value = []
        self.context.update_intake.return_value = True
        self.context.update_exercise.return_value = True
        self.day = date.today() - timedelta(days=2)
        self.when = datetime.combine(self.day, time(12, 30, 17, 123456), timezone(timedelta(hours=8)))
        self.record = DailyRecordDTO(
            "intake", 4, self.when, "Saved custom", note=" exact note ", amount=0.123456789012345,
            unit="scoops", meal_type="SNACK", energy_kj=100.12345678901234, intake_source_type="CUSTOM",
            intake_nutrition=NutritionContribution(NutrientValues(2.123456789012345, None, 0, None), False),
        )

    def keep(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def save(self, dialog):
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()

    def real_context(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        context = ApplicationContext(temporary.name)
        self.addCleanup(context.database.close)
        return context

    def test_custom_form_is_snapshot_backed_with_explicit_manual_mode(self):
        dialog = self.keep(FoodDialog(self.context, record=self.record))
        self.assertEqual(dialog.custom_name.text(), self.record.name)
        self.assertEqual(dialog.custom_unit.text(), self.record.unit)
        self.assertFalse(dialog.replace_source.isEnabled())
        self.assertFalse(dialog.snapshot_fields.isEnabled())
        self.assertFalse(dialog.manual_snapshot.isChecked())
        dialog.manual_snapshot.setChecked(True)
        self.assertTrue(dialog.snapshot_fields.isEnabled())
        self.assertEqual(dialog.nutrient_editors["protein_g"].value(), 2.123456789012345)
        self.assertIsNone(dialog.nutrient_editors["fiber_g"].value())
        self.assertEqual(dialog.nutrient_editors["fat_g"].value(), 0)
        dialog.custom_name.setText("Corrected name")
        dialog.custom_unit.setText("pieces")
        dialog.amount_spin.setValue(0.25)
        dialog.snapshot_unit.setCurrentIndex(dialog.snapshot_unit.findData("kcal"))
        dialog.snapshot_energy.setValue(25.123456789)
        for name, value in zip(NUTRIENT_NAMES, (None, 0, 1.987654321098765, 3), strict=True):
            editor = dialog.nutrient_editors[name]
            editor.known.setChecked(value is not None)
            if value is not None:
                editor.spin.setValue(value)
        dialog.meal_combo.setCurrentIndex(dialog.meal_combo.findData("晚餐"))
        dialog.at_edit.setDateTime(dialog.at_edit.dateTime().addDays(1))
        dialog.note_edit.setPlainText("corrected note")
        self.save(dialog)
        record_id, edit = self.context.update_intake.call_args.args
        self.assertEqual(record_id, self.record.record_id)
        self.assertEqual((edit.custom.name, edit.custom.unit, edit.amount), ("Corrected name", "pieces", 0.25))
        self.assertAlmostEqual(edit.snapshot.energy_kj, kcal_to_kj(25.123456789))
        self.assertEqual(edit.snapshot.nutrients.as_tuple(), (None, 0, 1.987654321098765, 3))
        self.assertEqual((edit.meal_category, edit.note, edit.occurred_at.date()), ("晚餐", "corrected note", self.day + timedelta(days=1)))
        self.assertIsNone(edit.replacement)
        self.context.record_intake.assert_not_called()

    def test_recipe_fraction_stays_fraction_until_deliberate_replacement(self):
        record = replace(self.record, intake_source_type="RECIPE", intake_source_id=8, unit="recipe", amount=0.5)
        source = IntakeSourceDTO("recipe", 9, "New mixed recipe", allowed_units=("ratio_percent",), default_amount=50)
        self.context.list_intake_sources.return_value = [source]
        for amount in (0.5, 0.25):
            with self.subTest(amount=amount):
                dialog = self.keep(FoodDialog(self.context, record=record))
                self.assertEqual(dialog.amount_spin.value(), 0.5)
                self.assertIn("份", dialog.unit_combo.currentText())
                self.assertIn("0.5 份 = 50%", dialog.nutrition_label.text())
                dialog.amount_spin.setValue(amount)
                self.save(dialog)
                edit = self.context.update_intake.call_args.args[1]
                self.assertEqual(edit.amount, amount)
                self.assertIsNone(edit.replacement)
                self.assertIsNone(edit.snapshot)
        dialog = self.keep(FoodDialog(self.context, record=record))
        dialog.manual_snapshot.setChecked(True)
        self.assertFalse(dialog.replace_source.isEnabled())
        dialog.manual_snapshot.setChecked(False)
        dialog.replace_source.setChecked(True)
        self.assertFalse(dialog.manual_snapshot.isEnabled())
        self.assertIsNone(dialog._selected)
        dialog.lists[0].setCurrentRow(0)
        dialog.amount_spin.setValue(50)
        self.save(dialog)
        edit = self.context.update_intake.call_args.args[1]
        self.assertEqual((edit.replacement.source_id, edit.replacement.unit, edit.amount), (9, "ratio_percent", 50))
        self.assertIsNone(edit.snapshot)

    def test_manual_display_toggle_preserves_long_floats_and_unknowns(self):
        self.context.update_intake.return_value = False
        dialog = self.keep(FoodDialog(self.context, record=self.record))
        dialog.manual_snapshot.setChecked(True)
        for unit in ("kcal", "kj", "kcal", "kj"):
            dialog.snapshot_unit.setCurrentIndex(dialog.snapshot_unit.findData(unit))
            dialog.snapshot_energy.interpretText()
        for editor in dialog.nutrient_editors.values():
            editor.spin.interpretText()
        dialog.amount_spin.interpretText()
        self.save(dialog)
        edit = self.context.update_intake.call_args.args[1]
        self.assertEqual(edit.snapshot.energy_kj, self.record.energy_kj)
        self.assertEqual(edit.snapshot.nutrients, self.record.intake_nutrition.values)
        self.assertEqual(edit.amount, self.record.amount)
        self.assertEqual(edit.occurred_at, self.when)
        self.assertEqual(edit.note, self.record.note)
        self.assertFalse(dialog.changed)

    def test_exercise_offset_zero_and_fractional_round_trips_and_explicit_type(self):
        self.context.list_exercise_types.return_value = [ExerciseTypeDTO(19, "New catalog type", default_active_kj=999)]
        for energy in (0, 123.12345678901234):
            with self.subTest(energy=energy):
                record = DailyRecordDTO("exercise", 8, self.when, "Deleted saved type", duration_min=0.123456789012345, energy_kj=energy)
                dialog = self.keep(ExerciseDialog(self.context, record=record))
                for unit in ("kcal", "kj", "kcal"):
                    dialog.energy_unit_combo.setCurrentIndex(dialog.energy_unit_combo.findData(unit))
                    dialog.energy_spin.interpretText()
                dialog.duration_spin.interpretText()
                self.save(dialog)
                edit = self.context.update_exercise.call_args.args[1]
                self.assertEqual((edit.active_kj, edit.duration_min, edit.occurred_at), (energy, record.duration_min, self.when))
                self.assertIsNone(edit.replacement_type_id)
                replace_dialog = self.keep(ExerciseDialog(self.context, record=record))
                replace_dialog.replace_type.setChecked(True)
                self.assertIsNone(replace_dialog._selected)
                replace_dialog.lists[0].setCurrentRow(0)
                self.assertEqual(replace_dialog.energy_spin.energy_kj(), energy)
                self.save(replace_dialog)
                self.assertEqual(self.context.update_exercise.call_args.args[1].replacement_type_id, 19)

    def test_cancel_does_not_call_write_api_for_either_editor(self):
        food = self.keep(FoodDialog(self.context, record=self.record))
        food.manual_snapshot.setChecked(True)
        food.custom_name.setText("not saved")
        food.snapshot_energy.set_energy_kj(555)
        food.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        exercise_record = DailyRecordDTO("exercise", 8, self.when, "Exercise", duration_min=1)
        exercise = self.keep(ExerciseDialog(self.context, record=exercise_record))
        exercise.duration_spin.setValue(2)
        exercise.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        self.context.update_intake.assert_not_called()
        self.context.update_exercise.assert_not_called()
        self.assertFalse(food.changed)
        self.assertFalse(exercise.changed)

    def test_timeline_only_refreshes_and_notifies_after_actual_change(self):
        exercise = DailyRecordDTO("exercise", 8, self.when, "Exercise", duration_min=1)
        self.context.get_daily_records.return_value = [self.record, exercise]
        self.context.get_daily_nutrition.return_value = DailyNutrition(self.day, NutrientValues(), 1, 1)
        timeline = self.keep(DailyTimelineDialog(self.context, self.day))
        notification = Mock()
        timeline.data_changed.connect(notification)
        for row, editor_name in ((0, "FoodDialog"), (1, "ExerciseDialog")):
            for accepted, changed in ((True, False), (False, False), (False, True)):
                with self.subTest(editor=editor_name, accepted=accepted, changed=changed):
                    with patch(f"app.ui.daily_timeline.{editor_name}") as editor:
                        editor.return_value.exec.return_value = QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected
                        editor.return_value.changed = changed
                        timeline.table.cellWidget(row, 6).click()
            notification.assert_not_called()
        self.assertEqual(self.context.get_daily_records.call_count, 1)
        for row, editor_name in ((0, "FoodDialog"), (1, "ExerciseDialog")):
            with patch(f"app.ui.daily_timeline.{editor_name}") as editor:
                editor.return_value.exec.return_value = QDialog.DialogCode.Accepted
                editor.return_value.changed = True
                timeline.table.cellWidget(row, 6).click()
        self.assertEqual(notification.call_count, 2)
        self.assertEqual(self.context.get_daily_records.call_count, 3)
        self.assertEqual(self.context.get_daily_nutrition.call_count, 3)

    def test_write_failure_leaves_each_dialog_open_editable_and_retriable(self):
        exercise = DailyRecordDTO("exercise", 8, self.when, "Saved exercise", duration_min=1)
        for dialog_type, record, method, module in (
            (FoodDialog, self.record, self.context.update_intake, "food_dialog"),
            (ExerciseDialog, exercise, self.context.update_exercise, "exercise_dialog"),
        ):
            with self.subTest(editor=module):
                method.side_effect = RuntimeError("injected write failure")
                dialog = self.keep(dialog_type(self.context, record=record))
                dialog.note_edit.setPlainText("retry this")
                with patch(f"app.ui.{module}.QMessageBox.critical") as error:
                    self.save(dialog)
                self.assertIn("injected write failure", error.call_args.args[2])
                self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
                self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
                self.assertFalse(dialog.changed)
                self.assertEqual(dialog.note_edit.toPlainText(), "retry this")
                method.side_effect = None
                self.save(dialog)
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertTrue(dialog.changed)

    def test_committed_rebuild_failure_rebases_saved_exercise_fields(self):
        original = DailyRecordDTO("exercise", 8, self.when, "Original exercise", duration_min=1, energy_kj=2)
        committed = replace(original, duration_min=10, energy_kj=20, occurred_at=self.when + timedelta(days=1), name="Replacement")
        self.context.update_exercise.side_effect = RecordRecalculationError("记录已保存，重算失败", committed)
        dialog = self.keep(ExerciseDialog(self.context, record=original))
        dialog.duration_spin.setValue(10)
        with patch("app.ui.exercise_dialog.QMessageBox.critical") as error:
            self.save(dialog)
        self.assertIn("记录已保存", error.call_args.args[1])
        self.assertFalse(dialog.changed)
        self.assertEqual(dialog.energy_spin.energy_kj(), 20)
        self.assertEqual(dialog.selected_label.text(), "Replacement")
        self.context.update_exercise.side_effect = None
        self.context.update_exercise.return_value = False
        self.save(dialog)
        edit = self.context.update_exercise.call_args.args[1]
        self.assertEqual((edit.occurred_at, edit.duration_min, edit.active_kj), (committed.occurred_at, 10, 20))
        self.assertIsNone(edit.replacement_type_id)
        self.assertFalse(dialog.changed)

    def test_real_adapter_noop_and_cancel_keep_legacy_nulls_precision_and_dirty_state(self):
        context = self.real_context()
        event = context.foods.record_custom_intake(
            name=self.record.name, amount=self.record.amount, unit=self.record.unit,
            kj=self.record.energy_kj, protein_g=2.123456789012345, fat_g=0, occurred_at=self.when,
        )
        exercise = context.exercises.record_exercise(1, duration_min=0.123456789012345, active_kj=100.12345678901234, occurred_at=self.when)
        for flag in (None, 0):
            with self.subTest(flag=flag):
                stamp = f"{self.day} 12:30:17.123456+08:00"
                with context.database.transaction() as connection:
                    connection.execute("UPDATE intake_events SET nutrition_complete=?, occurred_at=?, updated_at='2000-01-01' WHERE id=?", (flag, stamp, event))
                    connection.execute("UPDATE exercise_events SET occurred_at=?, updated_at='2000-01-01' WHERE id=?", (stamp, exercise))
                context.database.clear_dirty()
                before = context.foods.get_intake_event(event)
                exercise_before = context.exercises.get_exercise_event(exercise)
                records = context.get_daily_records(self.day)
                intake_dto = next(record for record in records if record.kind == "intake")
                exercise_dto = next(record for record in records if record.kind == "exercise")
                dialog = self.keep(FoodDialog(context, record=intake_dto))
                dialog.manual_snapshot.setChecked(True)
                dialog.snapshot_unit.setCurrentIndex(dialog.snapshot_unit.findData("kcal"))
                self.save(dialog)
                self.assertFalse(dialog.changed)
                canceled = self.keep(FoodDialog(context, record=intake_dto))
                canceled.amount_spin.setValue(123)
                canceled.reject()
                exercise_dialog = self.keep(ExerciseDialog(context, record=exercise_dto))
                exercise_dialog.energy_unit_combo.setCurrentIndex(exercise_dialog.energy_unit_combo.findData("kcal"))
                self.save(exercise_dialog)
                self.assertFalse(exercise_dialog.changed)
                self.assertEqual(context.foods.get_intake_event(event), before)
                self.assertEqual(context.exercises.get_exercise_event(exercise), exercise_before)
                self.assertIsNone(context.database.get_dirty_from_date())

    def test_real_snapshot_rebuild_failure_refreshes_manual_totals_before_retry(self):
        context = self.real_context()
        event = context.foods.record_custom_intake(name="Custom", amount=2, unit="pieces", kj=200, occurred_at=self.when)
        record = context.get_daily_records(self.day)[0]
        dialog = self.keep(FoodDialog(context, record=record))
        dialog.manual_snapshot.setChecked(True)
        dialog.amount_spin.setValue(1)
        dialog.snapshot_energy.set_energy_kj(80)
        with patch.object(context.daily, "ensure_calculated", side_effect=RuntimeError("injected rebuild")), \
                patch("app.ui.food_dialog.QMessageBox.critical") as error:
            self.save(dialog)
        self.assertIn("记录已保存", error.call_args.args[1])
        self.assertFalse(dialog.changed)
        self.assertFalse(dialog.manual_snapshot.isChecked())
        self.assertEqual(dialog.snapshot_energy.energy_kj(), 80)
        self.assertEqual(dialog.amount_spin.value(), 1)
        self.assertEqual(context.foods.get_intake_event(event)["kj_snapshot"], 80)
        dialog.manual_snapshot.setChecked(True)
        dialog.nutrient_editors["protein_g"].known.setChecked(True)
        dialog.nutrient_editors["protein_g"].spin.setValue(0)
        self.save(dialog)
        after = context.foods.get_intake_event(event)
        self.assertEqual((after["amount"], after["kj_snapshot"], after["protein_g"]), (1, 80, 0))
        self.assertTrue(dialog.changed)

    def test_real_profile_date_guard_leaves_window_retriable(self):
        context = self.real_context()
        context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175, current_weight_kg=70,
            wake_time="07:00", sleep_time="23:00", effective_from=self.day,
            measured_at=datetime.combine(self.day, time(8)),
        )
        event = context.foods.record_custom_intake(name="Custom", amount=2, unit="pieces", kj=200, occurred_at=self.when)
        context.daily.ensure_calculated(date.today())
        before = context.foods.get_intake_event(event)
        record = next(row for row in context.get_daily_records(self.day) if row.kind == "intake")
        dialog = self.keep(FoodDialog(context, record=record))
        dialog.at_edit.setDateTime(QDateTime(datetime.combine(self.day - timedelta(days=1), time(12))))
        with patch("app.ui.food_dialog.QMessageBox.critical") as error:
            self.save(dialog)
        self.assertIn("最早支持的记录日期", error.call_args.args[2])
        self.assertEqual(context.foods.get_intake_event(event), before)
        self.assertIsNone(context.database.get_dirty_from_date())
        self.assertFalse(dialog.changed)
        dialog.at_edit.setDateTime(QDateTime(datetime.combine(self.day, time(12))))
        dialog.amount_spin.setValue(1)
        self.save(dialog)
        self.assertTrue(dialog.changed)
        self.assertEqual(context.foods.get_intake_event(event)["kj_snapshot"], 100)
