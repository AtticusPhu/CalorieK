"""A QWidget treemap using canonical kJ areas and selectable energy text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from app.color_modes import treemap_palette
from app.energy_units import EnergyUnit, energy_unit_label, format_energy, normalize_energy_unit


TreemapColorMode = Literal["intake_red", "intake_green"]


@dataclass(frozen=True, slots=True)
class TreemapItem:
    key: str
    name: str
    kj: float
    side: Literal["intake", "burn"]
    category: str = "其它"
    protein_g: float | None = None
    fat_g: float | None = None
    carb_g: float | None = None
    fiber_g: float | None = None
    duration_min: float | None = None


@dataclass(frozen=True, slots=True)
class NumericRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)


@dataclass(frozen=True, slots=True)
class TreemapRegion:
    item: TreemapItem
    rect: NumericRect


def _split_weighted(
    weighted_keys: Sequence[tuple[str, float]],
    rect: NumericRect,
    split_vertical: bool | None = None,
) -> dict[str, NumericRect]:
    """Split a rectangle with exact area ratios along its longest dimension."""

    positive = [(key, abs(weight)) for key, weight in weighted_keys if abs(weight) > 0]
    total = sum(weight for _, weight in positive)
    if total <= 0 or rect.area <= 0:
        return {}
    vertical = rect.width >= rect.height if split_vertical is None else split_vertical
    result: dict[str, NumericRect] = {}
    cursor = rect.x if vertical else rect.y
    end = rect.x + rect.width if vertical else rect.y + rect.height
    for index, (key, weight) in enumerate(positive):
        ratio = weight / total
        if vertical:
            length = end - cursor if index == len(positive) - 1 else rect.width * ratio
            result[key] = NumericRect(cursor, rect.y, length, rect.height)
        else:
            length = end - cursor if index == len(positive) - 1 else rect.height * ratio
            result[key] = NumericRect(rect.x, cursor, rect.width, length)
        cursor += length
    return result


def layout_treemap(
    items: Sequence[TreemapItem],
    rect: NumericRect,
) -> tuple[TreemapRegion, ...]:
    """Build a two-level intake/burn treemap using ``abs(kj)`` only."""

    groups: dict[str, list[TreemapItem]] = {"intake": [], "burn": []}
    for item in items:
        if item.side in groups and abs(item.kj) > 0:
            groups[item.side].append(item)

    group_weights = [
        (side, sum(abs(item.kj) for item in group_items))
        for side, group_items in groups.items()
        if group_items
    ]
    group_rects = _split_weighted(group_weights, rect)
    regions: list[TreemapRegion] = []
    for side, group_items in groups.items():
        group_rect = group_rects.get(side)
        if group_rect is None:
            continue
        item_rects = _split_weighted(
            [(item.key, abs(item.kj)) for item in group_items],
            group_rect,
            split_vertical=group_rect.width < group_rect.height,
        )
        for item in group_items:
            item_rect = item_rects.get(item.key)
            if item_rect is not None:
                regions.append(TreemapRegion(item, item_rect))
    return tuple(regions)


class TreemapWidget(QWidget):
    """Paint daily intake and burn composition without implying event time."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(250)
        self.setMouseTracking(True)
        self.setAccessibleName("今日热量构成 Treemap")
        self._items: tuple[TreemapItem, ...] = ()
        self._regions: tuple[TreemapRegion, ...] = ()
        self._mode: TreemapColorMode = "intake_red"
        self._display_unit: EnergyUnit = "kj"
        self._last_hover_key: str | None = None

    def set_data(self, items: Sequence[TreemapItem]) -> None:
        canonical_items = tuple(item for item in items if abs(item.kj) > 0)
        if canonical_items != self._items:
            self._items = canonical_items
            self._rebuild_layout()
        self._last_hover_key = None
        self._update_accessibility()
        self.update()

    def set_display_unit(self, unit: str) -> None:
        """Change text without rescaling kJ data or rebuilding rectangles."""

        normalized = normalize_energy_unit(unit)
        if self._display_unit != normalized:
            self._display_unit = normalized
            self._last_hover_key = None
            QToolTip.hideText()
        self._update_accessibility()
        self.update()

    def _update_accessibility(self) -> None:
        total = sum(abs(item.kj) for item in self._items)
        self.setAccessibleDescription(
            f"当天共 {len(self._items)} 个热量组成项目，"
            f"绝对热量合计 {format_energy(total, self._display_unit)}。"
            + "；".join(self._tooltip(item) for item in self._items)
            if self._items
            else "当天暂无摄入或消耗事件"
        )

    def set_color_mode(self, mode: TreemapColorMode) -> None:
        self._mode = mode
        self.update()

    def layout_snapshot(self) -> tuple[TreemapRegion, ...]:
        """Expose immutable layout data for deterministic area-ratio tests."""

        return self._regions

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._rebuild_layout()

    def _rebuild_layout(self) -> None:
        contents = self.contentsRect().adjusted(8, 8, -8, -8)
        self._regions = layout_treemap(
            self._items,
            NumericRect(
                float(contents.x()),
                float(contents.y()),
                float(max(contents.width(), 0)),
                float(max(contents.height(), 0)),
            ),
        )

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#FFFFFF"))
        if not self._regions:
            painter.setPen(QColor("#526871"))
            painter.drawText(
                self.rect().adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "今天还没有热量构成数据\n"
                f"录入饮食或运动后会按 |{energy_unit_label(self._display_unit)}| 显示面积。",
            )
            return

        side_index = {"intake": 0, "burn": 0}
        for region in self._regions:
            item, number_rect = region.item, region.rect
            rect = QRectF(number_rect.x, number_rect.y, number_rect.width, number_rect.height)
            color = self._color_for(item.side, side_index[item.side])
            side_index[item.side] += 1
            painter.setBrush(color)
            painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
            painter.drawRect(rect)

            if rect.width() < 58 or rect.height() < 34:
                continue
            text_color = QColor("#FFFFFF") if color.lightnessF() < 0.55 else QColor("#102A35")
            painter.setPen(text_color)
            text_rect = rect.adjusted(7, 6, -7, -6)
            metrics = QFontMetrics(painter.font())
            name = metrics.elidedText(item.name, Qt.TextElideMode.ElideRight, int(text_rect.width()))
            prefix = "摄入" if item.side == "intake" else "消耗"
            if text_rect.height() >= 52:
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    f"{name}\n{format_energy(abs(item.kj), self._display_unit)}\n"
                    f"{prefix} · {item.category}",
                )
            else:
                painter.drawText(
                    text_rect,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    f"{name}\n{format_energy(abs(item.kj), self._display_unit)}",
                )

    def _color_for(self, side: str, index: int) -> QColor:
        palette = treemap_palette(side, self._mode)
        return QColor(palette[index % len(palette)])

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        point = QPointF(event.position())
        for region in self._regions:
            number_rect = region.rect
            rect = QRectF(number_rect.x, number_rect.y, number_rect.width, number_rect.height)
            if not rect.contains(point):
                continue
            item = region.item
            if self._last_hover_key != item.key:
                QToolTip.showText(event.globalPosition().toPoint(), self._tooltip(item), self)
                self._last_hover_key = item.key
            return
        self._last_hover_key = None
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().leaveEvent(event)
        self._last_hover_key = None
        QToolTip.hideText()

    def _tooltip(self, item: TreemapItem) -> str:
        side = "摄入" if item.side == "intake" else "消耗"
        lines = [
            f"{item.name} · {side}",
            format_energy(abs(item.kj), self._display_unit),
            f"分类：{item.category}",
        ]
        if item.side == "intake":
            if item.protein_g is not None:
                lines.append(f"蛋白质：{item.protein_g:.2f} g")
            if item.fat_g is not None:
                lines.append(f"脂肪：{item.fat_g:.2f} g")
            if item.carb_g is not None:
                lines.append(f"碳水：{item.carb_g:.2f} g")
            if item.fiber_g is not None:
                lines.append(f"膳食纤维：{item.fiber_g:.2f} g")
        elif item.duration_min is not None:
            lines.append(f"持续时间：{item.duration_min:.0f} min")
        return "\n".join(lines)


__all__ = [
    "NumericRect",
    "TreemapColorMode",
    "TreemapItem",
    "TreemapRegion",
    "TreemapWidget",
    "layout_treemap",
]
