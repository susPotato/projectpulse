"""Workspace screen — the app's home. Manages Projects (Claude-Projects style)
AND hosts, per selected project, the **Cowork** chat and **GraphRAG** views as
sub-tabs, all confined to that project's sandbox.

Left: the list of projects (create / delete, collapsible). Right: a tab strip
for the selected project —

* **Cowork** — the chat (with its per-project History sidebar).
* **GraphRAG** — the knowledge graph, locked to the project's sandbox.
* **Project** — name, description, shared **instructions** (injected into every
  chat of the project), its sandbox **workspace folder**, and the project's
  conversation **threads**.

The Cowork/GraphRAG widgets are created by the main window and handed in so the
whole app shares one instance of each; when they are not provided (e.g. unit
tests that exercise only project management) the screen still works as a plain
project manager.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QTabWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import collapse_left_icon, icon
from .osutil import open_folder
from .widgets import CollapseStrip


class WorkspaceTab(QWidget):
    status_message = Signal(str)
    open_chat = Signal(str, dict)      # kind, conversation — open a thread in Cowork
    new_chat = Signal(str)             # project_id — start a new thread in this project
    projects_changed = Signal()        # created/edited/deleted → History regroups
    subtabs_changed = Signal()         # visible sub-tabs changed → left-nav children refresh

    # ---- nav integration: the sub-tabs are driven from the left nav rail -----
    def nav_subtabs(self):
        """(label, index, icon_name) for each VISIBLE sub-tab — the left nav lists
        these as children under 'Workspaces'. Icons are keyed by tab index (not
        label) so they're correct in every language."""
        icons = {self._project_tab_idx: "folder", self._cowork_tab_idx: "chat",
                 self._co4e_tab_idx: "flow", self._folder_tab_idx: "folder",
                 self._graphrag_tab_idx: "graph"}
        return [(self.tabs.tabText(i), i, icons.get(i, "chevron-right"))
                for i in range(self.tabs.count()) if self.tabs.isTabVisible(i)]

    def select_subtab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def hide_tab_bar(self) -> None:
        """Hide the in-content tab strip (the nav rail drives the sub-tabs now),
        so the content area is as large as possible."""
        self.tabs.tabBar().hide()

    def __init__(self, ctx: AppContext, cowork=None, structure=None, sidebar=None):
        super().__init__()
        self.ctx = ctx
        self._current_id = ""
        # Shared widgets embedded as per-project sub-tabs (None in unit tests
        # that only drive project management).
        self._cowork = cowork
        self._structure = structure
        self._sidebar = sidebar

        root = QVBoxLayout(self)
        self._header = QLabel()
        self._header.setStyleSheet("font-weight:700; font-size:15px;")
        self._hint = QLabel()
        self._hint.setObjectName("hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._header)
        root.addWidget(self._hint)

        self._split = QSplitter(Qt.Horizontal)
        root.addWidget(self._split, 1)

        # ---- left: project list (collapsible — same chevron/strip pattern
        # as History and the GraphRAG Agent panel) ---------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        left_hdr = QHBoxLayout()
        self._proj_collapse_btn = QPushButton()
        self._proj_collapse_btn.setIcon(collapse_left_icon())
        self._proj_collapse_btn.setFixedWidth(28)
        self._proj_collapse_btn.clicked.connect(lambda: self._set_projects_collapsed(True))
        left_hdr.addWidget(self._proj_collapse_btn)
        left_hdr.addStretch(1)
        ll.addLayout(left_hdr)
        self.project_list = QListWidget()
        self.project_list.currentItemChanged.connect(self._on_select)
        ll.addWidget(self.project_list, 1)
        btns = QHBoxLayout()
        self._new_btn = QPushButton()
        self._new_btn.setIcon(icon("plus"))
        self._new_btn.setObjectName("primary")
        self._new_btn.clicked.connect(self._create)
        self._del_btn = QPushButton()
        self._del_btn.setIcon(icon("trash"))
        self._del_btn.clicked.connect(self._delete)
        btns.addWidget(self._new_btn, 1)
        btns.addWidget(self._del_btn)
        ll.addLayout(btns)
        self._projects_panel = left

        # Thin strip shown in place of the project list when collapsed —
        # clicking it re-expands (identical affordance to History's).
        self._projects_strip = CollapseStrip(tr("workspace.expand_projects_tooltip"), expand_dir="right")
        self._projects_strip.clicked.connect(lambda: self._set_projects_collapsed(False))
        self._projects_strip.setVisible(False)
        self._projects_pane = QWidget()
        ppl = QHBoxLayout(self._projects_pane)
        ppl.setContentsMargins(0, 0, 0, 0)
        ppl.setSpacing(0)
        ppl.addWidget(self._projects_strip)
        ppl.addWidget(left, 1)
        self._split.addWidget(self._projects_pane)

        # ---- right: per-project tabs (Project / Cowork / GraphRAG) -----------
        # Project comes FIRST; Cowork + GraphRAG only appear once a project is
        # actually selected (see _update_tab_visibility).
        self.tabs = QTabWidget()
        self._project_tab_idx = self.tabs.addTab(self._build_project_tab(), tr("workspace.tab_project"))
        self._cowork_tab_idx = -1
        self._graphrag_tab_idx = -1

        if self._cowork is not None:
            cowork_page = QWidget()
            cpl = QVBoxLayout(cowork_page)
            cpl.setContentsMargins(0, 0, 0, 0)
            cpl.addWidget(self._cowork)
            self._cowork_tab_idx = self.tabs.addTab(cowork_page, tr("workspace.tab_cowork"))

        # Co4E — node-graph workflow studio (built-in flows, agents, skills, a
        # runner + chat). Always available (not project-gated): its workflows
        # live globally under ~/.cowork_local/co4e, not inside one project.
        # Placed BEFORE GraphRAG in the tab order (user request).
        from .co4e_tab import Co4ETab

        self._co4e = Co4ETab(self.ctx)
        self._co4e_tab_idx = self.tabs.addTab(self._co4e, tr("workspace.tab_co4e"))
        self.tabs.setTabToolTip(self._co4e_tab_idx, tr("workspace.tab_co4e_tooltip"))

        # Folder — a two-pane file explorer (tree + view/edit) placed right below
        # Co4E. Always available (not project-gated); its root follows the
        # selected project's workspace folder when one is chosen.
        from .folder_tab import FolderTab

        self._folder = FolderTab(self.ctx, cowork=self._cowork)
        self._folder.status_message.connect(self.status_message)
        self._folder_tab_idx = self.tabs.addTab(self._folder, tr("workspace.tab_folder"))

        if self._structure is not None:
            self._graphrag_tab_idx = self.tabs.addTab(self._structure, tr("workspace.tab_graphrag"))

        self.tabs.currentChanged.connect(self._on_tab_changed)

        # History lives in its own pane of the OUTER splitter (not nested
        # inside the Cowork tab page) so it stays visible across Cowork AND
        # GraphRAG, instead of disappearing whenever GraphRAG is the active
        # sub-tab (QTabWidget only shows the current page's widget).
        if self._sidebar is not None:
            self._split.addWidget(self._sidebar)
            self._wire_sidebar()
        self._split.addWidget(self.tabs)
        self._split.setStretchFactor(0, 0)
        if self._sidebar is not None:
            self._split.setStretchFactor(1, 0)
            self._split.setStretchFactor(2, 1)
            self._split.setSizes([260, 240, 800])
        else:
            self._split.setStretchFactor(1, 1)
            self._split.setSizes([260, 900])
        self._apply_pane_visibility()

        self.refresh()
        on_language_changed(self._retranslate)
        self._retranslate()

    # ---- project settings tab -------------------------------------------
    def _build_project_tab(self) -> QWidget:
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 4, 4, 4)

        self.name_edit = QLineEdit()
        self.desc_edit = QLineEdit()
        self._name_lbl = QLabel()
        self._desc_lbl = QLabel()
        rl.addWidget(self._name_lbl)
        rl.addWidget(self.name_edit)
        rl.addWidget(self._desc_lbl)
        rl.addWidget(self.desc_edit)

        self._instr_lbl = QLabel()
        self.instr_edit = QPlainTextEdit()
        self.instr_edit.setMaximumHeight(120)
        rl.addWidget(self._instr_lbl)
        rl.addWidget(self.instr_edit)

        folder_row = QHBoxLayout()
        self.folder_lbl = QLabel()
        self.folder_lbl.setObjectName("hint")
        self._browse_btn = QPushButton()
        self._browse_btn.setIcon(icon("folder"))
        self._browse_btn.clicked.connect(self._pick_folder)
        self._open_btn = QPushButton()
        self._open_btn.setIcon(icon("upload"))
        self._open_btn.clicked.connect(self._open_workspace)
        folder_row.addWidget(self.folder_lbl, 1)
        folder_row.addWidget(self._browse_btn)
        folder_row.addWidget(self._open_btn)
        rl.addLayout(folder_row)

        save_row = QHBoxLayout()
        self._save_btn = QPushButton()
        self._save_btn.setIcon(icon("save"))
        self._save_btn.setObjectName("primary")
        self._save_btn.clicked.connect(self._save)
        save_row.addStretch(1)
        save_row.addWidget(self._save_btn)
        rl.addLayout(save_row)

        # The per-project conversation-threads list ("group chat") was removed
        # from here — chats live in the History sidebar + the Cowork tab. Keep
        # the settings compact at the top with a stretch below.
        rl.addStretch(1)
        return right

    # ---- embedded sidebar (History inside the Cowork tab) ---------------
    def _wire_sidebar(self) -> None:
        sb = self._sidebar
        sb.open_chat.connect(self._on_sidebar_open)
        sb.new_chat.connect(self._on_sidebar_new)
        sb.collapse_requested.connect(lambda: self._set_sidebar_collapsed(True))
        sb.expand_requested.connect(lambda: self._set_sidebar_collapsed(False))
        sb.refresh_requested.connect(self._on_sidebar_refresh)
        sb.history_changed.connect(self._reload_threads)

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        """Collapse/expand History sidebar and redistribute splitter space so
        the Cowork chat area fills the freed width (same pattern as
        _set_projects_collapsed)."""
        sb = self._sidebar
        sb.set_collapsed(collapsed)
        strip_w = CollapseStrip.WIDTH + 2
        if sb is None:
            return
        # Find sidebar index in splitter
        idx = self._split.indexOf(sb)
        if not (0 <= idx < self._split.count()):
            return
        sizes = self._split.sizes()
        if collapsed:
            freed = sizes[idx] - strip_w
            sizes[idx] = strip_w
        else:
            freed = 240 - sizes[idx]
            sizes[idx] = 240
        # Give freed width to the LAST pane (tabs/Cowork area)
        if freed != 0 and len(sizes) > 1:
            sizes[-1] = max(1, sizes[-1] + freed)
        self._split.setSizes(sizes)

    def _on_sidebar_open(self, kind: str, conv: dict) -> None:
        pid = conv.get("project_id", "") or "default"
        # The legacy "default"/no-project id is intentionally not a real
        # Project row (Cowork itself special-cases it as global/no-knowledge —
        # see chat_agent.py), so it's fine to open with nothing selected. But a
        # genuinely deleted project id must NOT force the Cowork tab open,
        # since _update_tab_visibility never ran for it — that would show the
        # Cowork page while the tab strip still says "no project selected".
        if pid not in ("", "default") and not self._select_project_row(pid):
            self.status_message.emit(tr("workspace.conversation_project_missing"))
            return
        if self._cowork is not None:
            self._cowork.load_conversation(conv)
        self._show_cowork_tab()
        self.open_chat.emit(kind or "cowork", conv)

    def _on_sidebar_new(self, kind: str) -> None:
        if self._cowork is not None:
            self._cowork.new_session()
        self._show_cowork_tab()

    def _on_sidebar_refresh(self) -> None:
        if self._cowork is not None:
            self._cowork.refresh_status()
        if self._sidebar is not None:
            self._sidebar.refresh()

    def _show_cowork_tab(self) -> None:
        if self._cowork_tab_idx >= 0:
            self.tabs.setCurrentIndex(self._cowork_tab_idx)

    def _on_tab_changed(self, idx: int) -> None:
        # Entering GraphRAG builds its (lazy) WebEngine view and scans the
        # project's sandbox; entering it is what keeps startup RAM low.
        if idx == self._graphrag_tab_idx and self._structure is not None:
            self._structure.auto_scan_and_fit()
        self._apply_pane_visibility()

    def _apply_pane_visibility(self) -> None:
        """Which side panes accompany each sub-tab:

            Project  → project list ✓   History ✗
            Cowork   → project list ✗   History ✓
            GraphRAG → project list ✗   History ✗

        Every page other than Project/Cowork (GraphRAG) gets the full width;
        switching projects is done from the Project tab (the list there is
        the app's only project picker)."""
        idx = self.tabs.currentIndex()
        on_project = idx == self._project_tab_idx
        on_cowork = self._cowork_tab_idx >= 0 and idx == self._cowork_tab_idx
        self._projects_pane.setVisible(on_project)
        if self._sidebar is not None:
            self._sidebar.setVisible(on_cowork)
            # Auto-expand History when entering Cowork tab so it's always usable
            if on_cowork:
                self._sidebar.set_collapsed(False)
        # QSplitter ignores hidden panes, but the freed width isn't handed to
        # the remaining panes deterministically — set explicit sizes after any
        # pane toggle (same lesson as _set_projects_collapsed).
        total = sum(self._split.sizes()) or 1300
        proj_w = 260 if on_project else 0
        hist_w = 240 if (on_cowork and self._sidebar is not None) else 0
        if self._split.count() >= 3:
            self._split.setSizes([proj_w, hist_w, max(1, total - proj_w - hist_w)])
        else:
            self._split.setSizes([proj_w, max(1, total - proj_w)])

    # ---- i18n ------------------------------------------------------------
    def _retranslate(self) -> None:
        self._header.setText(tr("workspace.header"))
        self._hint.setText(tr("workspace.hint"))
        self._new_btn.setText(tr("workspace.new_project"))
        self._del_btn.setText(tr("workspace.delete"))
        self._name_lbl.setText(tr("workspace.name"))
        self._desc_lbl.setText(tr("workspace.description"))
        self._instr_lbl.setText(tr("workspace.instructions"))
        self.instr_edit.setPlaceholderText(tr("workspace.instructions_placeholder"))
        self._browse_btn.setText(tr("workspace.browse"))
        self._browse_btn.setToolTip(tr("workspace.browse_tooltip"))
        self._open_btn.setText(tr("workspace.open_folder"))
        self._save_btn.setText(tr("workspace.save"))
        self._proj_collapse_btn.setToolTip(tr("workspace.collapse_projects_tooltip"))
        self._projects_strip.setToolTip(tr("workspace.expand_projects_tooltip"))
        self.tabs.setTabText(self._project_tab_idx, tr("workspace.tab_project"))
        if self._cowork_tab_idx >= 0:
            self.tabs.setTabText(self._cowork_tab_idx, tr("workspace.tab_cowork"))
        if self._co4e_tab_idx >= 0:
            self.tabs.setTabText(self._co4e_tab_idx, tr("workspace.tab_co4e"))
            self.tabs.setTabToolTip(self._co4e_tab_idx, tr("workspace.tab_co4e_tooltip"))
        if getattr(self, "_folder_tab_idx", -1) >= 0:
            self.tabs.setTabText(self._folder_tab_idx, tr("workspace.tab_folder"))
        if self._graphrag_tab_idx >= 0:
            self.tabs.setTabText(self._graphrag_tab_idx, tr("workspace.tab_graphrag"))

    # ---- project list collapse (same pattern as History / GraphRAG Agent panel) --
    def _set_projects_collapsed(self, collapsed: bool) -> None:
        strip_w = CollapseStrip.WIDTH + 2
        self._projects_panel.setVisible(not collapsed)
        self._projects_strip.setVisible(collapsed)
        if collapsed:
            self._projects_pane.setMaximumWidth(strip_w)
            # A maximumWidth constraint alone doesn't make QSplitter hand the
            # freed space to the OTHER pane(s) — it must be told explicitly
            # (same fix as StructureGraphView._set_agent_collapsed), otherwise
            # the tabs pane on the right stays stuck at its old (narrower)
            # size. The freed width goes to the LAST pane (tabs) regardless of
            # whether History occupies a middle pane or not.
            sizes = self._split.sizes()
            if len(sizes) >= 2:
                freed = sizes[0] - strip_w
                sizes[0] = strip_w
                sizes[-1] = max(1, sizes[-1] + freed)
                self._split.setSizes(sizes)
        else:
            self._projects_pane.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX
            if self._sidebar is not None:
                self._split.setSizes([260, 240, 800])
            else:
                self._split.setSizes([260, 900])

    # ---- data ------------------------------------------------------------
    def refresh_ai_models(self) -> None:
        """Reload the Folder tab's AI-edit model picker for the active provider —
        called when the active provider changes so the picker never keeps a
        stale model list from the old provider."""
        folder = getattr(self, "_folder", None)
        if folder is not None and hasattr(folder, "ai_model_combo"):
            folder.refresh_ai_models()

    def refresh(self) -> None:
        """Re-list projects, keeping the current selection when possible. No
        auto-seed: an empty workspace stays empty (the user must create a
        project before Cowork/GraphRAG appear — see _update_tab_visibility)."""
        from ..core.projects import list_projects

        keep = self._current_id
        self.project_list.blockSignals(True)
        self.project_list.clear()
        row_to_select = 0
        for i, p in enumerate(list_projects()):
            item = QListWidgetItem(p.name)
            item.setData(Qt.UserRole, p.project_id)
            if p.description:
                item.setToolTip(p.description)
            self.project_list.addItem(item)
            if p.project_id == keep:
                row_to_select = i
        self.project_list.blockSignals(False)
        self.project_list.setCurrentRow(row_to_select)
        self._load_current()

    def _selected_id(self) -> str:
        item = self.project_list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _select_project_row(self, project_id: str) -> bool:
        for i in range(self.project_list.count()):
            if self.project_list.item(i).data(Qt.UserRole) == project_id:
                self.project_list.setCurrentRow(i)
                return True
        return False

    def _on_select(self, *_a) -> None:
        self._load_current()

    def _load_current(self) -> None:
        from ..core.projects import load_project

        pid = self._selected_id()
        self._current_id = pid
        # Deactivate the Cowork/GraphRAG panes while (re)loading — creating a
        # project or switching to another one re-binds their sandbox/history
        # underneath them, so they must not look interactive mid-transition.
        self._set_tabs_busy(True)
        try:
            project = load_project(pid) if pid else None
            # Route conversation history INTO the project's workspace folder so
            # sharing that folder shares the history (another machine can view +
            # continue). No project → global history dir (attribute cleared).
            if project is not None:
                self.ctx.config._project_history_dir = project.workspace_dir() / ".cowork_history"
            else:
                self.ctx.config._project_history_dir = None
            if project is not None:
                self.name_edit.setText(project.name)
                self.desc_edit.setText(project.description)
                self.instr_edit.setPlainText(project.instructions)
                self.folder_lbl.setText(str(project.workspace_dir()))
                self._del_btn.setEnabled(True)
                self._reload_threads()
                if getattr(self, "_folder", None) is not None:
                    self._folder.set_root(str(project.workspace_dir()))
            else:
                self.name_edit.clear()
                self.desc_edit.clear()
                self.instr_edit.clear()
                self.folder_lbl.setText("")
                self._del_btn.setEnabled(False)
            # Show Cowork/GraphRAG ONLY when a project is actually selected.
            self._update_tab_visibility(project is not None)
            # Bind the embedded Cowork/GraphRAG/History to this project's sandbox.
            self._bind_project(pid)
        finally:
            self._set_tabs_busy(False)

    def _set_tabs_busy(self, busy: bool) -> None:
        for idx in (self._cowork_tab_idx, self._graphrag_tab_idx):
            if idx >= 0:
                widget = self.tabs.widget(idx)
                if widget is not None:
                    widget.setEnabled(not busy)

    def _update_tab_visibility(self, has_project: bool) -> None:
        """Cowork + GraphRAG tabs are visible only while a project is
        selected; otherwise the screen shows just the Project (management)
        tab."""
        for idx in (self._cowork_tab_idx, self._graphrag_tab_idx):
            if idx >= 0:
                self.tabs.setTabVisible(idx, has_project)
        if not has_project:
            self.tabs.setCurrentIndex(self._project_tab_idx)
        # setCurrentIndex doesn't fire currentChanged when the Project tab is
        # already current — re-apply the pane rules explicitly.
        self._apply_pane_visibility()
        self.subtabs_changed.emit()   # left-nav children follow visible sub-tabs

    def _bind_project(self, pid: str) -> None:
        # This is THE central project-switch hook — make the selected project the
        # ACTIVE workspace so per-workspace modes (routing + auto-run) resolve
        # against it, then refresh every surface's toggles to show its modes.
        self.ctx.active_project_id = pid or "default"
        self._refresh_mode_toggles()
        if self._structure is not None:
            self._structure.set_project(pid)
        if self._sidebar is not None:
            self._sidebar.set_project_filter(pid)   # "" → show all (no project selected)
        # Route Co4E flow output into THIS project's workspace folder (so flow
        # files land in the selected workspace, like Cowork — not the config dir).
        if self._co4e is not None:
            self._co4e.set_project(pid)
        if self._cowork is not None and pid:
            # Switch to a fresh thread in the newly selected project (past
            # threads are reopened from History), unless the current thread is
            # already in it.
            if getattr(self._cowork, "project_id", "") != pid:
                self._cowork.new_session()
                self._cowork.set_project(pid)

    def _refresh_mode_toggles(self) -> None:
        """Re-point every surface's Off/Auto/Manual + Auto-run toggles at the
        ACTIVE workspace's modes (called whenever the selected project changes)."""
        targets = [
            (self._cowork, "routing_toggle"),
            (self._cowork, "autorun_toggle"),
            (self._co4e, "co4e_routing_toggle"),
            (self._folder, "ai_routing_toggle"),
        ]
        for widget, attr in targets:
            toggle = getattr(widget, attr, None) if widget is not None else None
            if toggle is not None:
                toggle.refresh()

    def _reload_threads(self) -> None:
        # The threads list was removed from the Project tab; nothing to reload.
        if not hasattr(self, "threads"):
            return
        from ..core.history import list_conversations

        self.threads.clear()
        pid = self._current_id or "default"
        for conv in list_conversations(self.ctx.config.history_dir()):
            if conv.get("project_id", "default") != pid:
                continue
            label = conv["title"]
            if conv.get("created"):
                label += f"   ·   {conv['created'][:16].replace('T', ' ')}"
            item = QTreeWidgetItem([label])
            item.setData(0, Qt.UserRole, str(conv["path"]))
            self.threads.addTopLevelItem(item)

    # ---- actions -----------------------------------------------------------
    def _create(self) -> None:
        from ..core.projects import new_project

        project = new_project(tr("workspace.default_new_name"))
        self._current_id = project.project_id
        self.refresh()
        self.projects_changed.emit()
        self.name_edit.setFocus()
        self.name_edit.selectAll()

    def _delete(self) -> None:
        from ..core.projects import delete_project, load_project

        pid = self._selected_id()
        project = load_project(pid) if pid else None
        if project is None:
            return
        if QMessageBox.question(
                self, tr("workspace.delete"),
                tr("workspace.delete_confirm", name=project.name)) != QMessageBox.Yes:
            return
        delete_project(pid)
        self._current_id = ""
        self.refresh()   # empty workspace → Cowork/GraphRAG hidden until a new project
        self.projects_changed.emit()
        self.status_message.emit(tr("workspace.deleted", name=project.name))

    def _save(self) -> None:
        from ..core.projects import load_project, save_project

        pid = self._current_id
        project = load_project(pid) if pid else None
        if project is None:
            return
        project.name = self.name_edit.text().strip() or project.name
        project.description = self.desc_edit.text().strip()
        project.instructions = self.instr_edit.toPlainText().strip()
        save_project(project)
        self.refresh()
        self.projects_changed.emit()
        self.status_message.emit(tr("workspace.saved", name=project.name))

    def _pick_folder(self) -> None:
        from ..core.projects import load_project, save_project

        pid = self._current_id
        project = load_project(pid) if pid else None
        if project is None:
            return
        chosen = QFileDialog.getExistingDirectory(
            self, tr("workspace.browse_tooltip"), str(project.workspace_dir()))
        if not chosen:
            return
        project.output_dir = chosen
        save_project(project)
        self.folder_lbl.setText(chosen)
        self.status_message.emit(tr("workspace.saved", name=project.name))

    def _open_workspace(self) -> None:
        from ..core.projects import load_project

        project = load_project(self._current_id) if self._current_id else None
        if project is None:
            return
        wd = project.workspace_dir()
        wd.mkdir(parents=True, exist_ok=True)
        open_folder(str(wd))
