"""High-frequency actual weight entry dialog."""

from __future__ import annotations

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

from .context import UIContext, WeightDraft


class WeightDialog(QDialog):
    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self.setWindowTitle("记录实际体重")
        self.setMinimumWidth(430)
        self.setAccessibleName("体重录入")

        title = QLabel("记录实际称重")
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
        advanced.setCheckable(True)
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

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setProperty("primary", True)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存体重")
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

    def _save(self, advanced_enabled: bool) -> None:
        draft = WeightDraft(
            occurred_at=self.at_edit.dateTime().toPython(),
            weight_kg=self.weight_spin.value(),
            anchor=str(self.anchor_combo.currentData()) if advanced_enabled else "AUTO",  # type: ignore[arg-type]
            note=self.note_edit.toPlainText().strip() if advanced_enabled else "",
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setEnabled(False)
        try:
            self._context.record_weight(draft)
        except Exception as exc:
            QMessageBox.critical(self, "体重未保存", f"无法保存这次实际称重。\n\n{exc}")
            save_button.setEnabled(True)
            return
        self.accept()


__all__ = ["WeightDialog"]
