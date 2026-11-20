"""Chronological active and deleted records for one day."""

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.energy_units import format_energy
from app.nutrition import NUTRIENT_NAMES, format_nutrient
from .context import DailyRecordDTO, PostCommitRecalculationError, UIContext
from .exercise_dialog import ExerciseDialog
from .food_dialog import FoodDialog
from .nutrition import NUTRIENT_CAPTIONS
from .weight_dialog import WeightDialog


class DailyTimelineDialog(QDialog):
    data_changed = Signal(bool)  # A committed change, with its recalculation-pending flag.

    def __init__(self, context: UIContext, day: date, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self.day = day
        self.setWindowTitle(f"{day:%Y-%m-%d} · 当日记录")
        self.resize(880, 560)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.empty = QLabel("当天没有记录")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(("时间", "类型", "项目", "份量 / 时长", "餐次", "能量", "编辑", "删除 / 恢复"))
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName("当日时间线")
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(self.summary)
        layout.addWidget(self.empty)
        layout.addWidget(self.table, 1)
        layout.addWidget(close)
        self.refresh()

    def refresh(self) -> None:
        try:
            records = self._context.get_daily_records(self.day, include_inactive=True)
            nutrition = self._context.get_daily_nutrition(self.day)
            energy_unit = self._context.get_energy_display_unit()
        except Exception as exc:
            self.table.setRowCount(0)
            self.summary.clear()
            self.empty.setText("暂时无法读取记录，请重新打开当日时间线。")
            self.empty.show()
            QMessageBox.warning(self, "记录未刷新", f"无法读取当日记录。\n\n{exc}")
            return
        self.summary.setText("有效饮食合计  " + " · ".join(
            f"{label} {format_nutrient(getattr(nutrition.nutrients, field))}"
            for label, field in zip(NUTRIENT_CAPTIONS, NUTRIENT_NAMES, strict=True)
        ) + (f"\n{nutrition.indication}" if nutrition.indication else ""))
        self.table.setRowCount(0)
        meals = {"BREAKFAST": "早餐", "LUNCH": "午餐", "DINNER": "晚餐", "SNACK": "零食", "OTHER": "其它"}
        for record in records:
            row = self.table.rowCount()
            self.table.insertRow(row)
            amount = (f"{record.duration_min:.2f} min" if record.kind == "exercise" else
                      f"{record.amount:.2f} {'份食谱' if record.unit == 'recipe' else record.unit}")
            values = (
                record.occurred_at.strftime("%H:%M:%S"),
                {"intake": "饮食", "exercise": "运动", "weight": "体重"}[record.kind],
                record.name if record.active else f"{record.name}（已删除）",
                amount, meals.get(record.meal_type, record.meal_type) if record.kind == "intake" else "",
                format_energy(record.energy_kj, energy_unit) if record.kind != "weight" else "",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(record.note)
                self.table.setItem(row, col, item)
            if record.active:
                edit = QPushButton("编辑")
                edit.setAccessibleName(f"编辑 {record.occurred_at:%H:%M} {record.name}")
                edit.clicked.connect(lambda _checked=False, entry=record: self.edit_record(entry))
                self.table.setCellWidget(row, 6, edit)
            action = QPushButton("删除" if record.active else "恢复")
            action.setAccessibleName(f"{action.text()} {record.occurred_at:%H:%M:%S} {record.name}")
            action.clicked.connect(lambda _checked=False, entry=record: self.change_record_active(entry))
            self.table.setCellWidget(row, 7, action)
        self.table.resizeColumnsToContents()
        self.empty.setText("当天没有记录")
        self.empty.setVisible(not records)

    def _refresh_after_change(self, *, recalculation_pending: bool = False) -> None:
        self.refresh()
        self.data_changed.emit(recalculation_pending)

    def change_record_active(self, record: DailyRecordDTO) -> None:
        active = not record.active
        action = "恢复" if active else "删除"
        detail = "恢复后将按原日期重新计入统计。" if active else "删除后可回到这一天恢复，统计将随之更新。"
        if record.kind == "weight" and not active:
            detail += "若这是最后一条有效称重，体重图表将暂时清空。"
        answer = QMessageBox.question(
            self, f"{action}记录",
            f"确定{action} {record.occurred_at:%Y-%m-%d %H:%M:%S} 的“{record.name}”吗？\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            changed = self._context.set_daily_record_active(record.kind, record.record_id, active=active)
        except PostCommitRecalculationError as exc:
            self._refresh_after_change(recalculation_pending=True)
            QMessageBox.warning(self, f"记录已{action}，图表待刷新", str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self, f"记录未{action}", str(exc))
            return
        if changed:
            self._refresh_after_change()
        else:
            # A repeated request may have come from a stale row.
            self.refresh()

    def edit_record(self, record: DailyRecordDTO) -> None:
        dialog_type = {
            "intake": FoodDialog, "exercise": ExerciseDialog, "weight": WeightDialog,
        }[record.kind]
        dialog = dialog_type(self._context, self, record=record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if not dialog.changed:
                return
            self._refresh_after_change()
