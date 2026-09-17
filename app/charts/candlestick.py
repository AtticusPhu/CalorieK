"""Interactive daily weight candlestick chart backed by PyQtGraph."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPicture
from PySide6.QtWidgets import QLabel, QStackedLayout, QToolTip, QWidget

from app.color_modes import candle_palette
from app.energy_units import EnergyUnit, format_energy, normalize_energy_unit

try:  # PySide6 can still show the rest of the application without PyQtGraph.
    import pyqtgraph as pg
except ImportError:  # pragma: no cover - exercised only in a reduced installation
    pg = None  # type: ignore[assignment]


ColorMode = Literal["china", "international"]


@dataclass(frozen=True, slots=True)
class CandlePoint:
    local_date: date
    open_kg: float
    high_kg: float
    low_kg: float
    close_kg: float
    intake_kj: float = 0.0
    burn_kj: float = 0.0
    balance_kj: float = 0.0
    actual_weight_count: int = 0
    open_source: str = ""
    close_source: str = ""

    @property
    def direction_text(self) -> str:
        if self.close_kg > self.open_kg:
            return "上涨"
        if self.close_kg < self.open_kg:
            return "下跌"
        return "横盘"


if pg is not None:

    class _DateIndexAxis(pg.AxisItem):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)
            self._dates: tuple[date, ...] = ()

        def set_dates(self, dates: Sequence[date]) -> None:
            self._dates = tuple(dates)
            self.picture = None
            self.update()

        def tickStrings(
            self,
            values: Sequence[float],
            scale: float,
            spacing: float,
        ) -> list[str]:
            del scale, spacing
            labels: list[str] = []
            for value in values:
                index = int(round(value))
                if 0 <= index < len(self._dates) and abs(value - index) < 0.15:
                    labels.append(self._dates[index].strftime("%m-%d"))
                else:
                    labels.append("")
            return labels


    class _CandlestickGraphicsItem(pg.GraphicsObject):
        def __init__(self) -> None:
            super().__init__()
            self._data: tuple[CandlePoint, ...] = ()
            self._up = QColor("#DC2626")
            self._down = QColor("#059669")
            self._flat = QColor("#64748B")
            self._picture = QPicture()

        def set_data(
            self,
            data: Sequence[CandlePoint],
            up: QColor,
            down: QColor,
            flat: QColor,
        ) -> None:
            self.prepareGeometryChange()
            self._data = tuple(data)
            self._up, self._down, self._flat = up, down, flat
            self._build_picture()
            self.update()

        def _build_picture(self) -> None:
            picture = QPicture()
            painter = QPainter(picture)
            body_width = 0.62
            for index, candle in enumerate(self._data):
                if candle.close_kg > candle.open_kg:
                    color = self._up
                elif candle.close_kg < candle.open_kg:
                    color = self._down
                else:
                    color = self._flat

                pen = pg.mkPen(color, width=1.4)
                painter.setPen(pen)
                painter.setBrush(pg.mkBrush(color))
                painter.drawLine(
                    pg.Point(index, candle.low_kg),
                    pg.Point(index, candle.high_kg),
                )
                body_low = min(candle.open_kg, candle.close_kg)
                body_height = abs(candle.close_kg - candle.open_kg)
                if body_height < 0.00005:
                    painter.drawLine(
                        pg.Point(index - body_width / 2, candle.open_kg),
                        pg.Point(index + body_width / 2, candle.open_kg),
                    )
                else:
                    painter.drawRect(
                        pg.QtCore.QRectF(
                            index - body_width / 2,
                            body_low,
                            body_width,
                            body_height,
                        )
                    )
            painter.end()
            self._picture = picture

        def paint(self, painter: QPainter, *args: object) -> None:
            del args
            painter.drawPicture(0, 0, self._picture)

        def boundingRect(self):  # type: ignore[no-untyped-def]
            return self._picture.boundingRect()


class CandlestickChart(QWidget):
    """Daily OHLC widget with linear kg axis, pan/zoom and hover crosshair."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName("体重日 K 线")
        self.setMinimumHeight(290)
        self._data: tuple[CandlePoint, ...] = ()
        self._color_mode: ColorMode = "china"
        self._display_unit: EnergyUnit = "kj"

        self._stack = QStackedLayout(self)
        self._empty = QLabel("暂无可显示的体重日线\n录入实际体重后，这里会显示每日 OHLC。", self)
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setObjectName("muted")
        self._empty.setWordWrap(True)

        if pg is None:
            unavailable = QLabel(
                "K 线组件不可用：请安装 PyQtGraph 后重新启动。",
                self,
            )
            unavailable.setAlignment(Qt.AlignmentFlag.AlignCenter)
            unavailable.setWordWrap(True)
            unavailable.setObjectName("muted")
            self._stack.addWidget(unavailable)
            self._plot = None
            self._axis = None
            self._candles = None
            self._v_line = None
            self._h_line = None
            self._proxy = None
            return

        pg.setConfigOptions(antialias=True)
        self._axis = _DateIndexAxis(orientation="bottom")
        self._plot = pg.PlotWidget(axisItems={"bottom": self._axis}, parent=self)
        self._plot.setBackground("#FFFFFF")
        self._plot.showGrid(x=True, y=True, alpha=0.14)
        self._plot.setLabel("left", "体重", units="kg")
        self._plot.setLabel("bottom", "自然日")
        self._plot.setMouseEnabled(x=True, y=True)
        self._plot.getViewBox().setMouseMode(pg.ViewBox.PanMode)
        self._plot.getViewBox().setLogMode(False, False)
        self._plot.setMenuEnabled(False)

        self._candles = _CandlestickGraphicsItem()
        self._plot.addItem(self._candles)
        self._v_line = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#64748B"))
        self._h_line = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#64748B"))
        self._v_line.hide()
        self._h_line.hide()
        self._plot.addItem(self._v_line, ignoreBounds=True)
        self._plot.addItem(self._h_line, ignoreBounds=True)
        self._proxy = pg.SignalProxy(
            self._plot.scene().sigMouseMoved,
            rateLimit=40,
            slot=self._mouse_moved,
        )

        self._stack.addWidget(self._plot)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)

    def set_color_mode(self, mode: ColorMode) -> None:
        if self._color_mode == mode:
            return
        self._color_mode = mode
        # A display preference must not reset the user's kg-axis zoom/pan.
        self._paint_candles()

    def _paint_candles(self) -> None:
        if pg is None or self._candles is None:
            return
        up_hex, down_hex, flat_hex = candle_palette(self._color_mode)
        self._candles.set_data(
            self._data, QColor(up_hex), QColor(down_hex), QColor(flat_hex)
        )

    def set_data(self, data: Sequence[CandlePoint]) -> None:
        points = tuple(data)
        if points == self._data:
            self._update_accessibility()
            return
        self._data = points
        self._render()

    def set_display_unit(self, unit: str) -> None:
        """Change energy text only, preserving kg geometry and the view range."""

        normalized = normalize_energy_unit(unit)
        if self._display_unit != normalized:
            self._display_unit = normalized
            QToolTip.hideText()
        self._update_accessibility()
        self.update()

    def _update_accessibility(self) -> None:
        if not self._data:
            self.setAccessibleDescription("暂无体重日线数据")
            return
        self.setAccessibleDescription(
            f"共 {len(self._data)} 个自然日，最新 " + self._tooltip(self._data[-1])
        )

    def _render(self) -> None:
        self._update_accessibility()
        if pg is None or self._plot is None or self._candles is None or self._axis is None:
            return
        if not self._data:
            self._stack.setCurrentWidget(self._empty)
            return

        self._axis.set_dates([item.local_date for item in self._data])
        self._paint_candles()
        self._plot.setXRange(-0.8, max(len(self._data) - 0.2, 1), padding=0.02)
        lows = [item.low_kg for item in self._data]
        highs = [item.high_kg for item in self._data]
        span = max(max(highs) - min(lows), 0.2)
        self._plot.setYRange(
            max(0.0, min(lows) - span * 0.12),
            max(highs) + span * 0.12,
            padding=0.0,
        )
        self._stack.setCurrentWidget(self._plot)

    def _mouse_moved(self, event: tuple[object, ...]) -> None:
        if (
            pg is None
            or self._plot is None
            or self._v_line is None
            or self._h_line is None
            or not self._data
            or not event
        ):
            return
        scene_pos = event[0]
        if not self._plot.sceneBoundingRect().contains(scene_pos):
            self._v_line.hide()
            self._h_line.hide()
            return
        view_pos = self._plot.getPlotItem().vb.mapSceneToView(scene_pos)
        index = int(round(view_pos.x()))
        if not 0 <= index < len(self._data):
            self._v_line.hide()
            self._h_line.hide()
            return
        candle = self._data[index]
        self._v_line.setPos(index)
        self._h_line.setPos(view_pos.y())
        self._v_line.show()
        self._h_line.show()
        viewport_pos = self._plot.mapFromScene(scene_pos)
        QToolTip.showText(self._plot.mapToGlobal(viewport_pos), self._tooltip(candle), self._plot)

    def _tooltip(self, candle: CandlePoint) -> str:
        actual_text = f"{candle.actual_weight_count} 次"
        source_text = ""
        if candle.open_source or candle.close_source:
            source_text = f"\n来源：O {candle.open_source or '-'} / C {candle.close_source or '-'}"
        return (
            f"{candle.local_date:%Y-%m-%d} · {candle.direction_text}\n"
            f"O {candle.open_kg:.2f}  H {candle.high_kg:.2f}  "
            f"L {candle.low_kg:.2f}  C {candle.close_kg:.2f} kg\n"
            f"摄入 {format_energy(candle.intake_kj, self._display_unit)}  "
            f"消耗 {format_energy(candle.burn_kj, self._display_unit)}\n"
            f"余额 {format_energy(candle.balance_kj, self._display_unit, signed=True)}  "
            f"实际称重 {actual_text}{source_text}"
        )


__all__ = ["CandlePoint", "CandlestickChart", "ColorMode"]
