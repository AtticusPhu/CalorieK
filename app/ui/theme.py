"""Small token-driven visual system for the Windows desktop UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class ThemeTokens:
    primary: str = "#0E7490"
    primary_hover: str = "#155E75"
    primary_soft: str = "#CFFAFE"
    accent: str = "#047857"
    background: str = "#F4F8FA"
    surface: str = "#FFFFFF"
    surface_alt: str = "#EEF4F6"
    text: str = "#102A35"
    text_muted: str = "#526871"
    border: str = "#C8D7DC"
    danger: str = "#B91C1C"
    focus: str = "#0891B2"


TOKENS = ThemeTokens()


def light_palette(tokens: ThemeTokens = TOKENS) -> QPalette:
    """Build every native-control color from light tokens, not the OS palette."""

    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: tokens.background,
        QPalette.ColorRole.WindowText: tokens.text,
        QPalette.ColorRole.Base: tokens.surface,
        QPalette.ColorRole.AlternateBase: tokens.surface_alt,
        QPalette.ColorRole.Text: tokens.text,
        QPalette.ColorRole.Button: tokens.surface,
        QPalette.ColorRole.ButtonText: tokens.text,
        QPalette.ColorRole.BrightText: tokens.surface,
        QPalette.ColorRole.Light: tokens.surface,
        QPalette.ColorRole.Midlight: tokens.surface_alt,
        QPalette.ColorRole.Mid: tokens.border,
        QPalette.ColorRole.Dark: tokens.text_muted,
        QPalette.ColorRole.Shadow: tokens.text,
        QPalette.ColorRole.Highlight: tokens.primary,
        QPalette.ColorRole.HighlightedText: tokens.surface,
        QPalette.ColorRole.Link: tokens.primary,
        QPalette.ColorRole.LinkVisited: tokens.primary_hover,
        QPalette.ColorRole.ToolTipBase: tokens.surface,
        QPalette.ColorRole.ToolTipText: tokens.text,
        QPalette.ColorRole.PlaceholderText: tokens.text_muted,
        QPalette.ColorRole.Accent: tokens.primary,
    }
    for group in (
        QPalette.ColorGroup.Active,
        QPalette.ColorGroup.Inactive,
        QPalette.ColorGroup.Disabled,
    ):
        for role, color in roles.items():
            palette.setColor(group, role, QColor(color))
    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens.text_muted))
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Base, QColor(tokens.surface_alt)
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Button, QColor(tokens.surface_alt)
    )
    return palette


def apply_light_theme(application: QApplication, tokens: ThemeTokens = TOKENS) -> None:
    """Apply before any dialog is created, including startup/profile dialogs.

    Fusion's native glyphs use this explicit palette. An application-wide sheet
    also reaches separate popup windows; styling only MainWindow cannot do that.
    """

    from PySide6.QtCore import Qt

    stylesheet = application_stylesheet(tokens)
    palette = light_palette(tokens)
    if (
        getattr(application, "_caloriek_light_theme_tokens", None) == tokens
        and application.styleHints().colorScheme() == Qt.ColorScheme.Light
        and application.styleSheet() == stylesheet
        and application.palette() == palette
    ):
        return
    application.styleHints().setColorScheme(Qt.ColorScheme.Light)
    application.setStyle("Fusion")
    application.setPalette(palette)
    application.setStyleSheet(stylesheet)
    application._caloriek_light_theme_tokens = tokens


def application_stylesheet(tokens: ThemeTokens = TOKENS) -> str:
    """Return a restrained, high-contrast Qt stylesheet using an 8 px rhythm."""

    return f"""
    QMainWindow, QDialog, QWidget#appRoot {{
        background: {tokens.background};
        color: {tokens.text};
        font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
        font-size: 14px;
    }}
    QWidget {{ color: {tokens.text}; }}
    QFrame#sideBar {{
        background: {tokens.surface};
        border: none;
        border-right: 1px solid {tokens.border};
    }}
    QLabel#brandTitle {{
        color: {tokens.primary};
        font-size: 25px;
        font-weight: 700;
    }}
    QLabel#pageTitle {{ font-size: 24px; font-weight: 650; }}
    QLabel#sectionTitle {{ font-size: 16px; font-weight: 650; }}
    QLabel#metricValue {{
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 25px;
        font-weight: 650;
    }}
    QLabel#muted, QLabel#metricCaption {{ color: {tokens.text_muted}; }}
    QFrame#card, QGroupBox {{
        background: {tokens.surface};
        border: 1px solid {tokens.border};
        border-radius: 10px;
    }}
    QGroupBox {{
        margin-top: 12px;
        padding: 14px 12px 10px 12px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 4px;
        color: {tokens.text};
    }}
    QPushButton, QToolButton {{
        min-height: 34px;
        padding: 4px 14px;
        border: 1px solid {tokens.border};
        border-radius: 7px;
        background: {tokens.surface};
        color: {tokens.text};
    }}
    QPushButton:hover, QToolButton:hover {{
        border-color: {tokens.primary};
        background: {tokens.primary_soft};
    }}
    QPushButton:focus, QToolButton:focus,
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus,
    QDoubleSpinBox:focus, QDateEdit:focus, QTimeEdit:focus,
    QDateTimeEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
        border: 2px solid {tokens.focus};
    }}
    QPushButton[primary="true"] {{
        color: white;
        border-color: {tokens.primary};
        background: {tokens.primary};
        font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{ background: {tokens.primary_hover}; }}
    QPushButton[danger="true"] {{ color: {tokens.danger}; border-color: #F2B8B5; }}
    QPushButton[nav="true"] {{
        min-height: 38px;
        padding: 3px 12px;
        border-color: transparent;
        background: transparent;
        text-align: left;
        color: {tokens.text_muted};
    }}
    QPushButton[nav="true"]:hover {{
        border-color: {tokens.border};
        background: {tokens.surface_alt};
        color: {tokens.text};
    }}
    QPushButton[nav="true"]:checked {{
        border-color: {tokens.primary};
        background: {tokens.primary_soft};
        color: {tokens.primary_hover};
        font-weight: 650;
    }}
    QPushButton:disabled, QToolButton:disabled {{
        color: #7A8B91;
        border-color: {tokens.border};
        background: {tokens.surface_alt};
    }}
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QDateEdit, QTimeEdit, QDateTimeEdit, QTextEdit, QPlainTextEdit {{
        min-height: 32px;
        padding: 3px 8px;
        border: 1px solid {tokens.border};
        border-radius: 6px;
        background: {tokens.surface};
        selection-background-color: {tokens.primary};
        selection-color: {tokens.surface};
    }}
    QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled,
    QTextEdit:disabled, QPlainTextEdit:disabled {{
        color: {tokens.text_muted};
        background: {tokens.surface_alt};
    }}
    QComboBox::drop-down, QDateEdit::drop-down, QDateTimeEdit::drop-down {{
        background: {tokens.surface_alt};
        border-left: 1px solid {tokens.border};
        width: 22px;
    }}
    QComboBox QAbstractItemView {{
        background: {tokens.surface};
        color: {tokens.text};
        border: 1px solid {tokens.border};
        selection-background-color: {tokens.primary_soft};
        selection-color: {tokens.text};
    }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        background: {tokens.surface_alt};
        border-left: 1px solid {tokens.border};
        width: 18px;
    }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover,
    QComboBox::drop-down:hover, QDateEdit::drop-down:hover,
    QDateTimeEdit::drop-down:hover {{ background: {tokens.primary_soft}; }}
    QLineEdit[invalid="true"], QDoubleSpinBox[invalid="true"] {{
        border: 2px solid {tokens.danger};
    }}
    QTableView, QTableWidget, QListView, QListWidget {{
        background: {tokens.surface};
        alternate-background-color: {tokens.surface_alt};
        border: 1px solid {tokens.border};
        border-radius: 7px;
        gridline-color: {tokens.border};
        selection-background-color: {tokens.primary_soft};
        selection-color: {tokens.text};
    }}
    QHeaderView::section {{
        background: {tokens.surface_alt};
        color: {tokens.text};
        border: none;
        border-bottom: 1px solid {tokens.border};
        padding: 8px;
        font-weight: 600;
    }}
    QTableCornerButton::section {{
        background: {tokens.surface_alt};
        border: none;
        border-right: 1px solid {tokens.border};
        border-bottom: 1px solid {tokens.border};
    }}
    QTableView::item:selected, QListView::item:selected {{
        background: {tokens.primary_soft};
        color: {tokens.text};
    }}
    QMenu, QMenuBar {{
        background: {tokens.surface};
        color: {tokens.text};
    }}
    QMenu {{ border: 1px solid {tokens.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px; }}
    QMenu::item:selected, QMenuBar::item:selected {{
        background: {tokens.primary_soft};
        color: {tokens.text};
    }}
    QMenu::item:disabled {{ color: {tokens.text_muted}; }}
    QMenu::separator {{ height: 1px; background: {tokens.border}; margin: 4px; }}
    QCalendarWidget {{
        qproperty-gridVisible: true;
        background: {tokens.surface};
        color: {tokens.text};
    }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{
        background: {tokens.surface_alt};
        color: {tokens.text};
    }}
    QCalendarWidget QToolButton {{
        min-height: 24px;
        padding: 3px 6px;
        border: 1px solid transparent;
        border-radius: 4px;
        background: {tokens.surface_alt};
        color: {tokens.text};
    }}
    QCalendarWidget QToolButton:hover {{
        background: {tokens.primary_soft};
        border-color: {tokens.primary};
    }}
    QCalendarWidget QToolButton:disabled {{ color: {tokens.text_muted}; }}
    QCalendarWidget QToolButton:focus {{ border: 2px solid {tokens.focus}; }}
    QCalendarWidget QSpinBox {{
        min-height: 24px;
        padding: 1px 4px;
        background: {tokens.surface};
        color: {tokens.text};
        selection-background-color: {tokens.primary};
        selection-color: {tokens.surface};
    }}
    QCalendarWidget QAbstractItemView {{
        background: {tokens.surface};
        alternate-background-color: {tokens.surface_alt};
        color: {tokens.text};
        border: none;
        border-radius: 0;
        gridline-color: {tokens.border};
        selection-background-color: {tokens.primary};
        selection-color: {tokens.surface};
    }}
    QCalendarWidget QAbstractItemView::item:selected {{
        background: {tokens.primary};
        color: {tokens.surface};
    }}
    QListWidget::item {{ min-height: 34px; padding: 4px 8px; }}
    QListWidget::item:selected {{ border-left: 3px solid {tokens.primary}; }}
    QTabWidget::pane {{ border: 1px solid {tokens.border}; border-radius: 7px; }}
    QTabBar::tab {{ padding: 9px 16px; color: {tokens.text_muted}; }}
    QTabBar::tab:selected {{ color: {tokens.primary}; font-weight: 600; }}
    QScrollBar:vertical {{ width: 12px; background: transparent; }}
    QScrollBar::handle:vertical {{ background: #AABBC1; min-height: 28px; border-radius: 5px; }}
    QStatusBar {{ background: {tokens.surface}; border-top: 1px solid {tokens.border}; }}
    QScrollArea {{ border: none; background: transparent; }}
    QSplitter::handle {{ background: {tokens.border}; width: 1px; }}
    QToolTip {{
        color: {tokens.text};
        background: {tokens.surface};
        border: 1px solid {tokens.primary};
        padding: 6px;
    }}
    """


__all__ = [
    "TOKENS", "ThemeTokens", "apply_light_theme", "application_stylesheet", "light_palette"
]
