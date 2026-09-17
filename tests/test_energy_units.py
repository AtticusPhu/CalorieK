from __future__ import annotations

from math import isclose
import unittest

from app.energy_units import (
    DEFAULT_KJ_PER_KG,
    DEFAULT_ENERGY_DISPLAY_UNIT,
    KJ_PER_KCAL,
    energy_from_input,
    energy_to_display,
    energy_unit_label,
    format_energy,
    kcal_to_kj,
    kj_to_kcal,
    normalize_energy_unit,
)


class EnergyUnitTests(unittest.TestCase):
    def test_default_and_invalid_preferences_use_kj(self) -> None:
        self.assertEqual(DEFAULT_ENERGY_DISPLAY_UNIT, "kj")
        self.assertEqual(normalize_energy_unit("kcal"), "kcal")
        self.assertEqual(energy_unit_label("kcal"), "kcal")
        for value in (None, "", "kj", "KCAL", "kJ", "cal", 0, [], {}):
            with self.subTest(value=value):
                self.assertEqual(normalize_energy_unit(value), "kj")
                self.assertEqual(energy_unit_label(value), "kJ")
                self.assertEqual(energy_to_display(123.12345678901234, value), 123.12345678901234)

    def test_input_boundary_and_display_round_trip_do_not_round(self) -> None:
        for value in (1e-10, -27.1234567890123, 125000.123456789):
            with self.subTest(value=value):
                self.assertEqual(energy_from_input(value), value)
                self.assertEqual(energy_to_display(value), value)
                self.assertEqual(energy_from_input(value, "kcal"), kcal_to_kj(value))
                self.assertTrue(isclose(
                    energy_from_input(energy_to_display(value, "kcal"), "kcal"),
                    value, rel_tol=1e-15,
                ))

    def test_formatting_is_text_only_and_leaves_source_values_unchanged(self) -> None:
        original = {"intake_kj": 418.41234567890123, "balance_kj": -83.68}
        before = original.copy()
        self.assertEqual(format_energy(original["intake_kj"], decimals=1), "418.4 kJ")
        self.assertEqual(format_energy(original["intake_kj"], "kcal", decimals=2), "100.00 kcal")
        self.assertEqual(format_energy(original["balance_kj"], "kcal", signed=True), "-20.00 kcal")
        self.assertEqual(format_energy(83.68, "kcal", signed=True), "+20.00 kcal")
        self.assertEqual(format_energy(83.68, "invalid", decimals=2), "83.68 kJ")
        self.assertEqual(original, before)

    def test_fixed_conversion_and_default_weight_equivalence(self) -> None:
        self.assertEqual(KJ_PER_KCAL, 4.184)
        self.assertEqual(kcal_to_kj(1), 4.184)
        self.assertEqual(kj_to_kcal(4.184), 1.0)
        self.assertAlmostEqual(kcal_to_kj(7700), DEFAULT_KJ_PER_KG)
        self.assertEqual(DEFAULT_KJ_PER_KG, 32216.8)

    def test_conversion_preserves_small_values_without_rounding(self) -> None:
        self.assertTrue(isclose(kcal_to_kj(1e-10), 4.184e-10, rel_tol=1e-15))
        self.assertTrue(isclose(kj_to_kcal(4.184e-10), 1e-10, rel_tol=1e-15))

    def test_high_precision_values_round_trip_at_float_precision(self) -> None:
        for value in (0.00000012345, -27.1234567890123, 125000.123456789):
            with self.subTest(value=value):
                self.assertTrue(
                    isclose(kj_to_kcal(kcal_to_kj(value)), value, rel_tol=1e-15)
                )


if __name__ == "__main__":
    unittest.main()
