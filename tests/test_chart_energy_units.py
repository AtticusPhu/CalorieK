"""Authored display-boundary regressions; run via the Windows review workflow."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime

from app.energy_units import kcal_to_kj

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:
    QApplication = None  # type: ignore[assignment]
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class ChartEnergyUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert QApplication is not None
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_treemap_unit_switch_preserves_canonical_data_and_rectangle_identity(self) -> None:
        from app.charts.treemap import TreemapItem, TreemapWidget

        chart = TreemapWidget()
        self.addCleanup(chart.close)
        chart.resize(600, 300)
        items = (
            TreemapItem("meal", "午餐", kcal_to_kj(100), "intake", protein_g=8.0),
            TreemapItem("walk", "步行", kcal_to_kj(200), "burn", duration_min=30.0),
        )
        chart.set_data(items)
        canonical_items = chart._items
        rectangles = chart.layout_snapshot()
        self.assertEqual(len(rectangles), 2)
        self.assertAlmostEqual(rectangles[1].rect.area / rectangles[0].rect.area, 2.0)
        self.assertIn("418.40 kJ", chart._tooltip(items[0]))
        self.assertIn("1255.20 kJ", chart.accessibleDescription())
        self.assertIn("蛋白质：8.00 g", chart._tooltip(items[0]))
        self.assertIn("持续时间：30 min", chart._tooltip(items[1]))

        chart._last_hover_key = "meal"
        chart.set_display_unit("kcal")
        self.assertIs(chart._items, canonical_items)
        self.assertIs(chart.layout_snapshot(), rectangles)
        self.assertIsNone(chart._last_hover_key)
        self.assertIn("100.00 kcal", chart._tooltip(items[0]))
        self.assertIn("300.00 kcal", chart.accessibleDescription())
        self.assertNotIn("kJ", chart.accessibleDescription())

        # An unchanged dashboard snapshot must not re-layout the same regions.
        chart.set_data(items)
        self.assertIs(chart.layout_snapshot(), rectangles)
        chart.set_display_unit("unsupported")
        self.assertIs(chart.layout_snapshot(), rectangles)
        self.assertIn("418.40 kJ", chart._tooltip(items[0]))
        self.assertNotIn("kcal", chart.accessibleDescription())

    def test_candle_tooltip_and_accessibility_switch_but_kg_geometry_does_not(self) -> None:
        from app.charts.candlestick import CandlePoint, CandlestickChart

        chart = CandlestickChart()
        self.addCleanup(chart.close)
        candle = CandlePoint(
            local_date=date(2026, 9, 15),
            open_kg=70.0,
            high_kg=70.1,
            low_kg=69.8,
            close_kg=69.9,
            intake_kj=kcal_to_kj(100),
            burn_kj=kcal_to_kj(200),
            balance_kj=kcal_to_kj(-100),
            actual_weight_count=2,
            open_source="实际称重",
            close_source="预测",
        )
        chart.set_data((candle,))
        canonical_data = chart._data
        self.assertIn("摄入 418.40 kJ", chart._tooltip(candle))
        self.assertIn("余额 -418.40 kJ", chart._tooltip(candle))
        self.assertIn("kJ", chart.accessibleDescription())
        self.assertIn("实际称重 2 次", chart._tooltip(candle))

        view_range = None
        picture = None
        if chart._plot is not None:
            chart._plot.setXRange(-0.25, 0.25, padding=0)
            chart._plot.setYRange(69.85, 70.05, padding=0)
            view_range = [axis[:] for axis in chart._plot.viewRange()]
            picture = chart._candles._picture

        for unit, energy in (("kcal", "100.00 kcal"), ("invalid", "418.40 kJ")):
            chart.set_display_unit(unit)
            chart.set_data((candle,))
            self.assertIs(chart._data, canonical_data)
            self.assertIn(energy, chart._tooltip(candle))
            self.assertIn(energy, chart.accessibleDescription())
            self.assertIn("C 69.90 kg", chart._tooltip(candle))
            if chart._plot is not None:
                self.assertEqual(chart._plot.viewRange(), view_range)
                self.assertIs(chart._candles._picture, picture)
                self.assertEqual(chart._plot.getAxis("left").labelUnits, "kg")

    def test_empty_charts_can_switch_unit_without_creating_data(self) -> None:
        from app.charts.candlestick import CandlestickChart
        from app.charts.treemap import TreemapWidget

        candle = CandlestickChart()
        treemap = TreemapWidget()
        self.addCleanup(candle.close)
        self.addCleanup(treemap.close)
        for unit in ("kj", "kcal", "unknown"):
            candle.set_display_unit(unit)
            treemap.set_display_unit(unit)
            self.assertEqual(candle._data, ())
            self.assertEqual(treemap.layout_snapshot(), ())
            self.assertIn("暂无", candle.accessibleDescription())
            self.assertIn("暂无", treemap.accessibleDescription())

    def test_treemap_rounding_never_removes_small_canonical_regions(self) -> None:
        from app.charts.treemap import TreemapItem, TreemapWidget

        chart = TreemapWidget()
        self.addCleanup(chart.close)
        chart.resize(600, 300)
        items = (
            TreemapItem("small", "微量摄入", 0.01234567890123, "intake"),
            TreemapItem("meal", "午餐", 1234.56789012345, "intake"),
            TreemapItem("burn", "消耗", -4567.89012345678, "burn"),
        )
        chart.set_data(items)
        regions = chart.layout_snapshot()
        self.assertEqual(len(regions), 3)
        self.assertGreater(regions[0].rect.area, 0.0)
        for unit in ("kcal", "kj") * 10:
            chart.set_display_unit(unit)
            self.assertIs(chart.layout_snapshot(), regions)
            self.assertEqual(chart._items, items)
            self.assertEqual(tuple(region.item for region in regions), items)
            self.assertIn(("0.00 kcal" if unit == "kcal" else "0.01 kJ"), chart._tooltip(items[0]))

    def test_dashboard_reformats_cached_snapshot_without_recalculation(self) -> None:
        from app.ui.context import CandleDTO, DashboardDTO, SettingsDTO, TreemapItemDTO
        from app.ui.dashboard import DashboardPage

        snapshot = DashboardDTO(
            as_of=datetime(2026, 9, 15, 12, 0),
            latest_actual_weight_kg=70.0,
            latest_actual_at=datetime(2026, 9, 15, 7, 0),
            predicted_weight_kg=69.9,
            predicted_change_kg=-0.1,
            intake_kj=kcal_to_kj(100),
            baseline_kj=kcal_to_kj(150),
            exercise_kj=kcal_to_kj(50),
            total_burn_kj=kcal_to_kj(200),
            balance_kj=kcal_to_kj(-100),
            calibration_kj_day=kcal_to_kj(-10),
            candles=(CandleDTO(date(2026, 9, 15), 70.0, 70.1, 69.8, 69.9),),
            treemap_items=(TreemapItemDTO("meal", "午餐", kcal_to_kj(100), "intake"),),
        )

        class SnapshotContext:
            unit = "kj"
            dashboard_reads = 0
            candle_mode = "china"
            treemap_mode = "intake_red"

            def get_dashboard(self):
                self.dashboard_reads += 1
                return snapshot

            def get_settings(self):
                return SettingsDTO(
                    candle_color_mode=self.candle_mode,
                    treemap_color_mode=self.treemap_mode,
                )

            def get_energy_display_unit(self):
                return self.unit

        context = SnapshotContext()
        dashboard = DashboardPage(context)
        self.addCleanup(dashboard.close)
        dashboard.refresh()
        self.assertTrue(dashboard.is_loaded)
        self.assertEqual(context.dashboard_reads, 1)
        self.assertEqual(dashboard.intake_card.value.text(), "418.40 kJ")
        self.assertEqual(dashboard.balance_card.value.text(), "-418.40 kJ")
        self.assertIn("个人校准 -41.84 kJ/日", dashboard.change_card.detail.text())
        self.assertIn("kJ", dashboard.intake_card.accessibleDescription())
        self.assertEqual(dashboard.treemap_title.text(), "今日 kJ 构成")
        rectangles = dashboard.treemap.layout_snapshot()
        canonical_candles = dashboard.candlestick_chart._data

        context.unit = "kcal"
        dashboard.refresh_display_unit()
        self.assertEqual(context.dashboard_reads, 1)
        self.assertIs(dashboard._last_dashboard, snapshot)
        self.assertIs(dashboard.treemap.layout_snapshot(), rectangles)
        self.assertIs(dashboard.candlestick_chart._data, canonical_candles)
        self.assertEqual(dashboard.actual_card.value.text(), "70.00 kg")
        self.assertEqual(dashboard.predicted_card.value.text(), "69.90 kg")
        self.assertEqual(dashboard.intake_card.value.text(), "100.00 kcal")
        self.assertEqual(dashboard.burn_card.value.text(), "200.00 kcal")
        self.assertEqual(dashboard.balance_card.value.text(), "-100.00 kcal")
        self.assertIn("150.00 kcal", dashboard.burn_card.detail.text())
        self.assertIn("50.00 kcal", dashboard.burn_card.detail.text())
        self.assertIn("-10.00 kcal/日", dashboard.change_card.detail.text())
        self.assertIn("kcal", dashboard.accessibleDescription())
        self.assertNotIn("kJ", dashboard.accessibleDescription())
        self.assertIn("kcal", dashboard.candle_helper.text())
        self.assertIn("kcal", dashboard.treemap_helper.text())
        self.assertEqual(dashboard.treemap_title.text(), "今日 kcal 构成")

        context.unit = "unexpected"
        dashboard.refresh_display_unit()
        self.assertEqual(context.dashboard_reads, 1)
        self.assertEqual(dashboard.intake_card.value.text(), "418.40 kJ")
        self.assertNotIn("kcal", dashboard.accessibleDescription())

        view_range = None
        chart = dashboard.candlestick_chart
        if chart._plot is not None:
            chart._plot.setXRange(-0.25, 0.25, padding=0)
            chart._plot.setYRange(69.85, 70.05, padding=0)
            view_range = [axis[:] for axis in chart._plot.viewRange()]
        context.candle_mode = "international"
        context.treemap_mode = "intake_green"
        dashboard.refresh_display_unit()
        self.assertEqual(context.dashboard_reads, 1)
        self.assertEqual(chart._color_mode, "international")
        self.assertEqual(dashboard.treemap._mode, "intake_green")
        self.assertIs(chart._data, canonical_candles)
        self.assertIs(dashboard.treemap.layout_snapshot(), rectangles)
        if chart._plot is not None:
            self.assertEqual(chart._plot.viewRange(), view_range)


if __name__ == "__main__":
    unittest.main()
