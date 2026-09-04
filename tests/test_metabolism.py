from datetime import date, datetime, time
import unittest

from app.models.metabolism import MifflinStJeorModel, Sex, calculate_age


class MifflinStJeorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.model = MifflinStJeorModel()

    def test_male_and_female_formula(self) -> None:
        male = self.model.calculate_rmr(
            sex=Sex.MALE, weight_kg=70, height_cm=175, age_years=30
        )
        female = self.model.calculate_rmr(
            sex="female", weight_kg=70, height_cm=175, age_years=30
        )
        self.assertAlmostEqual(male, 1648.75)
        self.assertAlmostEqual(female, 1482.75)

    def test_age_is_calculated_for_requested_date(self) -> None:
        born = date(2000, 9, 1)
        self.assertEqual(calculate_age(born, date(2026, 8, 31)), 25)
        self.assertEqual(calculate_age(born, date(2026, 9, 1)), 26)

    def test_baseline_interval_crosses_midnight(self) -> None:
        result = self.model.calculate_baseline_burn(
            rmr_kcal_day=2400,
            start_at=datetime(2026, 8, 25, 22),
            end_at=datetime(2026, 8, 26, 8),
            wake_time=time(7),
            sleep_time=time(23),
        )
        self.assertAlmostEqual(result.awake_hours, 2.0)
        self.assertAlmostEqual(result.sleep_hours, 8.0)
        self.assertAlmostEqual(result.total_kcal, 1000.0)

    def test_full_natural_day_uses_awake_and_sleep_multipliers(self) -> None:
        result = self.model.calculate_day_baseline(
            local_date=date(2026, 8, 25),
            rmr_kcal_day=2400,
            wake_time=time(7),
            sleep_time=time(23),
        )
        self.assertAlmostEqual(result.awake_hours, 16.0)
        self.assertAlmostEqual(result.sleep_hours, 8.0)
        self.assertAlmostEqual(result.total_kcal, 2680.0)

    def test_night_shift_awake_period_can_cross_midnight(self) -> None:
        result = self.model.calculate_baseline_burn(
            rmr_kcal_day=2400,
            start_at=datetime(2026, 8, 25, 23),
            end_at=datetime(2026, 8, 26, 6),
            wake_time=time(20),
            sleep_time=time(4),
        )
        self.assertAlmostEqual(result.awake_hours, 5.0)
        self.assertAlmostEqual(result.sleep_hours, 2.0)


if __name__ == "__main__":
    unittest.main()
