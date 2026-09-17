"""Precision regressions for numeric and energy input boundaries."""

from __future__ import annotations

import os
import unittest

from app.energy_units import kcal_to_kj

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QDoubleSpinBox
    from app.ui.numeric_input import EnergySpinBox, KcalEnergySpinBox, PreciseDoubleSpinBox
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class NumericInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_untouched_editor_preserves_stored_float(self) -> None:
        spin = PreciseDoubleSpinBox()
        spin.setRange(0, 10000)
        spin.setDecimals(1)
        original = 123.12345678901234
        spin.setValue(original)
        self.assertGreaterEqual(spin.decimals(), 12)
        self.assertEqual(spin.text(), "123.12")
        self.assertEqual(spin.value(), original)
        QDoubleSpinBox.setValue(spin, 125.987654321012)
        self.assertAlmostEqual(spin.value(), 125.987654321012, places=11)

    def test_two_decimal_presentation_does_not_quantize_loaded_value(self) -> None:
        spin = PreciseDoubleSpinBox()
        spin.setRange(0, 10000)
        original = 165.12345678901234
        spin.setValue(original)
        self.assertEqual(spin.text(), "165.12")
        self.assertEqual(spin.value(), original)
        spin.setDecimals(2)
        self.assertEqual(spin.text(), "165.12")
        self.assertEqual(spin.value(), original)

    def test_energy_editor_keeps_original_kj_without_conversion_drift(self) -> None:
        spin = KcalEnergySpinBox()
        spin.setRange(0, 10000)
        original = 123.12345678901234
        spin.set_energy_kj(original)
        self.assertEqual(spin.energy_kj(), original)
        self.assertEqual(spin.suffix(), " kcal")
        QDoubleSpinBox.setValue(spin, 321.987654321)
        self.assertAlmostEqual(spin.energy_kj(), kcal_to_kj(321.987654321), places=9)

    def test_programmatic_input_replaces_loaded_canonical_value(self) -> None:
        spin = KcalEnergySpinBox()
        spin.setRange(0, 10000)
        spin.set_energy_kj(321.98765432101234)
        spin.setValue(50.12345678901234)
        self.assertEqual(spin.energy_kj(), kcal_to_kj(50.12345678901234))

    def test_loading_value_outside_initial_ui_range_does_not_clamp(self) -> None:
        spin = KcalEnergySpinBox()
        spin.setRange(0, 100)
        original = kcal_to_kj(12345.123456789012)
        spin.set_energy_kj(original)
        self.assertEqual(spin.energy_kj(), original)

    def test_default_kj_and_repeated_switch_preserve_exact_loaded_float(self) -> None:
        spin = EnergySpinBox()
        spin.set_kj_range(0, 10000)
        self.assertEqual(spin.display_unit, "kj")
        self.assertEqual(spin.suffix(), " kJ")
        for original in (1e-14, 123.12345678901234, 125000.123456789):
            spin.set_energy_kj(original)
            for unit in ("kcal", "kj", "kcal", "broken", "kj"):
                with self.subTest(original=original, unit=unit):
                    spin.set_display_unit(unit)
                    self.assertEqual(spin.energy_kj(), original)

    def test_direct_kj_input_and_converted_kcal_input(self) -> None:
        spin = EnergySpinBox()
        spin.set_kj_range(0, 10000)
        entered = 123.12345678901234
        spin.setValue(entered)
        self.assertEqual(spin.energy_kj(), entered)
        spin.set_display_unit("kcal")
        spin.setValue(entered)
        self.assertEqual(spin.energy_kj(), kcal_to_kj(entered))
        spin.set_display_unit("kj")
        self.assertEqual(spin.energy_kj(), kcal_to_kj(entered))

    def test_switch_preserves_canonical_limits_steps_and_emits_no_transient_values(self) -> None:
        spin = EnergySpinBox()
        spin.set_kj_range(0, 4184)
        spin.set_kj_single_step(41.84)
        spin.set_unit_tail("/kg")
        spin.set_energy_kj(836.8)
        changes = []
        spin.valueChanged.connect(changes.append)
        spin.set_display_unit("kcal")
        self.assertEqual(spin.suffix(), " kcal/kg")
        self.assertAlmostEqual(spin.maximum(), 1000)
        self.assertAlmostEqual(spin.singleStep(), 10)
        self.assertEqual(spin.energy_kj(), 836.8)
        spin.set_display_unit("kj")
        self.assertEqual(spin.suffix(), " kJ/kg")
        self.assertEqual(spin.maximum(), 4184)
        self.assertEqual(spin.singleStep(), 41.84)
        self.assertEqual(changes, [])

    def test_switch_commits_pending_text_using_old_unit(self) -> None:
        spin = EnergySpinBox(unit="kcal")
        spin.set_kj_range(0, 10000)
        spin.setKeyboardTracking(False)
        spin.set_energy_kj(41.84)
        spin.lineEdit().setText("25.5 kcal")
        spin.set_display_unit("kj")
        self.assertEqual(spin.energy_kj(), kcal_to_kj(25.5))


if __name__ == "__main__":
    unittest.main()
