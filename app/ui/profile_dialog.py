"""First-run profile collection dialog."""

from __future__ import annotations

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .context import ProfileDraft, UIContext


class ProfileDialog(QDialog):
    """Collect immutable identity data and the first effective profile revision."""

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self.setWindowTitle("开始使用 CalorieK")
        self.setModal(True)
        self.setMinimumWidth(520)
        self.setAccessibleName("首次用户资料录入")

        title = QLabel("建立你的体重与代谢基线")
        title.setObjectName("pageTitle")
        helper = QLabel("年龄会由出生日期动态计算；当前体重将保存为一条实际称重记录。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        identity_group = QGroupBox("基本资料")
        identity_form = QFormLayout(identity_group)
        identity_form.setHorizontalSpacing(18)
        identity_form.setVerticalSpacing(10)

        self.sex_combo = QComboBox()
        self.sex_combo.addItem("男性", "male")
        self.sex_combo.addItem("女性", "female")
        self.sex_combo.setAccessibleName("性别")
        identity_form.addRow("性别 *", self.sex_combo)

        self.birth_edit = QDateEdit(QDate.currentDate().addYears(-30))
        self.birth_edit.setCalendarPopup(True)
        self.birth_edit.setDisplayFormat("yyyy-MM-dd")
        self.birth_edit.setMinimumDate(QDate(1900, 1, 1))
        self.birth_edit.setMaximumDate(QDate.currentDate())
        self.birth_edit.setAccessibleName("出生日期")
        identity_form.addRow("出生日期 *", self.birth_edit)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(80.0, 250.0)
        self.height_spin.setDecimals(1)
        self.height_spin.setValue(170.0)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setAccessibleName("身高")
        identity_form.addRow("身高 *", self.height_spin)

        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(20.0, 500.0)
        self.weight_spin.setDecimals(2)
        self.weight_spin.setValue(70.0)
        self.weight_spin.setSuffix(" kg")
        self.weight_spin.setAccessibleName("当前实际体重")
        identity_form.addRow("当前体重 *", self.weight_spin)

        rhythm_group = QGroupBox("日常节律与消耗系数")
        rhythm_form = QFormLayout(rhythm_group)
        rhythm_form.setHorizontalSpacing(18)
        rhythm_form.setVerticalSpacing(10)

        self.wake_edit = QTimeEdit(QTime(7, 0))
        self.wake_edit.setDisplayFormat("HH:mm")
        self.wake_edit.setAccessibleName("通常起床时间")
        rhythm_form.addRow("通常起床时间 *", self.wake_edit)

        self.sleep_edit = QTimeEdit(QTime(23, 0))
        self.sleep_edit.setDisplayFormat("HH:mm")
        self.sleep_edit.setAccessibleName("通常睡眠时间")
        rhythm_form.addRow("通常睡眠时间 *", self.sleep_edit)

        self.activity_preset = QComboBox()
        self.activity_preset.addItem("久坐（1.10）", 1.10)
        self.activity_preset.addItem("轻度活动（1.20）", 1.20)
        self.activity_preset.addItem("中度活动（1.35）", 1.35)
        self.activity_preset.addItem("自定义", None)
        self.activity_preset.setCurrentIndex(1)
        self.activity_preset.setAccessibleName("清醒活动等级")
        rhythm_form.addRow("清醒活动等级", self.activity_preset)

        self.awake_spin = QDoubleSpinBox()
        self.awake_spin.setRange(0.50, 3.00)
        self.awake_spin.setDecimals(2)
        self.awake_spin.setSingleStep(0.05)
        self.awake_spin.setValue(1.20)
        self.awake_spin.setAccessibleName("清醒活动系数")
        rhythm_form.addRow("awake multiplier *", self.awake_spin)

        self.sleep_multiplier_spin = QDoubleSpinBox()
        self.sleep_multiplier_spin.setRange(0.50, 1.50)
        self.sleep_multiplier_spin.setDecimals(2)
        self.sleep_multiplier_spin.setSingleStep(0.05)
        self.sleep_multiplier_spin.setValue(0.95)
        self.sleep_multiplier_spin.setAccessibleName("睡眠消耗系数")
        rhythm_form.addRow("sleep multiplier *", self.sleep_multiplier_spin)

        self.note_edit = QPlainTextEdit()
        self.note_edit.setPlaceholderText("可选：记录当前阶段、设备或称重习惯")
        self.note_edit.setMaximumHeight(72)
        self.note_edit.setAccessibleName("个人备注")
        rhythm_form.addRow("备注", self.note_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("保存并开始")
        save_button.setProperty("primary", True)
        save_button.setDefault(True)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("退出")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(identity_group)
        layout.addWidget(rhythm_group)
        layout.addWidget(self.buttons)

        self.activity_preset.currentIndexChanged.connect(self._apply_activity_preset)
        self.awake_spin.valueChanged.connect(self._mark_activity_custom)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

    def _apply_activity_preset(self) -> None:
        value = self.activity_preset.currentData()
        if value is not None:
            self.awake_spin.blockSignals(True)
            self.awake_spin.setValue(float(value))
            self.awake_spin.blockSignals(False)

    def _mark_activity_custom(self) -> None:
        preset = self.activity_preset.currentData()
        if preset is not None and abs(float(preset) - self.awake_spin.value()) > 0.001:
            self.activity_preset.blockSignals(True)
            self.activity_preset.setCurrentIndex(self.activity_preset.count() - 1)
            self.activity_preset.blockSignals(False)

    def _save(self) -> None:
        if self.birth_edit.date() >= QDate.currentDate():
            QMessageBox.warning(self, "检查出生日期", "出生日期必须早于今天。")
            self.birth_edit.setFocus()
            return
        profile = ProfileDraft(
            sex=str(self.sex_combo.currentData()),
            birth_date=self.birth_edit.date().toPython(),
            height_cm=self.height_spin.value(),
            current_weight_kg=self.weight_spin.value(),
            wake_time=self.wake_edit.time().toPython(),
            sleep_time=self.sleep_edit.time().toPython(),
            awake_multiplier=self.awake_spin.value(),
            sleep_multiplier=self.sleep_multiplier_spin.value(),
            note=self.note_edit.toPlainText().strip(),
        )
        save_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save_button.setEnabled(False)
        try:
            self._context.save_initial_profile(profile)
        except Exception as exc:  # UI boundary: service supplies user-safe details.
            QMessageBox.critical(
                self,
                "资料未保存",
                f"无法保存首次资料，请检查输入后重试。\n\n{exc}",
            )
            save_button.setEnabled(True)
            return
        self.accept()


__all__ = ["ProfileDialog"]
