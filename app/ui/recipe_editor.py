"""Recipe library and ingredient editor for user-defined foods combinations."""

from __future__ import annotations

from app.energy_units import EnergyUnit, format_energy, normalize_energy_unit
from .numeric_input import PreciseDoubleSpinBox

from typing import cast

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context import (
    FoodDTO,
    NutritionDTO,
    RecipeDetailDTO,
    RecipeDraft,
    RecipeItemDTO,
    RecipeSummaryDTO,
    UIContext,
)


def _amount_summary(
    total_weight_g: float,
    total_volume_ml: float,
    normalization_unit: str | None,
) -> str:
    if normalization_unit == "g":
        return f"{total_weight_g:.2f} g"
    if normalization_unit == "ml":
        return f"{total_volume_ml:.2f} ml"
    parts = []
    if total_weight_g > 0:
        parts.append(f"{total_weight_g:.2f} g")
    if total_volume_ml > 0:
        parts.append(f"{total_volume_ml:.2f} ml")
    return " + ".join(parts) or "0"


class _NutritionSummary(QFrame):
    def __init__(
        self, parent: QWidget | None = None, *, unit: EnergyUnit = "kj"
    ) -> None:
        super().__init__(parent)
        self._energy_unit = normalize_energy_unit(unit)
        self._nutrition: NutritionDTO | None = None
        self.setObjectName("card")
        self.setAccessibleName("食谱营养汇总")
        self._labels: dict[str, QLabel] = {}
        self._captions: dict[str, QLabel] = {}
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 12, 14, 12)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for index, (key, caption) in enumerate(
            (
                ("weight", "总重量"),
                ("energy", "总能量"),
                ("protein", "蛋白质"),
                ("fat", "脂肪"),
                ("carb", "碳水"),
                ("fiber", "膳食纤维"),
                ("per100", "每 100g 能量"),
                ("per100_macros", "每 100g 宏量"),
            )
        ):
            caption_label = QLabel(caption)
            caption_label.setObjectName("metricCaption")
            self._captions[key] = caption_label
            value = QLabel("--")
            value.setWordWrap(True)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._labels[key] = value
            row, column = divmod(index, 4)
            cell = QVBoxLayout()
            cell.setSpacing(2)
            cell.addWidget(caption_label)
            cell.addWidget(value)
            grid.addLayout(cell, row, column)

    def set_display_unit(self, unit: EnergyUnit) -> None:
        self._energy_unit = normalize_energy_unit(unit)
        self.set_nutrition(self._nutrition)

    def set_nutrition(self, nutrition: NutritionDTO | None) -> None:
        self._nutrition = nutrition
        if nutrition is None:
            for label in self._labels.values():
                label.setText("--")
            self.setAccessibleDescription("营养数据暂不可用")
            return
        self._captions["weight"].setText(
            "总重量"
            if nutrition.normalization_unit == "g"
            else "总体积"
            if nutrition.normalization_unit == "ml"
            else "合计用量"
        )
        self._labels["weight"].setText(
            _amount_summary(
                nutrition.total_weight_g,
                nutrition.total_volume_ml,
                nutrition.normalization_unit,
            )
        )
        self._labels["energy"].setText(format_energy(nutrition.kj, self._energy_unit))
        self._labels["protein"].setText(f"{nutrition.protein_g:.2f} g")
        self._labels["fat"].setText(f"{nutrition.fat_g:.2f} g")
        self._labels["carb"].setText(f"{nutrition.carb_g:.2f} g")
        self._labels["fiber"].setText(f"{nutrition.fiber_g:.2f} g")
        if nutrition.normalization_unit is None:
            self._captions["per100"].setText("每 100 单位能量")
            self._captions["per100_macros"].setText("每 100 单位宏量")
            self._labels["per100"].setText("不适用于 g/ml 混合食谱")
            self._labels["per100_macros"].setText("请按整份比例记录摄入")
        else:
            basis = nutrition.normalization_unit
            self._captions["per100"].setText(f"每 100{basis} 能量")
            self._captions["per100_macros"].setText(f"每 100{basis} 宏量")
            self._labels["per100"].setText(
                format_energy(nutrition.per_100g_kj, self._energy_unit)
            )
            self._labels["per100_macros"].setText(
                f"蛋白 {nutrition.per_100g_protein_g:.2f} · "
                f"脂肪 {nutrition.per_100g_fat_g:.2f} · "
                f"碳水 {nutrition.per_100g_carb_g:.2f} · "
                f"纤维 {nutrition.per_100g_fiber_g:.2f} g"
            )
        amount_text = _amount_summary(
            nutrition.total_weight_g,
            nutrition.total_volume_ml,
            nutrition.normalization_unit,
        )
        self.setAccessibleDescription(
            f"合计用量 {amount_text}，总能量 {format_energy(nutrition.kj, self._energy_unit)}"
        )


