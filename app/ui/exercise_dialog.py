"""Exercise active-energy entry dialog with last-value carry-forward."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .context import ExerciseDraft, ExerciseSection, ExerciseTypeDTO, UIContext
from .numeric_input import EnergySpinBox, PreciseDoubleSpinBox
from app.energy_units import energy_unit_label, format_energy, kcal_to_kj


class ExerciseDialog(QDialog):
    _SECTIONS: tuple[ExerciseSection, ...] = ("recent", "favorites", "all")

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._energy_unit = context.get_energy_display_unit()
        self._selected: ExerciseTypeDTO | None = None
        self.setWindowTitle("记录运动")
        self.setMinimumSize(560, 560)
        self.setAccessibleName("运动录入")

        title = QLabel("记录额外运动消耗")
        title.setObjectName("pageTitle")
        helper = QLabel("这里填写额外运动消耗，不包含静息基础消耗。选择项目后会带入上次值。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("运动项目来源")
        self.lists: list[QListWidget] = []
        for label in ("最近使用", "收藏项目", "所有项目"):
            widget = QListWidget()
            widget.setAlternatingRowColors(True)
            widget.currentItemChanged.connect(self._selection_changed)
            widget.itemDoubleClicked.connect(lambda _item: self.duration_spin.setFocus())
            self.lists.append(widget)
            self.tabs.addTab(widget, label)
        self.tabs.currentChanged.connect(self._tab_changed)

        form = QFormLayout()
        form.setVerticalSpacing(11)
        self.selected_label = QLabel("请先选择运动项目")
        self.selected_label.setObjectName("muted")
        form.addRow("项目 *", self.selected_label)

        self.at_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.at_edit.setCalendarPopup(True)
        self.at_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.at_edit.setAccessibleName("运动发生时间")
        form.addRow("发生时间 *", self.at_edit)

        self.duration_spin = PreciseDoubleSpinBox()
        self.duration_spin.setRange(1.0, 1440.0)
        self.duration_spin.setDecimals(0)
        self.duration_spin.setValue(30.0)
        self.duration_spin.setSuffix(" min")
        self.duration_spin.setAccessibleName("运动持续时间")
        form.addRow("持续时间 *", self.duration_spin)

        self.energy_spin = EnergySpinBox(unit=self._energy_unit)
        self.energy_spin.set_kj_range(0.0, kcal_to_kj(10000.0))
        self.energy_spin.set_energy_kj(0.0)
        self.energy_spin.setAccessibleName(
            f"额外运动消耗（{energy_unit_label(self._energy_unit)}）"
        )
        form.addRow("额外运动消耗 *", self.energy_spin)

        self.note_edit = QPlainTextEdit()
        self.note_edit.setMaximumHeight(68)
        self.note_edit.setPlaceholderText("可选备注")
        self.note_edit.setAccessibleName("运动备注")
        form.addRow("备注", self.note_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存运动")
        save.setProperty("primary", True)
        save.setEnabled(False)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addWidget(self.tabs, 1)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self._load_all_sections()

    def _load_all_sections(self) -> None:
        for section, widget in zip(self._SECTIONS, self.lists, strict=True):
            widget.clear()
            try:
                types = self._context.list_exercise_types(section)
            except Exception as exc:
                QListWidgetItem(f"无法加载：{exc}", widget)
                widget.setEnabled(False)
                continue
            if not types:
                item = QListWidgetItem("暂无项目")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                widget.addItem(item)
            for exercise in types:
                duration = exercise.last_duration_min or exercise.default_duration_min
                kj = exercise.last_active_kj
                if kj is None:
                    kj = exercise.default_active_kj
                item = QListWidgetItem(
                    f"{exercise.name}    {duration:.0f} min / {format_energy(kj, self._energy_unit)}"
                )
                item.setToolTip(item.text())
                item.setData(Qt.ItemDataRole.UserRole, exercise)
                widget.addItem(item)
        current = self.lists[self.tabs.currentIndex()]
        if current.count() and current.item(0).flags() != Qt.ItemFlag.NoItemFlags:
            current.setCurrentRow(0)

    def _tab_changed(self, index: int) -> None:
        widget = self.lists[index]
        if widget.currentItem() is None and widget.count():
            widget.setCurrentRow(0)
        self._selection_changed(widget.currentItem())

    def _selection_changed(self, item: QListWidgetItem | None, *_args: object) -> None:
        if item is None:
            self._selected = None
        else:
            self._selected = cast(ExerciseTypeDTO | None, item.data(Qt.ItemDataRole.UserRole))
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(self._selected is not None)
        if self._selected is None:
            self.selected_label.setText("请先选择运动项目")
            return
        exercise = self._selected
        self.selected_label.setText(exercise.name)
        self.duration_spin.setValue(exercise.last_duration_min or exercise.default_duration_min)
        kj = exercise.last_active_kj
        self.energy_spin.set_energy_kj(exercise.default_active_kj if kj is None else kj)

    def _save(self) -> None:
        if self._selected is None:
            QMessageBox.warning(self, "请选择项目", "请先选择一个运动项目。")
            return
        if self.energy_spin.energy_kj() <= 0:
            QMessageBox.warning(
                self,
                "检查消耗",
                f"额外运动消耗必须大于 0 {energy_unit_label(self._energy_unit)}。",
            )
            self.energy_spin.setFocus()
            return
        draft = ExerciseDraft(
            occurred_at=self.at_edit.dateTime().toPython(),
            exercise_type_id=self._selected.exercise_type_id,
            duration_min=self.duration_spin.value(),
            active_kj=self.energy_spin.energy_kj(),
            note=self.note_edit.toPlainText().strip(),
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(False)
        try:
            self._context.record_exercise(draft)
        except Exception as exc:
            QMessageBox.critical(self, "运动未保存", f"无法保存这次运动。\n\n{exc}")
            save.setEnabled(True)
            return
        self.accept()


__all__ = ["ExerciseDialog"]
