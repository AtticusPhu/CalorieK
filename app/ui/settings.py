"""Profile, model, color, and local data-management settings page."""

from __future__ import annotations

from datetime import datetime, time
from pathlib import Path
from typing import Callable, TypeVar

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from app.energy_units import kcal_to_kj, normalize_energy_unit

from .context import SettingsDTO, SettingsDraft, UIContext
from .numeric_input import EnergySpinBox, PreciseDoubleSpinBox


_T = TypeVar("_T")


class SettingsPage(QWidget):
    """Edit effective settings and expose safe backup/restore/export actions."""

    settings_saved = Signal(bool)
    data_restored = Signal()

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._loaded = False
        self._settings: SettingsDTO | None = None
        self.setObjectName("appRoot")
        self.setAccessibleName("设置")

        title = QLabel("设置")
        title.setObjectName("pageTitle")
        helper = QLabel("资料修改会形成新的有效配置；历史原始事件和营养快照保持不变。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        profile_group = QGroupBox("个人资料与日常节律")
        profile_form = QFormLayout(profile_group)
        profile_form.setHorizontalSpacing(20)
        profile_form.setVerticalSpacing(10)

        self.sex_combo = QComboBox()
        self.sex_combo.addItem("男性", "male")
        self.sex_combo.addItem("女性", "female")
        self.sex_combo.setAccessibleName("性别")
        profile_form.addRow("性别 *", self.sex_combo)

        self.birth_edit = QDateEdit()
        self.birth_edit.setCalendarPopup(True)
        self.birth_edit.setDisplayFormat("yyyy-MM-dd")
        self.birth_edit.setMinimumDate(QDate(1900, 1, 1))
        self.birth_edit.setMaximumDate(QDate.currentDate())
        self.birth_edit.setAccessibleName("出生日期")
        profile_form.addRow("出生日期 *", self.birth_edit)

        self.height_spin = PreciseDoubleSpinBox()
        self.height_spin.setRange(80.0, 250.0)
        self.height_spin.setDecimals(2)
        self.height_spin.setSuffix(" cm")
        self.height_spin.setAccessibleName("身高")
        profile_form.addRow("身高 *", self.height_spin)

        self.wake_edit = QTimeEdit()
        self.wake_edit.setDisplayFormat("HH:mm")
        self.wake_edit.setAccessibleName("通常起床时间")
        profile_form.addRow("通常起床时间 *", self.wake_edit)

        self.sleep_edit = QTimeEdit()
        self.sleep_edit.setDisplayFormat("HH:mm")
        self.sleep_edit.setAccessibleName("通常睡眠时间")
        profile_form.addRow("通常睡眠时间 *", self.sleep_edit)

        self.awake_spin = PreciseDoubleSpinBox()
        self.awake_spin.setRange(0.50, 3.00)
        self.awake_spin.setDecimals(2)
        self.awake_spin.setSingleStep(0.05)
        self.awake_spin.setAccessibleName("清醒活动系数")
        profile_form.addRow("awake multiplier *", self.awake_spin)

        self.sleep_multiplier_spin = PreciseDoubleSpinBox()
        self.sleep_multiplier_spin.setRange(0.50, 1.50)
        self.sleep_multiplier_spin.setDecimals(2)
        self.sleep_multiplier_spin.setSingleStep(0.05)
        self.sleep_multiplier_spin.setAccessibleName("睡眠消耗系数")
        profile_form.addRow("sleep multiplier *", self.sleep_multiplier_spin)

        visual_group = QGroupBox("显示单位与图表颜色")
        visual_form = QFormLayout(visual_group)
        visual_form.setHorizontalSpacing(20)
        visual_form.setVerticalSpacing(10)

        self.energy_unit_combo = QComboBox()
        self.energy_unit_combo.addItem("千焦 (kJ)", "kj")
        self.energy_unit_combo.addItem("千卡 (kcal)", "kcal")
        self.energy_unit_combo.setAccessibleName("能量显示与输入单位")
        visual_form.addRow("能量单位", self.energy_unit_combo)
        unit_note = QLabel("切换只改变显示与输入单位；内部计算和已保存的能量始终使用 kJ。")
        unit_note.setObjectName("muted")
        unit_note.setWordWrap(True)
        visual_form.addRow("说明", unit_note)

        self.candle_mode_combo = QComboBox()
        self.candle_mode_combo.addItem("中国习惯：上涨红、下跌绿", "china")
        self.candle_mode_combo.addItem("国际习惯：上涨绿、下跌红", "international")
        self.candle_mode_combo.setAccessibleName("K 线颜色模式")
        visual_form.addRow("K 线", self.candle_mode_combo)

        self.treemap_mode_combo = QComboBox()
        self.treemap_mode_combo.addItem("摄入红、消耗绿", "intake_red")
        self.treemap_mode_combo.addItem("摄入绿、消耗红", "intake_green")
        self.treemap_mode_combo.setAccessibleName("Treemap 颜色模式")
        visual_form.addRow("Treemap", self.treemap_mode_combo)

        model_group = QGroupBox("体重模型")
        model_form = QFormLayout(model_group)
        model_form.setHorizontalSpacing(20)
        self.energy_per_kg_spin = EnergySpinBox()
        self.energy_per_kg_spin.set_kj_range(kcal_to_kj(1000.0), kcal_to_kj(20000.0))
        self.energy_per_kg_spin.set_kj_single_step(kcal_to_kj(100.0))
        self.energy_per_kg_spin.set_unit_tail("/kg")
        self.energy_per_kg_spin.setAccessibleName("每千克能量换算")
        model_form.addRow("能量换算 *", self.energy_per_kg_spin)
        self.energy_unit_combo.currentIndexChanged.connect(self._energy_unit_changed)
        model_note = QLabel("当前版本使用可配置的简化能量模型；该值只影响重新计算的派生结果。")
        model_note.setObjectName("muted")
        model_note.setWordWrap(True)
        model_form.addRow("说明", model_note)

        data_group = QGroupBox("本地数据")
        data_layout = QVBoxLayout(data_group)
        data_layout.setSpacing(10)

        self.data_directory_edit = QLineEdit()
        self.data_directory_edit.setReadOnly(True)
        self.data_directory_edit.setAccessibleName("数据目录")
        browse_button = QPushButton("选择目录")
        browse_button.clicked.connect(self._choose_data_directory)
        directory_row = QHBoxLayout()
        directory_row.setSpacing(8)
        directory_row.addWidget(self.data_directory_edit, 1)
        directory_row.addWidget(browse_button)
        data_layout.addWidget(QLabel("数据目录"))
        data_layout.addLayout(directory_row)

        data_note = QLabel(
            "选择新目录并保存时会复制当前数据库后切换，原数据库保留作恢复副本。"
            "从 ZIP 恢复前也会校验版本、表结构、外键与 SQLite 完整性，并自动备份当前数据库；失败不会替换现有数据。"
        )
        data_note.setObjectName("muted")
        data_note.setWordWrap(True)
        data_layout.addWidget(data_note)

        self.backup_button = QPushButton("创建 ZIP 备份")
        self.restore_button = QPushButton("从备份恢复")
        self.restore_button.setProperty("danger", True)
        self.export_button = QPushButton("导出 JSON")
        self.backup_button.clicked.connect(self.create_backup)
        self.restore_button.clicked.connect(self.restore_backup)
        self.export_button.clicked.connect(self.export_data)
        data_actions = QHBoxLayout()
        data_actions.setSpacing(10)
        data_actions.addWidget(self.backup_button)
        data_actions.addWidget(self.restore_button)
        data_actions.addWidget(self.export_button)
        data_actions.addStretch(1)
        data_layout.addLayout(data_actions)

        self.status_label = QLabel("")
        self.status_label.setObjectName("muted")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        data_layout.addWidget(self.status_label)

        self.save_button = QPushButton("保存设置")
        self.save_button.setProperty("primary", True)
        self.save_button.setMinimumHeight(42)
        self.save_button.clicked.connect(self.save)
        reload_button = QPushButton("放弃更改并重新载入")
        reload_button.clicked.connect(self.load)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_row.addWidget(reload_button)
        save_row.addWidget(self.save_button)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 8, 8)
        body_layout.setSpacing(14)
        body_layout.addWidget(profile_group)
        body_layout.addWidget(visual_group)
        body_layout.addWidget(model_group)
        body_layout.addWidget(data_group)
        body_layout.addLayout(save_row)
        body_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(body)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(scroll, 1)

    def load(self) -> None:
        try:
            settings = self._context.get_settings()
        except Exception as exc:
            QMessageBox.warning(self, "设置未载入", f"无法读取当前设置。\n\n{exc}")
            return
        self._settings = settings
        self._set_combo_data(self.sex_combo, settings.sex)
        self.birth_edit.setDate(QDate(settings.birth_date.year, settings.birth_date.month, settings.birth_date.day))
        self.height_spin.setValue(settings.height_cm)
        self.wake_edit.setTime(QTime(settings.wake_time.hour, settings.wake_time.minute))
        self.sleep_edit.setTime(QTime(settings.sleep_time.hour, settings.sleep_time.minute))
        self.awake_spin.setValue(settings.awake_multiplier)
        self.sleep_multiplier_spin.setValue(settings.sleep_multiplier)
        self._set_combo_data(self.candle_mode_combo, settings.candle_color_mode)
        self._set_combo_data(self.treemap_mode_combo, settings.treemap_color_mode)
        self._set_combo_data(self.energy_unit_combo, normalize_energy_unit(settings.energy_display_unit))
        self.energy_per_kg_spin.set_display_unit(settings.energy_display_unit)
        self.energy_per_kg_spin.set_energy_kj(settings.kj_per_kg)
        self.data_directory_edit.setText(str(settings.data_directory))
        self.status_label.setText("已载入当前设置。")
        self._loaded = True

    def refresh(self) -> None:
        self.load()

    def _energy_unit_changed(self, _index: int) -> None:
        # Preview the draft preference. Persistence waits for Save; reverting
        # the combo or reloading never mutates the underlying canonical value.
        self.energy_per_kg_spin.set_display_unit(self.energy_unit_combo.currentData())

    @staticmethod
    def _schedule_value(editor: QTimeEdit, original: time | None) -> time:
        chosen = editor.time().toPython()
        if original is not None and (chosen.hour, chosen.minute) == (original.hour, original.minute):
            return original
        return chosen.replace(second=0, microsecond=0)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: object) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def save(self) -> None:
        if self.birth_edit.date() >= QDate.currentDate():
            QMessageBox.warning(self, "检查出生日期", "出生日期必须早于今天。")
            self.birth_edit.setFocus()
            return
        directory_text = self.data_directory_edit.text().strip()
        if not directory_text:
            QMessageBox.warning(self, "检查数据目录", "请选择数据目录。")
            return
        requested_directory = Path(directory_text).expanduser().resolve()
        if (
            self._settings is not None
            and requested_directory != self._settings.data_directory.expanduser().resolve()
        ):
            answer = QMessageBox.question(
                self,
                "切换数据目录",
                "保存后会把当前数据库复制到新目录并切换到该副本；"
                "原目录数据会保留。是否继续？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        draft = SettingsDraft(
            sex=str(self.sex_combo.currentData()),
            birth_date=self.birth_edit.date().toPython(),
            height_cm=self.height_spin.value(),
            wake_time=self._schedule_value(self.wake_edit, self._settings.wake_time if self._settings else None),
            sleep_time=self._schedule_value(self.sleep_edit, self._settings.sleep_time if self._settings else None),
            awake_multiplier=self.awake_spin.value(),
            sleep_multiplier=self.sleep_multiplier_spin.value(),
            candle_color_mode=str(self.candle_mode_combo.currentData()),  # type: ignore[arg-type]
            treemap_color_mode=str(self.treemap_mode_combo.currentData()),  # type: ignore[arg-type]
            kj_per_kg=self.energy_per_kg_spin.energy_kj(),
            data_directory=requested_directory,
            energy_display_unit=normalize_energy_unit(self.energy_unit_combo.currentData()),
        )
        self.save_button.setEnabled(False)
        try:
            model_changed = self._context.save_settings(draft)
        except Exception as exc:
            QMessageBox.critical(self, "设置未保存", f"无法保存设置。\n\n{exc}")
            self.save_button.setEnabled(True)
            return
        self.save_button.setEnabled(True)
        self.load()
        self.status_label.setText("设置已保存；显示单位和图表已更新。")
        self.settings_saved.emit(model_changed)

    def _choose_data_directory(self) -> None:
        current = self.data_directory_edit.text().strip()
        selected = QFileDialog.getExistingDirectory(self, "选择 CalorieK 数据目录", current)
        if selected:
            self.data_directory_edit.setText(selected)

    def create_backup(self) -> None:
        directory = self._current_data_directory()
        suggested = directory / f"caloriek_backup_{datetime.now():%Y%m%d_%H%M%S}.zip"
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "创建数据备份",
            str(suggested),
            "CalorieK ZIP 备份 (*.zip)",
        )
        if not selected:
            return
        try:
            result = self._run_busy(lambda: self._context.backup_data(Path(selected)))
        except Exception as exc:
            QMessageBox.critical(self, "备份失败", f"无法创建备份，现有数据未被修改。\n\n{exc}")
            return
        self.status_label.setText(f"备份已创建：{result}")
        QMessageBox.information(self, "备份完成", f"备份已保存到：\n{result}")

    def restore_backup(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择 CalorieK 备份",
            str(self._current_data_directory()),
            "CalorieK ZIP 备份 (*.zip)",
        )
        if not selected:
            return
        answer = QMessageBox.warning(
            self,
            "确认恢复数据",
            "恢复会用备份内容替换当前数据库。程序会先自动创建安全备份；"
            "完成后将重新载入全部页面。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._run_busy(lambda: self._context.restore_data(Path(selected)))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "恢复失败",
                f"备份未能恢复，当前数据库保持不变。\n\n{exc}",
            )
            return
        self.status_label.setText(f"已从备份恢复：{selected}")
        self.load()
        self.data_restored.emit()
        QMessageBox.information(self, "恢复完成", "数据已恢复并重新载入。")

    def export_data(self) -> None:
        directory = self._current_data_directory()
        suggested = directory / f"caloriek_export_{datetime.now():%Y%m%d_%H%M%S}.json"
        selected, _filter = QFileDialog.getSaveFileName(
            self,
            "导出全部数据",
            str(suggested),
            "JSON 数据 (*.json)",
        )
        if not selected:
            return
        try:
            result = self._run_busy(lambda: self._context.export_data(Path(selected)))
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", f"无法导出数据。\n\n{exc}")
            return
        self.status_label.setText(f"数据已导出：{result}")
        QMessageBox.information(self, "导出完成", f"JSON 已保存到：\n{result}")

    def _current_data_directory(self) -> Path:
        text = self.data_directory_edit.text().strip()
        return Path(text).expanduser() if text else Path.cwd()

    def _run_busy(self, operation: Callable[[], _T]) -> _T:
        self.setEnabled(False)
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        QApplication.processEvents()
        try:
            return operation()
        finally:
            QApplication.restoreOverrideCursor()
            self.setEnabled(True)

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._loaded:
            self.load()


SettingsWidget = SettingsPage


__all__ = ["SettingsPage", "SettingsWidget"]
