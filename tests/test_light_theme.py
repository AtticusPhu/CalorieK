"""Light-theme regressions for the user-run Windows validation workflow."""

from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from app.ui.theme import TOKENS, application_stylesheet

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import (
        QApplication,
        QComboBox,
        QDateEdit,
        QDateTimeEdit,
        QDialog,
        QDoubleSpinBox,
        QMenu,
        QSpinBox,
        QTableWidget,
        QToolButton,
    )

    HAS_QT = True
except ImportError:
    HAS_QT = False


class ThemeStylesheetTests(unittest.TestCase):
    def test_popup_and_native_control_selectors_are_application_wide(self) -> None:
        stylesheet = application_stylesheet()
        for selector in (
            "QComboBox QAbstractItemView",
            "QDateEdit::drop-down",
            "QDateTimeEdit::drop-down",
            "QAbstractSpinBox::up-button",
            "QAbstractSpinBox::down-button",
            "QCalendarWidget QWidget#qt_calendar_navigationbar",
            "QCalendarWidget QSpinBox",
            "QCalendarWidget QAbstractItemView",
            "QTableCornerButton::section",
            "QMenu::item:selected",
        ):
            with self.subTest(selector=selector):
                self.assertIn(selector, stylesheet)
        self.assertIn("qproperty-gridVisible: true", stylesheet)


