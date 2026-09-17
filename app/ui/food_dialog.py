"""Food and recipe intake entry workflow."""

from __future__ import annotations

from app.energy_units import format_energy
from .numeric_input import PreciseDoubleSpinBox

from typing import cast

from PySide6.QtCore import QDateTime, QTimer, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .context import (
    FoodServingDTO,
    IntakeDraft,
    IntakeSection,
    IntakeSourceDTO,
    UIContext,
)


_UNIT_LABELS = {
    "g": "克 (g)",
    "ml": "毫升 (ml)",
    "serving": "份",
    "ratio_percent": "食谱比例 (%)",
}


class FoodDialog(QDialog):
    """Prioritize recent/favorite/recipe sources and keep entry to one amount."""

    _SECTIONS: tuple[IntakeSection, ...] = ("recent", "favorites", "recipes", "search")

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._energy_unit = context.get_energy_display_unit()
        self._selected: IntakeSourceDTO | None = None
        self.setWindowTitle("记录饮食")
        self.setMinimumSize(620, 650)
        self.setAccessibleName("饮食录入")

        title = QLabel("记录饮食")
        title.setObjectName("pageTitle")
        helper = QLabel("历史事件会保存营养快照；以后修改食品或食谱不会改变这次记录。")
        helper.setObjectName("muted")
        helper.setWordWrap(True)

        self.tabs = QTabWidget()
        self.tabs.setAccessibleName("饮食来源")
        self.lists: list[QListWidget] = []
        for section_label in ("最近使用", "收藏食品", "我的食谱"):
            widget = self._new_source_list()
            self.lists.append(widget)
            self.tabs.addTab(widget, section_label)

        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.setContentsMargins(10, 10, 10, 10)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("输入食品名称或品牌")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setAccessibleName("搜索食品")
        search_list = self._new_source_list()
        self.lists.append(search_list)
        search_layout.addWidget(self.search_edit)
        search_layout.addWidget(search_list, 1)
        self.tabs.addTab(search_page, "搜索食物")
        self.tabs.currentChanged.connect(self._tab_changed)

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(220)
        self._search_timer.timeout.connect(self._refresh_search)
        self.search_edit.textChanged.connect(lambda _text: self._search_timer.start())

        form = QFormLayout()
        form.setVerticalSpacing(11)
        self.selected_label = QLabel("请先选择食品或食谱")
        self.selected_label.setObjectName("muted")
        self.selected_label.setWordWrap(True)
        form.addRow("项目 *", self.selected_label)

        self.nutrition_label = QLabel("营养信息会由所选项目提供")
        self.nutrition_label.setObjectName("muted")
        self.nutrition_label.setWordWrap(True)
        form.addRow("参考营养", self.nutrition_label)

        self.at_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.at_edit.setCalendarPopup(True)
        self.at_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.at_edit.setAccessibleName("饮食发生时间")
        form.addRow("发生时间 *", self.at_edit)

        self.meal_combo = QComboBox()
        for text, value in (
            ("早餐", "早餐"),
            ("午餐", "午餐"),
            ("晚餐", "晚餐"),
            ("零食", "零食"),
            ("其它", "其它"),
        ):
            self.meal_combo.addItem(text, value)
        hour = QDateTime.currentDateTime().time().hour()
        self.meal_combo.setCurrentIndex(0 if hour < 10 else 1 if hour < 15 else 2 if hour < 21 else 3)
        self.meal_combo.setAccessibleName("餐次分类")
        form.addRow("餐次", self.meal_combo)

        self.amount_spin = PreciseDoubleSpinBox()
        self.amount_spin.setRange(0.1, 100000.0)
        self.amount_spin.setDecimals(2)
        self.amount_spin.setValue(100.0)
        self.amount_spin.setAccessibleName("摄入份量")
        form.addRow("份量 *", self.amount_spin)

        self.unit_combo = QComboBox()
        self.unit_combo.setAccessibleName("摄入单位")
        self.unit_combo.currentIndexChanged.connect(self._unit_changed)
        form.addRow("单位 *", self.unit_combo)

        self.note_edit = QPlainTextEdit()
        self.note_edit.setMaximumHeight(64)
        self.note_edit.setPlaceholderText("可选备注")
        self.note_edit.setAccessibleName("饮食备注")
        form.addRow("备注", self.note_edit)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText("保存饮食")
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
        self._load_initial_sections()

    def _new_source_list(self) -> QListWidget:
        widget = QListWidget()
        widget.setAlternatingRowColors(True)
        widget.currentItemChanged.connect(self._selection_changed)
        widget.itemDoubleClicked.connect(lambda _item: self.amount_spin.setFocus())
        return widget

    def _load_initial_sections(self) -> None:
        for index, section in enumerate(self._SECTIONS[:3]):
            self._fill_list(self.lists[index], section)
        self._fill_list(self.lists[3], "search", "")
        if self.lists[0].count() and self.lists[0].item(0).data(Qt.ItemDataRole.UserRole):
            self.lists[0].setCurrentRow(0)

    def _fill_list(self, widget: QListWidget, section: IntakeSection, query: str = "") -> None:
        widget.clear()
        widget.setEnabled(True)
        try:
            sources = self._context.list_intake_sources(section, query)
        except Exception as exc:
            item = QListWidgetItem(f"无法加载：{exc}")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(item)
            widget.setEnabled(False)
            return
        if not sources:
            item = QListWidgetItem("没有符合条件的项目")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            widget.addItem(item)
            return
        for source in sources:
            detail = f"  {source.detail}" if source.detail else ""
            item = QListWidgetItem(f"{source.name}{detail}")
            item.setData(Qt.ItemDataRole.UserRole, source)
            item.setToolTip(f"{source.name}\n{source.detail}".strip())
            widget.addItem(item)

    def _refresh_search(self) -> None:
        self._fill_list(self.lists[3], "search", self.search_edit.text().strip())

    def _tab_changed(self, index: int) -> None:
        if index == 3:
            self.search_edit.setFocus()
        widget = self.lists[index]
        if widget.currentItem() is None and widget.count() and widget.item(0).data(Qt.ItemDataRole.UserRole):
            widget.setCurrentRow(0)
        else:
            self._selection_changed(widget.currentItem())

    def _selection_changed(self, item: QListWidgetItem | None, *_args: object) -> None:
        source = None if item is None else item.data(Qt.ItemDataRole.UserRole)
        self._selected = cast(IntakeSourceDTO | None, source)
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(self._selected is not None)
        if self._selected is None:
            self.selected_label.setText("请先选择食品或食谱")
            self.nutrition_label.setText("营养信息会由所选项目提供")
            self.unit_combo.clear()
            return

        selected = self._selected
        kind = "食谱" if selected.source_type == "recipe" else "食品"
        self.selected_label.setText(f"{selected.name} · {kind}")
        nutrition = []
        if selected.kj_reference is not None:
            nutrition.append(format_energy(selected.kj_reference, self._energy_unit))
        for label, value in (
            ("蛋白", selected.protein_g),
            ("脂肪", selected.fat_g),
            ("碳水", selected.carb_g),
            ("纤维", selected.fiber_g),
        ):
            if value is not None:
                nutrition.append(f"{label} {value:.2f} g")
        self.nutrition_label.setText("  ·  ".join(nutrition) if nutrition else selected.detail or "由服务计算")

        self.unit_combo.blockSignals(True)
        self.unit_combo.clear()
        units = selected.allowed_units or (("g", "ratio_percent") if selected.source_type == "recipe" else ("g",))
        for unit in units:
            self.unit_combo.addItem(_UNIT_LABELS.get(unit, unit), unit)
        for serving in selected.servings:
            serving_unit_label = _UNIT_LABELS.get(
                serving.serving_unit, serving.serving_unit
            )
            self.unit_combo.addItem(
                (
                    f"{serving.name}（{serving.serving_amount:.2f} "
                    f"{serving_unit_label} ≈ {serving.base_amount:.2f} "
                    f"{serving.basis_unit}）"
                ),
                serving,
            )
        self.unit_combo.blockSignals(False)
        self.amount_spin.setValue(max(0.1, selected.default_amount))
        self._unit_changed(reset_amount=False)

    def _unit_changed(self, *_args: object, reset_amount: bool = True) -> None:
        choice = self.unit_combo.currentData()
        if isinstance(choice, FoodServingDTO):
            self.amount_spin.setRange(0.1, 10000.0)
            self.amount_spin.setDecimals(2)
            suffix = _UNIT_LABELS.get(choice.serving_unit, choice.serving_unit)
            self.amount_spin.setSuffix(f" {suffix}")
            if reset_amount:
                self.amount_spin.setValue(choice.serving_amount)
            return
        unit = choice
        if unit == "ratio_percent":
            self.amount_spin.setRange(0.1, 1000.0)
            self.amount_spin.setDecimals(2)
            self.amount_spin.setSuffix(" %")
            if self.amount_spin.value() > 1000:
                self.amount_spin.setValue(100.0)
        elif unit == "serving":
            self.amount_spin.setRange(0.1, 100.0)
            self.amount_spin.setDecimals(2)
            self.amount_spin.setSuffix(" 份")
        else:
            self.amount_spin.setRange(0.1, 100000.0)
            self.amount_spin.setDecimals(2)
            self.amount_spin.setSuffix(f" {unit or ''}")

    def _save(self) -> None:
        if self._selected is None or self.unit_combo.currentData() is None:
            QMessageBox.warning(self, "请选择项目", "请先选择食品或食谱。")
            return
        choice = self.unit_combo.currentData()
        serving = choice if isinstance(choice, FoodServingDTO) else None
        draft = IntakeDraft(
            occurred_at=self.at_edit.dateTime().toPython(),
            source_type=self._selected.source_type,
            source_id=self._selected.source_id,
            amount=self.amount_spin.value(),
            unit=serving.serving_unit if serving else str(choice),
            meal_category=str(self.meal_combo.currentData()),
            note=self.note_edit.toPlainText().strip(),
            serving_id=serving.serving_id if serving else None,
        )
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setEnabled(False)
        try:
            self._context.record_intake(draft)
        except Exception as exc:
            QMessageBox.critical(self, "饮食未保存", f"无法保存这次饮食记录。\n\n{exc}")
            save.setEnabled(True)
            return
        self.accept()


__all__ = ["FoodDialog"]
