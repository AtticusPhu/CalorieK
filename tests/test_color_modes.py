from __future__ import annotations

import unittest
from datetime import date, datetime

from app.color_modes import candle_palette, treemap_palette
from app.models.ohlc import OHLCGenerator, OHLCInput, WeightMeasurement


class ColorModeTests(unittest.TestCase):
    def test_candle_mode_swaps_colors_without_changing_values(self) -> None:
        result = OHLCGenerator().generate_day(
            OHLCInput(
                day=date(2026, 8, 25),
                measurements=(
                    WeightMeasurement(datetime(2026, 8, 25, 7, 30), 70.0),
                ),
                baseline_kj=7531.2,
            )
        )
        values_before = (
            result.open_kg,
            result.high_kg,
            result.low_kg,
            result.close_kg,
        )
        china = candle_palette("china")
        international = candle_palette("international")
        self.assertEqual(china[0], international[1])
        self.assertEqual(china[1], international[0])
        self.assertEqual(
            values_before,
            (result.open_kg, result.high_kg, result.low_kg, result.close_kg),
        )

    def test_treemap_mode_swaps_intake_and_burn_palettes(self) -> None:
        self.assertEqual(
            treemap_palette("intake", "intake_red"),
            treemap_palette("burn", "intake_green"),
        )
        self.assertEqual(
            treemap_palette("burn", "intake_red"),
            treemap_palette("intake", "intake_green"),
        )


if __name__ == "__main__":
    unittest.main()