@unittest.skipUnless(HAS_QT, "PySide6 is not installed")
class LightThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.old_palette = QPalette(self.qt_app.palette())
        self.old_stylesheet = self.qt_app.styleSheet()
        self.old_color_scheme = self.qt_app.styleHints().colorScheme()
        self.old_theme_tokens = getattr(self.qt_app, "_caloriek_light_theme_tokens", None)
        # QStyleSheetStyle has no object name. Temporarily remove the sheet to
        # record its base style so this suite does not leave Fusion installed.
        self.qt_app.setStyleSheet("")
        self.old_style_name = self.qt_app.style().objectName()
        self.qt_app.setStyleSheet(self.old_stylesheet)
        self.widgets = []

    def tearDown(self) -> None:
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.qt_app.setStyleSheet("")
        self.qt_app.styleHints().setColorScheme(self.old_color_scheme)
        self.qt_app.setStyle(self.old_style_name)
        self.qt_app.setPalette(self.old_palette)
        self.qt_app.setStyleSheet(self.old_stylesheet)
        if self.old_theme_tokens is None:
            if hasattr(self.qt_app, "_caloriek_light_theme_tokens"):
                del self.qt_app._caloriek_light_theme_tokens
        else:
            self.qt_app._caloriek_light_theme_tokens = self.old_theme_tokens

    def use_dark_starting_palette(self) -> None:
        dark_palette = QPalette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            for role in (
                QPalette.ColorRole.Window,
                QPalette.ColorRole.Base,
                QPalette.ColorRole.Button,
            ):
                dark_palette.setColor(group, role, QColor("#111111"))
            for role in (
                QPalette.ColorRole.Text,
                QPalette.ColorRole.ButtonText,
                QPalette.ColorRole.WindowText,
            ):
                dark_palette.setColor(group, role, QColor("#eeeeee"))
        self.qt_app.setStyleSheet("")
        self.qt_app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        self.qt_app.setPalette(dark_palette)

    def test_palette_defines_light_active_inactive_and_disabled_roles(self) -> None:
        from app.ui.theme import light_palette

        palette = light_palette()
        for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
            with self.subTest(group=group):
                self.assertEqual(palette.color(group, QPalette.ColorRole.Base), QColor(TOKENS.surface))
                self.assertEqual(palette.color(group, QPalette.ColorRole.Window), QColor(TOKENS.background))
                self.assertEqual(palette.color(group, QPalette.ColorRole.Text), QColor(TOKENS.text))
                self.assertEqual(palette.color(group, QPalette.ColorRole.Highlight), QColor(TOKENS.primary))
                self.assertEqual(palette.color(group, QPalette.ColorRole.HighlightedText), QColor(TOKENS.surface))
        self.assertEqual(
            palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text),
            QColor(TOKENS.text_muted),
        )
        self.assertEqual(
            palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base),
            QColor(TOKENS.surface_alt),
        )

    def test_apply_theme_overrides_dark_palette_and_is_repeatable(self) -> None:
        from app.ui.theme import apply_light_theme

        self.use_dark_starting_palette()
        apply_light_theme(self.qt_app)
        palette = QPalette(self.qt_app.palette())
        stylesheet = self.qt_app.styleSheet()
        apply_light_theme(self.qt_app)
        self.assertEqual(self.qt_app.palette(), palette)
        self.assertEqual(self.qt_app.styleSheet(), stylesheet)
        self.assertEqual(palette.color(QPalette.ColorRole.Base), QColor(TOKENS.surface))
        self.assertEqual(stylesheet, application_stylesheet())

    def test_date_and_datetime_calendars_have_light_grid_and_navigation(self) -> None:
        from app.ui.theme import apply_light_theme

        self.use_dark_starting_palette()
        apply_light_theme(self.qt_app)
        for editor_type in (QDateEdit, QDateTimeEdit):
            with self.subTest(editor=editor_type.__name__):
                editor = editor_type()
                self.widgets.append(editor)
                editor.setCalendarPopup(True)
                calendar = editor.calendarWidget()
                calendar.ensurePolished()
                self.assertTrue(calendar.isGridVisible())
                self.assertEqual(calendar.palette().color(QPalette.ColorRole.Base), QColor(TOKENS.surface))
                navigation_buttons = calendar.findChildren(QToolButton)
                self.assertTrue(navigation_buttons)
                for button in navigation_buttons:
                    button.ensurePolished()
                    self.assertEqual(button.palette().color(QPalette.ColorRole.ButtonText), QColor(TOKENS.text))
                year_editor = calendar.findChild(QSpinBox)
                self.assertIsNotNone(year_editor)
                year_editor.ensurePolished()
                self.assertEqual(year_editor.palette().color(QPalette.ColorRole.Base), QColor(TOKENS.surface))

    def test_combo_spinbox_menu_and_table_use_light_palettes(self) -> None:
        from app.ui.theme import apply_light_theme

        self.use_dark_starting_palette()
        apply_light_theme(self.qt_app)
        combo = QComboBox()
        combo.addItems(["kJ", "kcal"])
        spinbox = QDoubleSpinBox()
        menu = QMenu()
        menu.addAction("打开")
        table = QTableWidget(1, 1)
        self.widgets.extend((combo, spinbox, menu, table))
        for widget in (combo.view(), spinbox, menu, table):
            widget.ensurePolished()
            with self.subTest(widget=type(widget).__name__):
                self.assertEqual(widget.palette().color(QPalette.ColorRole.Base), QColor(TOKENS.surface))
                self.assertEqual(widget.palette().color(QPalette.ColorRole.Text), QColor(TOKENS.text))
        self.assertEqual(table.palette().color(QPalette.ColorRole.Highlight), QColor(TOKENS.primary_soft))
        self.assertEqual(table.palette().color(QPalette.ColorRole.HighlightedText), QColor(TOKENS.text))

    def test_first_run_applies_light_theme_before_profile_dialog_is_constructed(self) -> None:
        from app.ui.main_window import ensure_initial_profile
        from app.ui.profile_dialog import ProfileDialog

        self.use_dark_starting_palette()
        context = Mock()
        context.has_profile.return_value = False

        def create_dialog(dialog_context, parent):
            self.assertEqual(self.qt_app.styleSheet(), application_stylesheet())
            self.assertEqual(
                self.qt_app.palette().color(QPalette.ColorRole.Base), QColor(TOKENS.surface)
            )
            dialog = ProfileDialog(dialog_context, parent)
            self.widgets.append(dialog)
            calendar = dialog.birth_edit.calendarWidget()
            calendar.ensurePolished()
            self.assertTrue(calendar.isGridVisible())
            self.assertEqual(
                calendar.palette().color(QPalette.ColorRole.Base), QColor(TOKENS.surface)
            )
            return dialog

        with patch("app.ui.main_window.ProfileDialog", side_effect=create_dialog), patch.object(
            ProfileDialog, "exec", return_value=QDialog.DialogCode.Rejected
        ):
            self.assertFalse(ensure_initial_profile(context))
        context.save_initial_profile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
