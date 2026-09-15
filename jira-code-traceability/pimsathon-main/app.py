"""
pages (Dashboard / Schedule / Workspace / Cowork / Structure) and top bar."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout, QLabel, QMainWindow, QMenu,
    QPushButton, QSizePolicy, QSplitter, QStackedWidget, QSystemTrayIcon,
    QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import APP_NAME, DISPLAY_NAME, __version__
from .config import PROVIDER_LABELS, AppConfig
from .i18n import LANGUAGE_SHORT, LANGUAGES, get_language, on_language_changed, set_language, tr
from .state import AppContext
from .theme import ACCENT, stylesheet
from .core.task_scheduler import TaskScheduler
from .ui.cowork_tab import CoworkTab
from .ui.dashboard_tab import DashboardTab
from .ui.monitoring_tab import MonitoringTab
from .ui.schedule_task_tab import ScheduleTaskTab
from .ui.settings_dialog import SettingsDialog
from .ui.sidebar import HistorySidebar
from .ui.structure_graph_view import StructureGraphView
from .ui.workspace_tab import WorkspaceTab

ASSETS = Path(__file__).resolve().parent / "assets"

# Nav rail (sidebar navigation) widths — expanded shows icon+label, collapsed
# shows icon-only (still fully clickable, just narrower).
_NAV_EXPANDED_WIDTH = 150
_NAV_COLLAPSED_WIDTH = 54


def app_icon() -> QIcon:
    """The buffalo app icon, used everywhere (window title bar, Windows taskbar and
    the tray). The multi-size ``.ico`` is loaded FIRST so Windows has the right
    pixmap for the taskbar; the high-res ``.png`` is added so the icon stays crisp
    at large sizes. This keeps the taskbar icon identical to the app's icon."""
    icon = QIcon()
    for name in ("icon.ico", "icon.png"):
        path = ASSETS / name
        if path.exists():
            icon.addFile(str(path))
    return icon


