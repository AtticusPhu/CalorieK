"""Small token-driven visual system for the Windows desktop UI."""

from __future__ import annotations

from dataclasses import dataclass


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
    }}
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


__all__ = ["TOKENS", "ThemeTokens", "application_stylesheet"]
