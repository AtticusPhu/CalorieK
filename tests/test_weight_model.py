import unittest

from app.models.weight_model import EnergyBalance, SimpleEnergyWeightModel


class SimpleEnergyWeightModelTests(unittest.TestCase):
    def test_default_energy_equivalence_is_configurable_in_kj(self) -> None:
        model = SimpleEnergyWeightModel()
        self.assertAlmostEqual(model.energy_to_weight_delta(32216.8), 1.0)
        self.assertAlmostEqual(model.energy_to_weight_delta(-16108.4), -0.5)
        custom = SimpleEnergyWeightModel(kj_per_kg=29288)
        self.assertAlmostEqual(custom.energy_to_weight_delta(2928.8), 0.1)

    def test_positive_calibration_is_extra_burn_and_lowers_prediction(self) -> None:
        model = SimpleEnergyWeightModel()
        without_calibration = model.project_weight(
            70, intake_kj=9623.2, baseline_kj=8368, exercise_kj=1255.2
        )
        with_calibration = model.project_weight(
            70,
            intake_kj=9623.2,
            baseline_kj=8368,
            exercise_kj=1255.2,
            calibration_kj_day=836.8,
        )
        self.assertAlmostEqual(without_calibration, 70.0)
        self.assertAlmostEqual(with_calibration, 70 - 836.8 / 32216.8)
        self.assertLess(with_calibration, without_calibration)

    def test_energy_balance_rejects_negative_raw_components(self) -> None:
        with self.assertRaises(ValueError):
            EnergyBalance(intake_kj=-1)
        with self.assertRaises(ValueError):
            SimpleEnergyWeightModel(kj_per_kg=0)


if __name__ == "__main__":
    unittest.main()
