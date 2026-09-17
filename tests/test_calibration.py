from datetime import date, timedelta
import unittest

from app.models.calibration import (
    CalibrationDay,
    EWMAWeightTrend,
    GridSearchCalibrationEngine,
)


class CalibrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = GridSearchCalibrationEngine()

    def test_one_actual_weight_does_not_fit(self) -> None:
        result = self.engine.fit(
            [CalibrationDay(date(2026, 8, 25), actual_weight_kg=70)]
        )
        self.assertFalse(result.fitted)
        self.assertEqual(result.calibration_kj_day, 0)
        self.assertEqual(result.weight_sample_count, 1)

    def test_two_weights_across_five_days_can_fit_positive_extra_burn(self) -> None:
        start = date(2026, 8, 20)
        expected_delta = 836.8
        days = [CalibrationDay(start + timedelta(days=index)) for index in range(6)]
        days[0] = CalibrationDay(start, actual_weight_kg=70)
        days[-1] = CalibrationDay(
            start + timedelta(days=5),
            actual_weight_kg=70 - expected_delta * 5 / 32216.8,
        )
        result = self.engine.fit(days)
        self.assertTrue(result.fitted)
        self.assertEqual(result.days_span, 5)
        # A 7-observation-span EWMA has alpha=.25, so the raw two-point
        # movement becomes one quarter as large in the fitted trend.
        self.assertAlmostEqual(result.calibration_kj_day, expected_delta / 4)
        self.assertAlmostEqual(result.rmse_kg or 0, 0, places=10)
        self.assertEqual(result.weight_sample_count, 2)
        self.assertEqual(result.trend_model_version, "ewma-observation-span-7-v1")

    def test_ewma_trend_is_deterministic_and_versioned(self) -> None:
        trend = EWMAWeightTrend(span=7)
        self.assertAlmostEqual(trend.alpha, 0.25)
        self.assertEqual(trend.smooth([70, 69, 71]), (70, 69.75, 70.0625))
        self.assertEqual(trend.model_version, "ewma-observation-span-7-v1")

    def test_forty_days_use_only_latest_thirty_natural_days(self) -> None:
        start = date(2026, 7, 1)
        days = [
            CalibrationDay(
                start + timedelta(days=index), actual_weight_kg=70
            )
            for index in range(40)
        ]
        result = self.engine.fit(days)
        self.assertEqual(result.used_day_count, 30)
        self.assertEqual(result.window_start, start + timedelta(days=10))
        self.assertEqual(result.window_end, start + timedelta(days=39))
        self.assertEqual(result.weight_sample_count, 30)
        self.assertAlmostEqual(result.calibration_kj_day, 0)


if __name__ == "__main__":
    unittest.main()