class RecipeEditorDialog(QDialog):
    """Create or edit a recipe and preview aggregate nutrition before saving."""

    def __init__(
        self,
        context: UIContext,
        recipe: RecipeDetailDTO | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._recipe = recipe
        self._energy_unit = context.get_energy_display_unit()
        self._items = list(recipe.items if recipe else ())
        self.saved_recipe: RecipeDetailDTO | None = None
        self.setWindowTitle("编辑食谱" if recipe and recipe.recipe_id else "新建食谱")
        self.setMinimumSize(760, 680)
        self.setAccessibleName(self.windowTitle())

        title = QLabel(self.windowTitle())
        title.setObjectName("pageTitle")
        helper = QLabel("添加原料后系统自动计算总用量与营养；仅同单位食谱显示每 100g/100ml 值。历史摄入仍保留保存时快照。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(10)
        self.name_edit = QLineEdit(recipe.name if recipe else "")
        self.name_edit.setClearButtonEnabled(True)
        self.name_edit.setAccessibleName("食谱名称")
        form.addRow("食谱名称 *", self.name_edit)
        self.note_edit = QPlainTextEdit(recipe.note if recipe else "")
        self.note_edit.setMaximumHeight(64)
        self.note_edit.setPlaceholderText("可选：做法、成品状态或分装说明")
        self.note_edit.setAccessibleName("食谱备注")
        form.addRow("备注", self.note_edit)

        self.food_combo = QComboBox()
        self.food_combo.setEditable(True)
        self.food_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.food_combo.setAccessibleName("选择食谱原料")
        self.food_combo.currentIndexChanged.connect(self._food_changed)
        self.amount_spin = PreciseDoubleSpinBox()
        self.amount_spin.setRange(0.1, 100000.0)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setValue(100.0)
        self.amount_spin.setSuffix(" g")
        self.amount_spin.setAccessibleName("原料用量")
        add_button = QPushButton("添加或累加")
        add_button.setProperty("primary", True)
        add_button.clicked.connect(self._add_item)
        self.update_button = QPushButton("修改所选份量")
        self.update_button.clicked.connect(self._update_selected_item)
        self.remove_button = QPushButton("移除所选")
        self.remove_button.setProperty("danger", True)
        self.remove_button.clicked.connect(self._remove_selected_item)

        ingredient_controls = QHBoxLayout()
        ingredient_controls.setSpacing(8)
        ingredient_controls.addWidget(self.food_combo, 1)
        ingredient_controls.addWidget(self.amount_spin)
        ingredient_controls.addWidget(add_button)
        ingredient_controls.addWidget(self.update_button)
        ingredient_controls.addWidget(self.remove_button)

        self.ingredients = QTableWidget(0, 3)
        self.ingredients.setHorizontalHeaderLabels(("原料", "用量", "食品 ID"))
        self.ingredients.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.ingredients.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.ingredients.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.ingredients.verticalHeader().setVisible(False)
        self.ingredients.horizontalHeader().setStretchLastSection(False)
        self.ingredients.setColumnHidden(2, True)
        self.ingredients.setAccessibleName("食谱原料列表")
        self.ingredients.itemSelectionChanged.connect(self._ingredient_selected)

        self.nutrition = _NutritionSummary(unit=self._energy_unit)
        self.preview_status = QLabel("")
        self.preview_status.setObjectName("muted")
        self.preview_status.setWordWrap(True)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存食谱")
        save.setProperty("primary", True)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(title)
        layout.addWidget(helper)
        layout.addLayout(form)
        layout.addLayout(ingredient_controls)
        layout.addWidget(self.ingredients, 1)
        layout.addWidget(self.nutrition)
        layout.addWidget(self.preview_status)
        layout.addWidget(self.buttons)

        self._load_foods()
        self._render_items()
        self.name_edit.setFocus()

    def _load_foods(self) -> None:
        self.food_combo.clear()
        try:
            foods = self._context.list_foods(include_inactive=False)
        except Exception as exc:
            self.preview_status.setText(f"食品库读取失败：{exc}")
            self.food_combo.setEnabled(False)
            return
        for food in foods:
            label = food.name if not food.brand else f"{food.name} · {food.brand}"
            self.food_combo.addItem(label, food)
        if not foods:
            self.preview_status.setText("食品库为空，请先在食品库创建食品。")
            self.food_combo.setEnabled(False)
        else:
            self._food_changed()

    def _selected_food(self) -> FoodDTO | None:
        return cast(FoodDTO | None, self.food_combo.currentData())

    def _food_changed(self, *_args: object) -> None:
        food = self._selected_food()
        unit = food.basis_unit if food is not None else "g"
        self.amount_spin.setSuffix(f" {unit}")

    def _add_item(self) -> None:
        food = self._selected_food()
        if food is None:
            QMessageBox.warning(self, "请选择原料", "请从食品库选择一个有效原料。")
            self.food_combo.setFocus()
            return
        amount = self.amount_spin.value()
        for index, item in enumerate(self._items):
            if item.food_id == food.food_id:
                self._items[index] = RecipeItemDTO(
                    item.food_id, item.food_name, item.amount_g + amount, food.basis_unit
                )
                break
        else:
            self._items.append(
                RecipeItemDTO(food.food_id, food.name, amount, food.basis_unit)
            )
        self._render_items()

    def _ingredient_selected(self) -> None:
        row = self.ingredients.currentRow()
        has_selection = 0 <= row < len(self._items)
        self.update_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)
        if not has_selection:
            return
        item = self._items[row]
        self.amount_spin.setValue(item.amount_g)
        for index in range(self.food_combo.count()):
            food = cast(FoodDTO | None, self.food_combo.itemData(index))
            if food is not None and food.food_id == item.food_id:
                self.food_combo.setCurrentIndex(index)
                break

    def _update_selected_item(self) -> None:
        row = self.ingredients.currentRow()
        if not 0 <= row < len(self._items):
            return
        item = self._items[row]
        self._items[row] = RecipeItemDTO(
            item.food_id, item.food_name, self.amount_spin.value(), item.unit
        )
        self._render_items(select_row=row)

    def _remove_selected_item(self) -> None:
        row = self.ingredients.currentRow()
        if not 0 <= row < len(self._items):
            return
        self._items.pop(row)
        self._render_items(select_row=min(row, len(self._items) - 1))

    def _render_items(self, select_row: int | None = None) -> None:
        self.ingredients.setRowCount(0)
        for item in self._items:
            row = self.ingredients.rowCount()
            self.ingredients.insertRow(row)
            name_item = QTableWidgetItem(item.food_name)
            name_item.setData(Qt.ItemDataRole.UserRole, item)
            amount_item = QTableWidgetItem(f"{item.amount_g:.2f} {item.unit}")
            amount_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.ingredients.setItem(row, 0, name_item)
            self.ingredients.setItem(row, 1, amount_item)
            self.ingredients.setItem(row, 2, QTableWidgetItem(str(item.food_id)))
        self.ingredients.horizontalHeader().setStretchLastSection(True)
        self.ingredients.resizeColumnToContents(0)
        if select_row is not None and 0 <= select_row < len(self._items):
            self.ingredients.selectRow(select_row)
        self._ingredient_selected()
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        if not self._items:
            self.nutrition.set_nutrition(NutritionDTO())
            self.preview_status.setText("至少添加一种原料后才能保存食谱。")
            return
        try:
            nutrition = self._context.preview_recipe(tuple(self._items))
        except Exception as exc:
            self.nutrition.set_nutrition(None)
            self.preview_status.setText(f"暂时无法计算营养：{exc}")
            return
        self.nutrition.set_nutrition(nutrition)
        self.preview_status.setText("营养值将随原料份量自动更新。")

    def _save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "检查食谱名称", "请输入食谱名称。")
            self.name_edit.setFocus()
            return
        if not self._items:
            QMessageBox.warning(self, "检查原料", "请至少添加一种原料。")
            self.food_combo.setFocus()
            return
        draft = RecipeDraft(
            recipe_id=self._recipe.recipe_id if self._recipe else None,
            name=name,
            note=self.note_edit.toPlainText().strip(),
            items=tuple(self._items),
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(False)
        try:
            self.saved_recipe = self._context.save_recipe(draft)
        except Exception as exc:
            QMessageBox.critical(self, "食谱未保存", f"无法保存食谱，请检查输入。\n\n{exc}")
            save.setEnabled(True)
            return
        self.accept()


class RecipeLibraryPage(QWidget):
    """Browse recipe totals and open the full ingredient editor."""

    data_changed = Signal()

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._energy_unit = context.get_energy_display_unit()
        self._loaded = False
        self._detail: RecipeDetailDTO | None = None
        self.setObjectName("appRoot")
        self.setAccessibleName("我的食谱")

        title = QLabel("我的食谱")
        title.setObjectName("pageTitle")
        helper = QLabel("组合食品并自动计算总用量和宏量营养；混合 g/ml 时不做不可靠的每 100 单位换算。")
        helper.setObjectName("muted")

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索食谱名称")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索食谱")
        self.include_inactive = QCheckBox("显示已停用")

        search_row = QHBoxLayout()
        search_row.setSpacing(10)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.include_inactive)

        self.recipe_list = QListWidget()
        self.recipe_list.setAlternatingRowColors(True)
        self.recipe_list.setAccessibleName("食谱列表")
        self.recipe_list.currentItemChanged.connect(self._selection_changed)
        self.recipe_list.itemDoubleClicked.connect(lambda _item: self.edit_selected())

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.recipe_list)

        self.detail_title = QLabel("选择一个食谱查看详情")
        self.detail_title.setObjectName("sectionTitle")
        self.detail_note = QLabel("")
        self.detail_note.setObjectName("muted")
        self.detail_note.setWordWrap(True)
        self.detail_items = QTableWidget(0, 2)
        self.detail_items.setHorizontalHeaderLabels(("原料", "用量"))
        self.detail_items.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.detail_items.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.detail_items.verticalHeader().setVisible(False)
        self.detail_items.horizontalHeader().setStretchLastSection(True)
        self.detail_items.setAccessibleName("食谱原料详情")
        self.nutrition = _NutritionSummary(unit=self._energy_unit)

        detail_card = QFrame()
        detail_card.setObjectName("card")
        detail_layout = QVBoxLayout(detail_card)
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(10)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_note)
        detail_layout.addWidget(self.detail_items, 1)
        detail_layout.addWidget(self.nutrition)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(detail_card)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes((260, 620))

        self.new_button = QPushButton("新建食谱")
        self.new_button.setProperty("primary", True)
        self.edit_button = QPushButton("编辑")
        self.toggle_button = QPushButton("停用")
        refresh_button = QPushButton("刷新")
        self.new_button.clicked.connect(self.create_recipe)
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
        layout.addWidget(splitter, 1)
        layout.addLayout(actions)
        self._update_actions()

    def refresh(self) -> None:
        selected_id = self.selected_summary().recipe_id if self.selected_summary() else None
        try:
            self._energy_unit = self._context.get_energy_display_unit()
            recipes = self._context.list_recipes(
                self.search_edit.text().strip(),
                include_inactive=self.include_inactive.isChecked(),
            )
        except Exception as exc:
            QMessageBox.warning(self, "食谱未刷新", f"无法读取食谱库。\n\n{exc}")
            return
        self.nutrition.set_display_unit(self._energy_unit)
        self.recipe_list.clear()
        row_to_restore = -1
        for index, recipe in enumerate(recipes):
            state = " · 已停用" if not recipe.active else ""
            item = QListWidgetItem(
                f"{recipe.name}\n"
                f"{_amount_summary(recipe.total_weight_g, recipe.total_volume_ml, recipe.normalization_unit)}"
                f" · {format_energy(recipe.total_kj, self._energy_unit)}{state}"
            )
            item.setData(Qt.ItemDataRole.UserRole, recipe)
            item.setToolTip(item.text())
            item.setSizeHint(QSize(0, 52))
            self.recipe_list.addItem(item)
            if recipe.recipe_id == selected_id:
                row_to_restore = index
        if recipes:
            self.recipe_list.setCurrentRow(row_to_restore if row_to_restore >= 0 else 0)
        else:
            self._clear_detail("暂无符合条件的食谱", "点击“新建食谱”开始组合原料。")
        self._loaded = True
        self._update_actions()

    def selected_summary(self) -> RecipeSummaryDTO | None:
        item = self.recipe_list.currentItem()
        return None if item is None else cast(RecipeSummaryDTO | None, item.data(Qt.ItemDataRole.UserRole))

    def _selection_changed(self, item: QListWidgetItem | None, *_args: object) -> None:
        summary = None if item is None else cast(RecipeSummaryDTO | None, item.data(Qt.ItemDataRole.UserRole))
        self._detail = None
        if summary is None:
            self._clear_detail("选择一个食谱查看详情", "")
            self._update_actions()
            return
        try:
            detail = self._context.get_recipe(summary.recipe_id)
        except Exception as exc:
            self._clear_detail(summary.name, f"无法读取详情：{exc}")
            self._update_actions()
            return
        self._detail = detail
        self.detail_title.setText(detail.name)
        self.detail_note.setText(detail.note or "无备注")
        self.detail_items.setRowCount(0)
        for ingredient in detail.items:
            row = self.detail_items.rowCount()
            self.detail_items.insertRow(row)
            self.detail_items.setItem(row, 0, QTableWidgetItem(ingredient.food_name))
            amount = QTableWidgetItem(
                f"{ingredient.amount_g:.2f} {ingredient.unit}"
            )
            amount.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.detail_items.setItem(row, 1, amount)
        self.nutrition.set_nutrition(detail.nutrition)
        self._update_actions()

    def _clear_detail(self, title: str, note: str) -> None:
        self._detail = None
        self.detail_title.setText(title)
        self.detail_note.setText(note)
        self.detail_items.setRowCount(0)
        self.nutrition.set_nutrition(None)

    def _update_actions(self) -> None:
        summary = self.selected_summary()
        self.edit_button.setEnabled(summary is not None and self._detail is not None)
        self.toggle_button.setEnabled(summary is not None)
        self.toggle_button.setText("停用" if summary is None or summary.active else "恢复")
        self.toggle_button.setProperty("danger", bool(summary and summary.active))
        self.toggle_button.style().unpolish(self.toggle_button)
        self.toggle_button.style().polish(self.toggle_button)

    def create_recipe(self) -> None:
        dialog = RecipeEditorDialog(self._context, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def edit_selected(self) -> None:
        if self._detail is None:
            return
        dialog = RecipeEditorDialog(self._context, self._detail, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def toggle_selected_active(self) -> None:
        summary = self.selected_summary()
        if summary is None:
            return
        new_active = not summary.active
        if not new_active:
            answer = QMessageBox.question(
                self,
                "停用食谱",
                f"确定停用“{summary.name}”吗？\n历史摄入记录不会受影响。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            self._context.set_recipe_active(summary.recipe_id, new_active)
        except Exception as exc:
            QMessageBox.critical(self, "操作失败", f"无法更新食谱状态。\n\n{exc}")
            return
        self.refresh()
        self.data_changed.emit()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._loaded or self._energy_unit != self._context.get_energy_display_unit():
            self.refresh()


RecipeEditor = RecipeEditorDialog
RecipeEditorPage = RecipeLibraryPage


__all__ = [
    "RecipeEditor",
    "RecipeEditorDialog",
    "RecipeEditorPage",
    "RecipeLibraryPage",
]
