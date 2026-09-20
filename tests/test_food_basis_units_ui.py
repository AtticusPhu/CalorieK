"""CAL-13 editor coverage; synthetic DTOs, no real database or user data."""

import os
import unittest
from unittest.mock import Mock

from app.ui.context import FoodDTO

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import QApplication, QLabel
    from app.ui.food_library import FoodEditorDialog
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class FoodBasisUnitUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.context = Mock()
        self.context.get_energy_display_unit.return_value = "kj"

    def _editor(self, food=None):
        self.context.save_food.reset_mock()
        dialog = FoodEditorDialog(self.context, food)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.close)
        return dialog

    def test_every_existing_food_locks_unit_but_keeps_basis_amount_editable(self) -> None:
        for provenance in ("builtin", "legacy", "native"):
            for unit in ("g", "ml"):
                with self.subTest(provenance=provenance, unit=unit):
                    food = FoodDTO(
                        food_id=1, name="Synthetic food", category="Synthetic", basis_unit=unit,
                        is_builtin=provenance == "builtin",
                        legacy_nutrition_available=provenance != "native",
                    )
                    dialog = self._editor(food)
                    self.assertFalse(dialog.basis_unit_combo.isEnabled())
                    self.assertEqual(dialog.basis_unit_combo.currentData(), unit)
                    self.assertTrue(dialog.basis_amount_spin.isEnabled())
                    self.assertIn("新建食品", dialog.basis_unit_combo.toolTip())
                    # Keep the explanation visible even if disabled controls
                    # do not display tooltips on the user's Windows/Qt theme.
                    self.assertTrue(any(
                        label.text() == dialog.basis_unit_combo.toolTip()
                        for label in dialog.findChildren(QLabel)
                    ))
                    dialog.basis_amount_spin.setValue(200)
                    dialog._save()
                    self.context.save_food.assert_called_once()
                    saved = self.context.save_food.call_args.args[0]
                    self.assertEqual((saved.food_id, saved.basis_unit, saved.basis_amount), (1, unit, 200))

    def test_new_food_can_select_and_save_either_unit(self) -> None:
        for unit in ("g", "ml"):
            with self.subTest(unit=unit):
                dialog = self._editor()
                self.assertTrue(dialog.basis_unit_combo.isEnabled())
                dialog.name_edit.setText("Synthetic new food")
                dialog.basis_unit_combo.setCurrentIndex(dialog.basis_unit_combo.findData(unit))
                self.assertEqual(dialog.basis_unit_combo.currentData(), unit)
                dialog._save()
                self.context.save_food.assert_called_once()
                saved = self.context.save_food.call_args.args[0]
                self.assertIsNone(saved.food_id)
                self.assertEqual(saved.basis_unit, unit)


if __name__ == "__main__":
    unittest.main()
