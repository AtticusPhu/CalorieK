from __future__ import annotations

import unittest
from pathlib import Path

from main import build_parser


class MainCliTests(unittest.TestCase):
    def test_hidden_smoke_test_flag_and_data_dir_parse(self) -> None:
        parsed = build_parser().parse_args(
            ["--smoke-test", "--data-dir", "C:/Temp/CalorieK-smoke"]
        )
        self.assertTrue(parsed.smoke_test)
        self.assertEqual(parsed.data_dir, Path("C:/Temp/CalorieK-smoke"))

    def test_smoke_test_flag_is_not_advertised_in_help(self) -> None:
        self.assertNotIn("--smoke-test", build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
