"""Chronological saved facts for a K-line date, with explicit corrections."""

from datetime import date

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QLabel, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from app.energy_units import format_energy
from app.nutrition import NUTRIENT_NAMES, format_nutrient
from .context import DailyRecordDTO, UIContext
from .exercise_dialog import ExerciseDialog
from .food_dialog import FoodDialog
from .nutrition import NUTRIENT_CAPTIONS


class DailyTimelineDialog(QDialog):
    data_changed = Signal()

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
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(("时间", "类型", "项目", "份量 / 时长", "餐次", "能量", "操作"))
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
            records = self._context.get_daily_records(self.day)
            nutrition = self._context.get_daily_nutrition(self.day)
            energy_unit = self._context.get_energy_display_unit()
        except Exception as exc:
            QMessageBox.warning(self, "记录未刷新", f"无法读取当日记录。\n\n{exc}")
            return
        self.summary.setText(" · ".join(
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
                record.name, amount, meals.get(record.meal_type, record.meal_type) if record.kind == "intake" else "",
                format_energy(record.energy_kj, energy_unit) if record.kind != "weight" else "",
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(record.note)
                self.table.setItem(row, col, item)
            if record.kind != "weight":
                edit = QPushButton("编辑")
                edit.setAccessibleName(f"编辑 {record.occurred_at:%H:%M} {record.name}")
                edit.clicked.connect(lambda _checked=False, entry=record: self.edit_record(entry))
                self.table.setCellWidget(row, 6, edit)
        self.table.resizeColumnsToContents()
        self.empty.setVisible(not records)

    def edit_record(self, record: DailyRecordDTO) -> None:
        dialog_type = FoodDialog if record.kind == "intake" else ExerciseDialog
        dialog = dialog_type(self._context, self, record=record)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()
