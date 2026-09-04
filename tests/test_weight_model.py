import unittest

from app.models.weight_model import EnergyBalance, SimpleEnergyWeightModel


class SimpleEnergyWeightModelTests(unittest.TestCase):
    def test_default_energy_equivalence_is_configurable_7700(self) -> None:
        model = SimpleEnergyWeightModel()
        self.assertAlmostEqual(model.energy_to_weight_delta(7700), 1.0)
        self.assertAlmostEqual(model.energy_to_weight_delta(-3850), -0.5)
        custom = SimpleEnergyWeightModel(kcal_per_kg=7000)
        self.assertAlmostEqual(custom.energy_to_weight_delta(700), 0.1)

    def test_positive_calibration_is_extra_burn_and_lowers_prediction(self) -> None:
        model = SimpleEnergyWeightModel()
        without_calibration = model.project_weight(
            70, intake_kcal=2300, baseline_kcal=2000, exercise_kcal=300
        )
        with_calibration = model.project_weight(
            70,
            intake_kcal=2300,
            baseline_kcal=2000,
            exercise_kcal=300,
            calibration_kcal_day=200,
        )
        self.assertAlmostEqual(without_calibration, 70.0)
        self.assertAlmostEqual(with_calibration, 70 - 200 / 7700)
        self.assertLess(with_calibration, without_calibration)

    def test_energy_balance_rejects_negative_raw_components(self) -> None:
        with self.assertRaises(ValueError):
            EnergyBalance(intake_kcal=-1)
        with self.assertRaises(ValueError):
            SimpleEnergyWeightModel(kcal_per_kg=0)


if __name__ == "__main__":
    unittest.main()
