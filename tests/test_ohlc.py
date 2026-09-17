from datetime import date, datetime
import unittest

from app.models.ohlc import (
    EnergyEvent,
    EnergyEventType,
    MissingWeightAnchorError,
    OHLCGenerator,
    OHLCInput,
    WeightAnchor,
    WeightMeasurement,
)


DAY = date(2026, 8, 25)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 8, 25, hour, minute)


class OHLCGeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.generator = OHLCGenerator()

    def test_no_measurement_keeps_actual_candle_flat_but_predicts_gain(self) -> None:
        result = self.generator.generate_day(
            OHLCInput(
                day=DAY,
                previous_close_kg=70,
                baseline_kj=7531.2,
                events=(EnergyEvent(at(12), 12552, EnergyEventType.INTAKE),),
            )
        )
        self.assertEqual(
            (result.open_kg, result.high_kg, result.low_kg, result.close_kg),
            (70, 70, 70, 70),
        )
        self.assertGreater(result.predicted_close_kg, 70)
        self.assertEqual(result.actual_weight_count, 0)

    def test_first_day_without_any_anchor_fails_explicitly(self) -> None:
        with self.assertRaises(MissingWeightAnchorError):
            self.generator.generate_day(OHLCInput(day=DAY, baseline_kj=7531.2))

    def test_single_morning_measurement_is_open(self) -> None:
        result = self.generator.generate_day(
            OHLCInput(
                day=DAY,
                measurements=(WeightMeasurement(at(7, 30), 70),),
                baseline_kj=3221.68,
            )
        )
        self.assertAlmostEqual(result.open_kg, 70)
        self.assertAlmostEqual(result.close_kg, 69.9)
        self.assertAlmostEqual(result.trajectory[0].weight_kg, result.open_kg)
        self.assertAlmostEqual(result.trajectory[-1].weight_kg, result.close_kg)

    def test_single_evening_measurement_is_close_and_open_is_reversed(self) -> None:
        result = self.generator.generate_day(
            OHLCInput(
                day=DAY,
                measurements=(WeightMeasurement(at(22, 30), 69.8),),
                baseline_kj=3221.68,
            )
        )
        self.assertAlmostEqual(result.close_kg, 69.8)
        self.assertAlmostEqual(result.open_kg, 69.9)

    def test_manual_anchor_overrides_noon_rule(self) -> None:
        result = self.generator.generate_day(
            OHLCInput(
                day=DAY,
                measurements=(
                    WeightMeasurement(at(22, 30), 70, anchor=WeightAnchor.OPEN),
                ),
                baseline_kj=3221.68,
            )
        )
        self.assertAlmostEqual(result.open_kg, 70)
        self.assertAlmostEqual(result.close_kg, 69.9)

    def test_multiple_measurements_use_only_first_and_last_for_open_close(self) -> None:
        measurements = (
            WeightMeasurement(at(7, 30), 70.10),
            WeightMeasurement(at(13, 20), 70.56),
            WeightMeasurement(at(19), 70.42),
            WeightMeasurement(at(23, 15), 70.21),
        )
        result = self.generator.generate_day(
            OHLCInput(day=DAY, measurements=measurements, baseline_kj=7531.2)
        )
        self.assertAlmostEqual(result.open_kg, 70.10)
        self.assertAlmostEqual(result.close_kg, 70.21)
        self.assertEqual(result.actual_weight_count, 4)
        self.assertGreaterEqual(result.high_kg, max(result.open_kg, result.close_kg))
        self.assertLessEqual(result.low_kg, min(result.open_kg, result.close_kg))

    def test_intraday_events_affect_high_low_and_positive_delta_lowers_model(self) -> None:
        base = OHLCInput(
            day=DAY,
            measurements=(WeightMeasurement(at(7), 70, WeightAnchor.OPEN),),
            baseline_kj=4184,
            events=(
                EnergyEvent(at(12), 8368, EnergyEventType.INTAKE, "lunch"),
                EnergyEvent(at(18), 2092, EnergyEventType.EXERCISE, "run"),
            ),
        )
        calibrated = OHLCInput(
            day=DAY,
            measurements=base.measurements,
            events=base.events,
            baseline_kj=base.baseline_kj,
            calibration_kj_day=1255.2,
        )
        plain_result = self.generator.generate_day(base)
        calibrated_result = self.generator.generate_day(calibrated)
        self.assertGreater(plain_result.high_kg, plain_result.open_kg)
        self.assertLess(calibrated_result.predicted_close_kg, plain_result.predicted_close_kg)


if __name__ == "__main__":
    unittest.main()
