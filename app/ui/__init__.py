"""PySide6 presentation layer for CalorieK."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .context import UIContext

if TYPE_CHECKING:
    from .dashboard import DashboardPage, DashboardWidget
    from .food_library import FoodEditorDialog, FoodLibraryPage, FoodLibraryWidget
    from .exercise_library import ExerciseLibraryPage, ExerciseTypeEditorDialog
    from .main_window import MainWindow
    from .recipe_editor import RecipeEditorDialog, RecipeLibraryPage
    from .settings import SettingsPage, SettingsWidget


_LAZY_EXPORTS = {
    "DashboardPage": (".dashboard", "DashboardPage"),
    "DashboardWidget": (".dashboard", "DashboardWidget"),
    "FoodEditorDialog": (".food_library", "FoodEditorDialog"),
    "FoodLibraryPage": (".food_library", "FoodLibraryPage"),
    "FoodLibraryWidget": (".food_library", "FoodLibraryWidget"),
    "ExerciseLibraryPage": (".exercise_library", "ExerciseLibraryPage"),
    "ExerciseTypeEditorDialog": (".exercise_library", "ExerciseTypeEditorDialog"),
    "MainWindow": (".main_window", "MainWindow"),
    "RecipeEditorDialog": (".recipe_editor", "RecipeEditorDialog"),
    "RecipeLibraryPage": (".recipe_editor", "RecipeLibraryPage"),
    "SettingsPage": (".settings", "SettingsPage"),
    "SettingsWidget": (".settings", "SettingsWidget"),
    "ensure_initial_profile": (".main_window", "ensure_initial_profile"),
    "ensure_profile": (".main_window", "ensure_profile"),
    "run_first_time_setup": (".main_window", "run_first_time_setup"),
}


def __getattr__(name: str) -> Any:
    """Keep DTO-only imports usable in calculation-only environments."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


__all__ = ["UIContext", *_LAZY_EXPORTS]
