"""Co4E — node-graph workflow studio (a Workspace sub-tab).

Layout (3 columns): left sidebar (Workflows / Agents / Skills, with CRUD,
drag-to-canvas, run-in-background + a live "Running flows" status list) | center
(compact toolbar + node canvas + bottom Chat/Output) | right (step config panel).

Runs: pick a mode — Auto (each step's agent plans then executes), Plan
(read-only, each step only drafts a plan), or Manual (step-by-step, advance with
"Next step"). Adjacent steps feed the next automatically (no manual wiring).
Several flows can run at once (foreground on the canvas + background from the
Workflows list); the run manager keeps their status live across sub-tab switches.

Chat supports inline directives with autocomplete: ``/agent:<name>`` picks a
persona and ``/skill:<name>`` applies a skill — same as Cowork.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import QMimeData, QSize, Qt, Signal
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QListView, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpacerItem, QSplitter, QTabBar, QTableWidget,
    QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from ..core import co4e, skills as skills_mod
from ..core.co4e_builtins import BUILTIN_AGENTS
from ..core.co4e_run_manager import Co4ERunManager
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from .chat_view import ChatView
from .co4e_canvas import CO4E_MIME, Co4ECanvas
from .co4e_config_panel import StepConfigPanel
from .icons import icon


_PLAN_GLYPH = {"completed": "✓", "done": "✓", "in_progress": "▶", "running": "▶",
               "error": "✗", "pending": "○", "todo": "○"}


def _fmt_plan(steps) -> str:
    """Render plan steps ``[{title,status}]`` as a ticked-off checklist."""
    lines = []
    for s in steps or []:
        title = str((s or {}).get("title", "")).strip()
        if not title:
            continue
        glyph = _PLAN_GLYPH.get(str((s or {}).get("status", "pending")).lower(), "○")
        lines.append(f"{glyph} {title}")
    return "\n".join(lines)


def _skill_names() -> List[str]:
    try:
        return [s.name for s in skills_mod.list_skills() + skills_mod.builtin_skills()]
    except Exception:  # noqa: BLE001
        return []


def _agent_names() -> List[str]:
    names = [a.name for a in co4e.list_custom_agents()]
    names += [a.name for a in BUILTIN_AGENTS if a.name not in names]
    return names


class _EqualTabBar(QTabBar):
    """Icon-only sidebar tabs (Workflows / Agents / Skills), all the same width,
    sized to fill the sidebar with a comfortable minimum (~double the default
    icon-only width so they read as proper buttons) and an even gap between them.
    The icon sits centered in each tab."""

    _GAP = 6            # px between tabs — matches the QSS margin-right below

    def tabSizeHint(self, index):  # noqa: N802
        base = super().tabSizeHint(index)
        n = self.count() or 1
        avail = self.width()
        if avail <= 1:                       # width not resolved yet → use parent
            p = self.parentWidget()
            avail = p.width() if p is not None else 0
        share = (avail - n * self._GAP) // n if avail > 1 else 0
        return QSize(max(56, share), max(30, base.height()))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.updateGeometry()                # re-hint tab widths when resized


class _PaletteList(QListWidget):
    """A list whose rows can be dragged onto the canvas. Each item carries a
    JSON-able drag payload (a step dict, or a workflow dict) in ``payload_role``.
    Workflow rows also keep a (kind, id) tuple in Qt.UserRole for load/delete."""

    def __init__(self, parent=None, payload_role=Qt.UserRole):
        super().__init__(parent)
        self._payload_role = payload_role
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragOnly)

    def startDrag(self, _actions):  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        payload = item.data(self._payload_role)
        if not payload:
            return
        md = QMimeData()
        md.setData(CO4E_MIME, json.dumps(payload).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


def _directive_token(text: str, pos: int):
    """Locate a ``/skill[:x]`` or ``/agent[:x]`` directive the cursor is on,
    anywhere in the line. Returns ``(start, kind, partial)`` or ``None``."""
    before = text[:pos]
    start = re.search(r"\S*$", before).start()
    token = before[start:]
    m = re.match(r"^/(skill|agent):?([\w\-.]*)$", token)
    if m:
        return start, m.group(1), m.group(2)
    for kind in ("skill", "agent"):
        if len(token) >= 2 and ("/" + kind).startswith(token):
            return start, kind, ""
    return None


class _ChatInput(QLineEdit):
    """Chat box with ``/skill:`` and ``/agent:`` autocomplete (parity with the
    Cowork composer). The popup never grabs focus, so typing keeps flowing."""

    submit = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._popup = QListWidget()
        self._popup.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint
                                   | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self._popup.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._popup.setFocusPolicy(Qt.NoFocus)
        self._popup.itemClicked.connect(lambda _i: self._accept())
        self.textEdited.connect(self._maybe_popup)

    def _maybe_popup(self, *_a) -> None:
        tok = _directive_token(self.text(), self.cursorPosition())
        if tok is None:
            self._popup.hide()
            return
        _start, kind, partial = tok
        f = partial.lower()
        self._popup.clear()
        if kind == "skill":
            for name in _skill_names():
                if f in name.lower():
                    self._add_row(name, f"/skill:{co4e.slugify(name)} ", name)
        else:
            for name in _agent_names():
                if f in name.lower():
                    self._add_row(name, f"/agent:{name} ", name)
        if self._popup.count() == 0:
            self._popup.hide()
            return
        self._popup.setCurrentRow(0)
        rows = min(7, self._popup.count())
        h = 8 + rows * 22
        self._popup.resize(max(280, self.width()), h)
        tl = self.mapToGlobal(self.rect().topLeft())
        self._popup.move(tl.x(), tl.y() - h - 2)
        self._popup.show()

    def _add_row(self, label: str, replacement: str, tip: str) -> None:
        it = QListWidgetItem(label)
        it.setData(Qt.UserRole, replacement)
        it.setToolTip(tip)
        self._popup.addItem(it)

    def _accept(self) -> None:
        item = self._popup.currentItem()
        self._popup.hide()
        if item is None:
            return
        replacement = item.data(Qt.UserRole)
        tok = _directive_token(self.text(), self.cursorPosition())
        start = tok[0] if tok else self.cursorPosition()
        pos = self.cursorPosition()
        full = self.text()
        new_text = full[:start] + replacement + full[pos:]
        self.setText(new_text)
        self.setCursorPosition(start + len(replacement))
        self.setFocus()

    def focusOutEvent(self, e):  # noqa: N802
        if not self._popup.underMouse():
            self._popup.hide()
        super().focusOutEvent(e)

    def keyPressEvent(self, e):  # noqa: N802
        if self._popup.isVisible():
            k = e.key()
            n = self._popup.count()
            if k in (Qt.Key_Down, Qt.Key_Up) and n:
                step = 1 if k == Qt.Key_Down else -1
                self._popup.setCurrentRow((self._popup.currentRow() + step) % n)
                return
            if k in (Qt.Key_Tab,):
                self._accept()
                return
            if k == Qt.Key_Escape:
                self._popup.hide()
                return
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._accept()
                return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.submit.emit()
            return
        super().keyPressEvent(e)


class Co4ETab(QWidget):
    status_message = Signal(str)

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self._wf: co4e.Workflow = co4e.new_workflow(tr("co4e.untitled"))
        self._chat_worker: Optional[AgentWorker] = None
        # run state
        self.manager = Co4ERunManager(ctx)
        self.manager.changed.connect(self._refresh_runs)
        self.manager.event.connect(self._on_manager_event)
        # Per-flow run state so any number of flow tabs run in PARALLEL without
        # mixing (bounded only by the machine — each run is its own QThread).
        self._flow_runs: Dict[str, str] = {}        # wf_id -> its active canvas run id
        self._run_logs: Dict[str, "ChatView"] = {}  # run_id -> that flow's chat log
        self._flow_outputs: Dict[str, Dict[str, str]] = {}  # wf_id -> {node_id: output}
        # Running token/cost total per flow (↓in ↑out ▤ctx $cost), shown in the
        # Messages header like Cowork's conversation total.
        self._flow_usage: Dict[str, Dict[str, float]] = {}
        self._project_id: str = ""                  # selected Workspace project
        self._project_dir: Optional[Path] = None    # its workspace folder (flow output goes here)
        # manual mode
        self._manual_active = False
        self._manual_order: List[str] = []
        self._manual_idx = 0
        # open flows shown as browser-style tabs (each its own graph; runs are
        # independent via the run manager)
        self._flows: List[co4e.Workflow] = []
        self._active_flow_idx = -1

        root = QHBoxLayout(self)
        self._split = QSplitter(Qt.Horizontal)
        root.addWidget(self._split)

        sidebar = self._build_sidebar()
        sidebar.setMinimumWidth(210)
        self._split.addWidget(sidebar)
        self._split.addWidget(self._build_center())
        self.config = StepConfigPanel(ctx)
        self.config.setMinimumWidth(300)     # so fields (incl. the model row) are never clipped
        self.config.changed.connect(self._on_config_changed)
        self.config.run_node.connect(lambda nid: self._run_single(nid))
        self.config.run_from.connect(self._run_from)
        self.config.delete_node.connect(self.canvas.delete_node)
        self._config_collapsed = False
        self._config_expanded_w = 360
        self._split.addWidget(self._wrap_config())
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        self._split.setStretchFactor(2, 0)
        self._split.setSizes([250, 780, 360])
        self._split.setChildrenCollapsible(True)

        self.canvas.node_selected.connect(self._on_node_selected)
        self.canvas.node_activated.connect(self._on_node_selected)
        self.canvas.graph_changed.connect(self._autosave)

        self._reload_sidebar()
        self._open_flow(self._wf)          # first browser-style flow tab
        self._retranslate()                # set Runs table headers etc.
        self._refresh_runs()
        on_language_changed(self._retranslate)

    # ---- flow tabs (browser-style: several open flows, independent) --------
    def _open_flow(self, wf: co4e.Workflow) -> None:
        """Open ``wf`` in a tab — reuse its tab if already open (like a browser),
        else add a new one and switch to it. Bar index 0 is the pinned Runs tab,
        so flow ``i`` lives at bar index ``i + 1``. If the flow has an active run,
        its live status is reflected on the canvas."""
        for i, f in enumerate(self._flows):
            if f.id == wf.id:
                self._flows[i] = wf
                bar_idx = i + 1
                self.flow_bar.setTabText(bar_idx, wf.name or tr("co4e.untitled"))
                if self.flow_bar.currentIndex() == bar_idx:
                    self._active_flow_idx = -1        # force reload of same tab
                    self._on_flow_tab_changed(bar_idx)
                else:
                    self.flow_bar.setCurrentIndex(bar_idx)
                self._reflect_active_run(wf.id)
                return
        self._flows.append(wf)
        self.flow_bar.blockSignals(True)
        bar_idx = self.flow_bar.addTab(icon("flow"), wf.name or tr("co4e.untitled"))
        self._add_tab_close_button(bar_idx)
        self.flow_bar.blockSignals(False)
        if self.flow_bar.currentIndex() == bar_idx:
            self._on_flow_tab_changed(bar_idx)        # already current → load manually
        else:
            self.flow_bar.setCurrentIndex(bar_idx)
        self._reflect_active_run(wf.id)

    def _on_flow_tab_changed(self, idx: int) -> None:
        # save the outgoing flow (active_flow_idx is a FLOWS-list index) first
        if 0 <= self._active_flow_idx < len(self._flows) and (self._active_flow_idx + 1) != idx:
            self._sync_wf_from_canvas()
        if idx <= 0:                                   # the pinned Runs tab
            self._active_flow_idx = -1
            self.center_stack.setCurrentIndex(0)
            self._refresh_runs()
            return
        flow_idx = idx - 1
        if not (0 <= flow_idx < len(self._flows)):
            return
        self._active_flow_idx = flow_idx
        self.center_stack.setCurrentIndex(1)
        self._apply_workflow(self._flows[flow_idx])

    def _add_tab_close_button(self, idx: int) -> None:
        """Give a flow tab its own close button — a small ✕ placed by QTabBar on
        the tab's right side, vertically centered and INSIDE the tab (reliable
        across themes, unlike the CSS-positioned default which looked detached)."""
        btn = QPushButton("×")                    # ×
        btn.setObjectName("flowTabClose")
        btn.setFlat(True)
        btn.setFixedSize(16, 16)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._close_flow_tab_button(btn))
        self.flow_bar.setTabButton(idx, QTabBar.RightSide, btn)

    def _close_flow_tab_button(self, btn) -> None:
        for i in range(self.flow_bar.count()):
            if self.flow_bar.tabButton(i, QTabBar.RightSide) is btn:
                self._close_flow_tab(i)
                return

    def _close_flow_tab(self, idx: int) -> None:
        if idx <= 0:                                   # Runs tab is pinned
            return
        flow_idx = idx - 1
        if not (0 <= flow_idx < len(self._flows)):
            return
        closing = self._flows[flow_idx]
        # Stop mirroring the closed flow's run onto the canvas — the run itself
        # keeps going in the background and stays in Flow Status. (Per-flow run
        # tracking: only this flow's entry is dropped; other flows keep running.)
        rid = self._flow_runs.pop(closing.id, None)
        if rid is not None:
            self._run_logs.pop(rid, None)
            if getattr(self, "_wf", None) is not None and self._wf.id == closing.id:
                self._manual_active = False
                self.run_btn.setText(tr("co4e.run"))
        self._flows.pop(flow_idx)
        self.flow_bar.blockSignals(True)
        self.flow_bar.removeTab(idx)
        self.flow_bar.blockSignals(False)
        self._active_flow_idx = -1
        if not self._flows:
            self._open_flow(co4e.new_workflow(tr("co4e.untitled")))
        else:
            new_bar = min(idx, len(self._flows))       # clamp to the last flow tab
            self.flow_bar.blockSignals(True)
            self.flow_bar.setCurrentIndex(new_bar)
            self.flow_bar.blockSignals(False)
            self._on_flow_tab_changed(new_bar)

    def _sync_active_flow_tab_text(self) -> None:
        i = self.flow_bar.currentIndex()
        if i >= 1:                                     # never rename the Runs tab
            self.flow_bar.setTabText(i, self._wf.name or tr("co4e.untitled"))

    def _reflect_active_run(self, wf_id: str) -> None:
        """If a run for this flow is active, mirror its live node statuses onto the
        canvas and keep tracking it so updates continue to show."""
        for h in self.manager.all_runs():
            if h.wf_id == wf_id and h.running:
                self._flow_runs[wf_id] = h.id
                for nid, st in h.node_status.items():
                    self.canvas.update_node_status(nid, st)
                return

    # ---- per-flow run helpers (parallel, isolated per flow) ---------------
    def _cur_run_id(self) -> Optional[str]:
        """The active canvas run of the CURRENTLY-shown flow, or None. Clears a
        stale entry if that run already finished."""
        wf = getattr(self, "_wf", None)
        if wf is None:
            return None
        rid = self._flow_runs.get(wf.id)
        if rid is None:
            return None
        h = self.manager.get(rid)
        if h is None or not h.running:
            self._flow_runs.pop(wf.id, None)
            return None
        return rid

    def _outputs_for(self, wf_id: str) -> Dict[str, str]:
        """This flow's accumulated step outputs (kept separate per flow so parallel
        runs never seed each other's context)."""
        return self._flow_outputs.setdefault(wf_id, {})

    def _update_run_btn(self) -> None:
        self.run_btn.setText(tr("co4e.interrupt") if self._cur_run_id() is not None
                             else tr("co4e.run"))

    # ---- sidebar ----------------------------------------------------------
    def _build_sidebar(self) -> QWidget:
        self.sidebar = QTabWidget()
        # Icon-only tabs share the full sidebar width equally (line up with the
        # list below) via an equal-width tab bar — no left/right scroll. Colours
        # come from theme.py (QTabBar#co4eSideTabs — transparent tabs + a subtle
        # translucent selection with the theme text colour, like the app's lists).
        self.sidebar.setTabBar(_EqualTabBar())
        _tb = self.sidebar.tabBar()
        _tb.setObjectName("co4eSideTabs")
        _tb.setUsesScrollButtons(False)
        _tb.setElideMode(Qt.ElideNone)
        # Workflows
        wf_page = QWidget(); wl = QVBoxLayout(wf_page)
        wl.setContentsMargins(6, 6, 6, 6)
        # Draggable: drag a flow onto the canvas to merge it in (Nova-style);
        # double-click loads it into an empty canvas. (The old always-on hint
        # label was removed to give the flow list more room; it's a tooltip now.)
        self.wf_list = _PaletteList(payload_role=Qt.UserRole + 2)
        self.wf_list.setToolTip(tr("co4e.drag_hint"))
        self.wf_list.itemDoubleClicked.connect(self._load_selected_workflow)
        self.wf_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.wf_list.customContextMenuRequested.connect(self._wf_context_menu)
        wl.addWidget(self.wf_list, 1)
        wf_btns = QHBoxLayout(); wf_btns.setSpacing(4)
        # "New flow" moved to the "+" button on the flow tab strip (browser-style).
        # "Load selected flow to canvas" button removed — double-click a flow in
        # the list (or drag it onto the canvas) to open it; the explicit button
        # was redundant.
        self.wf_edit_btn = self._icon_btn("edit", "co4e.tt_edit_wf", self._edit_selected_workflow)
        self.wf_dup_btn = self._icon_btn("branch", "co4e.tt_dup_wf", self._duplicate_selected_workflow)
        self.wf_del_btn = self._icon_btn("trash", "co4e.tt_del_wf", self._delete_selected_workflow)
        for b in (self.wf_edit_btn, self.wf_dup_btn, self.wf_del_btn):
            wf_btns.addWidget(b)
        self.wf_runbg_btn = QPushButton(tr("co4e.run_bg")); self.wf_runbg_btn.setIcon(icon("play"))
        self.wf_runbg_btn.setToolTip(tr("co4e.tt_run_bg"))
        self.wf_runbg_btn.clicked.connect(self._run_selected_in_background)
        wf_btns.addWidget(self.wf_runbg_btn, 1)
        wl.addLayout(wf_btns)
        # (The "Running flows" list moved out of the sidebar into the pinned
        #  "Runs" tab at the front of the flow tabs — see _build_runs_page.)
        # Icon-only tabs keep the sidebar narrow; the name is a tooltip.
        self.sidebar.addTab(wf_page, icon("flow"), "")
        self.sidebar.setTabToolTip(0, tr("co4e.tab_workflows"))

        # Agents (drag onto canvas; CRUD custom)
        ag_page = QWidget(); al = QVBoxLayout(ag_page)
        al.setContentsMargins(6, 6, 6, 6)
        self.agent_list = _PaletteList()
        al.addWidget(self.agent_list, 1)
        ag_btns = QHBoxLayout(); ag_btns.setSpacing(4)
        self.ag_new_btn = QPushButton(tr("co4e.new")); self.ag_new_btn.setIcon(icon("plus"))
        self.ag_new_btn.setToolTip(tr("co4e.tt_new_agent"))
        self.ag_new_btn.clicked.connect(self._new_agent)
        self.ag_edit_btn = self._icon_btn("edit", "co4e.tt_edit_agent", self._edit_agent)
        self.ag_del_btn = self._icon_btn("trash", "co4e.tt_del_agent", self._delete_agent)
        ag_btns.addWidget(self.ag_new_btn, 1)
        ag_btns.addWidget(self.ag_edit_btn)
        ag_btns.addWidget(self.ag_del_btn)
        al.addLayout(ag_btns)
        self.sidebar.addTab(ag_page, icon("robot"), "")
        self.sidebar.setTabToolTip(1, tr("co4e.tab_agents"))

        # Skills (drag onto canvas; manage via existing Skills manager button)
        sk_page = QWidget(); sl = QVBoxLayout(sk_page)
        sl.setContentsMargins(6, 6, 6, 6)
        self.skill_list = _PaletteList()
        sl.addWidget(self.skill_list, 1)
        self.sk_manage_btn = QPushButton(tr("co4e.manage_skills"))
        self.sk_manage_btn.setToolTip(tr("co4e.tt_manage_skills"))
        self.sk_manage_btn.clicked.connect(self._manage_skills)
        sl.addWidget(self.sk_manage_btn)
        self.sidebar.addTab(sk_page, icon("sparkle"), "")
        self.sidebar.setTabToolTip(2, tr("co4e.tab_skills"))
        return self.sidebar

    def _icon_btn(self, icon_name: str, tip_key: str, slot) -> QPushButton:
        b = QPushButton(); b.setIcon(icon(icon_name)); b.setToolTip(tr(tip_key))
        b.setFixedWidth(34)
        b.clicked.connect(slot)
        return b

    def _reload_sidebar(self) -> None:
        self.wf_list.clear()
        for wf in co4e.list_workflows():
            tag = tr("co4e.template") if wf.is_template else tr("co4e.saved")
            it = QListWidgetItem(icon("workspaces"), f"{wf.name}  ·  {tag}")
            it.setData(Qt.UserRole, ("saved", wf.id))
            it.setData(Qt.UserRole + 2, {"kind": "workflow", "workflow": co4e.workflow_to_dict(wf)})
            self.wf_list.addItem(it)
        # Agents: only the Parallel fan-out node + the user's own custom agents
        # (create your own with "+ New agent"; drag onto the canvas). The blank
        # "New Step" palette entry was removed — use the toolbar "+ Add" instead.
        self.agent_list.clear()
        self.agent_list.addItem(self._palette_item(
            tr("co4e.parallel_node"), "server",
            {"variant": "parallel", "label": "Parallel", "role": "PARALLEL", "icon": "server",
             "sub_agents": []}))
        for ca in co4e.list_custom_agents():
            step = co4e.Step(label=ca.name, agent_slug=co4e.slugify(ca.name), role=ca.role or "AGENT",
                             icon=ca.icon, instructions=ca.instructions,
                             context=getattr(ca, "context", ""), model=ca.model,
                             permission_preset=ca.permission_preset, skills=list(ca.skills),
                             attachments=list(getattr(ca, "attachments", []) or []))
            it = self._palette_item(f"{ca.name}  ·  {ca.role} ·  {tr('co4e.custom')}", ca.icon or "robot",
                                    co4e._step_dict(step))
            it.setData(Qt.UserRole + 1, ca.id)
            self.agent_list.addItem(it)
        # Skills
        self.skill_list.clear()
        for name in _skill_names():
            content = skills_mod.skill_prefix_for(name)
            payload = co4e._step_dict(co4e.Step(
                label=name, agent_slug=co4e.slugify(name), role="SKILL", icon="sparkle",
                instructions=content, skills=[name]))
            self.skill_list.addItem(self._palette_item(name, "sparkle", payload))

    @staticmethod
    def _palette_item(text: str, icon_name: str, payload: dict) -> QListWidgetItem:
        it = QListWidgetItem(icon(icon_name), text)
        it.setData(Qt.UserRole, payload)
        return it

    # ---- center -----------------------------------------------------------
    def _build_center(self) -> QWidget:
        from PySide6.QtWidgets import QStackedWidget, QTabBar
        page = QWidget()
        lay = QVBoxLayout(page)

        # Flow tab bar: a pinned "Runs" tab first (manage every flow run), then a
        # browser-style tab per open flow — each keeps its own graph (no mixing).
        self.flow_bar = QTabBar()
        self.flow_bar.setObjectName("flowTabs")
        self.flow_bar.setTabsClosable(True)
        self.flow_bar.setMovable(True)
        self.flow_bar.setExpanding(False)
        self.flow_bar.setDrawBase(False)
        # No arrow scroll buttons — when the tabs overflow they scroll inside a
        # frameless horizontal scroller you drag left/right (see flow_row below).
        self.flow_bar.setUsesScrollButtons(False)
        # Tab colours + layout live in theme.py (QTabBar#flowTabs — theme-aware,
        # flush, centred). Here we only style the per-tab close (✕) button, which
        # QTabBar places centred on the tab's right (see _add_tab_close_button).
        self.flow_bar.setStyleSheet(
            "QPushButton#flowTabClose {"
            " border: none; background: transparent; color: #8FB2D4;"
            " font-size: 13px; font-weight: bold; padding: 0; margin: 0;"
            " border-radius: 8px; }"
            "QPushButton#flowTabClose:hover {"
            " background: rgba(229,72,77,0.18); color: #E5484D; }")
        runs_idx = self.flow_bar.addTab(icon("monitoring"), tr("co4e.runs_tab"))  # 0 = Runs
        self.flow_bar.setTabButton(runs_idx, QTabBar.RightSide, None)             # pinned
        self.flow_bar.currentChanged.connect(self._on_flow_tab_changed)
        self.flow_bar.tabCloseRequested.connect(self._close_flow_tab)
        # "+" new-flow button styled as the last tab in the strip (browser-style)
        # — the ＋ glyph sits inside a tab-shaped button flush with the tabs.
        self.flow_add_btn = QPushButton("＋")
        self.flow_add_btn.setObjectName("flowAddBtn")
        self.flow_add_btn.setFixedWidth(34)
        self.flow_add_btn.setToolTip(tr("co4e.tt_new_wf"))
        self.flow_add_btn.clicked.connect(self._new_workflow)
        # Frameless horizontal scroller around the tab strip: overflowing tabs
        # scroll (drag) left/right instead of being boxed with arrow buttons.
        # The tab bar AND the "+" button are pinned to the SAME fixed height —
        # giving the scroll area extra height for its scrollbar (as a previous
        # version did) left the tabs top-anchored inside a taller box while the
        # "+" button centered across that whole (taller) box, so the two drifted
        # out of alignment. Same height on both = always aligned, no centering
        # math needed; the scrollbar only appears on overflow (rare) and briefly
        # overlaps the tab strip's bottom edge in that case.
        _tab_h = self.flow_bar.sizeHint().height()
        self.flow_bar.setFixedHeight(_tab_h)
        self.flow_add_btn.setFixedHeight(_tab_h)
        self.flow_scroll = QScrollArea()
        self.flow_scroll.setObjectName("flowTabScroll")
        self.flow_scroll.setWidget(self.flow_bar)
        self.flow_scroll.setWidgetResizable(True)
        self.flow_scroll.setFrameShape(QScrollArea.NoFrame)     # no outer frame
        self.flow_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.flow_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.flow_scroll.setFixedHeight(_tab_h)
        self.flow_scroll.setStyleSheet(
            "QScrollArea#flowTabScroll { background: transparent; border: none; }"
            "QScrollArea#flowTabScroll QScrollBar:horizontal { height: 8px; background: transparent; margin: 0; }"
            "QScrollArea#flowTabScroll QScrollBar::handle:horizontal {"
            " background: rgba(143,178,212,0.45); border-radius: 4px; min-width: 30px; }"
            "QScrollArea#flowTabScroll QScrollBar::add-line:horizontal,"
            "QScrollArea#flowTabScroll QScrollBar::sub-line:horizontal { width: 0; height: 0; }")
        flow_row = QHBoxLayout()
        flow_row.setContentsMargins(0, 0, 0, 0)
        flow_row.setSpacing(3)          # same gap as between tabs → looks like one strip
        flow_row.addWidget(self.flow_scroll, 1)
        flow_row.addWidget(self.flow_add_btn, 0, Qt.AlignVCenter)
        lay.addLayout(flow_row)

        # Content switches between the Runs table (tab 0) and the flow editor.
        self.center_stack = QStackedWidget()
        lay.addWidget(self.center_stack, 1)
        self.center_stack.addWidget(self._build_runs_page())          # stack 0 = Runs

        flow_page = QWidget()
        lay = QVBoxLayout(flow_page)
        lay.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout(); bar.setSpacing(5)
        self.name_edit = QLineEdit(self._wf.name)
        self.name_edit.setToolTip(tr("co4e.tt_flow_name"))
        self.name_edit.textChanged.connect(self._on_name_changed)
        # "Add" is a labelled button (not a "+" icon) so it isn't mistaken for
        # the zoom-in control, which now lives in the canvas's bottom-left overlay.
        self.add_step_btn = QPushButton(tr("co4e.add")); self.add_step_btn.setIcon(icon("plus"))
        self.add_step_btn.setToolTip(tr("co4e.tt_add_step"))
        self.add_step_btn.clicked.connect(self._add_blank_step)
        self.save_btn = QPushButton(tr("co4e.save")); self.save_btn.setIcon(icon("save"))
        self.save_btn.setObjectName("primary")
        self.save_btn.setToolTip(tr("co4e.tt_save"))
        self.save_btn.clicked.connect(lambda: self._save(as_template=False))
        self.save_tpl_btn = self._icon_btn("star", "co4e.tt_save_template",
                                           lambda: self._save(as_template=True))
        self.mode_combo = QComboBox()
        self.mode_combo.setToolTip(tr("co4e.tt_mode"))
        for m in co4e.RUN_MODES:
            self.mode_combo.addItem(tr(f"co4e.mode.{m}"), m)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.run_btn = QPushButton(tr("co4e.run")); self.run_btn.setIcon(icon("play"))
        self.run_btn.setObjectName("primary")
        self.run_btn.setToolTip(tr("co4e.tt_run"))
        self.run_btn.clicked.connect(self._on_run_clicked)

        bar.addWidget(QLabel(tr("co4e.flow_name")))
        bar.addWidget(self.name_edit, 1)
        bar.addWidget(self.add_step_btn)
        bar.addWidget(self.save_btn)
        bar.addWidget(self.save_tpl_btn)
        bar.addWidget(self.mode_combo)
        bar.addWidget(self.run_btn)
        lay.addLayout(bar)

        self.canvas = Co4ECanvas()
        self._build_canvas_overlay()
        vsplit = QSplitter(Qt.Vertical)
        vsplit.addWidget(self.canvas)
        chat_widget = self._build_chat()          # default-collapsed (see _build_chat)
        vsplit.addWidget(chat_widget)
        vsplit.setStretchFactor(0, 1)
        self._vsplit = vsplit           # so the message panel can collapse/expand
        # Messages start collapsed — give the canvas the room from the start,
        # not the [540, 220] split that assumed an expanded chat box.
        collapsed_h = chat_widget.maximumHeight()
        vsplit.setSizes([max(0, 760 - collapsed_h), collapsed_h])
        lay.addWidget(vsplit, 1)
        self.center_stack.addWidget(flow_page)                       # stack 1 = flow editor
        self.center_stack.setCurrentIndex(1)
        return page

    def _build_runs_page(self) -> QWidget:
        """The pinned 'Runs' tab: a table of every flow run (name · status · steps
        done/total · creator · created) for tracking. Double-click a run to open
        that flow's tab with its live status."""
        w = QWidget()
        v = QVBoxLayout(w)
        hdr = QHBoxLayout()
        self.runs_title = QLabel(tr("co4e.running_flows"))
        self.runs_title.setObjectName("hint")
        hdr.addWidget(self.runs_title)
        # Show + open the workspace folder where flow outputs land (below the tab,
        # next to the title) so the files a flow produced are easy to find.
        self.ws_folder_btn = QPushButton()
        self.ws_folder_btn.setIcon(icon("folder"))
        self.ws_folder_btn.setFlat(True)
        self.ws_folder_btn.setCursor(Qt.PointingHandCursor)
        self.ws_folder_btn.clicked.connect(self._open_workspace_folder)
        self._refresh_ws_folder_btn()
        hdr.addWidget(self.ws_folder_btn)
        hdr.addStretch(1)
        self.run_stop_btn = QPushButton(tr("co4e.stop"))
        self.run_stop_btn.setIcon(icon("stop"))
        self.run_stop_btn.setObjectName("danger")
        self.run_stop_btn.setToolTip(tr("co4e.tt_stop_run"))
        self.run_stop_btn.clicked.connect(self._stop_selected_run)
        self.run_rename_btn = QPushButton(tr("co4e.rename_run"))
        self.run_rename_btn.setIcon(icon("edit"))
        self.run_rename_btn.setToolTip(tr("co4e.tt_rename_run"))
        self.run_rename_btn.clicked.connect(self._rename_selected_run)
        self.run_del_btn = QPushButton(tr("co4e.delete_run"))
        self.run_del_btn.setIcon(icon("trash"))
        self.run_del_btn.setToolTip(tr("co4e.tt_delete_run"))
        self.run_del_btn.clicked.connect(self._delete_selected_run)
        self.run_clear_btn = QPushButton(tr("co4e.clear_done"))
        self.run_clear_btn.setToolTip(tr("co4e.tt_clear_runs"))
        self.run_clear_btn.clicked.connect(lambda: self.manager.clear_finished())
        hdr.addWidget(self.run_stop_btn)
        hdr.addWidget(self.run_rename_btn)
        hdr.addWidget(self.run_del_btn)
        hdr.addWidget(self.run_clear_btn)
        v.addLayout(hdr)
        self.runs_table = QTableWidget(0, 5)
        self.runs_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.runs_table.verticalHeader().setVisible(False)
        self.runs_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.runs_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.runs_table.setToolTip(tr("co4e.tt_runs_list"))
        self.runs_table.itemDoubleClicked.connect(self._open_run_from_table)
        # Right-click a run → Open / Delete (delete a single old run from history).
        self.runs_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.runs_table.customContextMenuRequested.connect(self._runs_context_menu)
        v.addWidget(self.runs_table, 1)
        return w

    def _wrap_config(self) -> QWidget:
        """Wrap the step-config panel with a header that has an expand/collapse
        toggle, so it can be folded away to give the canvas more room."""
        container = QWidget()
        container.setObjectName("configContainer")
        v = QVBoxLayout(container)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        header = QWidget()
        hb = QHBoxLayout(header)
        hb.setContentsMargins(4, 3, 4, 3)
        hb.setSpacing(4)
        self.config_toggle_btn = QPushButton()
        self.config_toggle_btn.setIcon(icon("chevron-right"))
        self.config_toggle_btn.setToolTip(tr("co4e.tt_collapse_config"))
        self.config_toggle_btn.setFixedSize(26, 24)
        self.config_toggle_btn.clicked.connect(self._toggle_config)
        self.config_title = QLabel(tr("co4e.config_title"))
        self.config_title.setObjectName("hint")
        hb.addWidget(self.config_toggle_btn)
        hb.addWidget(self.config_title, 1)
        v.addWidget(header)
        v.addWidget(self.config, 1)
        self._cfg_vlayout = v
        # Spacers used ONLY while collapsed, to keep the lone toggle icon
        # vertically CENTERED in the thin strip (its position no longer jumps to
        # the top after collapsing).
        self._cfg_top_spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self._cfg_bot_spacer = QSpacerItem(0, 0, QSizePolicy.Minimum, QSizePolicy.Expanding)
        self.config_container = container
        return container

    def _toggle_config(self) -> None:
        self._config_collapsed = not self._config_collapsed
        v = self._cfg_vlayout
        if self._config_collapsed:
            w = self.config_container.width()
            if w > 60:
                self._config_expanded_w = w
            self.config.hide()
            self.config_title.hide()
            self.config_container.setMaximumWidth(34)
            self.config_toggle_btn.setIcon(icon("chevron-left"))
            self.config_toggle_btn.setToolTip(tr("co4e.tt_expand_config"))
            # center the toggle vertically in the collapsed strip
            v.insertItem(0, self._cfg_top_spacer)
            v.addItem(self._cfg_bot_spacer)
            # A maximumWidth alone doesn't make the splitter hand the freed width
            # to the canvas — set sizes explicitly so the panel folds to the right.
            sizes = self._split.sizes()
            if len(sizes) == 3:
                freed = sizes[2] - 34
                sizes[2] = 34
                sizes[1] = max(200, sizes[1] + freed)
                self._split.setSizes(sizes)
        else:
            v.removeItem(self._cfg_top_spacer)
            v.removeItem(self._cfg_bot_spacer)
            self.config_container.setMaximumWidth(16777215)
            self.config.show()
            self.config_title.show()
            self.config_toggle_btn.setIcon(icon("chevron-right"))
            self.config_toggle_btn.setToolTip(tr("co4e.tt_collapse_config"))
            sizes = self._split.sizes()
            if len(sizes) == 3:
                want = self._config_expanded_w
                delta = want - sizes[2]
                sizes[2] = want
                sizes[1] = max(200, sizes[1] - delta)
                self._split.setSizes(sizes)

    def _build_canvas_overlay(self) -> None:
        """Zoom +/− and Fit as a small floating control at the canvas's
        bottom-left, stacked vertically. The frame is transparent (so it follows
        the dark/light theme — only the buttons carry a themed background) and the
        buttons are half-size."""
        from PySide6.QtCore import QSize

        bar = QFrame()
        bar.setObjectName("canvasOverlay")
        bar.setStyleSheet("QFrame#canvasOverlay { background: transparent; border: none; }")
        v = QVBoxLayout(bar)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(3)
        self.zoom_in_btn = self._icon_btn("plus", "co4e.tt_zoom_in", lambda: self.canvas.zoom_in())
        self.zoom_out_btn = self._icon_btn("minus", "co4e.tt_zoom_out", lambda: self.canvas.zoom_out())
        self.fit_btn = self._icon_btn("search", "co4e.fit_tooltip", lambda: self.canvas.fit_view())
        for b in (self.zoom_in_btn, self.zoom_out_btn, self.fit_btn):
            b.setFixedSize(16, 16)          # ~half the previous size
            b.setIconSize(QSize(11, 11))
            b.setStyleSheet("QPushButton { padding: 0px; }")   # keep themed bg, drop padding
            v.addWidget(b)
        self.canvas.add_overlay(bar)

    def _build_chat(self) -> QWidget:
        w = QWidget()
        self._chat_widget = w
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # "Messages" header at the TOP, above the chat box. Toggling it shows or
        # hides the WHOLE chat box (message list + composer) below it.
        self._mhdr = QWidget(); self._mhdr.setObjectName("msgHeader")
        mh = QHBoxLayout(self._mhdr); mh.setContentsMargins(6, 3, 6, 3); mh.setSpacing(6)
        self.msgs_icon = QLabel(); self.msgs_icon.setPixmap(icon("message").pixmap(14, 14))
        self.msgs_title = QLabel(tr("co4e.messages")); self.msgs_title.setObjectName("hint")
        self.chat_toggle_btn = QPushButton()
        self.chat_toggle_btn.setObjectName("msgToggle")
        self.chat_toggle_btn.setFlat(True)
        self.chat_toggle_btn.setIcon(icon("chevron-up"))    # collapsed → points up (click to expand)
        self.chat_toggle_btn.setFixedSize(22, 22)
        self.chat_toggle_btn.clicked.connect(self._toggle_messages)
        mh.addWidget(self.msgs_icon)
        mh.addWidget(self.msgs_title)
        mh.addStretch(1)
        mh.addWidget(self.chat_toggle_btn)
        lay.addWidget(self._mhdr)               # header on top
        # Point-conversation (message bubbles) like Cowork, not a flat textbox.
        # ONE ChatView PER FLOW (keyed by workflow id) inside a stack, so each flow
        # tab has its OWN separate conversation and they never bleed into each other.
        from PySide6.QtWidgets import QStackedWidget
        self.chat_stack = QStackedWidget()
        self._flow_logs: Dict[str, ChatView] = {}
        lay.addWidget(self.chat_stack, 1)
        self.chat_input_row = QWidget()
        crow = QVBoxLayout(self.chat_input_row)
        crow.setContentsMargins(0, 4, 0, 0); crow.setSpacing(3)
        # Running conversation token/cost TOTAL for this flow (↓in ↑out ▤ctx
        # $cost) at the bottom, exactly like Cowork's conversation total.
        self._usage_total_lbl = QLabel("")
        self._usage_total_lbl.setObjectName("hint")
        self._usage_total_lbl.setStyleSheet("color: rgba(140,146,152,0.9); font-size: 11px;")
        crow.addWidget(self._usage_total_lbl)
        _inp = QWidget(); row = QHBoxLayout(_inp); row.setContentsMargins(0, 0, 0, 0)
        self.chat_input = _ChatInput()
        self.chat_input.setPlaceholderText(tr("co4e.chat_placeholder"))
        self.chat_input.submit.connect(self._chat_send)
        self.chat_send_btn = QPushButton(tr("co4e.send")); self.chat_send_btn.setIcon(icon("send"))
        self.chat_send_btn.clicked.connect(self._chat_send)
        row.addWidget(self.chat_input, 1)
        # Off/Auto/Manual routing toggle for Co4E (surface key "co4e").
        from .routing_toggle import RoutingToggle
        self.co4e_routing_toggle = RoutingToggle(self.ctx, "co4e")
        self._co4e_routed_provider = None   # routing provider override for the next turn
        row.addWidget(self.co4e_routing_toggle)
        row.addWidget(self.chat_send_btn)
        crow.addWidget(_inp)
        lay.addWidget(self.chat_input_row)
        # Default = COLLAPSED: only the "Messages" header shows; the chat box is
        # hidden and the canvas gets the room until the user expands it.
        self._vsplit_sizes = [540, 220]         # sizes to restore when expanded
        self._msgs_collapsed = True
        self.chat_stack.hide()
        self.chat_input_row.hide()
        self.chat_toggle_btn.setToolTip(tr("co4e.tt_expand_msgs"))
        w.setMaximumHeight(self._mhdr.sizeHint().height() + 6)
        return w

    def _toggle_messages(self) -> None:
        """Show/hide the WHOLE chat box (message list + composer) below the
        header. Collapsing hands the freed height to the canvas.

        A QSplitter's ``setMaximumHeight`` on one side does NOT automatically
        redistribute the freed space to the other side — it just shrinks the
        splitter's own total height, leaving the canvas frozen at its old size
        and blank space below it. So this explicitly calls ``setSizes`` on both
        the collapse AND the expand path, computed from the splitter's CURRENT
        total (not a hardcoded guess) — that total stays constant; only how
        it's split between canvas/chat changes."""
        self._msgs_collapsed = not self._msgs_collapsed
        collapsed_h = self._mhdr.sizeHint().height() + 6
        if self._msgs_collapsed:
            if hasattr(self, "_vsplit"):
                self._vsplit_sizes = self._vsplit.sizes()   # remember to restore
            self.chat_stack.hide()
            self.chat_input_row.hide()
            self._chat_widget.setMaximumHeight(collapsed_h)
            self.chat_toggle_btn.setIcon(icon("chevron-up"))     # collapsed → click to expand
            self.chat_toggle_btn.setToolTip(tr("co4e.tt_expand_msgs"))
            if hasattr(self, "_vsplit"):
                total = sum(self._vsplit.sizes()) or (self._vsplit_sizes and sum(self._vsplit_sizes)) or 760
                self._vsplit.setSizes([max(0, total - collapsed_h), collapsed_h])
        else:
            self._chat_widget.setMaximumHeight(16777215)
            self.chat_stack.show()
            self.chat_input_row.show()
            self.chat_toggle_btn.setIcon(icon("chevron-down"))   # expanded → click to collapse
            self.chat_toggle_btn.setToolTip(tr("co4e.tt_collapse_msgs"))
            if getattr(self, "_vsplit_sizes", None) and hasattr(self, "_vsplit"):
                self._vsplit.setSizes(self._vsplit_sizes)
        return

    # ---- per-flow chat logs (each flow = its own conversation) ------------
    def _ensure_flow_log(self, wf_id: str) -> ChatView:
        """The ChatView for a flow, created + added to the stack on first use so
        each flow tab keeps a SEPARATE conversation."""
        log = self._flow_logs.get(wf_id)
        if log is None:
            log = ChatView()
            log._co4e_plan_bubble = None       # per-flow 'current plan' bubble
            self._flow_logs[wf_id] = log
            self.chat_stack.addWidget(log)
        return log

    def _active_log(self) -> ChatView:
        wf = getattr(self, "_wf", None)
        return self._ensure_flow_log(wf.id if wf is not None else "__none__")

    @property
    def chat_log(self) -> ChatView:
        """The conversation of the CURRENTLY-shown flow (all append/stream calls
        go here). Assignment is not supported — logs are per-flow now."""
        return self._active_log()

    @property
    def _plan_bubble(self):
        return getattr(self._active_log(), "_co4e_plan_bubble", None)

    @_plan_bubble.setter
    def _plan_bubble(self, value) -> None:
        self._active_log()._co4e_plan_bubble = value

    # ---- workflow load/save ----------------------------------------------
    def _apply_workflow(self, wf: co4e.Workflow) -> None:
        self._wf = wf
        # Per-flow outputs are kept in self._flow_outputs[wf.id] — do NOT clear
        # here (switching tabs must not wipe another flow's accumulated context).
        # Switch the visible conversation to THIS flow's own log.
        self.chat_stack.setCurrentWidget(self._ensure_flow_log(wf.id))
        self.name_edit.setText(wf.name)
        self.canvas.load(wf.nodes, wf.edges)
        self.config.clear_step()
        if wf.nodes:
            self.canvas.relayout_if_vertical()   # convert old top-down flows to left→right
            self.canvas.fit_view()
        self._update_run_btn()                   # reflect THIS flow's run state
        self._refresh_usage_total()              # show THIS flow's token/cost total

    def _new_workflow(self) -> None:
        self._open_flow(co4e.new_workflow(tr("co4e.untitled")))   # opens a new tab

    def _selected_wf(self) -> Optional[co4e.Workflow]:
        """Materialise the selected saved-flow row into a Workflow."""
        item = self.wf_list.currentItem()
        if item is None:
            return None
        _kind, ident = item.data(Qt.UserRole)
        return co4e.get_workflow(ident)

    def _load_selected_workflow(self, *_a) -> None:
        wf = self._selected_wf()
        if wf is not None:
            self._open_flow(wf)          # open (or focus) its browser-style tab

    def _edit_selected_workflow(self) -> None:
        wf = self._selected_wf()
        if wf is None:
            self.status_message.emit(tr("co4e.select_flow"))
            return
        self._open_flow(wf)

    def _duplicate_selected_workflow(self) -> None:
        wf = self._selected_wf()
        if wf is None:
            self.status_message.emit(tr("co4e.select_flow"))
            return
        dup = co4e.duplicate_workflow(wf)
        self._reload_sidebar()
        self.status_message.emit(tr("co4e.duplicated_msg", name=dup.name))

    def _wf_context_menu(self, pos) -> None:
        lw = self.wf_list
        item = lw.itemAt(pos)
        if item is None:
            return
        lw.setCurrentItem(item)
        _kind, ident = item.data(Qt.UserRole)
        menu = QMenu(lw)
        act_edit = menu.addAction(icon("edit"), tr("co4e.edit"))
        act_rename = menu.addAction(icon("edit"), tr("co4e.rename"))
        act_dup = menu.addAction(icon("branch"), tr("co4e.duplicate"))
        act_run = menu.addAction(icon("play"), tr("co4e.run_bg"))
        act_del = menu.addAction(icon("trash"), tr("co4e.delete"))
        chosen = menu.exec(lw.viewport().mapToGlobal(pos))
        if chosen is act_edit:
            self._edit_selected_workflow()
        elif chosen is act_rename:
            self._rename_workflow(ident)
        elif chosen is act_dup:
            self._duplicate_selected_workflow()
        elif chosen is act_run:
            self._run_selected_in_background()
        elif chosen is act_del:
            self._delete_selected_workflow()

    def _rename_workflow(self, ident: str) -> None:
        """Rename a saved flow in place (e.g. to match its function/task)."""
        wf = co4e.get_workflow(ident)
        if wf is None:
            return
        name, ok = QInputDialog.getText(self, tr("co4e.rename"), tr("co4e.rename_prompt"),
                                        text=wf.name)
        name = (name or "").strip()
        if not ok or not name:
            return
        wf.name = name
        co4e.save_workflow(wf)
        if self._wf.id == ident:
            self.name_edit.setText(name)
            self._wf.name = name
        self._reload_sidebar()
        self.status_message.emit(tr("co4e.renamed_msg", name=name))

    def _delete_selected_workflow(self) -> None:
        item = self.wf_list.currentItem()
        if item is None:
            return
        _kind, ident = item.data(Qt.UserRole)
        co4e.delete_workflow(ident)
        self._reload_sidebar()

    def _sync_wf_from_canvas(self) -> None:
        self._wf.nodes = self.canvas.nodes()
        self._wf.edges = self.canvas.edges()
        self._wf.name = self.name_edit.text().strip() or tr("co4e.untitled")

    def _save(self, as_template: bool) -> None:
        self._sync_wf_from_canvas()
        self._wf.is_template = as_template
        co4e.save_workflow(self._wf)
        self._reload_sidebar()
        self.status_message.emit(tr("co4e.saved_msg", name=self._wf.name))

    def _autosave(self) -> None:
        if co4e.get_workflow(self._wf.id) is not None:
            self._sync_wf_from_canvas()
            co4e.save_workflow(self._wf)

    def _on_name_changed(self, text: str) -> None:
        self._wf.name = text.strip() or tr("co4e.untitled")
        self._sync_active_flow_tab_text()

    def _add_blank_step(self) -> None:
        self.canvas.add_palette_step(co4e.Step(label="New Step"),
                                     self.canvas.mapToScene(self.canvas.rect().center()))

    # ---- node selection / config -----------------------------------------
    def _on_node_selected(self, node_id: str) -> None:
        for n in self.canvas.nodes():
            if n.id == node_id:
                self.config.load_step(node_id, n.data, _skill_names())
                if self._config_collapsed:
                    self._toggle_config()
                return

    def _on_config_changed(self) -> None:
        for n in self.canvas.nodes():
            self.canvas.refresh_node(n.id)
        self._autosave()

    # ---- custom agents ----------------------------------------------------
    def _new_agent(self) -> None:
        self._edit_agent_dialog(co4e.new_custom_agent(""))

    def _edit_agent(self) -> None:
        item = self.agent_list.currentItem()
        cid = item.data(Qt.UserRole + 1) if item else None
        if not cid:
            self.status_message.emit(tr("co4e.select_custom_agent"))
            return
        agent = next((a for a in co4e.list_custom_agents() if a.id == cid), None)
        if agent is not None:
            self._edit_agent_dialog(agent)

    def _edit_agent_dialog(self, agent: co4e.CustomAgent) -> None:
        from .co4e_agent_dialog import Co4EAgentDialog

        dlg = Co4EAgentDialog(self.ctx, agent, _skill_names(), self)
        if dlg.exec():
            co4e.save_custom_agent(dlg.result_agent())
            self._reload_sidebar()

    def _delete_agent(self) -> None:
        item = self.agent_list.currentItem()
        cid = item.data(Qt.UserRole + 1) if item else None
        if not cid:
            self.status_message.emit(tr("co4e.select_custom_agent"))
            return
        co4e.delete_custom_agent(cid)
        self._reload_sidebar()

    def _manage_skills(self) -> None:
        from .skills_dialog import SkillsDialog

        SkillsDialog(self, self.ctx).exec()
        self._reload_sidebar()

    # ---- running ----------------------------------------------------------
    def _skill_map(self) -> Dict[str, str]:
        out = {}
        for name in _skill_names():
            block = skills_mod.skill_prefix_for(name)
            if block:
                out[name] = block.split("\n", 1)[1] if "\n" in block else block
        return out

    def _current_mode(self) -> str:
        return self.mode_combo.currentData() or "auto"

    def _on_mode_changed(self, *_a) -> None:
        # switching mode resets any in-progress manual sequence
        self._manual_active = False
        self._manual_order = []
        self._manual_idx = 0
        if self._cur_run_id() is None:
            self.run_btn.setText(tr("co4e.run"))

    def _on_run_clicked(self) -> None:
        # THIS flow's run is active → interrupt it (other flows keep running).
        cur = self._cur_run_id()
        if cur is not None:
            self.manager.stop(cur)
            return
        mode = self._current_mode()
        if mode == "manual":
            self._manual_run_or_advance()
        else:
            self._start_canvas_run(plan_mode=(mode == "plan"))

    def _start_canvas_run(self, *, plan_mode: bool, only: Optional[set] = None,
                          seed: Optional[Dict[str, str]] = None) -> None:
        self._sync_wf_from_canvas()
        if not self._wf.nodes:
            self.status_message.emit(tr("co4e.no_steps"))
            return
        wf_id = self._wf.id
        if only is None:
            self.canvas.reset_statuses()
            self._outputs_for(wf_id).clear()
            self._plan_bubble = None
        self._append_chat("system", tr("co4e.run_started", name=self._wf.name))
        run_id = self.manager.start(
            self._wf, skill_map=self._skill_map(), plan_mode=plan_mode,
            only_nodes=only, seed_outputs=seed or dict(self._outputs_for(wf_id)))
        self._flow_runs[wf_id] = run_id          # track THIS flow's run (parallel-safe)
        self._run_logs[run_id] = self.chat_log   # route its events to THIS flow's log
        self.run_btn.setText(tr("co4e.interrupt"))

    def _run_single(self, node_id: str) -> None:
        """Run one step (config panel "Run this step") with upstream context."""
        if self._cur_run_id() is not None:
            return
        self._start_canvas_run(plan_mode=(self._current_mode() == "plan"),
                               only={node_id}, seed=dict(self._outputs_for(self._wf.id)))

    def _run_from(self, node_id: str) -> None:
        if self._cur_run_id() is not None:
            return
        self._start_canvas_run(plan_mode=(self._current_mode() == "plan"),
                               only=self._downstream(node_id), seed=dict(self._outputs_for(self._wf.id)))

    def _downstream(self, node_id: str) -> set:
        adj: Dict[str, List[str]] = {}
        for e in self.canvas.edges():
            adj.setdefault(e.source, []).append(e.target)
        seen, stack = set(), [node_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adj.get(cur, []))
        return seen

    # ---- manual mode (step-by-step) --------------------------------------
    def _manual_run_or_advance(self) -> None:
        if not self._manual_active:
            self._sync_wf_from_canvas()
            if not self._wf.nodes:
                self.status_message.emit(tr("co4e.no_steps"))
                return
            self.canvas.reset_statuses()
            self._outputs_for(self._wf.id).clear()
            self._plan_bubble = None
            self._manual_order = self._topo_order()
            self._manual_idx = 0
            self._manual_active = True
            self._append_chat("system", tr("co4e.manual_started", name=self._wf.name))
        self._manual_step()

    def _manual_step(self) -> None:
        if self._manual_idx >= len(self._manual_order):
            self._manual_active = False
            self.run_btn.setText(tr("co4e.run"))
            self._append_chat("system", tr("co4e.run_done"))
            return
        nid = self._manual_order[self._manual_idx]
        label = next((n.data.label for n in self.canvas.nodes() if n.id == nid), nid)
        self._append_chat("system", tr("co4e.manual_step",
                                        i=self._manual_idx + 1, n=len(self._manual_order), label=label))
        run_id = self.manager.start(
            self._wf, skill_map=self._skill_map(),
            plan_mode=False, only_nodes={nid}, seed_outputs=dict(self._outputs_for(self._wf.id)),
            manual=True)
        self._flow_runs[self._wf.id] = run_id
        self._run_logs[run_id] = self.chat_log
        self.run_btn.setText(tr("co4e.interrupt"))

    def _topo_order(self) -> List[str]:
        nodes = self.canvas.nodes()
        edges = self.canvas.edges()
        waves = co4e.compute_waves(nodes, edges)
        y = {n.id: n.y for n in nodes}
        return sorted((n.id for n in nodes), key=lambda nid: (waves.get(nid, 0), y.get(nid, 0)))

    # ---- run-manager events ----------------------------------------------
    def _on_manager_event(self, run_id: str, ev: dict) -> None:
        # Per-flow routing: every run's events go to ITS OWN flow log (so parallel
        # runs never mix), and the canvas mirrors ONLY the run whose flow is the
        # one currently shown. Flow Status refreshes on its own via `changed`.
        h = self.manager.get(run_id)
        run_wf = h.wf_id if h is not None else None
        log = self._run_logs.get(run_id) or self.chat_log
        shown = getattr(self, "_wf", None) is not None and run_wf == self._wf.id
        t = ev.get("type")
        if t == "node_status":
            if shown:
                self.canvas.update_node_status(ev.get("node_id"), ev.get("status"))
        elif t == "node_output":
            if run_wf is not None:
                self._outputs_for(run_wf)[ev["node_id"]] = ev.get("output", "")
            label = ev["node_id"]
            if shown:
                label = next((n.data.label for n in self.canvas.nodes() if n.id == ev["node_id"]),
                             ev["node_id"])
            elif h is not None and h.wf is not None:
                label = next((n.data.label for n in h.wf.nodes if n.id == ev["node_id"]), ev["node_id"])
            if ev.get("output"):
                bub = self._append_chat("assistant", f"**{label}**\n\n{ev['output']}", log=log)
                # Per-message token/cost footer (↓in ↑out ▤ctx $cost), like Cowork.
                self._apply_usage(bub, run_wf, ev.get("usage"))
        elif t == "node_diff":
            self._append_diff(ev.get("title", ""), ev.get("diff", ""), log=log)
        elif t == "node_plan":
            self._append_plan(ev.get("steps") or [], log=log)
        elif t == "node_tool":
            if not ev.get("ok", True):
                # A single failed tool call isn't a step failure — the agent is told
                # to recover and continue, so show it as a neutral notice (not a red
                # "Error" that reads like the whole flow crashed).
                self._append_chat("system", "⚠ " + tr("co4e.tool_failed", name=ev.get("name", "")), log=log)
        elif t in ("run_done", "run_error"):
            # Drop THIS flow's run tracking (other flows keep running in parallel).
            if run_wf is not None and self._flow_runs.get(run_wf) == run_id:
                self._flow_runs.pop(run_wf, None)
            self._run_logs.pop(run_id, None)
            if self._manual_active and shown:
                self._manual_idx += 1
                self._manual_step()
            else:
                if shown:
                    self.run_btn.setText(tr("co4e.run"))
                self._append_chat("system", tr("co4e.run_done"), log=log)
                # Clickable link to the output folder so files are one click away.
                out = (h.out_dir if h is not None and h.out_dir else "") or str(self._flow_output_root())
                try:
                    log.add_folder_link(out, tr("co4e.open_output_link"))
                    log.scroll_to_bottom()
                except Exception:  # noqa: BLE001 - link is a nicety, never fatal
                    pass
                self._notify_run_finished(run_id)      # popup: the flow finished
                if not shown and h is not None:
                    self.status_message.emit(tr("co4e.bg_done", name=h.name, status=h.status))

    def _notify_run_finished(self, run_id: str) -> None:
        """Show a non-blocking popup when a flow finishes (done / error / stopped),
        so the user is notified even if they're on another screen."""
        h = self.manager.get(run_id)
        if h is None:
            return
        from PySide6.QtWidgets import QMessageBox

        if not hasattr(self, "_run_popups"):
            self._run_popups = []
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning if h.status == "error" else QMessageBox.Information)
        box.setWindowTitle(tr("co4e.run_done_title"))
        box.setText(tr("co4e.run_done_popup", name=h.name,
                       status=tr("co4e.status." + h.status)))
        box.setStandardButtons(QMessageBox.Ok)
        box.setModal(False)                       # non-blocking notification
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.finished.connect(
            lambda _r=0, b=box: self._run_popups.remove(b) if b in self._run_popups else None)
        self._run_popups.append(box)              # keep a ref so it isn't GC'd
        box.show()

    def _refresh_runs(self) -> None:
        # Rebuild the always-fresh Runs table from the manager (single source of truth).
        if not hasattr(self, "runs_table"):
            return
        color = {"running": "#48CAE4", "done": "#48D9A0", "error": "#E5484D",
                 "stopped": "#8FB2D4"}
        dots = {"running": "▶", "done": "✓", "error": "✕", "stopped": "■"}
        # Most-recent run at the TOP, oldest at the bottom (manager keeps runs in
        # chronological insertion order, so reverse it for display).
        runs = list(reversed(self.manager.runs()))
        t = self.runs_table
        # Preserve the selected run across the rebuild by its id (row indices shift
        # as runs are added/deleted, so a row-index restore would jump).
        sel_item = t.item(t.currentRow(), 0) if t.currentRow() >= 0 else None
        sel_id = sel_item.data(Qt.UserRole) if sel_item is not None else None
        t.setRowCount(len(runs))
        sel_row = -1
        for r, h in enumerate(runs):
            vals = [f"{dots.get(h.status, '•')} {h.name}", tr("co4e.status." + h.status),
                    h.progress_text(), h.created_by or "-", h.created_at or "-"]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(str(val))
                if c == 0:
                    it.setData(Qt.UserRole, h.id)
                if c == 1:
                    it.setForeground(_qcolor(color.get(h.status, "#E0F0FF")))
                t.setItem(r, c, it)
            if h.id == sel_id:
                sel_row = r
        if sel_row >= 0:
            t.setCurrentCell(sel_row, 0)
        # reflect the active run count in the pinned Runs tab title
        if hasattr(self, "flow_bar"):
            n = self.manager.active_count()
            self.flow_bar.setTabText(0, tr("co4e.runs_tab_n", n=n) if n else tr("co4e.runs_tab"))

    def _stop_selected_run(self) -> None:
        row = self.runs_table.currentRow()
        it = self.runs_table.item(row, 0) if row >= 0 else None
        if it is None:
            self.manager.stop_all()
            return
        self.manager.stop(it.data(Qt.UserRole))

    def _delete_selected_run(self) -> None:
        """Delete the selected run from the Flow Status history (a running one is
        stopped first). Removes just that single entry."""
        row = self.runs_table.currentRow()
        it = self.runs_table.item(row, 0) if row >= 0 else None
        if it is None:
            self.status_message.emit(tr("co4e.select_run"))
            return
        run_id = it.data(Qt.UserRole)
        h = self.manager.get(run_id)             # stop tracking it per-flow if we were
        if h is not None and self._flow_runs.get(h.wf_id) == run_id:
            self._flow_runs.pop(h.wf_id, None)
        self._run_logs.pop(run_id, None)
        self.manager.remove(run_id)             # emits `changed` → _refresh_runs

    def _runs_context_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu
        item = self.runs_table.itemAt(pos)
        if item is None:
            return
        self.runs_table.selectRow(item.row())
        menu = QMenu(self)
        menu.addAction(tr("co4e.open_run"),
                       lambda: self._open_run_from_table(self.runs_table.item(item.row(), 0)))
        it0 = self.runs_table.item(item.row(), 0)
        rid = it0.data(Qt.UserRole) if it0 is not None else None
        menu.addAction(tr("co4e.open_output"), lambda: self._open_run_output_folder(rid))
        menu.addAction(tr("co4e.rename_run"), self._rename_selected_run)
        menu.addAction(tr("co4e.delete_run"), self._delete_selected_run)
        menu.exec(self.runs_table.viewport().mapToGlobal(pos))

    # ---- workspace binding + output folder (where flow files land) --------
    def set_project(self, project_id: str) -> None:
        """Bind Co4E to the SELECTED Workspace project so flow output (and per-run
        folders) land in THAT project's workspace — mirroring how Cowork writes to
        the project folder — instead of the global/config output dir."""
        from ..core.projects import load_project
        self._project_id = project_id or ""
        self._project_dir = None
        if project_id and project_id not in ("", "default"):
            proj = load_project(project_id)
            if proj is not None:
                self._project_dir = Path(proj.workspace_dir())
        # Route the run manager's output at the selected workspace, and filter
        # Flow Status to this workspace's runs.
        self.manager.set_output_root(self._flow_output_root())
        self.manager.set_current_project(self._project_id)
        if hasattr(self, "ws_folder_btn"):
            self._refresh_ws_folder_btn()

    def _flow_output_root(self) -> Path:
        """The workspace folder flow outputs are written under (one subfolder per
        flow). Uses the SELECTED project's workspace when one is bound, else the
        global Cowork output dir. Mirrors co4e_run_manager._out_dir's base."""
        if self._project_dir is not None:
            return self._project_dir / "co4e"
        try:
            base = self.ctx.config.cowork_output_dir()
        except Exception:  # noqa: BLE001
            base = co4e.CO4E_DIR / "runs"
        return Path(base) / "co4e"

    def _refresh_ws_folder_btn(self) -> None:
        root = self._flow_output_root()
        parts = root.parts
        short = "…/" + "/".join(parts[-2:]) if len(parts) > 2 else str(root)
        self.ws_folder_btn.setText(short)
        self.ws_folder_btn.setToolTip(tr("co4e.tt_open_workspace", path=str(root)))

    def _open_workspace_folder(self) -> None:
        from .osutil import open_location
        root = self._flow_output_root()
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        open_location(str(root))

    def _open_run_output_folder(self, run_id) -> None:
        """Open the workspace folder a specific run wrote its files into."""
        from .osutil import open_location
        h = self.manager.get(run_id) if run_id else None
        path = Path(h.out_dir) if (h is not None and h.out_dir) else self._flow_output_root()
        if not path.exists():
            path = self._flow_output_root()
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        open_location(str(path))

    def _rename_selected_run(self) -> None:
        """Rename the selected run in Flow Status — updates the run entry AND its
        underlying saved flow / open tab so the name stays consistent everywhere."""
        row = self.runs_table.currentRow()
        it = self.runs_table.item(row, 0) if row >= 0 else None
        if it is None:
            self.status_message.emit(tr("co4e.select_run"))
            return
        run_id = it.data(Qt.UserRole)
        h = self.manager.get(run_id)
        if h is None:
            return
        from PySide6.QtWidgets import QInputDialog
        new, ok = QInputDialog.getText(self, tr("co4e.rename_run"),
                                       tr("co4e.rename_run_label"), text=h.name)
        new = (new or "").strip()
        if not ok or not new or new == h.name:
            return
        self.manager.rename(run_id, new)          # run entry + snapshot (→ refresh)
        # Keep the underlying saved flow + any open tab in sync.
        wf = co4e.get_workflow(h.wf_id)
        if wf is not None:
            wf.name = new
            co4e.save_workflow(wf)
            self._reload_sidebar()
        for i, f in enumerate(self._flows):
            if f.id == h.wf_id:
                f.name = new
                self.flow_bar.setTabText(i + 1, new)
                break
        if self._wf.id == h.wf_id and self.name_edit.text() != new:
            self.name_edit.setText(new)           # updates _wf.name + active tab text

    def _run_selected_in_background(self) -> None:
        wf = self._selected_wf()
        if wf is None:
            self.status_message.emit(tr("co4e.select_flow"))
            return
        self.manager.start(wf, skill_map=self._skill_map(),
                           plan_mode=(self._current_mode() == "plan"))
        self.sidebar.setCurrentIndex(0)
        self.status_message.emit(tr("co4e.bg_started", name=wf.name))

    def _wf_by_id(self, wf_id: str) -> Optional[co4e.Workflow]:
        """Resolve a flow id to a Workflow — saved, or the open canvas."""
        wf = co4e.get_workflow(wf_id)
        if wf is not None:
            return wf
        if self._wf.id == wf_id:
            self._sync_wf_from_canvas()
            return self._wf
        return None

    def _rerun_run_item(self, item) -> None:
        """Double-click a run in the history → run that flow again (in background)."""
        h = self.manager.get(item.data(Qt.UserRole))
        if h is None:
            return
        wf = self._wf_by_id(h.wf_id)
        if wf is None:
            self.status_message.emit(tr("co4e.flow_gone"))
            return
        self.manager.start(wf, skill_map=self._skill_map(),
                           plan_mode=(self._current_mode() == "plan"))
        self.status_message.emit(tr("co4e.bg_started", name=wf.name))

    def _open_run_from_table(self, item) -> None:
        """Double-click a run row in the Runs tab → open that flow's tab and show
        its live status (opens/focuses the tab; _open_flow reflects the run)."""
        id_item = self.runs_table.item(item.row(), 0)
        if id_item is None:
            return
        h = self.manager.get(id_item.data(Qt.UserRole))
        if h is None:
            return
        # Prefer the flow the run kept a reference to (works even after its tab was
        # closed or if it was never saved); fall back to resolving by id.
        wf = getattr(h, "wf", None) or self._wf_by_id(h.wf_id)
        if wf is None:
            self.status_message.emit(tr("co4e.flow_gone"))
            return
        self._open_flow(wf)
        # reflect this run's step statuses (done/error/running) on the canvas
        for nid, st in h.node_status.items():
            self.canvas.update_node_status(nid, st)
        self.status_message.emit(tr("co4e.viewing_flow", name=wf.name))

    def showEvent(self, e):  # noqa: N802
        # Guarantee the status list is current whenever the tab is shown again.
        self._refresh_runs()
        super().showEvent(e)

    def _out_dir(self) -> Path:
        # Save chat/flow deliverables into the SELECTED workspace (the active
        # project's folder via _flow_output_root), so files land where the user
        # works with them — not in the config/install folder.
        d = self._flow_output_root() / co4e.slugify(self._wf.name or "flow")
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ---- chat (with /agent /skill directives) -----------------------------
    def _chat_send(self) -> None:
        text = self.chat_input.text().strip()
        if not text or self._chat_worker is not None:
            return
        self.chat_input.clear()
        self._append_chat("user", text)
        skill_prefix, request, info = skills_mod.parse_skill_command(text)
        if info is not None:
            self._append_chat("system", info)
            return
        system_parts = []
        if skill_prefix:
            system_parts.append(skill_prefix)
        agent_name, request = self._extract_agent_directive(request)
        model = ""
        if agent_name:
            persona = self._resolve_agent(agent_name)
            if persona is None:
                self._append_chat("system", tr("co4e.agent_not_found", name=agent_name))
                return
            system_parts.append(persona[0])
            model = persona[1]
        # Auto Model Routing — only when the user hasn't pinned an agent's own
        # model (an explicit pin wins). May switch provider+model for this turn.
        if not model:
            model = self._apply_co4e_routing(request)
        self._run_chat_turn(system_parts, request, model)

    def _apply_co4e_routing(self, request: str) -> str:
        """Route this Co4E turn to the best-fit model. Returns the model id to
        use ('' → provider default) and sets ``self._co4e_routed_provider`` when
        a cross-provider switch is chosen. Off → no-op. Manual → confirm first.
        Never raises — falls back to the default model on any error."""
        self._co4e_routed_provider = None
        if not (request or "").strip():
            return ""
        try:
            mode = self.ctx.project_routing_mode("co4e")  # per-workspace mode
            if mode == "off":
                return ""
            service = self.ctx.routing()
            cur_provider = self.ctx.config.active_provider
            cur_model = self.ctx.config.provider_conf(cur_provider).get("model", "")
            result = service.route("co4e", request, cur_provider, cur_model, mode_override=mode)
            if not result.should_switch:
                return ""
            target = result.target()
            if target is None:
                return ""
            to_provider, to_model = target
            if mode == "manual":
                from .routing_toggle import confirm_switch
                timeout = float(self.ctx.config.routing.get("confirm_timeout_sec", 60) or 60)
                if not confirm_switch(self, result.decision, timeout):
                    return ""
            self._co4e_routed_provider = to_provider
            self._append_chat("system", tr(
                "routing.switched_notice",
                model=to_model, task=result.task_type.value,
                gain=f"{result.decision.score_gain:.2f}"))
            return to_model
        except Exception:  # noqa: BLE001 — routing must never block a Co4E turn
            self._co4e_routed_provider = None
            return ""

    def _extract_agent_directive(self, text: str):
        m = re.search(r"(?<!\S)/agent:([\w\-.]+)", text)
        if not m:
            return "", text
        name = m.group(1)
        rest = (text[:m.start()] + " " + text[m.end():]).strip()
        return name, rest

    def _resolve_agent(self, name: str):
        low = name.lower()
        for a in BUILTIN_AGENTS:
            if a.slug == low or a.name.lower() == low:
                return (f"You are the {a.role} agent — {a.name}.\n{a.instructions}", "")
        for ca in co4e.list_custom_agents():
            if co4e.slugify(ca.name) == low or ca.name.lower() == low:
                return (f"You are the {ca.role} agent — {ca.name}.\n{ca.instructions}", ca.model)
        return None

    def _run_chat_turn(self, system_parts: List[str], request: str, model: str) -> None:
        self.chat_send_btn.setEnabled(False)
        log = self.chat_log                 # THIS flow's conversation (captured)
        log._co4e_plan_bubble = None        # a fresh plan for this turn
        ctx = self.ctx
        out_dir = self._out_dir()
        sys_text = "\n\n".join(p for p in system_parts if p)
        prompt = f"{sys_text}\n\n{request}" if sys_text else request
        assistant = log.add_assistant()      # stream into this live bubble
        state = {"text": ""}
        wf = getattr(self, "_wf", None)
        wf_id = wf.id if wf is not None else None
        flow_label = wf.name if wf is not None else "flow"

        def job(worker: AgentWorker):
            from ..core import agent_roles, usage_tracker as ut
            from ..core.chat_agent import run_cowork
            from ..core.co4e_runner import _usage_delta
            # An Auto/Manual routing switch may target a different provider.
            provider = ctx.build_provider_for(getattr(self, "_co4e_routed_provider", None), model or None)
            messages = [{"role": "user", "content": prompt}]
            ut.set_context("co4e", flow_label)   # attribute + measure this turn's usage
            ut.begin_accumulation()
            base = ut.accumulated()

            def _emit(ev):
                if not isinstance(ev, dict):
                    return
                t = ev.get("type")
                if t == "text":
                    worker.emit_event({"type": "text", "delta": ev.get("delta", "")})
                elif t == "plan_set":
                    worker.emit_event({"type": "plan_set", "steps": ev.get("steps") or []})
            try:
                run_cowork(provider, messages, out_dir, _emit, worker.is_cancelled,
                           security_config=ctx.config, agent_role=agent_roles.COWORK,
                           run_to_completion=True, enforce_rules=False)
                usage = _usage_delta(base, ctx.config)
            finally:
                ut.end_accumulation()
            for m in reversed(messages):
                if m.get("role") == "assistant" and m.get("content"):
                    return {"text": str(m["content"]), "usage": usage}
            return {"text": "", "usage": usage}

        def on_event(ev):
            if ev.get("type") == "text":
                state["text"] += ev.get("delta", "")
                assistant.set_markdown(state["text"])
                log.scroll_to_bottom()
            elif ev.get("type") == "plan_set":
                self._append_plan(ev.get("steps") or [], log=log)

        def done(result: dict):
            self._chat_worker = None
            self.chat_send_btn.setEnabled(True)
            final = result.get("text") or state["text"]
            assistant.set_markdown(final or "(no output)")
            self._apply_usage(assistant, wf_id, result.get("usage"))
            log.scroll_to_bottom()

        def failed(err: str):
            self._chat_worker = None
            self.chat_send_btn.setEnabled(True)
            self._append_chat("error", f"[error: {err}]", log=log)

        w = AgentWorker(job)
        w.event.connect(on_event)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._chat_worker = w
        w.start()

    def _append_chat(self, role: str, text: str, log: "ChatView" = None) -> None:
        """Add one message bubble to a flow's conversation. ``log`` defaults to the
        active flow's log; a run/stream passes its OWN captured log so events land
        in the right flow even if the user switches tabs mid-run."""
        log = log or self.chat_log
        if role == "user":
            bub = log.add_user(text)
        elif role == "assistant":
            bub = log.add_assistant()
            bub.set_markdown(text)
        elif role == "error":
            bub = log.add_error(text)
        else:                       # system status marker
            bub = log.add_status(text)
        log.scroll_to_bottom()
        return bub

    # ---- token / cost accounting (shown per-message + as a flow total) ------
    def _fmt_usage(self, d_in: int, d_out: int, d_cache: int, cost_usd: float) -> str:
        """The Cowork-style footer string: ↓in ↑out ▤(in+out+cache) $cost, priced
        with the Monitoring model-price table in the app's display currency."""
        from ..core import model_pricing as mp, usage_tracker as ut
        pricing = {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}
        return (f"↓{mp.format_tokens(d_in)} ↑{mp.format_tokens(d_out)} "
                f"▤{mp.format_tokens(d_in + d_out + d_cache)} "
                f"{ut.format_cost(cost_usd, pricing)}")

    def _apply_usage(self, bub, wf_id, usage) -> None:
        """Attach a token/cost footer to a step's bubble and add it to the flow's
        running total (mirrors Cowork's per-message + conversation-total display)."""
        if not isinstance(usage, dict):
            return
        d_in = int(usage.get("in", 0) or 0)
        d_out = int(usage.get("out", 0) or 0)
        d_cache = int(usage.get("cache", 0) or 0)
        cost = float(usage.get("cost_usd", 0.0) or 0.0)
        if bub is not None and (d_in or d_out):
            try:
                bub.add_usage(self._fmt_usage(d_in, d_out, d_cache, cost))
            except Exception:  # noqa: BLE001 - a usage footer must never break the run
                pass
        if wf_id is not None:
            tot = self._flow_usage.setdefault(wf_id, {"in": 0, "out": 0, "cache": 0, "cost": 0.0})
            tot["in"] += d_in; tot["out"] += d_out; tot["cache"] += d_cache; tot["cost"] += cost
            self._refresh_usage_total(wf_id)

    def _refresh_usage_total(self, only_wf: str = None) -> None:
        """Update the bottom conversation total to the CURRENT flow's running
        usage (skip if the event is for a different, background flow)."""
        lbl = getattr(self, "_usage_total_lbl", None)
        if lbl is None:
            return
        wf = getattr(self, "_wf", None)
        wf_id = wf.id if wf is not None else None
        if only_wf is not None and only_wf != wf_id:
            return
        tot = self._flow_usage.get(wf_id) if wf_id else None
        if not tot or not (tot["in"] or tot["out"]):
            lbl.setText("")
            return
        lbl.setText(self._fmt_usage(int(tot["in"]), int(tot["out"]),
                                    int(tot["cache"]), float(tot["cost"])))

    def _append_diff(self, title: str, diff: str, log: "ChatView" = None) -> None:
        """Render a before/after diff as a collapsible colored diff bubble."""
        log = log or self.chat_log
        log.add_diff(f"▤ {title}", diff)
        log.scroll_to_bottom()

    def _append_plan(self, steps, log: "ChatView" = None) -> None:
        """Show the plan INLINE in the conversation as an expandable block; update
        the same (per-flow) bubble in place so steps tick off (✓) as they complete."""
        log = log or self.chat_log
        body = _fmt_plan(steps)
        if not body:
            return
        if getattr(log, "_co4e_plan_bubble", None) is None:
            log._co4e_plan_bubble = log.add_plan(body)
        else:
            log._co4e_plan_bubble.set_plain(body)
        log.scroll_to_bottom()

    # ---- i18n -------------------------------------------------------------
    def _retranslate(self) -> None:
        self.sidebar.setTabToolTip(0, tr("co4e.tab_workflows"))
        self.sidebar.setTabToolTip(1, tr("co4e.tab_agents"))
        self.sidebar.setTabToolTip(2, tr("co4e.tab_skills"))
        self.runs_title.setText(tr("co4e.running_flows"))
        self.run_stop_btn.setText(tr("co4e.stop"))
        self.run_rename_btn.setText(tr("co4e.rename_run"))
        self.run_del_btn.setText(tr("co4e.delete_run"))
        self.run_clear_btn.setText(tr("co4e.clear_done"))
        self._refresh_ws_folder_btn()
        self.runs_table.setHorizontalHeaderLabels([
            tr("co4e.runs_col_flow"), tr("co4e.runs_col_status"), tr("co4e.runs_col_steps"),
            tr("co4e.runs_col_by"), tr("co4e.runs_col_at")])
        self._reload_sidebar()
        self._refresh_runs()


def _html_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _qcolor(hex_str: str):
    from PySide6.QtGui import QColor
    return QColor(hex_str)
