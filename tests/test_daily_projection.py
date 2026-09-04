from datetime import date, datetime
import unittest

from app.models.daily_projection import (
    DailyProjectionInput,
    DefaultDailyProjectionService,
)
from app.models.ohlc import EnergyEvent, EnergyEventType


DAY = date(2026, 8, 25)


class DailyProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = DefaultDailyProjectionService()

    def test_no_actual_anchor_returns_none(self) -> None:
        result = self.service.calculate_today_projection(
            DailyProjectionInput(
                day=DAY,
                as_of=datetime(2026, 8, 25, 12),
                latest_actual_weight_kg=None,
                latest_actual_at=None,
                remaining_baseline_burn_kcal=1000,
            )
        )
        self.assertIsNone(result)

    def test_projection_uses_post_anchor_events_and_remaining_burn(self) -> None:
        inputs = DailyProjectionInput(
            day=DAY,
            as_of=datetime(2026, 8, 25, 12),
            latest_actual_weight_kg=70,
            latest_actual_at=datetime(2026, 8, 25, 8),
            events=(
                EnergyEvent(
                    datetime(2026, 8, 25, 7), 500, EnergyEventType.INTAKE, "before weigh"
                ),
                EnergyEvent(
                    datetime(2026, 8, 25, 10), 1000, EnergyEventType.INTAKE, "meal"
                ),
                EnergyEvent(
                    datetime(2026, 8, 25, 11), 300, EnergyEventType.EXERCISE, "run"
                ),
            ),
            baseline_burn_since_anchor_kcal=400,
            remaining_baseline_burn_kcal=1000,
            today_baseline_burn_elapsed_kcal=600,
            calibration_kcal_day=150,
            calibration_days_from_anchor_to_end=2 / 3,
        )
        result = self.service.calculate_today_projection(inputs)
        assert result is not None
        expected_energy = 1000 - 300 - 400 - 1000 - 150 * (2 / 3)
        self.assertAlmostEqual(
            result.predicted_close_kg, 70 + expected_energy / 7700
        )
        self.assertAlmostEqual(result.today_intake_kcal, 1500)
        self.assertAlmostEqual(result.today_exercise_kcal, 300)
        self.assertAlmostEqual(result.today_total_burn_projected_kcal, 1900)

    def test_positive_delta_always_lowers_prediction(self) -> None:
        common = dict(
            day=DAY,
            as_of=datetime(2026, 8, 25, 12),
            latest_actual_weight_kg=70,
            latest_actual_at=datetime(2026, 8, 25, 8),
            remaining_baseline_burn_kcal=1000,
            calibration_days_from_anchor_to_end=1,
        )
        plain = self.service.calculate_today_projection(DailyProjectionInput(**common))
        calibrated = self.service.calculate_today_projection(
            DailyProjectionInput(**common, calibration_kcal_day=200)
        )
        assert plain is not None and calibrated is not None
        self.assertLess(calibrated.predicted_close_kg, plain.predicted_close_kg)


if __name__ == "__main__":
    unittest.main()
