"""Optional nutrient input and an independent, read-only daily summary."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from app.nutrition import DailyNutrition, NUTRIENT_NAMES, format_nutrient
from .context import UIContext
from .numeric_input import PreciseDoubleSpinBox


NUTRIENT_CAPTIONS = ("蛋白质", "膳食纤维", "脂肪", "碳水化合物")


class OptionalNutrientEditor(QWidget):
    """An explicit known/unknown choice; an unchecked zero is still NULL."""

    def __init__(self, caption: str, value: float | None = None) -> None:
        super().__init__()
        self.known = QCheckBox("已知")
        self.known.setAccessibleName(f"{caption}每 100g 数值已知")
        self.spin = PreciseDoubleSpinBox()
        self.spin.setRange(0.0, 100000.0)
        self.spin.setDecimals(2)
        self.spin.setSuffix(" g")
        self.spin.setValue(value if value is not None else 0.0)
        self.spin.setAccessibleName(f"每 100g {caption}克数")
        self.unknown = QLabel("未知（不按 0 计算）")
        self.unknown.setObjectName("muted")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.known)
        layout.addWidget(self.spin, 1)
        layout.addWidget(self.unknown, 1)
        self.known.toggled.connect(self._set_known)
        self.known.setChecked(value is not None)
        self._set_known(value is not None)

    def _set_known(self, known: bool) -> None:
        self.spin.setVisible(known)
        self.spin.setEnabled(known)
        self.unknown.setVisible(not known)

    def value(self) -> float | None:
        return self.spin.value() if self.known.isChecked() else None


class DailyNutritionCard(QFrame):
    """Nutrition dates do not alter today's energy cards or weight charts."""

    def __init__(self, context: UIContext) -> None:
        super().__init__()
        self._context = context
        self._follow_today = True
        self.setObjectName("card")
        self.setAccessibleName("每日营养构成")
        title = QLabel("每日营养构成")
        title.setObjectName("sectionTitle")
        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setAccessibleName("营养汇总日期（仅影响此卡片）")
        today = QPushButton("今天")
        today.setAccessibleName("查看今天的营养构成")
        today.clicked.connect(self._today)
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.date_edit)
        header.addWidget(today)
        grid = QGridLayout()
        self.values: dict[str, QLabel] = {}
        for index, (name, caption) in enumerate(zip(NUTRIENT_NAMES, NUTRIENT_CAPTIONS, strict=True)):
            label = QLabel("—")
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setAccessibleName(caption)
            self.values[name] = label
            grid.addWidget(QLabel(caption), index // 2, (index % 2) * 2)
            grid.addWidget(label, index // 2, (index % 2) * 2 + 1)
        self.indicator = QLabel("")
        self.indicator.setWordWrap(True)
        self.detail = QLabel("读取所选日期的摄入快照；不影响今天的能量和体重图表。")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(header)
        layout.addLayout(grid)
        layout.addWidget(self.indicator)
        layout.addWidget(self.detail)
        self.date_edit.dateChanged.connect(self._date_changed)

    def selected_date(self) -> date:
        value = self.date_edit.date()
        return date(value.year(), value.month(), value.day())

    def _today(self) -> None:
        self._follow_today = True
        self.refresh()

    def _date_changed(self, _value: QDate) -> None:
        self._follow_today = self.selected_date() == date.today()
        self.refresh()

    def refresh(self, snapshot: DailyNutrition | None = None) -> None:
        if self._follow_today:
            previous = self.date_edit.blockSignals(True)
            try:
                self.date_edit.setDate(QDate.currentDate())
            finally:
                self.date_edit.blockSignals(previous)
        selected = self.selected_date()
        try:
            if snapshot is None or snapshot.local_date != selected:
                snapshot = self._context.get_daily_nutrition(selected)
        except Exception:
            # Never leave stale totals labelled with a newly selected date.
            for label in self.values.values():
                label.setText("—")
            self.indicator.setText("营养数据读取失败")
            self.detail.setText("可重新选择日期或点击首页刷新重试。")
            self.setAccessibleDescription(f"{selected.isoformat()} 营养数据读取失败")
            return
        self.render(snapshot)

    def render(self, snapshot: DailyNutrition) -> None:
        for name, label in self.values.items():
            label.setText(format_nutrient(getattr(snapshot.nutrients, name)))
        self.indicator.setText(snapshot.indication)
        if snapshot.incomplete:
            detail = (
                f"已知部分合计（不是完整总量）；{snapshot.incomplete_count} / "
                f"{snapshot.intake_count} 条记录数据不完整。未知不按 0 计算。"
            )
        elif snapshot.intake_count == 0:
            detail = "当日暂无饮食记录；0.00 g 是已记录合计，不代表实际未摄入。"
        else:
            detail = f"当日 {snapshot.intake_count} 条饮食记录的营养合计。"
        self.detail.setText(detail + " 日期仅影响此卡片；能量独立计算。")
        self.setAccessibleDescription(
            f"{snapshot.local_date.isoformat()} {snapshot.indication} {self.detail.text()}"
        )
