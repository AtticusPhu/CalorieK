"""Dashboard page for today's actual facts, prediction, and visual summaries."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from app.charts.candlestick import CandlePoint, CandlestickChart
from app.charts.treemap import TreemapItem, TreemapWidget
from app.energy_units import EnergyUnit, energy_unit_label, format_energy, normalize_energy_unit

from .context import DashboardDTO, UIContext
from .exercise_dialog import ExerciseDialog
from .food_dialog import FoodDialog
from .nutrition import DailyNutritionCard
from .weight_dialog import WeightDialog


class _MetricCard(QFrame):
    """Small accessible metric card with a stable caption/value hierarchy."""

    def __init__(self, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.setAccessibleName(caption)

        self.caption = QLabel(caption)
        self.caption.setObjectName("metricCaption")
        self.value = QLabel("--")
        self.value.setObjectName("metricValue")
        self.value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail = QLabel("")
        self.detail.setObjectName("muted")
        self.detail.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.detail)

    def set_metric(self, value: str, detail: str = "") -> None:
        self.value.setText(value)
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.setAccessibleDescription(f"{self.caption.text()}：{value}。{detail}")


class DashboardPage(QWidget):
    """Home surface with three high-frequency entry actions and live summaries."""

    data_changed = Signal()

    def __init__(self, context: UIContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._loaded = False
        self._display_unit: EnergyUnit = "kj"
        self._last_dashboard: DashboardDTO | None = None
        self.setObjectName("appRoot")
        self.setAccessibleName("首页仪表盘")

        title = QLabel("今天")
        title.setObjectName("pageTitle")
        self.as_of_label = QLabel("正在读取最新数据…")
        self.as_of_label.setObjectName("muted")

        refresh_button = QPushButton("刷新")
        refresh_button.setAccessibleName("刷新首页数据")
        refresh_button.clicked.connect(self.refresh)

        header = QHBoxLayout()
        header.setSpacing(12)
        header.addWidget(title)
        header.addWidget(self.as_of_label)
        header.addStretch(1)
        header.addWidget(refresh_button)

        self.actual_card = _MetricCard("最新实际体重")
        self.predicted_card = _MetricCard("今日预计体重")
        self.change_card = _MetricCard("今日预计变化")
        self.intake_card = _MetricCard("今日摄入")
        self.burn_card = _MetricCard("今日消耗")
        self.balance_card = _MetricCard("今日热量余额")

        metrics = QGridLayout()
        metrics.setHorizontalSpacing(12)
        metrics.setVerticalSpacing(12)
        cards = (
            self.actual_card,
            self.predicted_card,
            self.change_card,
            self.intake_card,
            self.burn_card,
            self.balance_card,
        )
        for index, card in enumerate(cards):
            metrics.addWidget(card, index // 3, index % 3)

        candle_title = QLabel("体重日 K 线")
        candle_title.setObjectName("sectionTitle")
        self.candle_helper = QLabel("纵轴固定为 kg；横杠表示当天没有实际称重，悬停可查看 OHLC 与热量（kJ）。")
        self.candle_helper.setObjectName("muted")
        self.candle_helper.setWordWrap(True)
        self.candlestick_chart = CandlestickChart()

        candle_card = QFrame()
        candle_card.setObjectName("card")
        candle_layout = QVBoxLayout(candle_card)
        candle_layout.setContentsMargins(16, 16, 16, 16)
        candle_layout.setSpacing(8)
        candle_layout.addWidget(candle_title)
        candle_layout.addWidget(self.candle_helper)
        candle_layout.addWidget(self.candlestick_chart, 1)

        self.treemap_title = QLabel("今日 kJ 构成")
        self.treemap_title.setObjectName("sectionTitle")
        self.treemap_helper = QLabel("方块面积按每个摄入或消耗项目的绝对能量成比例；显示 kJ，不表示发生时间。")
        self.treemap_helper.setObjectName("muted")
        self.treemap_helper.setWordWrap(True)
        self.treemap = TreemapWidget()

        treemap_card = QFrame()
        treemap_card.setObjectName("card")
        treemap_layout = QVBoxLayout(treemap_card)
        treemap_layout.setContentsMargins(16, 16, 16, 16)
        treemap_layout.setSpacing(8)
        treemap_layout.addWidget(self.treemap_title)
        treemap_layout.addWidget(self.treemap_helper)
        treemap_layout.addWidget(self.treemap, 1)

        scroll_body = QWidget()
        body_layout = QVBoxLayout(scroll_body)
        body_layout.setContentsMargins(0, 0, 0, 12)
        body_layout.setSpacing(16)
        body_layout.addLayout(metrics)
        self.nutrition_card = DailyNutritionCard(context)
        body_layout.addWidget(self.nutrition_card)
        body_layout.addWidget(candle_card)
        body_layout.addWidget(treemap_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(scroll_body)

        self.food_button = QPushButton("记录饮食")
        self.exercise_button = QPushButton("记录运动")
        self.weight_button = QPushButton("记录体重")
        self.food_button.setProperty("primary", True)
        for button, description in (
            (self.food_button, "打开饮食录入窗口"),
            (self.exercise_button, "打开运动录入窗口"),
            (self.weight_button, "打开实际体重录入窗口"),
        ):
            button.setMinimumHeight(46)
            button.setAccessibleDescription(description)

        self.food_button.clicked.connect(self.open_food_dialog)
        self.exercise_button.clicked.connect(self.open_exercise_dialog)
        self.weight_button.clicked.connect(self.open_weight_dialog)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addWidget(self.food_button, 1)
        actions.addWidget(self.exercise_button, 1)
        actions.addWidget(self.weight_button, 1)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)
        root.addLayout(header)
        root.addWidget(scroll, 1)
        root.addLayout(actions)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def refresh(self) -> None:
        """Fetch a complete immutable snapshot and render it atomically."""

        self.as_of_label.setText("正在刷新…")
        try:
            dashboard = self._context.get_dashboard()
            settings = self._context.get_settings()
            display_unit = self._context.get_energy_display_unit()
        except Exception as exc:
            self.as_of_label.setText("数据读取失败，可点击刷新重试")
            self.setAccessibleDescription(f"首页数据读取失败：{exc}")
            if self.isVisible():
                QMessageBox.warning(self, "首页未刷新", f"无法读取首页数据。\n\n{exc}")
            return

        self._apply_display_unit(display_unit)
        self._render(dashboard)
        self.nutrition_card.refresh(dashboard.nutrition)
        self.candlestick_chart.set_color_mode(settings.candle_color_mode)
        self.treemap.set_color_mode(settings.treemap_color_mode)
        self._loaded = True

    def refresh_display_unit(self) -> None:
        """Apply display preferences to the cached snapshot without model work."""

        settings = self._context.get_settings()
        self._apply_display_unit(self._context.get_energy_display_unit())
        if self._last_dashboard is not None:
            self._render(self._last_dashboard)
        self.candlestick_chart.set_color_mode(settings.candle_color_mode)
        self.treemap.set_color_mode(settings.treemap_color_mode)

    def _apply_display_unit(self, unit: str) -> None:
        self._display_unit = normalize_energy_unit(unit)
        label = energy_unit_label(self._display_unit)
        self.candle_helper.setText(
            "纵轴固定为 kg；横杠表示当天没有实际称重，"
            f"悬停可查看 OHLC 与热量（{label}）。"
        )
        self.treemap_title.setText(f"今日 {label} 构成")
        self.treemap_helper.setText(
            "方块面积按每个摄入或消耗项目的绝对能量成比例；"
            f"显示 {label}，不表示发生时间。"
        )
        self.candlestick_chart.set_display_unit(self._display_unit)
        self.treemap.set_display_unit(self._display_unit)

    def _render(self, dashboard: DashboardDTO) -> None:
        self._last_dashboard = dashboard
        as_of = dashboard.as_of
        self.as_of_label.setText(f"截至 {as_of:%Y-%m-%d %H:%M}")

        if dashboard.latest_actual_weight_kg is None:
            self.actual_card.set_metric("暂无", "请记录第一条实际称重")
        else:
            at_text = self._format_measurement_time(dashboard.latest_actual_at, as_of)
            self.actual_card.set_metric(f"{dashboard.latest_actual_weight_kg:.2f} kg", at_text)

        if dashboard.predicted_weight_kg is None:
            self.predicted_card.set_metric("暂无", "需要至少一条实际称重")
        else:
            self.predicted_card.set_metric(
                f"{dashboard.predicted_weight_kg:.2f} kg",
                "理论预测，不会写入实际称重历史",
            )

        if dashboard.predicted_change_kg is None:
            self.change_card.set_metric("--")
        else:
            self.change_card.set_metric(
                f"{dashboard.predicted_change_kg:+.2f} kg",
                f"个人校准 {format_energy(dashboard.calibration_kj_day, self._display_unit, signed=True)}/日",
            )

        self.intake_card.set_metric(format_energy(dashboard.intake_kj, self._display_unit))
        self.burn_card.set_metric(
            format_energy(dashboard.total_burn_kj, self._display_unit),
            f"基础/日常 {format_energy(dashboard.baseline_kj, self._display_unit)} · "
            f"运动 {format_energy(dashboard.exercise_kj, self._display_unit)}",
        )
        balance_description = "热量盈余" if dashboard.balance_kj > 0 else "热量赤字" if dashboard.balance_kj < 0 else "收支平衡"
        self.balance_card.set_metric(
            format_energy(dashboard.balance_kj, self._display_unit, signed=True),
            balance_description,
        )

        candles = tuple(
            CandlePoint(
                local_date=item.local_date,
                open_kg=item.open_kg,
                high_kg=item.high_kg,
                low_kg=item.low_kg,
                close_kg=item.close_kg,
                intake_kj=item.intake_kj,
                burn_kj=item.burn_kj,
                balance_kj=item.balance_kj,
                actual_weight_count=item.actual_weight_count,
                open_source=item.open_source,
                close_source=item.close_source,
            )
            for item in dashboard.candles
        )
        self.candlestick_chart.set_data(candles)

        items = tuple(
            TreemapItem(
                key=item.key,
                name=item.name,
                kj=item.kj,
                side=item.side,
                category=item.category,
                protein_g=item.protein_g,
                fat_g=item.fat_g,
                carb_g=item.carb_g,
                fiber_g=item.fiber_g,
                duration_min=item.duration_min,
            )
            for item in dashboard.treemap_items
        )
        self.treemap.set_data(items)
        self.setAccessibleDescription(
            f"今日摄入 {format_energy(dashboard.intake_kj, self._display_unit)}，"
            f"消耗 {format_energy(dashboard.total_burn_kj, self._display_unit)}，"
            f"余额 {format_energy(dashboard.balance_kj, self._display_unit, signed=True)}"
        )

    @staticmethod
    def _format_measurement_time(measured_at: datetime | None, as_of: datetime) -> str:
        if measured_at is None:
            return "时间未知"
        if measured_at.date() == as_of.date():
            return f"今天 {measured_at:%H:%M}"
        return measured_at.strftime("%Y-%m-%d %H:%M")

    def _run_entry_dialog(self, dialog: QDialog) -> None:
        # Keeping the common orchestration here guarantees the dashboard is
        # refreshed after any successful high-frequency entry.
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.refresh()
            self.data_changed.emit()

    def open_food_dialog(self) -> None:
        self._run_entry_dialog(FoodDialog(self._context, self))

    def open_exercise_dialog(self) -> None:
        self._run_entry_dialog(ExerciseDialog(self._context, self))

    def open_weight_dialog(self) -> None:
        self._run_entry_dialog(WeightDialog(self._context, self))

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if self._loaded:
            return
        # A MainWindow may be constructed before first-run setup.  Do not issue
        # dashboard service calls until the composition root/profile dialog has
        # established the required facts.
        try:
            has_profile = self._context.has_profile()
        except Exception:
            has_profile = False
        if has_profile:
            self.refresh()


DashboardWidget = DashboardPage


__all__ = ["DashboardPage", "DashboardWidget"]
