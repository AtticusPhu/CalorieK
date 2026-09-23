"""Release metadata regressions; execution belongs to the reviewed Windows BAT."""

from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

import app
from app.db.database import LATEST_SCHEMA_VERSION
from app.version import (
    APP_DISPLAY_NAME, APP_NAME, APP_VERSION, CALCULATION_VERSION, SCHEMA_VERSION,
)
from main import main


class VersionMetadataTests(unittest.TestCase):
    def test_nutrition_release_keeps_independent_energy_model_generation(self) -> None:
        self.assertEqual(APP_VERSION, "0.0.3")
        self.assertEqual(SCHEMA_VERSION, 3)
        self.assertEqual(LATEST_SCHEMA_VERSION, SCHEMA_VERSION)
        self.assertEqual(CALCULATION_VERSION, "v2-simple-energy-kj-1")
        self.assertEqual(APP_DISPLAY_NAME, f"{APP_NAME} v{APP_VERSION} · 体重热量 K 线")

    def test_package_exports_share_the_version_source(self) -> None:
        self.assertEqual(app.APP_VERSION, APP_VERSION)
        self.assertEqual(app.SCHEMA_VERSION, SCHEMA_VERSION)
        self.assertEqual(app.CALCULATION_VERSION, CALCULATION_VERSION)

    def test_cli_version_exits_before_gui_or_database_startup(self) -> None:
        output = StringIO()
        with (
            redirect_stdout(output),
            patch("main.prepare_frozen_qt_dll_paths") as prepare_qt,
            patch("app.application.ApplicationContext") as context,
            self.assertRaises(SystemExit) as stopped,
        ):
            main(["--version"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), f"{APP_NAME} {APP_VERSION}")
        prepare_qt.assert_not_called()
        context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
