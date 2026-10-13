"""CAL-37 weight editor regressions; synthetic fixtures only, not run by Codex."""

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.application import ApplicationContext
from app.nutrition import DailyNutrition, NutrientValues
from app.services.weight_service import WeightService
from app.ui.context import DailyRecordDTO, WeightEditDraft, WeightRecalculationError

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QDateTime
    from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QGroupBox, QLabel
    from app.ui.weight_dialog import WeightDialog
    from app.ui.daily_timeline import DailyTimelineDialog
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class WeightEditUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.context = Mock()
        self.context.update_weight.return_value = True
        self.when = datetime(2026, 9, 1, 8, 9, 17, 123456, tzinfo=timezone(timedelta(hours=8)))
        self.record = DailyRecordDTO(
            "weight", 37, self.when, "体重", note=" original \n备注 ",
            amount=70.123456789, unit="kg", weight_anchor="CLOSE",
        )

    def keep(self, widget):
        self.addCleanup(widget.deleteLater)
        self.addCleanup(widget.close)
        return widget

    def save(self, dialog):
        dialog.buttons.button(QDialogButtonBox.StandardButton.Save).click()

    def test_edit_prefills_fields_and_untouched_save_omits_lossy_values(self):
        for anchor in ("OPEN", "CLOSE"):
            with self.subTest(anchor=anchor):
                self.context.reset_mock()
                self.context.update_weight.return_value = False
                record = replace(self.record, weight_anchor=anchor)
                dialog = self.keep(WeightDialog(self.context, record=record))
                self.assertEqual(dialog.windowTitle(), "编辑实际体重")
                self.assertEqual(dialog.accessibleName(), "体重编辑")
                self.assertEqual(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).text(), "保存修改")
                self.assertEqual(dialog.at_edit.dateTime().toString("yyyy-MM-dd HH:mm"), "2026-09-01 08:09")
                self.assertEqual(dialog.weight_spin.value(), 70.12)
                self.assertEqual(dialog.anchor_combo.currentData(), anchor)
                self.assertEqual(dialog.note_edit.toPlainText(), record.note)
                advanced = dialog.findChild(QGroupBox)
                self.assertFalse(advanced.isCheckable())
                self.assertTrue(dialog.anchor_combo.isEnabled())
                self.assertTrue(any("70.123456789" in label.text() for label in dialog.findChildren(QLabel)))
                self.save(dialog)
                self.context.update_weight.assert_called_once_with(record.record_id, WeightEditDraft())
                self.context.record_weight.assert_not_called()
                self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
                self.assertFalse(dialog.changed)

    def test_independent_field_edits_submit_only_requested_changes(self):
        changed_time = datetime(2026, 9, 2, 12, 0)
        cases = (
            ("note", lambda d: d.note_edit.setPlainText(" changed "), WeightEditDraft(note=" changed ")),
            ("clear", lambda d: d.note_edit.clear(), WeightEditDraft(note="")),
            ("anchor", lambda d: d.anchor_combo.setCurrentIndex(d.anchor_combo.findData("OPEN")), WeightEditDraft(anchor="OPEN")),
            ("weight", lambda d: d.weight_spin.setValue(69.5), WeightEditDraft(weight_kg=69.5)),
            ("time", lambda d: d.at_edit.setDateTime(QDateTime(changed_time)), WeightEditDraft(occurred_at=changed_time)),
        )
        for name, change, expected in cases:
            with self.subTest(field=name):
                self.context.reset_mock()
                dialog = self.keep(WeightDialog(self.context, record=self.record))
                change(dialog)
                self.save(dialog)
                self.context.update_weight.assert_called_once_with(self.record.record_id, expected)
                self.context.record_weight.assert_not_called()
                self.assertTrue(dialog.changed)

    def test_reverting_displayed_values_is_a_noop_and_preserves_hidden_precision(self):
        self.context.update_weight.return_value = False
        dialog = self.keep(WeightDialog(self.context, record=self.record))
        original_time = dialog.at_edit.dateTime()
        dialog.weight_spin.setValue(69.5)
        dialog.weight_spin.setValue(70.12)
        dialog.at_edit.setDateTime(original_time.addDays(1))
        dialog.at_edit.setDateTime(original_time)
        dialog.note_edit.clear()
        dialog.note_edit.setPlainText(self.record.note)
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("OPEN"))
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("CLOSE"))
        self.save(dialog)
        self.context.update_weight.assert_called_once_with(self.record.record_id, WeightEditDraft())
        self.assertFalse(dialog.changed)

    def test_valid_historical_weight_outside_new_entry_range_is_not_clamped(self):
        for weight in (10.123456789, 600.123456789, 0.004):
            with self.subTest(weight=weight):
                self.context.reset_mock()
                record = replace(self.record, amount=weight)
                dialog = self.keep(WeightDialog(self.context, record=record))
                self.assertLessEqual(dialog.weight_spin.minimum(), weight)
                self.assertGreaterEqual(dialog.weight_spin.maximum(), weight)
                self.assertAlmostEqual(dialog.weight_spin.value(), round(weight, 2))
                self.save(dialog)
                self.context.update_weight.assert_called_once_with(record.record_id, WeightEditDraft())

    def test_cancel_does_not_call_any_write_api(self):
        dialog = self.keep(WeightDialog(self.context, record=self.record))
        dialog.weight_spin.setValue(69.5)
        dialog.buttons.button(QDialogButtonBox.StandardButton.Cancel).click()
        self.context.update_weight.assert_not_called()
        self.context.record_weight.assert_not_called()
        self.assertFalse(dialog.changed)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_timeline_button_reaches_update_api_and_notifies_only_after_changed_save(self):
        second = replace(self.record, record_id=91, amount=79.5)
        self.context.get_daily_records.return_value = [self.record, second]
        self.context.get_daily_nutrition.return_value = DailyNutrition(self.when.date(), NutrientValues(), 0, 0)
        self.context.get_energy_display_unit.return_value = "kj"
        for outcome in ("changed", "noop", "cancel", "failure"):
            with self.subTest(outcome=outcome):
                self.context.reset_mock()
                self.context.update_weight.return_value = outcome == "changed"
                self.context.update_weight.side_effect = RuntimeError("injected") if outcome == "failure" else None
                timeline = self.keep(DailyTimelineDialog(self.context, self.when.date()))
                changes = Mock()
                timeline.data_changed.connect(changes)

                def interact(dialog):
                    if outcome == "cancel":
                        dialog.reject()
                    else:
                        if outcome != "noop":
                            dialog.weight_spin.setValue(69.5)
                        self.save(dialog)
                    return dialog.result()

                with patch.object(WeightDialog, "exec", new=interact), \
                        patch("app.ui.weight_dialog.QMessageBox.critical") as error:
                    timeline.table.cellWidget(1, 6).click()
                self.context.record_weight.assert_not_called()
                if outcome == "cancel":
                    self.context.update_weight.assert_not_called()
                else:
                    expected = WeightEditDraft() if outcome == "noop" else WeightEditDraft(weight_kg=69.5)
                    self.context.update_weight.assert_called_once_with(second.record_id, expected)
                self.assertEqual(error.call_count, int(outcome == "failure"))
                self.assertEqual(changes.call_count, int(outcome == "changed"))
                self.assertEqual(self.context.get_daily_records.call_count, 2 if outcome == "changed" else 1)

    def test_write_failure_leaves_form_editable_and_unaccepted(self):
        self.context.update_weight.side_effect = RuntimeError("injected write failure")
        dialog = self.keep(WeightDialog(self.context, record=self.record))
        dialog.weight_spin.setValue(69.5)
        with patch("app.ui.weight_dialog.QMessageBox.critical") as error:
            self.save(dialog)
        self.assertIn("injected write failure", error.call_args.args[2])
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
        self.assertFalse(dialog.changed)
        self.context.record_weight.assert_not_called()
        self.context.update_weight.side_effect = None
        self.save(dialog)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertTrue(dialog.changed)
        self.assertEqual(self.context.update_weight.call_count, 2)

    def test_add_mode_keeps_existing_create_path_and_advanced_behavior(self):
        for advanced in (False, True):
            with self.subTest(advanced=advanced):
                self.context.reset_mock()
                dialog = self.keep(WeightDialog(self.context))
                self.assertEqual(dialog.windowTitle(), "记录实际体重")
                self.assertEqual(dialog.accessibleName(), "体重录入")
                group = dialog.findChild(QGroupBox)
                self.assertTrue(group.isCheckable())
                self.assertFalse(group.isChecked())
                group.setChecked(advanced)
                dialog.weight_spin.setValue(69.5)
                dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("OPEN"))
                dialog.note_edit.setPlainText(" new note ")
                self.save(dialog)
                draft = self.context.record_weight.call_args.args[0]
                self.assertEqual(draft.weight_kg, 69.5)
                self.assertEqual(draft.anchor, "OPEN" if advanced else "AUTO")
                self.assertEqual(draft.note, "new note" if advanced else "")
                self.context.update_weight.assert_not_called()
                self.assertTrue(dialog.changed)

    def test_committed_rebuild_failure_rebases_form_for_further_corrections(self):
        record = replace(self.record, amount=79.5)
        self.context.update_weight.side_effect = WeightRecalculationError("称重记录已保存，但图表重算失败")
        dialog = self.keep(WeightDialog(self.context, record=record))
        dialog.weight_spin.setValue(69.5)
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("OPEN"))
        dialog.note_edit.setPlainText("changed")
        changed_time = dialog.at_edit.dateTime().addDays(1)
        dialog.at_edit.setDateTime(changed_time)
        with patch("app.ui.weight_dialog.QMessageBox.critical") as error:
            self.save(dialog)
        self.assertIn("称重记录已保存", error.call_args.args[2])
        self.assertFalse(dialog.changed)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        # Restoring the displayed opening values now IS a correction, since the
        # preceding failed rebuild happened after a committed fact update.
        dialog.weight_spin.setValue(79.5)
        dialog.anchor_combo.setCurrentIndex(dialog.anchor_combo.findData("CLOSE"))
        dialog.note_edit.setPlainText(record.note)
        dialog.at_edit.setDateTime(changed_time.addDays(-1))
        self.context.update_weight.side_effect = None
        self.save(dialog)
        self.context.update_weight.assert_called_with(record.record_id, WeightEditDraft(
            weight_kg=79.5, anchor="CLOSE", note=record.note,
            occurred_at=self.when.replace(tzinfo=None, second=0, microsecond=0),
        ))
        self.assertTrue(dialog.changed)

    def test_real_adapter_round_trip_keeps_exact_weight_timestamp_null_note_and_audit(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        context = ApplicationContext(temporary.name)
        self.addCleanup(context.database.close)
        weights = WeightService(context.database)
        record_id = weights.record_weight(self.record.amount, occurred_at=self.when, anchor="CLOSE")
        for timestamp in (
            "2026-09-01 08:09:17.123456+08:00",
            "2026-09-01T08:09:17.123456Z",
            "2026-09-01T08:09:17.1234-12:00",
        ):
            with self.subTest(timestamp=timestamp):
                with context.database.transaction() as connection:
                    connection.execute(
                        "UPDATE weight_measurements SET occurred_at=? WHERE id=?", (timestamp, record_id),
                    )
                context.database.clear_dirty()
                before = weights.get_measurement(record_id)
                record = context.get_daily_records(self.when.date())[0]
                dialog = self.keep(WeightDialog(context, record=record))
                with patch.object(context.daily, "ensure_calculated") as calculate, \
                        patch.object(context.database, "transaction", wraps=context.database.transaction) as transaction:
                    self.save(dialog)
                calculate.assert_not_called()
                transaction.assert_not_called()
                self.assertFalse(dialog.changed)
                self.assertEqual(weights.get_measurement(record_id), before)
                self.assertIsNone(context.database.get_dirty_from_date())

    def test_profile_date_guard_keeps_timeline_record_and_allows_valid_retry(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        context = ApplicationContext(temporary.name)
        self.addCleanup(context.database.close)
        today = date.today()
        first_day = today - timedelta(days=2)
        record_id = context.profile.create_profile(
            gender="male", birth_date="1990-01-01", height_cm=175,
            current_weight_kg=79.5, effective_from=first_day,
            measured_at=datetime.combine(first_day, time(8)),
        )
        context.daily.ensure_calculated(today)
        weights = WeightService(context.database)
        before = weights.get_measurement(record_id)
        timeline = self.keep(DailyTimelineDialog(context, first_day))
        changes = Mock()
        timeline.data_changed.connect(changes)
        original_cell = timeline.table.item(0, 3).text()

        def interact(dialog):
            dialog.at_edit.setDateTime(QDateTime(datetime.combine(first_day - timedelta(days=1), time(8))))
            dialog.weight_spin.setValue(69.5)
            with patch("app.ui.weight_dialog.QMessageBox.critical") as error:
                self.save(dialog)
            self.assertIn(f"最早支持的称重日期为 {first_day.isoformat()}", error.call_args.args[2])
            self.assertIn("称重记录未修改", error.call_args.args[2])
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            self.assertFalse(dialog.changed)
            self.assertTrue(dialog.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled())
            self.assertEqual(weights.get_measurement(record_id), before)
            self.assertEqual([row.record_id for row in context.get_daily_records(first_day)], [record_id])
            self.assertEqual(context.get_daily_records(first_day - timedelta(days=1)), ())
            self.assertEqual(timeline.table.rowCount(), 1)
            self.assertEqual(timeline.table.item(0, 3).text(), original_cell)
            self.assertIsNone(context.database.get_dirty_from_date())
            changes.assert_not_called()
            # Invalid edits must not rebase the form. Restore the opening date,
            # keep the weight correction, and retry in the same live editor.
            dialog.at_edit.setDateTime(QDateTime(datetime.combine(first_day, time(8))))
            self.save(dialog)
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            self.assertTrue(dialog.changed)
            return dialog.result()

        with patch.object(WeightDialog, "exec", new=interact):
            # Call the Python route directly so assertions inside interact are
            # not swallowed by Qt's exception handling for signal callbacks.
            timeline.edit_record(context.get_daily_records(first_day)[0])
        after = weights.get_measurement(record_id)
        self.assertEqual(after["weight_kg"], 69.5)
        self.assertEqual(after["occurred_at"], before["occurred_at"])
        self.assertEqual(after["created_at"], before["created_at"])
        self.assertEqual([row["id"] for row in weights.list_measurements()], [record_id])
        self.assertEqual(timeline.table.rowCount(), 1)
        self.assertIn("69.50", timeline.table.item(0, 3).text())
        changes.assert_called_once()
