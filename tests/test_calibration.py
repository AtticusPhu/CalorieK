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


    def test_grid_search_precomputes_observation_energy_once(self) -> None:
        class CountingDay:
            accesses = 0

            def __init__(self, day, actual_weight_kg=None):
                self.day = day
                self.actual_weight_kg = actual_weight_kg

            @property
            def uncalibrated_balance_kj(self):
                type(self).accesses += 1
                return 0.0

        start = date(2026, 7, 1)
        days = [
            CountingDay(
                start + timedelta(days=index),
                actual_weight_kg=70.0 if index in (0, 29) else None,
            )
            for index in range(30)
        ]
        engine = GridSearchCalibrationEngine(
            minimum_kj_day=-8.368, maximum_kj_day=8.368, step_kj_day=4.184
        )

        result = engine.fit(days)

        self.assertTrue(result.fitted)
        # Day energy is independent of candidate δ. Each natural day after the
        # anchor is therefore read once, not once per grid candidate.
        self.assertEqual(CountingDay.accesses, 29)

    def test_grid_search_keeps_weight_model_replaceable(self) -> None:
        class ReplacementWeightModel:
            def energy_to_weight_delta(self, energy_kj):
                return float(energy_kj) / 30000.0

            def project_weight(
                self, start_weight_kg, *, intake_kj=0.0, baseline_kj=0.0,
                exercise_kj=0.0, calibration_kj_day=0.0, days=1.0,
            ):
                return float(start_weight_kg) + self.energy_to_weight_delta(
                    float(intake_kj) - float(baseline_kj) - float(exercise_kj)
                    - float(calibration_kj_day) * float(days)
                )

        start = date(2026, 8, 20)
        days = [CalibrationDay(start + timedelta(days=index)) for index in range(6)]
        days[0] = CalibrationDay(start, actual_weight_kg=70)
        days[-1] = CalibrationDay(start + timedelta(days=5), actual_weight_kg=69.99)

        result = GridSearchCalibrationEngine(
            weight_model=ReplacementWeightModel()
        ).fit(days)

        self.assertTrue(result.fitted)

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
