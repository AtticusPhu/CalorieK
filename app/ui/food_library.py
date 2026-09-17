"""Editable food library backed exclusively by :class:`UIContext`."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
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

from .context import FoodDTO, FoodDraft, UIContext
from .numeric_input import EnergySpinBox, PreciseDoubleSpinBox
from app.energy_units import energy_to_display, energy_unit_label, kcal_to_kj


class FoodEditorDialog(QDialog):
    """Create or edit one normalized per-100g/per-100ml food record."""

    def __init__(
        self,
        context: UIContext,
        food: FoodDTO | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._food = food
        self._energy_unit = context.get_energy_display_unit()
        self.saved_food: FoodDTO | None = None
        self.setWindowTitle("编辑食品" if food else "新建食品")
        self.setMinimumWidth(540)
        self.setAccessibleName(self.windowTitle())

        title = QLabel(self.windowTitle())
        title.setObjectName("pageTitle")
        helper = QLabel("营养值按统一基准保存；历史饮食事件保留自己的营养快照。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)

        self.name_edit = QLineEdit(food.name if food else "")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName("食品名称")
        form.addRow("食品名称 *", self.name_edit)

        self.category_combo = QComboBox()
        self.category_combo.setEditable(True)
        self.category_combo.addItems(("主食", "肉类", "蛋奶", "蔬菜", "水果", "油脂", "豆制品", "其它"))
        if food:
            self.category_combo.setCurrentText(food.category)
        form.addRow("分类 *", self.category_combo)

        self.brand_edit = QLineEdit(food.brand if food else "")
        self.brand_edit.setPlaceholderText("可选")
        form.addRow("品牌", self.brand_edit)

        self.basis_amount_spin = self._number_spin(1.0, 100000.0, 2, food.basis_amount if food else 100.0)
        self.basis_unit_combo = QComboBox()
        self.basis_unit_combo.addItem("克 (g)", "g")
        self.basis_unit_combo.addItem("毫升 (ml)", "ml")
        if food and food.basis_unit == "ml":
            self.basis_unit_combo.setCurrentIndex(1)

        basis_row = QWidget()
        basis_layout = QHBoxLayout(basis_row)
        basis_layout.setContentsMargins(0, 0, 0, 0)
        basis_layout.setSpacing(8)
        basis_layout.addWidget(self.basis_amount_spin, 1)
        basis_layout.addWidget(self.basis_unit_combo)
        form.addRow("营养基准 *", basis_row)

        self.energy_spin = EnergySpinBox(unit=self._energy_unit)
        self.energy_spin.set_kj_range(0.0, kcal_to_kj(100000.0))
        self.energy_spin.set_energy_kj(food.kj if food else 0.0)
        self.energy_spin.setAccessibleName(
            f"食品能量（{energy_unit_label(self._energy_unit)}）"
        )
        self.protein_spin = self._number_spin(0.0, 10000.0, 2, food.protein_g if food else 0.0, " g")
        self.fat_spin = self._number_spin(0.0, 10000.0, 2, food.fat_g if food else 0.0, " g")
        self.carb_spin = self._number_spin(0.0, 10000.0, 2, food.carb_g if food else 0.0, " g")
        self.fiber_spin = self._number_spin(0.0, 10000.0, 2, food.fiber_g if food else 0.0, " g")
        form.addRow("能量 *", self.energy_spin)
        form.addRow("蛋白质", self.protein_spin)
        form.addRow("脂肪", self.fat_spin)
        form.addRow("碳水", self.carb_spin)
        form.addRow("膳食纤维", self.fiber_spin)

        self.serving_spin = self._number_spin(
            0.1,
            100000.0,
            2,
            food.default_serving if food else 100.0,
        )
        form.addRow("默认份量 *", self.serving_spin)

        self.favorite_check = QCheckBox("加入收藏，便于饮食录入时快速选择")
        self.favorite_check.setChecked(food.is_favorite if food else False)
        form.addRow("收藏", self.favorite_check)

        if food and food.is_builtin:
            builtin_note = QLabel("这是内置食品。保存后会标记为用户已修改，但不会改写历史饮食记录。")
            builtin_note.setObjectName("muted")
            builtin_note.setWordWrap(True)
            form.addRow("数据来源", builtin_note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存食品")
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

    @staticmethod
    def _number_spin(
        minimum: float,
        maximum: float,
        decimals: int,
        value: float,
        suffix: str = "",
    ) -> QDoubleSpinBox:
        spin = PreciseDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        if suffix:
            spin.setSuffix(suffix)
        return spin

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        category = self.category_combo.currentText().strip()
        if not name:
            QMessageBox.warning(self, "检查食品名称", "请输入食品名称。")
            self.name_edit.setFocus()
            return
        if not category:
            QMessageBox.warning(self, "检查分类", "请输入或选择食品分类。")
            self.category_combo.setFocus()
            return

        draft = FoodDraft(
            food_id=self._food.food_id if self._food else None,
            name=name,
            category=category,
            brand=self.brand_edit.text().strip(),
            basis_amount=self.basis_amount_spin.value(),
            basis_unit=str(self.basis_unit_combo.currentData()),  # type: ignore[arg-type]
            kj=self.energy_spin.energy_kj(),
            protein_g=self.protein_spin.value(),
            fat_g=self.fat_spin.value(),
            carb_g=self.carb_spin.value(),
            fiber_g=self.fiber_spin.value(),
            default_serving=self.serving_spin.value(),
            is_favorite=self.favorite_check.isChecked(),
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(False)
        try:
            self.saved_food = self._context.save_food(draft)
        except Exception as exc:
            QMessageBox.critical(self, "食品未保存", f"无法保存食品，请检查输入。\n\n{exc}")
            save.setEnabled(True)
            return
        self.accept()


class FoodLibraryPage(QWidget):
    """Search, create, edit, soft-delete, and restore foods."""

    data_changed = Signal()

    _COLUMNS = (
        "名称",
        "分类",
        "品牌",
        "基准",
        "能量",
        "蛋白质",
        "脂肪",
        "碳水",
        "纤维",
        "收藏",
        "状态",
    )

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._energy_unit = context.get_energy_display_unit()
        self._loaded = False
        self.setObjectName("appRoot")
        self.setAccessibleName("食品库")

        title = QLabel("食品库")
        title.setObjectName("pageTitle")
        helper = QLabel("维护内置与自定义食品。修改食品不会反向改变已保存的饮食快照。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索食品名称或品牌")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索食品库")
        self.include_inactive = QCheckBox("显示已停用")
        self.include_inactive.setAccessibleName("显示已停用食品")

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
        self.table.setAccessibleName("食品列表")
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemDoubleClicked.connect(lambda _item: self.edit_selected())

        self.empty_label = QLabel("食品库中没有符合条件的项目。")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setVisible(False)

        self.new_button = QPushButton("新建食品")
        self.new_button.setProperty("primary", True)
        self.edit_button = QPushButton("编辑")
        self.toggle_button = QPushButton("停用")
        refresh_button = QPushButton("刷新")
        self.new_button.clicked.connect(self.create_food)
        self.edit_button.clicked.connect(self.edit_selected)
        self.toggle_button.clicked.connect(self.toggle_selected_active)
        refresh_button.clicked.connect(self.refresh)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        action_row.addWidget(self.new_button)
        action_row.addWidget(self.edit_button)
        action_row.addWidget(self.toggle_button)
        action_row.addStretch(1)
        action_row.addWidget(refresh_button)

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
        layout.addLayout(action_row)
        self._update_actions()

    def refresh(self) -> None:
        try:
            self._energy_unit = self._context.get_energy_display_unit()
            foods = self._context.list_foods(
                self.search_edit.text().strip(),
                include_inactive=self.include_inactive.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "食品库未刷新", f"无法读取食品库。\n\n{exc}")
            return

        self.table.setSortingEnabled(False)
        columns = list(self._COLUMNS)
        columns[4] = f"能量（{energy_unit_label(self._energy_unit)}）"
        self.table.setHorizontalHeaderLabels(columns)
        self.table.setRowCount(0)
        for food in foods:
            row = self.table.rowCount()
            self.table.insertRow(row)
            values = (
                food.name,
                food.category,
                food.brand or "—",
                f"{food.basis_amount:.2f} {food.basis_unit}",
                f"{energy_to_display(food.kj, self._energy_unit):.2f}",
                f"{food.protein_g:.2f} g",
                f"{food.fat_g:.2f} g",
                f"{food.carb_g:.2f} g",
                f"{food.fiber_g:.2f} g",
                "是" if food.is_favorite else "否",
                "正常" if food.active else "已停用",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, food)
                if column in (4, 5, 6, 7, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
        self.table.setSortingEnabled(True)
        self.table.resizeColumnsToContents()
        self.empty_label.setVisible(not foods)
        self.table.setVisible(bool(foods))
        self._loaded = True
        self._update_actions()

    def selected_food(self) -> FoodDTO | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        return None if item is None else cast(FoodDTO | None, item.data(Qt.ItemDataRole.UserRole))

    def _update_actions(self) -> None:
        food = self.selected_food()
        self.edit_button.setEnabled(food is not None)
        self.toggle_button.setEnabled(food is not None)
        self.toggle_button.setText("停用" if food is None or food.active else "恢复")
        self.toggle_button.setProperty("danger", bool(food and food.active))
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)

    def create_food(self) -> None:
        dialog = FoodEditorDialog(self._context, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def edit_selected(self) -> None:
        food = self.selected_food()
        if food is None:
            return
        dialog = FoodEditorDialog(self._context, food, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def toggle_selected_active(self) -> None:
        food = self.selected_food()
        if food is None:
            return
        new_active = not food.active
        if not new_active:
            answer = QMessageBox.question(
                self,
                "停用食品",
                f"确定停用“{food.name}”吗？\n历史饮食记录不会受影响。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self._context.set_food_active(food.food_id, new_active)
        except Exception as exc:
            QMessageBox.critical(self, "操作失败", f"无法更新食品状态。\n\n{exc}")
            return
        self.refresh()
        self.data_changed.emit()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._loaded or self._energy_unit != self._context.get_energy_display_unit():
            self.refresh()


FoodLibraryWidget = FoodLibraryPage


__all__ = ["FoodEditorDialog", "FoodLibraryPage", "FoodLibraryWidget"]
