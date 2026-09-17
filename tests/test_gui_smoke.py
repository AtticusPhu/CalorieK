from __future__ import annotations

import os
import tempfile
import unittest
from datetime import date, time

from app.energy_units import kcal_to_kj
from app.version import APP_DISPLAY_NAME

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication

    HAS_QT = True
except ImportError:
    QApplication = None  # type: ignore[assignment]
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert QApplication is not None
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_construct_and_refresh_complete_main_window(self) -> None:
        from app.application import ApplicationContext
        from app.ui.context import ProfileDraft
        from app.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as temporary:
            context = ApplicationContext(temporary)
            context.save_initial_profile(
                ProfileDraft(
                    sex="male",
                    birth_date=date(1990, 1, 1),
                    height_cm=175.0,
                    current_weight_kg=70.0,
                    wake_time=time(7, 0),
                    sleep_time=time(23, 0),
                )
            )
            window = MainWindow(context, ensure_profile_on_show=False)
            window.show()
            self.qt_app.processEvents()
            window.dashboard_page.refresh()
            self.qt_app.processEvents()
            self.assertEqual(window.windowTitle(), APP_DISPLAY_NAME)
            self.assertEqual(window.pages.count(), 5)
            self.assertTrue(window.dashboard_page.actual_card.value.text().endswith("kg"))
            window.exercise_library_page.refresh()
            self.qt_app.processEvents()
            self.assertGreaterEqual(window.exercise_library_page.table.rowCount(), 4)
            window.close()

    def test_food_dialog_records_named_default_serving(self) -> None:
        from app.application import ApplicationContext
        from app.ui.context import FoodServingDTO, ProfileDraft
        from app.ui.food_dialog import FoodDialog

        with tempfile.TemporaryDirectory() as temporary:
            context = ApplicationContext(temporary)
            context.save_initial_profile(
                ProfileDraft(
                    sex="male",
                    birth_date=date(1990, 1, 1),
                    height_cm=175.0,
                    current_weight_kg=70.0,
                    wake_time=time(7, 0),
                    sleep_time=time(23, 0),
                )
            )
            drink_id = context.foods.create_food(
                name="GUI测试饮品",
                category="其它",
                basis_unit="ml",
                kj=kcal_to_kj(30.0),
                default_serving=200.0,
            )
            dialog = FoodDialog(context)
            dialog._fill_list(dialog.lists[3], "search", "GUI测试饮品")
            dialog.lists[3].setCurrentRow(0)
            self.qt_app.processEvents()

            serving_index = next(
                index
                for index in range(dialog.unit_combo.count())
                if isinstance(dialog.unit_combo.itemData(index), FoodServingDTO)
                and dialog.unit_combo.itemData(index).name == "默认份"
            )
            dialog.unit_combo.setCurrentIndex(serving_index)
            self.qt_app.processEvents()
            self.assertEqual(dialog.amount_spin.value(), 1.0)
            dialog.amount_spin.setValue(2.0)
            dialog._save()

            event = next(
                row
                for row in context.foods.list_intake_events()
                if row["source_id"] == drink_id
            )
            self.assertEqual(event["amount"], 2.0)
            self.assertEqual(event["unit"], "serving")
            self.assertAlmostEqual(float(event["kj_snapshot"]), kcal_to_kj(120.0))


if __name__ == "__main__":
    unittest.main()
