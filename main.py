"""CalorieK Windows desktop entry point."""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []


def prepare_frozen_qt_dll_paths() -> None:
    """Register bundled Qt DLL folders before importing PySide on Windows."""

    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    for folder in (bundle_root / "PySide6", bundle_root / "shiboken6"):
        if folder.is_dir():
            # Keep each handle alive for the lifetime of the process.  Python 3.8+
            # removes the directory from the loader search path when it is closed.
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(folder)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CalorieK 体重热量 K 线")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="覆盖本次运行使用的外置数据目录",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    prepare_frozen_qt_dll_paths()
    try:
        # Import the binding layers in dependency order. PyInstaller's
        # one-folder loader can otherwise ask Windows to initialize
        # QtWidgets before the QtCore/QtGui extension modules have registered
        # their Shiboken types and DLL dependencies.
        from PySide6 import QtCore as _QtCore  # noqa: F401
        from PySide6 import QtGui as _QtGui  # noqa: F401
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        print(
            "CalorieK 需要 PySide6。请使用 Python 3.14 创建虚拟环境并运行 "
            "`python -m pip install -r requirements.txt`。\n"
            f"详细错误：{exc}",
            file=sys.stderr,
        )
        return 2

    from app.application import ApplicationContext
    from app.ui.main_window import MainWindow, ensure_initial_profile

    application = QApplication(sys.argv[:1] + (argv or []))
    application.setApplicationName("CalorieK")
    application.setApplicationDisplayName("CalorieK · 体重热量 K 线")
    application.setOrganizationName("CalorieK")
    application.setStyle("Fusion")

    try:
        context = ApplicationContext(arguments.data_dir)
    except Exception as exc:
        QMessageBox.critical(
            None,
            "CalorieK 无法启动",
            f"数据库初始化失败。\n\n{exc}",
        )
        return 1

    def show_unhandled(exc_type, exc_value, exc_traceback) -> None:  # type: ignore[no-untyped-def]
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        QMessageBox.critical(
            None,
            "CalorieK 遇到错误",
            f"发生未处理异常，已保存的 SQLite 事务不会被部分提交。\n\n{exc_value}",
        )

    sys.excepthook = show_unhandled

    if arguments.smoke_test:
        # Packaging verification must never touch a user's normal data directory.
        if arguments.data_dir is None:
            print("--smoke-test 必须同时指定临时 --data-dir。", file=sys.stderr)
            return 2
        if not context.has_profile():
            from datetime import date, time

            from app.ui.context import ProfileDraft

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
        application.processEvents()
        window.dashboard_page.refresh()
        application.processEvents()
        if window.pages.count() < 5:
            print("GUI smoke test failed: expected all V1 pages.", file=sys.stderr)
            window.close()
            return 1
        print("CalorieK packaged GUI smoke test: OK")
        window.close()
        return 0

    if not ensure_initial_profile(context):
        return 0
    window = MainWindow(context, ensure_profile_on_show=False)
    window.show()
    return int(application.exec())


if __name__ == "__main__":
    raise SystemExit(main())
