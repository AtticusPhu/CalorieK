"""Actual weight entry and precision-preserving correction dialog."""

from __future__ import annotations

from math import ceil, floor

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .context import DailyRecordDTO, UIContext, WeightDraft, WeightEditDraft, WeightRecalculationError


class WeightDialog(QDialog):
    def __init__(
        self, context: UIContext, parent: QWidget | None = None,
        *, record: DailyRecordDTO | None = None,
    ) -> None:
        super().__init__(parent)
        if record is not None and record.kind != "weight":
            raise ValueError("WeightDialog requires an actual weight record")
        self._context = context
        self._record = record
        self.changed = False
        self.setWindowTitle("编辑实际体重" if record else "记录实际体重")
        self.setMinimumWidth(430)
        self.setAccessibleName("体重编辑" if record else "体重录入")

        title = QLabel("编辑实际称重" if record else "记录实际称重")
        title.setObjectName("pageTitle")
        helper = QLabel("保留真实称重时间；AUTO 会按中午 12:00 自动判断 OPEN 或 CLOSE。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.at_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.at_edit.setCalendarPopup(True)
        self.at_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.at_edit.setAccessibleName("实际称重时间")
        form.addRow("称重时间 *", self.at_edit)

        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(20.0, 500.0)
        self.weight_spin.setDecimals(2)
        self.weight_spin.setSingleStep(0.05)
        self.weight_spin.setValue(70.0)
        self.weight_spin.setSuffix(" kg")
        self.weight_spin.setAccessibleName("实际体重")
        form.addRow("体重 *", self.weight_spin)

        advanced = QGroupBox("高级选项")
        advanced.setCheckable(record is None)
        if record is None:
            advanced.setChecked(False)
        advanced.setAccessibleName("体重锚点高级选项")
        advanced_form = QFormLayout(advanced)
        self.anchor_combo = QComboBox()
        self.anchor_combo.addItem("AUTO（按时间自动判断）", "AUTO")
        self.anchor_combo.addItem("OPEN（指定为开盘锚点）", "OPEN")
        self.anchor_combo.addItem("CLOSE（指定为收盘锚点）", "CLOSE")
        self.anchor_combo.setAccessibleName("OHLC 锚点")
        advanced_form.addRow("锚点", self.anchor_combo)

        self.note_edit = QPlainTextEdit()
        self.note_edit.setMaximumHeight(70)
        self.note_edit.setPlaceholderText("可选：设备、衣着或其它说明")
        self.note_edit.setAccessibleName("称重备注")
        advanced_form.addRow("备注", self.note_edit)

        if record is not None:
            # Qt's minute display cannot represent historical fractions/offsets.
            # Use a display baseline only; untouched fields are omitted from the
            # patch so the service keeps the exact original stored values.
            self.at_edit.setDateTime(QDateTime(record.occurred_at.replace(
                tzinfo=None, second=0, microsecond=0,
            )))
            self.weight_spin.setRange(
                min(20.0, floor(record.amount)), max(500.0, ceil(record.amount)),
            )
            self.weight_spin.setValue(record.amount)
            self.anchor_combo.setCurrentIndex(self.anchor_combo.findData(record.weight_anchor))
            self.note_edit.setPlainText(record.note)
            helper.setText(
                f"原始称重：{record.amount!r} kg · {record.occurred_at.isoformat()}\n"
                "未改动的字段将原样保留。修改时间时按所选分钟保存；"
                "AUTO 按中午 12:00 判断 OPEN 或 CLOSE。"
            )
            self._remember_edit_values()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存修改" if record else "保存体重")
        save.setAccessibleName("保存体重修改" if record else "保存体重")
        self.buttons.accepted.connect(lambda: self._save(advanced.isChecked()))
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addLayout(form)
        layout.addWidget(advanced)
        layout.addWidget(self.buttons)
        self.weight_spin.setFocus()
        self.weight_spin.selectAll()

    def _remember_edit_values(self) -> None:
        self._original_ui_time = self.at_edit.dateTime()
        self._original_ui_weight = self.weight_spin.value()
        self._original_ui_anchor = self.anchor_combo.currentData()
        self._original_ui_note = self.note_edit.toPlainText()

    def _save(self, advanced_enabled: bool) -> None:
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setEnabled(False)
        try:
            if self._record is None:
                self._context.record_weight(WeightDraft(
                    occurred_at=self.at_edit.dateTime().toPython(),
                    weight_kg=self.weight_spin.value(),
                    anchor=str(self.anchor_combo.currentData()) if advanced_enabled else "AUTO",  # type: ignore[arg-type]
                    note=self.note_edit.toPlainText().strip() if advanced_enabled else "",
                ))
                self.changed = True
            else:
                self.changed = self._context.update_weight(self._record.record_id, WeightEditDraft(
                    weight_kg=(self.weight_spin.value()
                               if self.weight_spin.value() != self._original_ui_weight else None),
                    occurred_at=(self.at_edit.dateTime().toPython()
                                 if self.at_edit.dateTime() != self._original_ui_time else None),
                    anchor=(self.anchor_combo.currentData()
                            if self.anchor_combo.currentData() != self._original_ui_anchor else None),
                    note=(self.note_edit.toPlainText()
                          if self.note_edit.toPlainText() != self._original_ui_note else None),
                ))
        except Exception as exc:
            if isinstance(exc, WeightRecalculationError):
                # This patch really committed. Further edits (including undoing
                # a field) must compare against that new fact, not the old form.
                self._remember_edit_values()
            QMessageBox.critical(self, "体重操作未完成", f"无法完成这次实际称重保存。\n\n{exc}")
            save_button.setEnabled(True)
            return
        self.accept()


__all__ = ["WeightDialog"]
