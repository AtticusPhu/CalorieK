"""Editable exercise shortcut library for fast Active Calories entry."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context import ExerciseTypeDTO, ExerciseTypeDraft, UIContext


class ExerciseTypeEditorDialog(QDialog):
    """Create or edit one reusable exercise shortcut."""

    def __init__(
        self,
        context: UIContext,
        exercise: ExerciseTypeDTO | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._exercise = exercise
        self.saved_exercise: ExerciseTypeDTO | None = None
        self.setWindowTitle("编辑运动项目" if exercise else "新建运动项目")
        self.setMinimumWidth(500)
        self.setAccessibleName(self.windowTitle())

        title = QLabel(self.windowTitle())
        title.setObjectName("pageTitle")
        helper = QLabel(
            "这里只维护快速录入的默认值；每次实际运动仍可单独修改时长和 Active Calories。"
        )
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit(exercise.name if exercise else "")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName("运动项目名称")
        form.addRow("项目名称 *", self.name_edit)

        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(1.0, 1440.0)
        self.duration_spin.setDecimals(0)
        self.duration_spin.setSuffix(" min")
        self.duration_spin.setValue(
            exercise.default_duration_min if exercise else 30.0
        )
        self.duration_spin.setAccessibleName("默认运动时长")
        form.addRow("默认时长 *", self.duration_spin)

        self.kcal_spin = QDoubleSpinBox()
        self.kcal_spin.setRange(0.0, 10000.0)
        self.kcal_spin.setDecimals(0)
        self.kcal_spin.setSuffix(" kcal")
        self.kcal_spin.setValue(
            exercise.default_active_kcal if exercise else 0.0
        )
        self.kcal_spin.setAccessibleName("默认 Active Calories")
        form.addRow("默认 Active Calories", self.kcal_spin)

        self.favorite_check = QCheckBox("加入收藏，运动录入时优先显示")
        self.favorite_check.setChecked(exercise.favorite if exercise else False)
        form.addRow("收藏", self.favorite_check)

        note = QLabel(
            "Active Calories 是额外运动消耗，不包含程序已经计算的基础/日常消耗。"
        )
        note.setObjectName("muted")
        note.setWordWrap(True)
        form.addRow("说明", note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存运动项目")
        save.setProperty("primary", True)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addLayout(form)
        layout.addWidget(self.buttons)
        self.name_edit.setFocus()

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "检查项目名称", "请输入运动项目名称。")
            self.name_edit.setFocus()
            return

        draft = ExerciseTypeDraft(
            exercise_type_id=(
                self._exercise.exercise_type_id if self._exercise else None
            ),
            name=name,
            default_duration_min=self.duration_spin.value(),
            default_active_kcal=self.kcal_spin.value(),
            favorite=self.favorite_check.isChecked(),
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(False)
        try:
            self.saved_exercise = self._context.save_exercise_type(draft)
        except Exception as exc:
            QMessageBox.critical(
                self,
                "运动项目未保存",
                f"无法保存运动项目，请检查输入。\n\n{exc}",
            )
            save.setEnabled(True)
            return
        self.accept()


class ExerciseLibraryPage(QWidget):
    """Search, create, edit, favorite, soft-delete, and restore shortcuts."""

    data_changed = Signal()

    _COLUMNS = (
        "项目",
        "默认时长",
        "默认 Active Calories",
        "最近时长",
        "最近 kcal",
        "收藏",
        "状态",
    )

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._loaded = False
        self.setObjectName("appRoot")
        self.setAccessibleName("运动项目库")

        title = QLabel("运动项目")
        title.setObjectName("pageTitle")
        helper = QLabel(
            "维护运动快捷项目及默认值。历史运动事件保存名称、时长和 kcal 快照，不受这里后续修改影响。"
        )
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索运动项目")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索运动项目")
        self.include_inactive = QCheckBox("显示已停用")
        self.include_inactive.setAccessibleName("显示已停用运动项目")

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.include_inactive)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAccessibleName("运动项目列表")
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())

        self.empty_label = QLabel("没有符合条件的运动项目。")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(False)

        self.new_button = QPushButton("新建运动项目")
        self.new_button.setProperty("primary", True)
        self.edit_button = QPushButton("编辑")
        self.toggle_button = QPushButton("停用")
        refresh_button = QPushButton("刷新")
        self.new_button.clicked.connect(self.create_exercise)
        self.edit_button.clicked.connect(self.edit_selected)
        self.toggle_button.clicked.connect(self.toggle_selected_active)
        refresh_button.clicked.connect(self.refresh)

        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addWidget(self.new_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.toggle_button)
        actions.addStretch(1)
        actions.addWidget(refresh_button)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self.refresh)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())
        self.include_inactive.toggled.connect(self.refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addLayout(search_row)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty_label)
        layout.addLayout(actions)
        self._update_actions()

    def refresh(self) -> None:
        try:
            exercises = self._context.list_exercise_shortcuts(
                query=self.search_edit.text().strip(),
                include_inactive=self.include_inactive.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(
                self, "运动项目未刷新", f"无法读取运动项目。\n\n{exc}"
            )
            return

        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for exercise in exercises:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                exercise.name,
                f"{exercise.default_duration_min:.0f} min",
                f"{exercise.default_active_kcal:.0f} kcal",
                (
                    f"{exercise.last_duration_min:.0f} min"
                    if exercise.last_duration_min is not None
                    else "—"
                ),
                (
                    f"{exercise.last_active_kcal:.0f} kcal"
                    if exercise.last_active_kcal is not None
                    else "—"
                ),
                "是" if exercise.favorite else "否",
                "正常" if exercise.active else "已停用",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, exercise)
                if column in (1, 2, 3, 4):
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.empty_label.setVisible(not exercises)
        self.table.setVisible(bool(exercises))
        self._loaded = True
        self._update_actions()

    def selected_exercise(self) -> ExerciseTypeDTO | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return (
            None
            if item is None
            else cast(ExerciseTypeDTO | None, item.data(Qt.ItemDataRole.UserRole))
        )

    def _update_actions(self) -> None:
        exercise = self.selected_exercise()
        self.edit_button.setEnabled(exercise is not None)
        self.toggle_button.setEnabled(exercise is not None)
        self.toggle_button.setText(
            "停用" if exercise is None or exercise.active else "恢复"
        )
        self.toggle_button.setProperty("danger", bool(exercise and exercise.active))
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)

    def create_exercise(self) -> None:
        dialog = ExerciseTypeEditorDialog(self._context, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def edit_selected(self) -> None:
        exercise = self.selected_exercise()
        if exercise is None:
            return
        dialog = ExerciseTypeEditorDialog(self._context, exercise, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def toggle_selected_active(self) -> None:
        exercise = self.selected_exercise()
        if exercise is None:
            return
        new_active = not exercise.active
        if not new_active:
            answer = QMessageBox.question(
                self,
                "停用运动项目",
                f"确定停用“{exercise.name}”吗？\n历史运动记录不会受影响。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self._context.set_exercise_type_active(
                exercise.exercise_type_id, new_active
            )
        except Exception as exc:
            QMessageBox.critical(
                self, "操作失败", f"无法更新运动项目状态。\n\n{exc}"
            )
            return
        self.refresh()
        self.data_changed.emit()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._loaded:
            self.refresh()


__all__ = ["ExerciseLibraryPage", "ExerciseTypeEditorDialog"]
