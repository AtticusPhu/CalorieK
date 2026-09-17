"""Primary Windows desktop shell and first-run profile orchestration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.version import APP_DISPLAY_NAME

from .context import UIContext
from .dashboard import DashboardPage
from .food_library import FoodLibraryPage
from .exercise_library import ExerciseLibraryPage
from .profile_dialog import ProfileDialog
from .recipe_editor import RecipeLibraryPage
from .settings import SettingsPage
from .theme import apply_light_theme


def ensure_initial_profile(context: UIContext, parent: QWidget | None = None) -> bool:
    """Ensure a first profile exists; safe for a composition root to call.

    The function must run after ``QApplication`` has been created.  ``False``
    means the user cancelled setup or the profile state could not be read.
    """

    application = QApplication.instance()
    if application is not None:
        apply_light_theme(application)
    try:
        if context.has_profile():
            return True
    except Exception as exc:
        QMessageBox.critical(parent, "无法启动", f"无法读取用户资料。\n\n{exc}")
        return False

    dialog = ProfileDialog(context, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted


# A concise alias for composition roots that prefer an imperative name.
run_first_time_setup = ensure_initial_profile
ensure_profile = ensure_initial_profile


class MainWindow(QMainWindow):
    """Navigation shell joining the dashboard, libraries, and settings."""

    def __init__(
        self,
        context: UIContext,
        parent: QWidget | None = None,
        *,
        ensure_profile_on_show: bool = True,
    ) -> None:
        super().__init__(parent)
        application = QApplication.instance()
        if application is not None:
            apply_light_theme(application)
        self._context = context
        self._ensure_profile_on_show = ensure_profile_on_show
        self._bootstrapped = False
        self._page_indexes: dict[str, int] = {}
        self._nav_buttons: dict[str, QPushButton] = {}

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(980, 700)
        self.resize(1240, 840)
        self.setAccessibleName("CalorieK 主窗口")

        self.dashboard_page = DashboardPage(context)
        self.food_library_page = FoodLibraryPage(context)
        self.recipe_library_page = RecipeLibraryPage(context)
        self.exercise_library_page = ExerciseLibraryPage(context)
        self.settings_page = SettingsPage(context)

        self.pages = QStackedWidget()
        self.pages.setAccessibleName("主内容区域")
        for key, page in (
            ("dashboard", self.dashboard_page),
            ("foods", self.food_library_page),
            ("recipes", self.recipe_library_page),
            ("exercises", self.exercise_library_page),
            ("settings", self.settings_page),
        ):
            self._page_indexes[key] = self.pages.addWidget(page)

        sidebar = self._build_sidebar()
        root_widget = QWidget()
        root_widget.setObjectName("appRoot")
        root_layout = QHBoxLayout(root_widget)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.pages, 1)
        self.setCentralWidget(root_widget)

        self.dashboard_page.data_changed.connect(
            lambda: self.statusBar().showMessage("记录已保存，首页已重新计算。", 5000)
        )
        self.food_library_page.data_changed.connect(self._food_library_changed)
        self.recipe_library_page.data_changed.connect(self._recipe_library_changed)
        self.exercise_library_page.data_changed.connect(self._exercise_library_changed)
        self.settings_page.settings_saved.connect(self._settings_changed)
        self.settings_page.data_restored.connect(self.refresh_all)

        self._install_shortcuts()
        self.navigate("dashboard")
        self.statusBar().showMessage("数据仅保存在本机。")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sideBar")
        sidebar.setFixedWidth(210)
        sidebar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        brand = QLabel("CalorieK")
        brand.setObjectName("brandTitle")
        brand.setAccessibleName("CalorieK")
        tagline = QLabel("实际称重优先\n本地体重热量记录")
        tagline.setObjectName("muted")
        tagline.setWordWrap(True)

        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        for key, label in (
            ("dashboard", "今天"),
            ("foods", "食品库"),
            ("recipes", "我的食谱"),
            ("exercises", "运动项目"),
            ("settings", "设置与数据"),
        ):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setProperty("nav", True)
            button.setMinimumHeight(44)
            button.setAccessibleName(f"打开{label}")
            button.clicked.connect(lambda _checked=False, page_key=key: self.navigate(page_key))
            nav_group.addButton(button)
            self._nav_buttons[key] = button

        privacy = QLabel("离线运行 · 无账号 · 无云同步")
        privacy.setObjectName("muted")
        privacy.setWordWrap(True)

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 24, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(brand)
        layout.addWidget(tagline)
        layout.addSpacing(18)
        for key in ("dashboard", "foods", "recipes", "exercises", "settings"):
            layout.addWidget(self._nav_buttons[key])
        layout.addStretch(1)
        layout.addWidget(privacy)
        return sidebar

    def _install_shortcuts(self) -> None:
        bindings: tuple[tuple[str, Callable[[], None]], ...] = (
            ("Ctrl+1", lambda: self.navigate("dashboard")),
            ("Ctrl+2", lambda: self.navigate("foods")),
            ("Ctrl+3", lambda: self.navigate("recipes")),
            ("Ctrl+4", lambda: self.navigate("exercises")),
            ("Ctrl+5", lambda: self.navigate("settings")),
            ("Ctrl+R", self.refresh_current_page),
        )
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in bindings:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def ensure_profile(self) -> bool:
        """Public instance counterpart to :func:`ensure_initial_profile`."""

        return ensure_initial_profile(self._context, self)

    def navigate(self, key: str) -> None:
        if key not in self._page_indexes:
            raise KeyError(f"unknown main page: {key}")
        self.pages.setCurrentIndex(self._page_indexes[key])
        self._nav_buttons[key].setChecked(True)
        page = self.pages.currentWidget()
        refresh = getattr(page, "refresh", None)
        if self._bootstrapped and callable(refresh):
            refresh()

    def refresh_current_page(self) -> None:
        refresh = getattr(self.pages.currentWidget(), "refresh", None)
        if callable(refresh):
            refresh()

    def refresh_all(self) -> None:
        """Reload all derived and library views after a restore or mutation."""

        for page in (
            self.dashboard_page,
            self.food_library_page,
            self.recipe_library_page,
            self.exercise_library_page,
            self.settings_page,
        ):
            refresh = getattr(page, "refresh", None)
            if callable(refresh):
                refresh()
        self.statusBar().showMessage("所有页面已重新载入。", 5000)

    def _food_library_changed(self) -> None:
        self.statusBar().showMessage("食品库已更新；历史饮食快照未改变。", 5000)

    def _recipe_library_changed(self) -> None:
        self.statusBar().showMessage("食谱库已更新；历史摄入快照未改变。", 5000)

    def _exercise_library_changed(self) -> None:
        self.statusBar().showMessage("运动快捷项目已更新；历史运动记录未改变。", 5000)

    def _settings_changed(self, model_changed: bool) -> None:
        if model_changed:
            self.dashboard_page.refresh()
        else:
            self.dashboard_page.refresh_display_unit()
        for page in (self.food_library_page, self.recipe_library_page, self.exercise_library_page):
            page.refresh()
        self.statusBar().showMessage("设置已保存，所有页面的显示单位与图表已更新。", 5000)

    def _bootstrap(self) -> None:
        if self._bootstrapped:
            return
        if self._ensure_profile_on_show and not self.ensure_profile():
            self.close()
            return
        self._bootstrapped = True
        if not self.dashboard_page.is_loaded:
            self.dashboard_page.refresh()

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        if not self._bootstrapped:
            QTimer.singleShot(0, self._bootstrap)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Writes are committed by the context before dialogs accept, so there is
        # no hidden pending state to discard at application shutdown.
        event.accept()


__all__ = [
    "MainWindow",
    "ensure_initial_profile",
    "ensure_profile",
    "run_first_time_setup",
]
