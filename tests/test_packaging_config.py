from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingConfigTests(unittest.TestCase):
    def test_release_executable_does_not_open_console_window(self) -> None:
        spec = (PROJECT_ROOT / "CalorieK.spec").read_text(encoding="utf-8")
        self.assertIn("console=False", spec)
        self.assertNotIn("console=True", spec)
        self.assertIn('"concrt140.dll"', spec)
        self.assertIn('"vcomp140.dll"', spec)
        self.assertIn('!= "icuuc.dll"', spec)
        self.assertIn('startswith("icudt")', spec)

    def test_gui_subsystem_smoke_test_waits_for_real_exe_exit_code(self) -> None:
        script = (PROJECT_ROOT / "build_windows.ps1").read_text(encoding="ascii")
        self.assertIn("Start-Process -FilePath $Exe", script)
        self.assertIn("-WindowStyle Hidden", script)
        self.assertIn("-Wait -PassThru", script)
        self.assertIn("-RedirectStandardError", script)
        self.assertIn("-RedirectStandardOutput", script)
        self.assertIn("$SmokeProcess.ExitCode", script)
        self.assertIn("$SmokeDataArgument", script)


if __name__ == "__main__":
    unittest.main()