class _Toast(QLabel):
    """A small auto-hiding notification shown at the window's top-left."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setWordWrap(True)
        self.setMaximumWidth(380)
        self.setVisible(False)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, ok: bool = True, ms: int = 4500) -> None:
        bg = "#1f9d63" if ok else "#e5484d"
        self.setStyleSheet(
            f"#toast {{ background:{bg}; color:white; border-radius:12px;"
            f" padding:10px 16px; font-weight:600; }}")
        self.setText(text)
        self.adjustSize()
        self.move(14, 14)          # top-left of the window
        self.raise_()
        self.setVisible(True)
        self._timer.start(ms)


class MainWindow(QMainWindow):
    # Nav rows (Dashboard/Schedule/Monitoring are lazy; Workspace is the eager home page).
    _ROW_DASHBOARD, _ROW_SCHEDULE, _ROW_WORKSPACE, _ROW_MONITORING = 0, 1, 2, 3

    def __init__(self, ctx: AppContext, user_name: str = ""):
        super().__init__()
        self.ctx = ctx
        self._user_name = user_name
        self._really_quit = False
        self.tray = None
        self._nav_collapsed = False    # icon-only nav rail toggle (Task: collapsible nav)
        self._history_collapsed = False   # remembers History's own collapse-to-strip state
        self.setWindowTitle(f"{DISPLAY_NAME} v{__version__}")
        self.setWindowIcon(app_icon())
        # Fit to the available screen so the window never opens larger than the
        # monitor (auto-fit). Keep a modest minimum that still fits small laptops.
        self._fit_to_screen(1180, 760)

        self.sidebar = HistorySidebar(ctx)
        # Task scheduler ENGINE runs in the background whether or not its Kanban
        # UI (built lazily) is on screen — scheduled tasks must fire regardless.
        self.task_scheduler = TaskScheduler(ctx, parent=self)
        # Desktop notification when a scheduled task finishes; also refresh
        # History — a cowork/code task run saves itself as a new session there.
        self.task_scheduler.task_finished.connect(self._on_scheduled_task_done)
        # NOTE: task_started fires BEFORE the worker thread even begins, so its
        # session doesn't exist on disk yet — refreshing History here would
        # find nothing. history_ready fires once the session is actually
        # saved (right as the run starts, then again after each turn), which
        # is what really makes a Running task's session show up live.
        self.task_scheduler.history_ready.connect(lambda _tid: self._refresh_history())

        # Cowork chat + GraphRAG view are embedded as sub-tabs INSIDE the
        # Workspace screen (per selected project). GraphRAG's heavy
        # QtWebEngine is still built lazily on first display
        # (StructureGraphView._ensure_web).
        self.cowork = CoworkTab(ctx)
        self.structure = StructureGraphView(ctx)
        self.structure.status_message.connect(self.statusBar().showMessage)
        self.cowork.output_changed.connect(self.structure.schedule_rescan)
        self.cowork.status_message.connect(self.statusBar().showMessage)
        # Refresh History (list + running markers + current highlight) whenever a
        # conversation is created/updated or a turn finishes.
        self.cowork.turn_finished.connect(lambda *_: self._refresh_history())
        self.cowork.history_changed.connect(self._refresh_history)
        self.cowork.turn_finished.connect(
            lambda result: self._notify_task(self.cowork, "cowork", result))

        # Workspace screen — the app HOME: project management + the per-project
        # Cowork / GraphRAG sub-tabs and History.
        self.workspace = WorkspaceTab(ctx, cowork=self.cowork, structure=self.structure,
                                      sidebar=self.sidebar)
        self.workspace.status_message.connect(self.statusBar().showMessage)
        self.workspace.projects_changed.connect(self._on_projects_changed)
        self.workspace.open_chat.connect(lambda *_: self._refresh_history())
        self.workspace.new_chat.connect(lambda *_: self._refresh_history())

        # Dashboard + Schedule pages are built lazily on first visit (lazy page
        # creation — keeps startup light); None until then.
        self.dashboard = None
        self.schedule = None
        self.monitoring = None

        # --- right side: top bar + pages (nav rail drives the stack) ---
        right = QWidget()
        right.setObjectName("contentArea")
        rlay = QVBoxLayout(right)
        rlay.setContentsMargins(10, 10, 10, 10)
        rlay.setSpacing(10)
        rlay.addWidget(self._build_topbar())

        self.pages = QStackedWidget()
        # (i18n key, icon, builder-or-None, eager-widget-or-None) — page index == list index
        self._nav_defs = [
            ("app.tab.dashboard", "dashboard", self._build_dashboard, None),
            ("app.tab.schedule", "schedule", self._build_schedule, None),
            ("app.tab.workspace", "workspaces", None, self.workspace),
            ("app.tab.monitoring", "monitoring", self._build_monitoring, None),
        ]
        # Container pages whose sub-tabs become expandable nav children.
        self._nav_parents = {self._ROW_WORKSPACE, self._ROW_MONITORING}
        self._page_widgets = []   # page index → widget (placeholder until lazily built)
        self._built = []
        for _key, _icon_name, _builder, widget in self._nav_defs:
            page = widget if widget is not None else QWidget()
            self.pages.addWidget(page)
            self._page_widgets.append(page)
            self._built.append(widget is not None)

        # Left nav rail as a parent→child accordion (Claude-style): the container
        # pages (Workspaces, Monitoring) expand to list their sub-views as
        # children, and their in-content tab strips are hidden — so the content
        # area is as large as possible.
        from .ui.icons import icon as _icon
        self.nav = QTreeWidget()
        self.nav.setObjectName("navrail")
        self.nav.setHeaderHidden(True)
        self.nav.setIndentation(14)
        self.nav.setRootIsDecorated(True)
        self.nav.setExpandsOnDoubleClick(False)
        self._nav_items = []      # page index → top-level QTreeWidgetItem
        for page, (key, icon_name, _b, _w) in enumerate(self._nav_defs):
            it = QTreeWidgetItem([tr(key)])
            it.setIcon(0, _icon(icon_name))
            it.setData(0, Qt.UserRole, {"page": page, "sub": None,
                                        "parent": page in self._nav_parents, "key": key})
            # A container (Monitoring/Workspace) always shows the dropdown arrow —
            # even before its page/children are lazily built — so it's obvious it
            # holds multiple sub-views. It stays collapsed until first expanded.
            if page in self._nav_parents:
                it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
            self.nav.addTopLevelItem(it)
            self._nav_items.append(it)
        self.workspace.hide_tab_bar()
        self._reload_nav_children(self._ROW_WORKSPACE)     # Workspace is eager
        self.workspace.subtabs_changed.connect(
            lambda: self._reload_nav_children(self._ROW_WORKSPACE))
        self.nav.currentItemChanged.connect(lambda cur, _prev: self._navigate(cur))
        self.nav.itemClicked.connect(self._on_nav_click)
        self.nav.itemExpanded.connect(self._on_nav_expanded)   # build children lazily
        rlay.addWidget(self.pages, 1)

        # Nav rail wrapper: a small toggle button ABOVE the page list so the
        # whole rail can collapse to icon-only (still fully clickable). Same
        # collapse/expand chevron iconography as every other collapsible panel.
        from .ui.icons import collapse_left_icon, collapse_right_icon
        self._collapse_left_icon = collapse_left_icon
        self._collapse_right_icon = collapse_right_icon
        self._nav_wrap = QWidget()
        self._nav_wrap.setObjectName("navWrap")
        self._nav_wrap.setFixedWidth(_NAV_EXPANDED_WIDTH)
        nvl = QVBoxLayout(self._nav_wrap)
        nvl.setContentsMargins(0, 0, 0, 0)
        nvl.setSpacing(0)
        # Small, left-aligned "MENU" button (icon + label) instead of a
        # full-width centered icon — sits flush with the rail's left edge,
        # matching how the nav items themselves align their icon+label.
        self._nav_toggle_btn = QPushButton(tr("app.nav.menu_label"))
        self._nav_toggle_btn.setIcon(collapse_left_icon())
        self._nav_toggle_btn.setObjectName("navMenuBtn")
        self._nav_toggle_btn.setFlat(True)
        self._nav_toggle_btn.setCursor(Qt.PointingHandCursor)
        self._nav_toggle_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._nav_toggle_btn.clicked.connect(self._toggle_nav)
        # Zero left margin: the button's own QSS padding (6px) then lines its
        # 16px icon up with the nav items' icons below (1px list frame + item
        # padding) — same indent level, same icon size as e.g. Dashboard.
        toggle_row = QHBoxLayout()
        toggle_row.setContentsMargins(0, 8, 10, 8)
        toggle_row.addWidget(self._nav_toggle_btn, 0, Qt.AlignLeft)
        toggle_row.addStretch(1)
        nvl.addLayout(toggle_row)
        nvl.addWidget(self.nav, 1)

        self.split = QSplitter(Qt.Horizontal)
        self.split.addWidget(self._nav_wrap)
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        self.split.setSizes([_NAV_EXPANDED_WIDTH, 1000])
        self.setCentralWidget(self.split)
        # Workspace = landing/home (expand it and select its first sub-view).
        self._nav_items[self._ROW_WORKSPACE].setExpanded(True)
        self.nav.setCurrentItem(self._nav_items[self._ROW_WORKSPACE])
        self.toast = _Toast(self)   # top-left "task done" popup
        # Floating in-app Help assistant — a robot icon pinned bottom-right on
        # every screen; expands into a small help-only chat (see
        # ui/help_agent_widget.py). Managed in Monitoring → Agents Admin.
        from .ui.help_agent_widget import HelpAgentWidget
        self.help_agent = HelpAgentWidget(ctx, self, user_name=self._user_name)
        self.help_agent.status_message.connect(self.statusBar().showMessage)

        self.statusBar().showMessage(tr("app.status.ready"))
        # Author credit, pinned to the bottom-right corner. A permanent status-bar
        # widget sits at the right end and is never cleared by showMessage (which
        # writes on the left).
        self._credit = QLabel(tr("app.credit"))
        self._credit.setObjectName("hint")
        self._credit.setStyleSheet("color: rgba(140,146,152,0.85); padding: 0 10px;")
        self.statusBar().addPermanentWidget(self._credit)
        self._restore_sessions()
        self._setup_tray()
        # Start the task scheduler last, once the whole window exists — it
        # catches up any overdue tasks right away (first tick runs inline).
        self.task_scheduler.start()
        # Auto Model Routing: periodic reassess + pending-switch expiry. Runs
        # background probes only when genuinely due (never a burst at launch).
        try:
            from .core.routing.scheduler import RoutingScheduler
            self.routing_scheduler = RoutingScheduler(self.ctx, self.ctx.routing(), parent=self)
            self.routing_scheduler.start()
        except Exception:  # noqa: BLE001 — routing must never block app startup
            self.routing_scheduler = None
        on_language_changed(self._retranslate)

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Keep the floating Help assistant pinned to the bottom-right corner.
        if getattr(self, "help_agent", None) is not None:
            self.help_agent.reposition()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if getattr(self, "help_agent", None) is not None:
            self.help_agent.reposition()
            self.help_agent.raise_()

    # ---- i18n ----------------------------------------------------------
    def _retranslate(self) -> None:
        """Re-apply the current language to this window's own static chrome
        (tabs are the only long-lived text here; the tabs/dialogs retranslate
        themselves)."""
        self._apply_nav_labels()
        self._nav_toggle_btn.setText("" if self._nav_collapsed else tr("app.nav.menu_label"))
        self._nav_toggle_btn.setToolTip(
            tr("app.nav.expand_tooltip") if self._nav_collapsed else tr("app.nav.collapse_tooltip"))
        self._credit.setText(tr("app.credit"))
        if hasattr(self, "provider_lbl"):
            self.provider_lbl.setText(tr("app.provider"))
        if hasattr(self, "settings_btn"):
            self.settings_btn.setText(tr("app.settings"))
        if hasattr(self, "theme_btn"):
            self.theme_btn.setToolTip(tr("settings.theme"))
            for value, act in self._theme_actions.items():
                act.setText(tr(f"settings.theme_{value}"))
        if hasattr(self, "logo_lbl"):
            self.logo_lbl.setText(tr("app.logo"))
        if getattr(self, "help_agent", None) is not None:
            self.help_agent.retranslate()
        if self.tray is not None:
            self.tray.setToolTip(DISPLAY_NAME)
        if hasattr(self, "_tray_open_act"):
            self._tray_open_act.setText(tr("app.tray.open"))
            self._tray_quit_act.setText(tr("app.tray.quit"))

    # ---- system tray (run in background when the window is closed) ---
    def _setup_tray(self) -> None:
        from PySide6.QtGui import QAction

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(DISPLAY_NAME)
        menu = QMenu()
        self._tray_open_act = QAction(tr("app.tray.open"), self)
        self._tray_open_act.triggered.connect(self._show_window)
        self._tray_quit_act = QAction(tr("app.tray.quit"), self)
        self._tray_quit_act.triggered.connect(self._quit_app)
        menu.addAction(self._tray_open_act)
        menu.addAction(self._tray_quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self._show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.tray.show()

    def _page_index(self, widget) -> int:
        return self.pages.indexOf(widget)

    # ---- lazy page building -------------------------------------------
    def _build_dashboard(self):
        d = DashboardTab(self.ctx)
        d.status_message.connect(self.statusBar().showMessage)
        self.dashboard = d
        return d

    def _build_schedule(self):
        s = ScheduleTaskTab(self.ctx, self.task_scheduler)
        s.status_message.connect(self.statusBar().showMessage)
        self.schedule = s
        return s

    def _build_monitoring(self):
        m = MonitoringTab(self.ctx, cowork=self.cowork, structure=self.structure,
                          task_scheduler=self.task_scheduler)
        m.status_message.connect(self.statusBar().showMessage)
        self.monitoring = m
        return m

    def _ensure_page(self, row: int) -> None:
        """Build a lazy nav page on first visit and swap it in for its placeholder."""
        if not (0 <= row < len(self._built)) or self._built[row]:
            return
        builder = self._nav_defs[row][2]
        if builder is None:
            return
        real = builder()
        placeholder = self._page_widgets[row]
        self.pages.insertWidget(row, real)     # placeholder shifts to row+1
        self.pages.removeWidget(placeholder)
        placeholder.deleteLater()
        self._page_widgets[row] = real
        self._built[row] = True
        # Container pages: hide their in-content tab strip + list their sub-views
        # as children in the nav rail now that the real widget exists.
        if hasattr(real, "hide_tab_bar"):
            real.hide_tab_bar()
        if row in self._nav_parents:
            self._reload_nav_children(row)

    def _page_index(self, widget) -> int:
        if widget is self.workspace:
            return self._ROW_WORKSPACE
        if self.dashboard is not None and widget is self.dashboard:
            return self._ROW_DASHBOARD
        if self.schedule is not None and widget is self.schedule:
            return self._ROW_SCHEDULE
        if self.monitoring is not None and widget is self.monitoring:
            return self._ROW_MONITORING
        return self.pages.indexOf(widget)

    # ---- nav rail collapse (icon-only) --------------------------------
    def _apply_nav_labels(self) -> None:
        """Set each top-level nav item's text for the current language AND
        collapse state: collapsed shows icon-only (label → tooltip) and folds
        the accordion so only the top-level icons show."""
        for page, item in enumerate(self._nav_items):
            key = self._nav_defs[page][0]
            label = tr(key)
            item.setText(0, "" if self._nav_collapsed else label)
            item.setToolTip(0, label if self._nav_collapsed else "")
            if self._nav_collapsed:
                item.setExpanded(False)
        # Refresh child labels (language-aware, from each container's tabText).
        if not self._nav_collapsed:
            for page in self._nav_parents:
                if self._built[page]:
                    self._reload_nav_children(page)

    def _toggle_nav(self) -> None:
        self._nav_collapsed = not self._nav_collapsed
        width = _NAV_COLLAPSED_WIDTH if self._nav_collapsed else _NAV_EXPANDED_WIDTH
        self._nav_wrap.setFixedWidth(width)
        self._apply_nav_labels()
        # Same chevron convention as every other collapsible panel: right-
        # pointing (fill-right) means "click to expand", left means "collapse".
        self._nav_toggle_btn.setIcon(
            self._collapse_right_icon() if self._nav_collapsed else self._collapse_left_icon())
        # Collapsed rail is icon-only (54px) — the "MENU" label wouldn't fit
        # next to the icon, same rule the nav items themselves follow.
        self._nav_toggle_btn.setText("" if self._nav_collapsed else tr("app.nav.menu_label"))
        self._nav_toggle_btn.setToolTip(
            tr("app.nav.expand_tooltip") if self._nav_collapsed else tr("app.nav.collapse_tooltip"))
        # Give/reclaim the width difference to the main content pane.
        sizes = self.split.sizes()
        if len(sizes) == 2:
            diff = sizes[0] - width
            sizes[0] = width
            sizes[1] = max(1, sizes[1] + diff)
            self.split.setSizes(sizes)

    def _running_session_ids(self):
        """All conversation ids currently running — interactive Cowork/Code
        chat tab AgentWorkers, plus Schedule Task runs (their own session,
        tracked by the scheduler), so a task's live run gets the same
        "running" marker in History an interactive chat gets."""
        return set(self.cowork.running_session_ids()) | self.task_scheduler.running_session_ids()

    def _refresh_history(self) -> None:
        """Rebuild the History list with the current conversation highlighted and
        the running ones marked. Deferred to the next event-loop tick: this is often
        triggered (via load_conversation) from inside the sidebar's own item-click
        handler, and clearing the tree there would delete the item mid-click."""
        from PySide6.QtCore import QTimer

        def _do() -> None:
            current = self.cowork.session_id
            self.sidebar.set_view_state(current, self._running_session_ids())
            self.sidebar.refresh()

        QTimer.singleShot(0, _do)

    def _on_scheduled_task_done(self, task_id: str, ok: bool) -> None:
        """Desktop notification for a finished scheduled task (toast always,
        tray balloon when the window isn't focused), then refresh History —
        cowork/co4e task runs just saved themselves as new sessions there."""
        from .core.tasks import load_task

        task = load_task(task_id) or {}
        title = task.get("title", "")
        msg = (tr("app.toast.task_done", title=title) if ok
               else tr("app.toast.task_failed", title=title))
        self.toast.show_message(msg, ok=ok)
        if (self.tray is not None
                and self.ctx.config.data.get("tray", {}).get("notify_on_done", True)
                and not self.isActiveWindow()):
            try:
                self.tray.showMessage(
                    DISPLAY_NAME, msg,
                    QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning, 5000)
            except Exception:  # noqa: BLE001
                pass
        self._refresh_history()

    def _notify_task(self, tab, kind: str, result: dict) -> None:
        """Notify when a task finishes/fails (skip if more stages queued)."""
        if tab.composer.has_queue():
            return  # a flow / queue is still running — notify only at the end
        name = tr(f"app.tab.{kind}")
        err = (result or {}).get("error")
        # In-app popup at the top-left (shown whether or not the window is focused).
        self.toast.show_message(
            tr("app.toast.error", name=name) if err else tr("app.toast.done", name=name), ok=not err)
        # System-tray balloon only when the window isn't the active one.
        if self.tray is None:
            return
        if not self.ctx.config.data.get("tray", {}).get("notify_on_done", True):
            return
        if self.isActiveWindow():
            return  # user is looking at the window already
        err = (result or {}).get("error")
        title = tr("app.toast.error", name=name) if err else tr("app.toast.done", name=name)
        body = (err if err else (tab._last_assistant_text() or "Task completed."))[:140]
        icon = QSystemTrayIcon.Critical if err else QSystemTrayIcon.Information
        try:
            self.tray.showMessage(title, body, icon, 5000)
        except Exception:
            pass

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit_app(self) -> None:
        self._really_quit = True
        self.close()

    def _restore_sessions(self) -> None:
        """Reopen the last conversation per tab (recover after a crash/abrupt exit)."""
        from pathlib import Path

        from .core.history import load_conversation

        last = self.ctx.config.data.get("last_session", {})
        path = last.get("cowork", "")
        if path and Path(path).exists():
            try:
                self.cowork.load_conversation(load_conversation(path))
                # Reflect the restored thread's project in the Workspace home
                # (selecting the matching row won't wipe it — the project id
                # already matches, so _bind_project starts no new session).
                # Skip forcing the Cowork tab open for a project that no
                # longer exists (deleted since this session was saved) — that
                # would show the Cowork page while the tab strip still says
                # "no project selected" (see WorkspaceTab._on_sidebar_open).
                pid = self.cowork.project_id
                if pid in ("", "default") or self.workspace._select_project_row(pid):
                    self.workspace._show_cowork_tab()
            except Exception:
                pass

    # ---- top bar -----------------------------------------------------
    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        # Transparent: the logo/provider/language text sits directly on the
        # window background, no separate card box behind it.
        bar.setStyleSheet("#topbar { background: transparent; border: none; }")
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 10, 12, 10)
        h.setSpacing(10)
        # FPT logo slot in front of the brand text: shown only when a logo
        # image has been dropped into assets/ (see _brand_logo_pixmap) — the
        # brand works text-only until the real artwork is supplied.
        self.logo_img = QLabel()
        logo_pm = self._brand_logo_pixmap()
        if logo_pm is not None:
            self.logo_img.setPixmap(logo_pm)
        else:
            self.logo_img.setVisible(False)
        h.addWidget(self.logo_img)
        self.logo_lbl = QLabel(tr("app.logo"))
        self.logo_lbl.setStyleSheet(f"font-weight:800; font-size:16px; color:{ACCENT};")
        h.addWidget(self.logo_lbl)
        h.addStretch(1)

        self.provider_lbl = QLabel(tr("app.provider"))
        self.provider_lbl.setObjectName("hint")
        h.addWidget(self.provider_lbl)
        self.provider_combo = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, key)
        idx = self.provider_combo.findData(self.ctx.config.active_provider)
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        h.addWidget(self.provider_combo)

        self.language_combo = QComboBox()
        for key in LANGUAGES:
            self.language_combo.addItem(LANGUAGE_SHORT.get(key, key.upper()), key)
            self.language_combo.setItemData(
                self.language_combo.count() - 1, LANGUAGES[key], Qt.ToolTipRole)
        idx = self.language_combo.findData(get_language())
        if idx >= 0:
            self.language_combo.setCurrentIndex(idx)
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        h.addWidget(self.language_combo)

        self.theme_btn = self._build_theme_button()
        h.addWidget(self.theme_btn)

        if self._user_name:
            user_lbl = QLabel(f"👤 {self._user_name}")
            user_lbl.setObjectName("hint")
            h.addWidget(user_lbl)
        self.settings_btn = QPushButton(tr("app.settings"))
        from .ui.icons import icon as _icon
        self.settings_btn.setIcon(_icon("settings"))
        self.settings_btn.clicked.connect(self._open_settings)
        h.addWidget(self.settings_btn)
        return bar

    _BRAND_LOGO_NAMES = ("fpt_logo.png", "fpt-logo.png", "logo_fpt.png", "fpt_logo.jpg")
    _BRAND_LOGO_HEIGHT = 22

    def _brand_logo_pixmap(self):
        """The FPT logo scaled to top-bar height, or None while no logo file
        exists yet — drop the artwork into src/cowork_local/assets/ under one
        of the _BRAND_LOGO_NAMES and it appears on next launch."""
        from PySide6.QtGui import QPixmap

        for name in self._BRAND_LOGO_NAMES:
            path = ASSETS / name
            if not path.exists():
                continue
            pm = QPixmap(str(path))
            if pm.isNull():
                continue
            return pm.scaledToHeight(self._BRAND_LOGO_HEIGHT, Qt.SmoothTransformation)
        return None

    _THEME_ICONS = {"system": "monitor", "dark": "moon", "light": "sun"}

    def _build_theme_button(self) -> QToolButton:
        """A single icon button (System/Dark/Light) replacing the old
        Settings-only theme dropdown — one click applies the choice
        immediately via the existing _apply_theme(), no dialog round-trip."""
        from .ui.icons import icon as _icon

        btn = QToolButton()
        btn.setPopupMode(QToolButton.InstantPopup)
        menu = QMenu(btn)
        self._theme_actions = {}
        for value, icon_name in self._THEME_ICONS.items():
            act = menu.addAction(_icon(icon_name), tr(f"settings.theme_{value}"))
            act.triggered.connect(lambda _checked=False, v=value: self._set_theme(v))
            self._theme_actions[value] = act
        btn.setMenu(menu)
        btn.setIcon(_icon(self._THEME_ICONS.get(self.ctx.config.theme, "monitor")))
        return btn

    def _set_theme(self, value: str) -> None:
        from .ui.icons import icon as _icon

        self.ctx.config.theme = value
        self.ctx.save()
        self._apply_theme()
        self.theme_btn.setIcon(_icon(self._THEME_ICONS.get(value, "monitor")))

    # ---- handlers ----------------------------------------------------
    def _on_provider_changed(self, _idx: int) -> None:
        self.ctx.config.active_provider = self.provider_combo.currentData()
        self.ctx.save()
        self.cowork.refresh_header()
        # Reload the Cowork tab's Agent (Model) list for the newly selected provider.
        self.cowork.refresh_agents()
        self.workspace.refresh_ai_models()   # + the Folder AI-edit model picker
        self.statusBar().showMessage(
            tr("app.status.using_provider",
               label=PROVIDER_LABELS.get(self.ctx.config.active_provider))
        )

    def _on_language_changed(self, _idx: int) -> None:
        lang = self.language_combo.currentData()
        if not lang or lang == get_language():
            return
        self.ctx.config.language = lang
        self.ctx.save()
        set_language(lang)   # notifies every registered persistent widget

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.ctx, self)
        if dlg.exec():
            self._apply_theme()
            set_language(self.ctx.config.language)   # apply if changed in Settings
            # reflect provider/theme/language changes
            i = self.provider_combo.findData(self.ctx.config.active_provider)
            if i >= 0:
                self.provider_combo.setCurrentIndex(i)
            li = self.language_combo.findData(get_language())
            if li >= 0:
                self.language_combo.blockSignals(True)
                self.language_combo.setCurrentIndex(li)
                self.language_combo.blockSignals(False)
            self.cowork.refresh_header()
            self.cowork.refresh_agents()
            self.workspace.refresh_ai_models()   # + the Folder AI-edit model picker
            max_files = int(self.ctx.config.data.get("attachments", {}).get("max_files", 10) or 0)
            self.cowork.composer.set_max_attachments(max_files)
            self.sidebar.refresh()
            self.statusBar().showMessage(tr("app.status.settings_saved"))

    def _reload_nav_children(self, page: int) -> None:
        """(Re)build the nav children of a container page from its current
        sub-views. Called when the page is built, when Workspace sub-tab
        visibility changes, and on language change."""
        if not (0 <= page < len(self._nav_items)):
            return
        item = self._nav_items[page]
        expanded = item.isExpanded()
        item.takeChildren()
        widget = self._page_widgets[page]
        if not hasattr(widget, "nav_subtabs"):
            return
        from .ui.icons import icon as _icon
        for label, sub, icon_name in widget.nav_subtabs():
            child = QTreeWidgetItem([label])
            child.setIcon(0, _icon(icon_name))
            child.setData(0, Qt.UserRole, {"page": page, "sub": sub, "parent": False})
            item.addChild(child)
        item.setExpanded(True)

    def _on_nav_click(self, item, _col: int = 0) -> None:
        # Clicking a parent toggles its expansion (its page is still shown).
        data = item.data(0, Qt.UserRole) or {}
        if data.get("parent"):
            item.setExpanded(not item.isExpanded())

    def _on_nav_expanded(self, item) -> None:
        # Expanding a container whose children aren't built yet (e.g. Monitoring
        # on first open, shown via its always-on dropdown arrow) builds its page
        # so the sub-views appear.
        data = item.data(0, Qt.UserRole) or {}
        if data.get("parent") and item.childCount() == 0:
            self._ensure_page(data.get("page", 0))

    def _navigate(self, item) -> None:
        if item is None:
            return
        data = item.data(0, Qt.UserRole) or {}
        self._goto(data.get("page", 0), data.get("sub"))

    def _goto(self, page: int, sub) -> None:
        self._ensure_page(page)                # build lazy page on first visit
        self.pages.setCurrentIndex(page)
        if page == self._ROW_WORKSPACE:
            self.workspace.refresh()           # re-list projects + threads on entry
        widget = self._page_widgets[page]
        if sub is not None and hasattr(widget, "select_subtab"):
            widget.select_subtab(sub)
        # Switching pages updates which conversation is "current".
        self._refresh_history()

    def _on_projects_changed(self) -> None:
        self.sidebar.refresh()                 # History regroups by project
        self.cowork._apply_output_folder_label()  # project may have been renamed
        self.structure._refresh_project_combo()   # GraphRAG's project lock list follows too

    def _apply_theme(self) -> None:
        app = QApplication.instance()
        if app:
            app.setStyleSheet(stylesheet(self.ctx.config.theme))
        # Re-apply theme styles to chat bubbles so they adapt to the new theme.
        self.cowork.apply_theme()
        if getattr(self, "help_agent", None) is not None:
            self.help_agent.apply_theme()   # chat body follows theme (header stays fixed)

    # ---- sizing ------------------------------------------------------
    def _fit_to_screen(self, want_w: int, want_h: int) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        if avail is None:
            self.resize(want_w, want_h)
            return
        margin = 60
        w = min(want_w, avail.width() - margin)
        h = min(want_h, avail.height() - margin)
        # minimum must never exceed what the screen can show
        self.setMinimumSize(min(820, avail.width() - margin), min(520, avail.height() - margin))
        self.resize(max(w, 1), max(h, 1))
        frame = self.frameGeometry()
        frame.moveCenter(avail.center())
        self.move(frame.topLeft())

    # ---- lifecycle ---------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        keep = (self.tray is not None
                and self.ctx.config.data.get("tray", {}).get("minimize_on_close", True))
        if keep and not self._really_quit:
            # Keep running in the background; tasks continue and autosave.
            event.ignore()
            self.hide()
            try:
                self.tray.showMessage(
                    DISPLAY_NAME, tr("app.tray.running_body"),
                    QSystemTrayIcon.Information, 4000)
            except Exception:
                pass
            return
        # Real quit: stop every running turn (a tab may have several), then close.
        self.task_scheduler.stop()   # also stops any scheduled tasks
        if getattr(self, "routing_scheduler", None) is not None:
            self.routing_scheduler.stop()
        for tab in (self.cowork,):
            for w in tab.active_workers():
                if w.isRunning():
                    w.request_stop()
                    w.wait(1500)
        # Safely stop codebase-memory UI if the method exists
        if hasattr(self.structure, 'stop_cmem_ui'):
            self.structure.stop_cmem_ui()
        self.ctx.stop_mcp_connections()  # never leave a connected MCP server subprocess behind
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(event)


def _set_windows_app_id() -> None:
    """Make Windows use our window icon on the taskbar (not python.exe's)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FPT.CoworkLocal.2.0")
    except Exception:
        pass


def run(argv: List[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    _set_windows_app_id()
    app = QApplication.instance() or QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    ctx = AppContext(AppConfig.load())
    set_language(ctx.config.language)
    # Built-in default skills (if any are bundled) are always-on and loaded
    # straight from the package; tidy away any copy seeded by older versions so they
    # stay hidden from the Skills manager.
    try:
        from .core.skills import prune_seeded_builtins
        prune_seeded_builtins()
    except Exception:  # noqa: BLE001 - housekeeping must never block startup
        pass
    # Seed the bundled built-in skill library + the built-in Co4E flow into the
    # user's editable stores on first run, so they show up in the Skill Manager
    # and the Flow sidebar out-of-the-box (a user-deleted one is not re-seeded).
    try:
        from .core.skills import seed_library_skills
        from .core.co4e_builtins import seed_builtin_flows
        changed = False
        # Content-versioned: returns the full tag list to persist (delivers updates
        # to shipped skills, preserves user edits to unchanged ones, respects deletion).
        skill_tags = seed_library_skills(ctx.config.seeded_library_skills)
        if set(skill_tags) != set(ctx.config.seeded_library_skills):
            ctx.config.seeded_library_skills = skill_tags
            changed = True
        new_flows = seed_builtin_flows(ctx.config.seeded_builtin_flows)
        if new_flows:
            ctx.config.seeded_builtin_flows = ctx.config.seeded_builtin_flows + new_flows
            changed = True
        if changed:
            ctx.config.save()
    except Exception:  # noqa: BLE001 - seeding must never block startup
        pass
    app.setStyleSheet(stylesheet(ctx.config.theme))

    # Follow the OS light/dark scheme live when theme is "Auto (System)".
    import socket

    from .core import audit_log, usage_tracker

    machine = socket.gethostname()
    usage_tracker.set_identity("local", machine, "")
    audit_log.set_identity("local", machine, "admin", "")

    win = MainWindow(ctx, user_name="local")

    def _reapply_system_theme(*_a):
        if ctx.config.theme == "system":
            app.setStyleSheet(stylesheet("system"))
            win.cowork.apply_theme()
    try:
        app.styleHints().colorSchemeChanged.connect(_reapply_system_theme)
    except Exception:
        pass

    win.show()
    return app.exec()
