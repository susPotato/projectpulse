"""Base chat panel shared by the Cowork and Code tabs.

Provides: streaming transcript, a message queue, and history autosave.

Several messages can run **at the same time** inside one tab: each turn owns its
own worker thread and its own turn-context (assistant bubble, transcript record,
message list, output folder), so their streaming output and files never collide.
The number of simultaneous turns is capped by ``cowork.max_parallel`` (default 5);
extra messages wait in the composer queue and start automatically as slots free
up. Graph events are still forwarded per session.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtCore import QFileSystemWatcher
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSplitter,
    QVBoxLayout, QWidget,
)

from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .chat_view import ChatView, ThinkingIndicator
from .composer import Composer
from .icons import collapse_right_icon, icon as app_icon
from .osutil import is_image, open_path
from .widgets import CollapsibleSection, CollapseStrip, PlanSection


_PLAN_ICONS = {"pending": "○", "running": "▶", "done": "✓", "error": "✗"}

# Friendly "what the agent is doing now" translation keys for the working
# indicator, so a long file/document build reads as "Creating…" rather than a
# generic "Running".
_TOOL_STATUS = {
    "save_file": "chat.creating",
    "write_file": "chat.creating",
    "run_command": "chat.creating",
    "edit_file": "chat.editing",
    "install_package": "chat.installing",
    "read_file": "chat.reading",
}


def _format_plan_steps(steps) -> str:
    """Render plan steps ``[{title, status}]`` as an icon checklist for the chat."""
    lines = []
    for s in steps or []:
        title = str((s or {}).get("title", "")).strip()
        if not title:
            continue
        icon = _PLAN_ICONS.get(str((s or {}).get("status", "pending")).lower(), "○")
        lines.append(f"{icon} {title}")
    return "\n".join(lines)


def _is_scratch(path: str) -> bool:
    """True for helper/intermediate files (kept out of the Output list)."""
    try:
        return ".scratch" in Path(path).parts
    except Exception:  # noqa: BLE001
        return False


class ChatPanel(QWidget):
    graph_event = Signal(str, dict)   # (session_name, event)
    turn_finished = Signal(dict)
    status_message = Signal(str)
    output_changed = Signal(str)      # workspace dir; emitted when a file is written
    history_changed = Signal()        # a session was created/updated → refresh History

    def __init__(self, ctx: AppContext, kind: str, session_name: str,
                 placeholder_key: str = "composer.placeholder_default"):
        super().__init__()
        from ..core.history import new_session_id

        self.ctx = ctx
        self.kind = kind
        self.session_name = session_name
        self.session_id = new_session_id()
        self.title = ""
        # Which project (workspace) this conversation belongs to — every new
        # thread inherits the currently selected project (Claude-Projects style).
        self.project_id = "default"
        self.messages: List[Dict[str, Any]] = []
        # self.worker points at the most-recently-started worker (kept for
        # back-compat); every running turn is tracked in self._active so several
        # can run concurrently. Each value is a turn-context dict — see _start_turn.
        self.worker: AgentWorker | None = None
        self._active: Dict[AgentWorker, Dict[str, Any]] = {}
        self._turn_seq: int = 0
        # session_id -> its live messages list, for every conversation that still has
        # a turn running. Lets you start a new chat / reopen an old one WHILE work
        # runs: the running turn keeps writing to its own conversation in the
        # background, and reopening it attaches to the SAME list (never a stale disk
        # copy), so the two never race on save.
        self._sessions_live: Dict[str, List[Dict[str, Any]]] = {}
        self._teams_worker: AgentWorker | None = None
        self.turns: List[Dict[str, Any]] = []

        # File system watcher — watches the workspace/output folder for new files
        # and auto-loads them into the agent's context on the next turn.
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.directoryChanged.connect(self._on_watched_dir_changed)
        self._known_files: set = set()  # set of known file paths in the watched dir
        self._watch_debounce = QTimer(self)
        self._watch_debounce.setSingleShot(True)
        self._watch_debounce.setInterval(800)  # debounce rapid file changes
        self._watch_debounce.timeout.connect(self._process_new_watched_files)
        self._watched_dir: Optional[Path] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._toolbar = QWidget()
        self.toolbar_v = QVBoxLayout(self._toolbar)
        self.toolbar_v.setContentsMargins(10, 8, 10, 4)
        self.toolbar_v.setSpacing(4)
        self.toolbar_layout = QHBoxLayout()   # first row; tabs may add more rows
        self.toolbar_layout.setSpacing(8)
        self.toolbar_v.addLayout(self.toolbar_layout)
        root.addWidget(self._toolbar)

        self.chat_view = ChatView()
        self.composer = Composer(placeholder_key)
        self.composer.submitted.connect(self.submit)
        self.composer.stop_requested.connect(self.stop)
        self.composer.attachments_added.connect(self._on_attachments_added)
        self.composer.attachment_removed.connect(self._on_attachment_removed)
        self.composer.attach_limit_note.connect(self.status_message)
        self.composer.manage_skills.connect(self._open_skills_manager)
        self.composer.set_max_attachments(
            int(ctx.config.data.get("attachments", {}).get("max_files", 10) or 0))
        # Conversation token/cost total (↓in ↑out ▤total $cost) — bottom-left,
        # updated after each turn; cost uses the Monitoring model-price table.
        self._usage_total_lbl = QLabel("")
        self._usage_total_lbl.setObjectName("hint")
        self._usage_total_lbl.setStyleSheet("color: rgba(140,146,152,0.9);")
        self.composer.add_bottom_left(self._usage_total_lbl)

        # Per-tab "Agent" = which MODEL this tab uses (e.g. deepseek / gemma /
        # qwen for the local provider). Cowork and Code pick independently and
        # run in parallel. The list is fetched from the active provider.
        # The per-tab Agent defaults to the Settings model on startup; a manual
        # pick (override) is remembered only until the active provider changes.
        self._model = ctx.config.provider_conf().get("model", "")
        self._agent_provider = ctx.config.active_provider
        self._agent_user_override = False
        self._admin_agent = None   # selected Admin-defined agent preset, if any
        # Auto Model Routing override for the NEXT turn (set by _apply_routing when
        # the router picks a different model). None → use the tab's own selection.
        self._routed_provider: Optional[str] = None
        self._routed_model: Optional[str] = None
        self._last_turn_agent_signature = None   # what ran the LAST turn (see _note_agent_switch)
        self._pending_agent_switch_review = False
        self._agent_worker: AgentWorker | None = None
        self._agent_lbl = QLabel(tr("chatpanel.agent_label"))
        self._agent_lbl.setObjectName("hint")
        self.agent_combo = QComboBox()
        self.agent_combo.setMinimumWidth(150)
        self.agent_combo.setToolTip(tr("chatpanel.agent_tooltip"))
        self.agent_combo.currentIndexChanged.connect(self._on_agent_changed)
        self.composer.add_bottom_right(self._agent_lbl)
        self.composer.add_bottom_right(self.agent_combo)
        # Off/Auto/Manual routing toggle — lets the router pick the best-fit
        # model per message (see core/routing + _apply_routing).
        from .routing_toggle import RoutingToggle
        self.routing_toggle = RoutingToggle(ctx, self.kind)
        self.composer.add_bottom_right(self.routing_toggle)
        # Manual "compress conversation" — trim old history to cut tokens.
        self.compress_btn = QPushButton(tr("chatpanel.compress_btn"))
        self.compress_btn.setIcon(app_icon("compress"))
        self.compress_btn.setToolTip(tr("chatpanel.compress_tooltip"))
        self.compress_btn.clicked.connect(self._compress_messages)
        self.composer.add_bottom_right(self.compress_btn)
        self.refresh_agents()

        # Chat column: transcript expands, the chat box is pinned at the bottom.
        chat_col = QWidget()
        cc = QVBoxLayout(chat_col)
        cc.setContentsMargins(0, 0, 0, 0)
        cc.setSpacing(0)
        cc.addWidget(self.chat_view, 1)
        self.thinking = ThinkingIndicator()   # animated "working…" line while we wait
        cc.addWidget(self.thinking)
        composer_wrap = QWidget()
        cwl = QVBoxLayout(composer_wrap)
        cwl.setContentsMargins(8, 4, 8, 8)
        cwl.addWidget(self.composer)
        cc.addWidget(composer_wrap)

        self.center_split = QSplitter(Qt.Horizontal)
        self.center_split.addWidget(chat_col)
        root.addWidget(self.center_split, 1)

        # Right sidebar: Output files only (see below — Input is tracked but
        # not shown).
        self.input_section = CollapsibleSection(tr("widgets.input_files"))
        self.output_section = CollapsibleSection(tr("widgets.output_files"))
        # Input files are NOT shown in Cowork's UI anymore — but they're still
        # fully tracked (add/remove/paths()) exactly as before, since that list
        # is what gets written into the conversation's own "inputs" field on
        # save (kept alongside the conversation; nothing here deletes the
        # user's actual files — the conversation JSON itself only disappears
        # when the conversation is deleted, same as always). Give input_section
        # a real, permanently-hidden PARENT (not just "never added to a layout")
        # so its own internal auto-show-on-add() call can never pop it up as a
        # stray floating window.
        self._input_hidden_host = QWidget(self)
        self._input_hidden_host.setVisible(False)
        _hh_lay = QVBoxLayout(self._input_hidden_host)
        _hh_lay.setContentsMargins(0, 0, 0, 0)
        _hh_lay.addWidget(self.input_section)
        self.plan_section = PlanSection(tr("widgets.plan_title"))   # live step checklist, above the Files panel
        # The plan is shown INLINE in the conversation now (see add_plan), so this
        # legacy right-panel checklist is parked inside the permanently-hidden
        # host. Without a parent it would pop as a stray top-level "Plan (N)"
        # window the moment set_steps() made it visible — parenting it here keeps
        # its set_steps/clear calls truly inert (a hidden ancestor never renders).
        _hh_lay.addWidget(self.plan_section)
        self.input_section.activated.connect(self._open_io_item)
        self.output_section.activated.connect(self._open_io_item)
        # Right-click a file → Open / "View & AI Edit" (in-app viewer+editor).
        for section in (self.input_section, self.output_section):
            section.list.setContextMenuPolicy(Qt.CustomContextMenu)
            section.list.customContextMenuRequested.connect(
                lambda pos, s=section: self._io_context_menu(s, pos))
        self._io_widget = QWidget()
        iol = QVBoxLayout(self._io_widget)
        iol.setContentsMargins(6, 6, 6, 6)
        iol.setSpacing(4)
        io_hdr = QHBoxLayout()
        self._io_collapse_btn = QPushButton()
        self._io_collapse_btn.setIcon(collapse_right_icon())
        self._io_collapse_btn.setFixedWidth(28)
        self._io_collapse_btn.clicked.connect(lambda: self._set_io_collapsed(True))
        self._files_header = QLabel()
        self._files_header.setStyleSheet("font-weight:600;")
        io_hdr.addWidget(self._io_collapse_btn)
        io_hdr.addWidget(self._files_header, 1)
        # The plan now shows INLINE in the conversation (an expandable block whose
        # steps tick off as they complete), not in this right panel — so it's kept
        # out of the layout here. The object stays (its set_steps/clear calls are
        # harmless no-ops on a hidden widget).
        self.plan_section.setVisible(False)
        iol.addLayout(io_hdr)
        bl_host = QWidget()
        bl = QVBoxLayout(bl_host)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(4)
        bl.addWidget(self.output_section)   # Output only — Input is tracked but hidden
        bl.addStretch(1)
        iol.addWidget(bl_host, 1)

        # Collapsing shrinks the panel to a thin clickable line (not hidden).
        # The collapse button lives in the panel header; the strip re-expands.
        self._io_strip = CollapseStrip(tr("chatpanel.expand_files_tooltip"), expand_dir="left")
        self._io_strip.clicked.connect(lambda: self._set_io_collapsed(False))
        self._io_strip.setVisible(False)
        self._io_pane = QWidget()
        pl = QHBoxLayout(self._io_pane)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        pl.addWidget(self._io_strip)
        pl.addWidget(self._io_widget, 1)

        self.center_split.addWidget(self._io_pane)
        self.center_split.setStretchFactor(0, 1)
        self.center_split.setStretchFactor(1, 0)
        self.center_split.setChildrenCollapsible(False)
        self.center_split.setSizes([820, 220])
        on_language_changed(self._retranslate_base)

    def _retranslate_base(self) -> None:
        """Re-apply the current language to the chrome shared by every tab
        (Cowork/Code toolbars call their own retranslate on top of this)."""
        self._agent_lbl.setText(tr("chatpanel.agent_label"))
        self.agent_combo.setToolTip(tr("chatpanel.agent_tooltip"))
        self.compress_btn.setText(tr("chatpanel.compress_btn"))
        self.compress_btn.setToolTip(tr("chatpanel.compress_tooltip"))
        self.input_section.set_title(tr("widgets.input_files"))
        self.output_section.set_title(tr("widgets.output_files"))
        self.plan_section.set_title(tr("widgets.plan_title"))
        self._io_collapse_btn.setToolTip(tr("chatpanel.collapse_files_tooltip"))
        self._files_header.setText(tr("chatpanel.files_header"))
        self._io_strip.setToolTip(tr("chatpanel.expand_files_tooltip"))

    def apply_theme(self) -> None:
        """Re-apply theme styles to the chat view so all existing message bubbles
        adapt when the app switches between light and dark modes."""
        self.chat_view.apply_theme()

    # ---- hooks for subclasses ---------------------------------------
    def build_job(self, text: str, messages: List[Dict[str, Any]],
                  out_dir: Optional[Path]):
        """Return the agent job for this turn.

        ``messages`` is the turn's OWN message list (a snapshot of the history so
        far plus the new user message) — the job must read/append to it, never to
        ``self.messages``, so parallel turns don't race. ``out_dir`` is the turn's
        isolated output folder (or None when the tab produces no files)."""
        raise NotImplementedError

    def _turn_output_dir(self, turn_id: str) -> Optional[Path]:
        """Isolated output folder for one turn (None = share/no files). Overridden
        by tabs that write files, so concurrent turns never clobber each other."""
        return None

    def assistant_title(self) -> str:
        return tr("chat.assistant")

    def workspace_dir(self) -> Optional[Path]:
        """Folder shown via the 'open folder' link on messages (None = no link)."""
        return None

    def register_output(self, path: str) -> None:
        """Add a finished file to the Output list — skips intermediate/helper
        files (.scratch/ and, for Cowork, generator scripts) so only real
        deliverables show up. Auto-expands the Files panel if it was collapsed."""
        if _is_scratch(path) or self._is_intermediate_output(path):
            return
        # Auto-expand the Files panel if collapsed so the new file is visible.
        if not self._io_widget.isVisible():
            self._set_io_collapsed(False)
        self.output_section.add(path)
        wd = self.workspace_dir()
        if wd:
            # Let the Structure (RAG) graph auto-refresh from this workspace.
            self.output_changed.emit(str(wd))

    # ---- file system watcher for auto-loading new files --------------
    def _start_watching(self, directory: Path) -> None:
        """Start watching ``directory`` for new files. When new supported files
        appear, they are automatically loaded into the agent's context on the
        next turn (via ``_augment``)."""
        if self._watched_dir == directory:
            return
        self._stop_watching()
        try:
            directory = directory.resolve()
            if not directory.is_dir():
                return
            self._watched_dir = directory
            self._file_watcher.addPath(str(directory))
            # Snapshot the current set of files so we can detect NEW ones.
            self._known_files = set(
                str(p) for p in directory.iterdir()
                if p.is_file() and not p.name.startswith(".")
                and p.suffix.lower() in self._INPUT_EXTS
            )
        except OSError:
            self._watched_dir = None
            self._known_files = set()

    def _stop_watching(self) -> None:
        """Stop watching the current directory."""
        if self._watched_dir is not None:
            try:
                self._file_watcher.removePath(str(self._watched_dir))
            except OSError:
                pass
            self._watched_dir = None
            self._known_files = set()

    def _on_watched_dir_changed(self, path: str) -> None:
        """Called when the watched directory changes. Debounces rapid changes."""
        if path == str(self._watched_dir):
            self._watch_debounce.start()

    def _process_new_watched_files(self) -> None:
        """Compare current files against the known set and notify about new ones."""
        if self._watched_dir is None:
            return
        try:
            current = set(
                str(p) for p in self._watched_dir.iterdir()
                if p.is_file() and not p.name.startswith(".")
                and p.suffix.lower() in self._INPUT_EXTS
            )
        except OSError:
            return
        new_files = current - self._known_files
        if not new_files:
            self._known_files = current
            return
        self._known_files = current
        # Add new files to the Input section so the user can see them.
        for fp in sorted(new_files):
            self.input_section.add(fp)
        # Emit a status message so the user knows new files were detected.
        names = ", ".join(Path(p).name for p in sorted(new_files))
        self.status_message.emit(
            tr("chatpanel.new_files_detected", names=names, n=len(new_files))
        )

    def _is_intermediate_output(self, path: str) -> bool:
        """Override hook: hide helper/generator files from the Output list."""
        return False

    def on_file_written(self, path: str) -> None:
        """Hook: the agent created/edited a file (shown in the Output box)."""
        self.register_output(path)

    def on_inputs_added(self, paths: List[str]) -> None:
        for p in paths:
            self.input_section.add(p)

    def _on_attachments_added(self, paths: List[str]) -> None:
        # Push attachments into the Input box as soon as they're attached.
        for p in paths:
            self.input_section.add(p)

    def _on_attachment_removed(self, path: str) -> None:
        # A file added by mistake was removed in the composer — drop it from the
        # Input panel too (only matters before the message is sent).
        self.input_section.remove(path)

    def _open_io_item(self, path: str) -> None:
        open_path(path)

    def _io_context_menu(self, section, pos) -> None:
        """Right-click menu on a file in the Input/Output lists: Open with the
        OS app, or view + AI-edit it inside the app (FileEditDialog)."""
        item = section.list.itemAt(pos)
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path:
            return
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        act_open = menu.addAction(app_icon("link"), tr("chatpanel.menu_open"))
        act_edit = menu.addAction(app_icon("edit"), tr("chatpanel.menu_ai_edit"))
        chosen = menu.exec(section.list.mapToGlobal(pos))
        if chosen is act_open:
            open_path(path)
        elif chosen is act_edit:
            from .file_edit_dialog import FileEditDialog

            FileEditDialog(self.ctx, path, self).exec()

    # ---- skills management (shared by Cowork and Code) ---------------
    def _open_skills_manager(self) -> None:
        """Open the Skills manager (add / edit / delete / enable skills)."""
        from .skills_dialog import SkillsDialog

        SkillsDialog(self, self.ctx).exec()
        self._skills_changed()
        self.status_message.emit(tr("chatpanel.skills_updated"))

    def _skills_changed(self) -> None:
        """Hook after skills were edited (Code tab refreshes its Skills button)."""

    # ---- per-tab agent (model / admin-agent preset) selection --------
    _ADMIN_AGENT_PREFIX = "admin:"
    # Sent (invisibly — folded into the outgoing content, never the visible
    # chat bubble) as a one-shot prefix on the FIRST turn run under a newly
    # picked model/agent, when the conversation already has prior turns: asks
    # the new model to check over the most recent step before doing anything
    # new, so a mid-conversation switch doesn't silently drop continuity.
    _MODEL_SWITCH_REVIEW_NOTE = (
        "[Note: the AI model/agent for this conversation was just switched.] Before "
        "addressing the request below, briefly re-check the most recent step above — "
        "if anything there looks incomplete, inconsistent, or wrong, redo or fix it "
        "first, then continue."
    )

    def _agent_signature(self) -> str:
        """Identifies WHAT will run the next turn (admin agent id, or plain
        provider:model) — comparing this across turns is how a genuine
        mid-conversation switch is detected."""
        agent = getattr(self, "_admin_agent", None)
        if agent is not None:
            return f"{self._ADMIN_AGENT_PREFIX}{agent.agent_id}"
        return f"{self.ctx.config.active_provider}:{self._model}"

    def _current_agent_label(self) -> str:
        """Human-friendly name of what will run the next turn — for the visible
        'auto-switched model' notice in the transcript."""
        agent = getattr(self, "_admin_agent", None)
        if agent is not None:
            return agent.name
        return self._model or tr("chat.provider_default_short")

    def _on_agent_changed(self, _i: int) -> None:
        data = self.agent_combo.currentData() or ""
        if isinstance(data, str) and data.startswith(self._ADMIN_AGENT_PREFIX):
            # An Admin-defined agent preset (Monitoring → Agents Admin): runs
            # on its pinned model (or the Settings default when unpinned) and
            # injects its instructions into every turn of this tab.
            from ..core import admin_agents

            agent_id = data[len(self._ADMIN_AGENT_PREFIX):]
            self._admin_agent = admin_agents.load_agent(
                agent_id, admin_agents.agents_admin_dir(self.ctx.config.shared_dir))
            self._agent_user_override = True
            self._agent_provider = self.ctx.config.active_provider
            self._model = (self._admin_agent.model if self._admin_agent else "") or ""
            if self._admin_agent is not None:
                self.status_message.emit(f"{self.session_name} agent: {self._admin_agent.name}")
            self._note_agent_switch()
            return
        self._admin_agent = None
        new = data or ""   # "" → provider default
        if new != self._model:
            # A deliberate pick by the user — remember it until the provider changes.
            self._agent_user_override = True
            self._agent_provider = self.ctx.config.active_provider
        self._model = new
        if self._model:
            self.status_message.emit(f"{self.session_name} agent: {self._model}")
        self._note_agent_switch()

    def _note_agent_switch(self) -> None:
        """Flag a pending review note for the NEXT turn when the selection
        genuinely changed mid-conversation (there's already history AND this
        isn't just the initial default being applied)."""
        sig = self._agent_signature()
        last = getattr(self, "_last_turn_agent_signature", None)
        if last is not None and sig != last and self.messages:
            self._pending_agent_switch_review = True

    def admin_agent_prompt(self) -> str:
        """The selected admin agent's instructions ('' when a plain model is
        selected) — appended to the project context of every turn."""
        agent = getattr(self, "_admin_agent", None)
        return agent.effective_prompt() if agent is not None else ""

    def refresh_agents(self) -> None:
        """Fetch the model list from the active provider (in the background) and
        fill the per-tab Agent combo — called at start and on provider change.

        The default follows Settings; see state.resolve_agent_default."""
        from ..state import resolve_agent_default

        name = self.ctx.config.active_provider
        setting_model = self.ctx.config.provider_conf(name).get("model", "")
        keep, self._agent_user_override = resolve_agent_default(
            name, setting_model, self._model, self._agent_provider, self._agent_user_override)
        self._model = keep
        self._agent_provider = name

        def job(worker: AgentWorker):
            error = ""
            try:
                prov = self.ctx.build_provider_for(name)
                models = list(getattr(prov, "list_models", lambda: [])() or [])
                if not models:
                    error = getattr(prov, "last_error", "")
            except Exception as exc:  # noqa: BLE001 - never break the UI over a model list
                models, error = [], str(exc)
            return {"models": models, "keep": keep, "error": error}

        def done(result) -> None:
            self._populate_agents(result.get("models", []), result.get("keep", ""))
            # Surface the REAL reason models didn't load (network/auth/config)
            # instead of silently falling back to "(provider default)".
            err = result.get("error", "")
            if err:
                self.status_message.emit(tr("chatpanel.agent_list_error", err=err))

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        self._agent_worker = w
        w.start()

    def _populate_agents(self, models, keep: str) -> None:
        self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        # The Agent picker is a MODEL picker — the raw model list of the active
        # provider. Admin-defined agents (Monitoring → Agents Admin) are NOT
        # listed here: they are system-management presets, not a model/agent to
        # pick for a Cowork conversation. To apply a work agent's persona, use
        # the /agent command (built-in + custom Flow agents).
        items = list(dict.fromkeys([m for m in models if m]))   # dedupe, keep order
        if keep and keep not in items:
            items.insert(0, keep)
        for m in items:
            self.agent_combo.addItem(m, m)
        if not items and self.agent_combo.count() == 0:
            # No models found and none configured — placeholder with data=None so
            # we fall back to the provider's default model (never a fake name).
            self.agent_combo.addItem("(provider default)", None)
        keep_data = (f"{self._ADMIN_AGENT_PREFIX}{self._admin_agent.agent_id}"
                     if getattr(self, "_admin_agent", None) is not None else keep)
        idx = self.agent_combo.findData(keep_data) if keep_data else -1
        if idx >= 0:
            self.agent_combo.setCurrentIndex(idx)
        self.agent_combo.blockSignals(False)
        data = self.agent_combo.currentData() or ""
        if not (isinstance(data, str) and data.startswith(self._ADMIN_AGENT_PREFIX)):
            self._model = data or ""

    def build_provider(self):
        """Provider for THIS tab: the selected admin agent's pinned
        provider/model when one is selected, else the tab's selected model
        (or the provider's configured default when none is chosen)."""
        agent = getattr(self, "_admin_agent", None)
        if agent is not None:
            from ..core.admin_agents import build_agent_provider

            return build_agent_provider(self.ctx, agent)
        # An Auto/Manual routing override (set by _apply_routing for this turn)
        # wins over the tab's own provider/model selection.
        provider = self._routed_provider or self.ctx.config.active_provider
        model = self._routed_model or self._model or None
        return self.ctx.build_provider_for(provider, model)

    def _apply_routing(self, text: str, turn: Dict[str, Any]) -> None:
        """Auto Model Routing hook — run once per outgoing message.

        Off → no-op. Auto → silently switch to the best-fit model. Manual → ask
        the user (modal, with the configured confirm timeout) before switching.
        Sets ``self._routed_provider``/``self._routed_model`` for THIS turn;
        :meth:`build_provider` honours them. Never raises — a routing failure
        must never block sending a message; it just falls back to the tab's
        own model.
        """
        # Recompute fresh each message; clear any previous turn's override.
        self._routed_provider = None
        self._routed_model = None
        # An explicitly-pinned Admin agent takes precedence over routing.
        if getattr(self, "_admin_agent", None) is not None:
            return
        if not (text or "").strip():
            return
        try:
            mode = self.ctx.project_routing_mode(self.kind)  # per-workspace mode
            if mode == "off":
                return
            service = self.ctx.routing()
            cur_provider = self.ctx.config.active_provider
            cur_model = self._model or self.ctx.config.provider_conf(cur_provider).get("model", "")
            result = service.route(self.kind, text, cur_provider, cur_model, mode_override=mode)
            if not result.should_switch:
                return
            target = result.target()
            if target is None:
                return
            to_provider, to_model = target
            if mode == "manual":
                from .routing_toggle import confirm_switch
                timeout = float(self.ctx.config.routing.get("confirm_timeout_sec", 60) or 60)
                if not confirm_switch(self, result.decision, timeout):
                    return  # declined / timed out → keep current model
            self._routed_provider = to_provider
            self._routed_model = to_model
            notice = self.chat_view.add_status(tr(
                "routing.switched_notice",
                model=to_model, task=result.task_type.value,
                gain=f"{result.decision.score_gain:.2f}"))
            turn["bubbles"].append(notice)
        except Exception:  # noqa: BLE001 — routing must never block a chat turn
            self._routed_provider = None
            self._routed_model = None

    def _compress_messages(self) -> None:
        """Manual compress: keep the system prompt + the last 2 turns verbatim and
        DIGEST all older messages into one compact summary, shrinking it until the
        whole conversation is under 25% of its original token size."""
        if self._view_busy():
            self.status_message.emit(tr("chatpanel.compress_busy"))
            return
        from ..core.usage_tracker import estimate_tokens

        msgs = list(self.messages)

        def _tok(ms):
            return sum(estimate_tokens(str(m.get("content", ""))) for m in ms)

        orig = _tok(msgs)
        systems = [m for m in msgs if m.get("role") == "system"]
        rest = [m for m in msgs if m.get("role") != "system"]
        starts = [i for i, m in enumerate(rest) if m.get("role") == "user"]
        if len(starts) <= 2 or orig <= 0:
            self.status_message.emit(tr("chatpanel.compress_short"))
            return
        cut = starts[-2]                       # keep the last 2 turns verbatim
        old, recent = rest[:cut], rest[cut:]
        old_tok = _tok(old) or 1               # target: digest < 25% of the OLD part

        def _digest(per_msg: int):
            parts = []
            for m in old:
                c = str(m.get("content", "")).strip().replace("\n", " ")
                if c:
                    parts.append(f"- {m.get('role', '')}: {c[:per_msg]}")
            body = "\n".join(parts)
            return {"role": "user",
                    "content": f"[{tr('chatpanel.compress_digest_header', n=len(old))}]\n{body}"}

        per_msg = 240
        digest = _digest(per_msg)
        # shrink the digest until the OLD conversation is under 25% of its size
        while _tok([digest]) > 0.25 * old_tok and per_msg > 20:
            per_msg = max(20, per_msg // 2)
            digest = _digest(per_msg)
        self.messages = systems + [digest] + recent
        pct = int(_tok([digest]) * 100 / old_tok)
        self.status_message.emit(tr("chatpanel.compress_reduced", pct=pct, n=len(old)))

    def _set_io_collapsed(self, collapsed: bool) -> None:
        self._io_widget.setVisible(not collapsed)
        self._io_strip.setVisible(collapsed)
        strip_w = CollapseStrip.WIDTH + 2
        if collapsed:
            self._io_pane.setMaximumWidth(strip_w)
            self._collapse_split_pane(self._io_pane, strip_w)
        else:
            self._io_pane.setMaximumWidth(16777215)  # QWIDGETSIZE_MAX
            self._restore_split_sizes()

    # ---- shared split-pane collapse helpers (used by subclasses too) ----
    def _collapse_split_pane(self, pane: QWidget, strip_w: int) -> None:
        """Shrink one splitter pane to ``strip_w`` and hand the freed width to
        the widest remaining pane. Works for any number of panes."""
        sizes = self.center_split.sizes()
        idx = self.center_split.indexOf(pane)
        if not (0 <= idx < len(sizes)):
            return
        diff = sizes[idx] - strip_w
        sizes[idx] = strip_w
        others = [i for i in range(len(sizes)) if i != idx and sizes[i] > 0]
        if others and diff != 0:
            big = max(others, key=lambda i: sizes[i])
            sizes[big] = max(strip_w, sizes[big] + diff)
        self.center_split.setSizes(sizes)

    def _restore_split_sizes(self) -> None:
        """Default expanded layout; panes still collapsed stay thin (max-width)."""
        self.center_split.setSizes([820, 220])

    # ---- delete a turn (message + its input/output files) ------------
    def _delete_turn(self, turn: Dict[str, Any]) -> None:
        files = [p for p in (turn.get("inputs", []) + turn.get("outputs", [])) if p]
        if files:
            preview = "\n".join("• " + str(p) for p in files[:12])
            prompt = tr("chatpanel.delete_confirm_files", n=len(files), preview=preview)
        else:
            prompt = tr("chatpanel.delete_confirm_plain")
        if QMessageBox.question(self, tr("chatpanel.delete_confirm_title"), prompt) != QMessageBox.Yes:
            return
        for bubble in turn.get("bubbles", []):
            bubble.setParent(None)
            bubble.deleteLater()
        ids = {id(m) for m in turn.get("messages", [])}
        if ids:
            self.messages = [m for m in self.messages if id(m) not in ids]
        for p in files:
            try:
                fp = Path(p)
                if fp.is_file():
                    fp.unlink()
            except OSError:
                pass
        if turn in self.turns:
            self.turns.remove(turn)
        self._rebuild_io()
        self._autosave()
        self.status_message.emit(tr("chatpanel.delete_done"))

    def _rebuild_io(self) -> None:
        self.input_section.clear()
        self.output_section.clear()
        for t in self.turns:
            for p in t.get("inputs", []):
                self.input_section.add(p)
            for p in t.get("outputs", []):
                self.output_section.add(p)

    # ---- turn lifecycle ---------------------------------------------
    def submit(self, text: str, attachments: Optional[List[str]] = None) -> None:
        # Composer only emits 'submitted' when not busy; queued items are
        # drained from here after each turn completes.
        self._start_turn(text, attachments or [])

    def _attach_char_limit(self) -> int:
        """Per-file content cap (characters) from the Settings token limit
        (~4 chars/token)."""
        try:
            tokens = int(self.ctx.config.data.get("attachments", {}).get("max_tokens", 500000))
        except (TypeError, ValueError):
            tokens = 500000
        return max(1000, tokens) * 4

    # File types considered valid input data in the workspace/output folder
    _INPUT_EXTS = {
        ".csv", ".json", ".txt", ".md", ".log", ".xml", ".yaml", ".yml",
        ".docx", ".docm", ".xlsx", ".xlsm", ".pptx", ".pdf", ".odt", ".ods", ".odp",
        ".rtf", ".tsv",
    }

    def _augment(self, text: str, attachments: List[str], notify=None) -> str:
        """Embed attachment paths AND their extracted contents into the prompt so
        the agent actually reads and analyses each attached file.

        Additionally, scans the workspace/output folder for existing files and
        loads them as input data so the agent can read/process them automatically.

        ``notify``, if given, is called with UI-visible events (a live "reading
        page X/Y" progress notice, and a warning when a file's content could not
        be read) instead of failures being silently handed to the model as an
        opaque inline note."""
        has_attachments = bool(attachments)
        limit = self._attach_char_limit()
        lines = [text] if text else []

        # --- User-attached files ---
        if has_attachments:
            lines.append("\n[Attachments] — read and use these files to answer the request:")
            for p in attachments:
                lines.extend(self._read_one_attachment(p, limit, notify))

        # --- Auto-load existing workspace/output folder files as input data ---
        # This is what makes "📁 Chọn thư mục khác" useful as an INPUT folder
        # too: every file already in the chosen folder is read and embedded so
        # the agent can act on their contents without manual attaching.
        workspace = self.workspace_dir()
        max_files = int(self.ctx.config.data.get("attachments", {})
                        .get("max_files", 10) or 0)
        if workspace is not None:
            lines.extend(self._folder_input_lines(
                workspace,
                "[Workspace files] — existing files in output folder, "
                "read and use as input data. The user expects you to "
                "process these files automatically:",
                limit, max_files, notify))

        # --- Project knowledge (Claude-Projects style) ---
        # Only scanned separately when it's a DIFFERENT folder from the
        # session's own workspace — for Cowork the two are now the same
        # folder (a project has one shared workspace, no per-thread
        # sub-folder), so this never double-scans the same directory.
        knowledge = self.project_knowledge_dir()
        if knowledge is not None and knowledge != workspace:
            lines.extend(self._folder_input_lines(
                knowledge,
                "[Project files] — shared knowledge files of this project, "
                "available to every conversation in it. Read and use them "
                "as context for the request:",
                limit, max_files, notify))

        return "\n".join(lines)

    def project_knowledge_dir(self):
        """Folder of project-level shared knowledge files (None = no project
        knowledge). Overridden by the Cowork tab for non-default projects."""
        return None

    def _folder_input_lines(self, folder: Path, header: str, limit: int,
                            max_files: int, notify=None) -> list:
        """Embed a folder's readable files into the prompt — recursing into
        every sub-folder, any depth, not just the top level, so files placed
        in nested folders are read and processed too (same per-message file
        cap as manual attachments — Settings → Attachments → max files;
        0 = unlimited — so a folder with dozens of files can't blow the
        context window)."""
        from ..core.doc_extract import find_input_files

        out: list = []
        shown, total = find_input_files(folder, self._INPUT_EXTS, max_files)
        if shown:
            out.append("\n" + header)
            for f in shown:
                out.extend(self._read_one_attachment(str(f), limit, notify))
        if total > len(shown):
            skipped = total - len(shown)
            out.append(f"…({skipped} more files in the folder were not "
                       "loaded — per-message attachment limit; mention a "
                       "file by name if the user asks about it)")
            if notify is not None:
                notify({"type": "notice", "level": "warning",
                        "text": tr("chat.workspace_files_capped",
                                   shown=len(shown), total=total)})
        return out

    def _read_one_attachment(self, path: str, limit: int, notify=None) -> list:
        """Read and format one attachment/workspace file. Returns list of lines.

        Handles every file type: images (noted with path), MS Office / PDF /
        OpenDocument / text (extracted), and ZIP archives — which are auto-
        extracted into the workspace and their contents read + processed."""
        name = Path(path).name
        result = []
        if is_image(path):
            result.append(f"- {name} (image at {path})")
            return result
        from ..core.doc_extract import is_zip
        if is_zip(path):
            result.extend(self._read_zip_attachment(path, name, limit, notify))
            return result

        def progress(page: int, total: int, _name=name) -> None:
            if notify is not None and total > 1:
                notify({"type": "notice", "level": "progress",
                        "text": tr("chat.reading_progress", name=_name, page=page, total=total)})

        content, note = self._read_attachment_text(path, progress=progress)
        if content is None:
            result.append(f"- {name} ({note}; located at {path})")
            if notify is not None:
                notify({"type": "notice", "level": "warning",
                        "text": tr("chat.attachment_failed", name=name, note=note)})
            return result
        self._enforce_attachment_security(name, content)   # raises SecurityBlocked on a violation
        extra = ""
        if len(content) > limit:
            content = content[:limit]
            extra = f"\n…(truncated to ~{limit // 4} tokens)…"
        result.append(f"- {name} ({path})")
        result.append(f"\n--- Content of {name} ---\n{content}{extra}\n--- end of {name} ---")
        return result

    def _read_zip_attachment(self, path: str, name: str, limit: int, notify=None) -> list:
        """Auto-extract a .zip into the workspace and read+process its files, so
        an attached archive is unpacked and its contents used automatically."""
        from ..core.doc_extract import extract_archive
        ws = self.workspace_dir()
        dest = (Path(ws) if ws is not None else Path(path).parent) / Path(name).stem
        files = extract_archive(path, dest)
        result = [f"- {name} (archive) — extracted {len(files)} file(s) into the workspace at "
                  f"{dest}. Read/edit them there as needed."]
        if self.workspace_dir() is not None:
            self.output_changed.emit(str(self.workspace_dir()))   # let the graph/folder refresh
        max_files = int(self.ctx.config.data.get("attachments", {}).get("max_files", 10) or 0)
        shown = files[:max_files] if max_files else files
        for f in shown:
            result.extend(self._read_one_attachment(str(f), limit, notify))
        if max_files and len(files) > max_files:
            result.append(f"- …and {len(files) - max_files} more file(s) in {dest} "
                          "(not inlined; open/read them from the workspace as needed).")
        return result

    def _enforce_attachment_security(self, filename: str, content: str) -> None:
        """Agent Security's attachment layer (Settings → 🛡 Agent Security) —
        scans extracted file content for malicious payloads BEFORE it enters
        the model's context. No-op when disabled. Raises SecurityBlocked
        (propagates out of _augment → the worker job → AgentWorker.failed,
        which the panel shows as a chat error) on a violation."""
        sec = self.ctx.config.data.get("agent_security", {})
        if not sec.get("enabled") or not sec.get("validate_attachments", True):
            return
        from ..core.agent_security import SecurityBlocked, combined_rules_text, validate_attachment
        from ..core.agent_security_alert import notify_admin

        rules_text = combined_rules_text(self.ctx.config)
        verdict = validate_attachment(self.build_provider(), filename, content, rules_text)
        if verdict.allowed:
            return
        notify_admin(self.ctx.config, verdict, detail=f"file: {filename}")
        raise SecurityBlocked(verdict)

    @staticmethod
    def _read_attachment_text(path: str, progress=None):
        """Best-effort text extraction so the agent can read the attachment.
        Returns (text, note); text is None when nothing readable was found.

        Delegates to core.doc_extract, which parses docx/xlsx/pptx/odf directly
        (stdlib, no extra packages), uses pypdf for PDFs (reporting per-page
        ``progress`` for multi-page files), and falls back to a headless
        LibreOffice conversion for anything else."""
        from ..core.doc_extract import extract_text

        return extract_text(path, progress=progress)

    def _apply_skill_command(self, text: str):
        """Parse a leading ``/skill`` command typed in the chat box.

        Returns ``(prefix, request, info)`` — see ``core.skills.parse_skill_command``."""
        try:
            from ..core.skills import parse_skill_command
            return parse_skill_command(text)
        except Exception:
            return "", text, "Could not read skills from the Skills manager."

    def _apply_agent_command(self, text: str):
        """Parse a ``/agent`` command typed in the chat box (Cowork parity with
        Co4E): apply a named agent PERSONA to the turn. Returns
        ``(prefix, request, info)`` — see ``core.agent_command.parse_agent_command``."""
        try:
            from ..core.agent_command import parse_agent_command
            return parse_agent_command(text, self.ctx.config.shared_dir)
        except Exception:  # noqa: BLE001
            return "", text, "Could not read the agent catalog."

    def run_prompts(self, prompts: List[str]) -> None:
        """Enqueue several prompts and run them (used by flows). They start up to
        the parallel limit; the rest stay queued and start as slots free up."""
        prompts = [p for p in prompts if p and p.strip()]
        if not prompts:
            return
        for p in prompts:
            self.composer.enqueue(p)
        self._drain_queue()

    def _start_turn(self, text: str, attachments: Optional[List[str]] = None) -> None:
        attachments = attachments or []
        typed = text
        prefix, request, info = self._apply_skill_command(text)
        if info is not None:
            # A local /skill command (list / select / error) — answer inline.
            self.chat_view.add_user(typed)
            self.chat_view.add_assistant(self.assistant_title()).set_markdown(info)
            self._drain_queue()
            return
        text = request
        # /agent directive → apply a named agent persona to this turn (parity with
        # the Co4E chat). Combined with any /skill prefix already parsed above.
        agent_prefix, text, agent_info = self._apply_agent_command(text)
        if agent_info is not None:
            self.chat_view.add_user(typed)
            self.chat_view.add_assistant(self.assistant_title()).set_markdown(agent_info)
            self._drain_queue()
            return
        if agent_prefix:
            prefix = f"{prefix}\n\n{agent_prefix}" if prefix else agent_prefix
        if not self.title:
            base = text or (Path(attachments[0]).name if attachments else "(attachment)")
            self.title = (base[:60] + "…") if len(base) > 60 else base

        # Reset the Plan panel so each message starts from a clean checklist (the
        # previous message's plan never lingers/flickers into this one).
        self.plan_section.clear()

        # Each turn works on its OWN message list: a snapshot of the history so far
        # plus the new user message, merged back into self.messages when the turn
        # finishes (see _finalize_turn). This keeps concurrent turns from racing on
        # the shared list. The user content is filled in by the worker (below) —
        # reading attachment text can pip-install a parser or call LibreOffice,
        # which must not run on the UI thread.
        snapshot = list(self.messages)
        user_msg: Dict[str, Any] = {"role": "user", "content": prefix or text}
        local_messages = snapshot + [user_msg]

        # Consume the pending switch-review flag exactly once, for THIS turn —
        # and record what's running it so the next genuine switch is detected
        # against this, not against the selection that was current mid-turn.
        review_switch = self._pending_agent_switch_review
        self._pending_agent_switch_review = False
        self._last_turn_agent_signature = self._agent_signature()

        bubble = self.chat_view.add_user(text or "(attachment)")
        turn: Dict[str, Any] = {"bubbles": [bubble], "messages": [],
                                "inputs": list(attachments), "outputs": []}
        if review_switch:
            # Make the mid-conversation model switch VISIBLE (it was silent
            # before): a one-line notice so the user sees the run continued
            # smoothly on the newly-picked model rather than wondering.
            notice = self.chat_view.add_status(
                tr("chat.model_switched", model=self._current_agent_label()))
            turn["bubbles"].append(notice)
        self.turns.append(turn)
        bubble.add_delete_link(lambda t=turn: self._delete_turn(t))
        if attachments:
            bubble.add_attachments(attachments)
            self.on_inputs_added(attachments)
        folder = self.workspace_dir()
        if folder:
            bubble.add_folder_link(str(folder))

        self.graph_event.emit(self.session_name, {"type": "user", "content": text})

        # Auto Model Routing: may switch this turn's provider/model (Auto), or
        # ask first (Manual). Runs before build_job so build_provider() sees the
        # routed choice. No-op when the toggle is Off.
        self._apply_routing(text, turn)

        self._turn_seq += 1
        out_dir = self._turn_output_dir(f"t{self._turn_seq}")
        base_job = self.build_job(text, local_messages, out_dir)

        def job(worker, _m=user_msg, _t=text, _a=attachments, _p=prefix, _j=base_job,
                _review=review_switch):
            # Worker thread: do the (possibly slow) attachment extraction here so
            # the UI stays responsive, then run the real agent job.
            from ..core import usage_tracker
            usage_tracker.set_context(self.kind, self.title or self.session_id)
            body = self._augment(_t, _a, notify=worker.emit_event)
            notes = self._session_notes()
            if notes:
                body = f"{body}\n\n{notes}" if body else notes
            _m["content"] = (_p + "\n\n---\n\n" + body) if _p else body
            if _review:
                # Invisible to the chat bubble (that already shows the plain
                # typed text) — only the payload actually sent to the model
                # carries the note.
                _m["content"] = f"{self._MODEL_SWITCH_REVIEW_NOTE}\n\n{_m['content']}"
            return _j(worker)

        worker = AgentWorker(job)
        # A self-contained context for THIS turn, so its streaming events and files
        # never touch another running turn's state. Signals bind the context via a
        # default-arg so the right ctx is delivered on the UI thread. The "home_*"
        # fields pin the turn to the conversation it started in, so it keeps saving
        # there even if the user switches to another chat while it runs.
        ctx: Dict[str, Any] = {
            "worker": worker, "user_msg": user_msg, "assistant": None,
            "record": turn, "messages": local_messages,
            "snapshot_len": len(snapshot), "out_dir": out_dir,
            "home_id": self.session_id, "home_messages": self.messages,
            "home_title": self.title, "home_out_root": self.workspace_dir(),
            "detached": False,
            # For re-rendering the in-progress turn if the user reopens this chat:
            "display_text": text, "partial": "", "plan_steps": [],
            # token/cost accounting: cumulative session usage BEFORE this turn, so
            # the turn's own tokens are (after − before).
            "usage_base": self._usage_snapshot(),
        }
        self._sessions_live[self.session_id] = self.messages
        self._active[worker] = ctx
        self.worker = worker
        # Record the conversation in History right away (with the new user message,
        # so it has a title) — it shows up and can be selected while it's running.
        self._save_snapshot(self.session_id, local_messages, self.title)
        self.history_changed.emit()
        worker.event.connect(lambda ev, c=ctx: self._on_event(c, ev))
        worker.permission_requested.connect(lambda a, c=ctx: self._on_permission(c, a))
        worker.finished_ok.connect(lambda r, c=ctx: self._on_finished(c, r))
        worker.failed.connect(lambda e, c=ctx: self._on_failed(c, e))

        self.composer.set_running(True)
        # One turn at a time PER conversation: this conversation now has a running
        # turn, so further sends here go to the Queue (in order, no interleaving).
        # Other conversations can still run in parallel up to the global cap.
        if self._view_busy() or len(self._active) >= self._max_parallel():
            self.composer.set_busy(True)
        self.status_message.emit(tr("chatpanel.working", name=tr(f"app.tab.{self.kind}")))
        self.thinking.start("chat.running")
        worker.start()

    def _on_event(self, ctx: Dict[str, Any], ev: Dict[str, Any]) -> None:
        etype = ev.get("type")
        # Track the in-progress state even while this turn is a detached background
        # job, so reopening its conversation can re-render the CURRENT task (partial
        # answer + live plan) — see _reattach_running_turn.
        if etype == "text":
            ctx["partial"] = ctx.get("partial", "") + ev.get("delta", "")
        elif etype == "assistant_done":
            ctx["partial"] = ""
        elif etype == "plan_set":
            ctx["plan_steps"] = ev.get("steps") or []
        # A turn only RENDERS into the transcript/sidebar of the conversation it was
        # started in. If the user navigated away, skip live rendering (the data is
        # tracked above and shown when the conversation is reopened).
        if ctx.get("detached") or ctx.get("home_id") != self.session_id:
            return
        record = ctx["record"]
        if etype == "text":
            self.thinking.stop()  # real output is streaming now
            if ctx["assistant"] is None:
                ctx["assistant"] = self.chat_view.add_assistant(self.assistant_title())
                ctx["last_assistant"] = ctx["assistant"]   # for the per-turn usage footer
                record["bubbles"].append(ctx["assistant"])
                folder = self.workspace_dir()
                if folder:
                    ctx["assistant"].add_folder_link(str(folder))
            ctx["assistant"].append_delta(ev.get("delta", ""))
        elif etype == "assistant_done":
            self.graph_event.emit(self.session_name, ev)
            ctx["assistant"] = None
            ctx["reasoning"] = None   # next step starts a fresh Thinking box
            self._autosave()  # persist latest result (crash-safe, mid-turn)
        elif etype == "tool_proposed":
            # Show WHAT it's doing (e.g. "Creating…" while a document is generated).
            self.thinking.start(_TOOL_STATUS.get(ev.get("name"), "chat.running"))
            if ev.get("name") == "update_plan":
                return  # the plan tool drives the Plan view, not a chat bubble
            # Show the step in the transcript (the code being written / diff /
            # command being run) so the whole process is visible, CLI-style.
            preview = ev.get("preview") or {}
            body = preview.get("text", "")
            if body:
                icons = {"diff": "✎", "command": "▶"}
                title = preview.get("title") or ev.get("name", "tool")
                label = f"{icons.get(preview.get('kind'), '⚙')} {title}"
                # A diff/create/edit preview renders as a colored before/after
                # (additions/deletions), not a flat text block.
                if preview.get("kind") == "diff":
                    step = self.chat_view.add_diff(label, body, True)
                else:
                    step = self.chat_view.add_tool(label, body, True)
                record["bubbles"].append(step)
                # Remember this step's bubble so live stdout/stderr ("tool_output")
                # can be appended to it in real time while the command runs.
                ctx.setdefault("step_bubbles", {})[ev.get("id")] = step
            self.graph_event.emit(self.session_name, ev)
        elif etype == "tool_output":
            # Live output from a running command/install (see run_cancellable) —
            # append to its step bubble so progress is visible before it finishes.
            step = ctx.get("step_bubbles", {}).get(ev.get("id"))
            if step is not None:
                step.append_plain(ev.get("delta", ""))
        elif etype == "notice":
            # A UI-visible aside outside the model's own turn: either a live
            # "reading page X/Y" progress line, or a warning that something
            # (e.g. an attachment) could not be processed.
            if ev.get("level") == "progress":
                self.thinking.set_progress_text(ev.get("text", ""))
            else:
                bubble = self.chat_view.add_tool(
                    tr("chat.attachment_warning_title"), ev.get("text", ""), False)
                record["bubbles"].append(bubble)
        elif etype == "tool_result":
            ctx.get("step_bubbles", {}).pop(ev.get("id"), None)
            self.thinking.start("chat.running")  # back to the model for the next step
            if ev.get("name") == "update_plan":
                return  # plan tool: no chat bubble (Plan view already updated)
            mark = "✓" if ev.get("ok") else "✗"
            tool_bubble = self.chat_view.add_tool(
                f"{ev.get('name')} {mark}", ev.get("output", ""), ev.get("ok", True))
            record["bubbles"].append(tool_bubble)
            folder = ev.get("path") or self.workspace_dir()
            if folder:
                tool_bubble.add_folder_link(str(folder), tr("chat.open_folder"))
            if ev.get("path"):
                record["outputs"].append(ev["path"])
                self.on_file_written(ev["path"])
            # Files produced by a command (e.g. a script that builds a .pptx) —
            # surface the real deliverable, not the generator script.
            for pr in ev.get("produced", []) or []:
                record["outputs"].append(pr)
                self.register_output(pr)
            self.graph_event.emit(self.session_name, ev)
            self._autosave()  # persist after each tool result (crash-safe)
        elif etype == "outputs_removed":
            # Intermediate/generator files were cleaned up — drop them from Output.
            for p in ev.get("paths", []) or []:
                self.output_section.remove(p)
                if p in record.get("outputs", []):
                    record["outputs"].remove(p)
        elif etype == "outputs_added":
            # Deliverables flattened out of a sub-folder into the Output root.
            for p in ev.get("paths", []) or []:
                if p not in record.get("outputs", []):
                    record["outputs"].append(p)
                self.register_output(p)
        elif etype == "reasoning":
            # A reasoning model is "thinking" (Qwen3/DeepSeek-R1 etc.). Relabel the
            # indicator AND stream the reasoning into a collapsed "🧠 Thinking" box
            # so the process is visible without flooding the chat.
            self.thinking.set_label("chat.thinking")
            piece = ev.get("delta", "")
            if piece:
                if ctx.get("reasoning") is None:
                    ctx["reasoning"] = self.chat_view.add_reasoning()
                    record["bubbles"].append(ctx["reasoning"])
                ctx["reasoning"].append_delta(piece)
        elif etype == "plan_set":
            steps = ev.get("steps") or []
            self.on_plan(steps)   # Plan panel (right sidebar)
            # Also show the checklist inline in the chat, updated in place.
            body = _format_plan_steps(steps)
            if ctx.get("plan_bubble") is None:
                ctx["plan_bubble"] = self.chat_view.add_plan(body)
                record["bubbles"].append(ctx["plan_bubble"])
            else:
                ctx["plan_bubble"].set_plain(body)

    def on_plan(self, steps) -> None:
        """Render the current message's step checklist in the Plan panel above the
        Output list. The agent sends the full list on each ``update_plan`` call."""
        self.plan_section.set_steps(steps)

    def _cleanup_turn(self, ctx: Dict[str, Any], ok: bool) -> None:
        """Hook: a turn just ended (``ok`` = finished vs failed). Given the turn
        context, so a tab can promote/discard that turn's isolated output folder.
        No-op in the base."""

    def _session_notes(self) -> str:
        """Extra context folded into the outgoing user message (same layer as
        attachment content) — e.g. Cowork lists files already produced earlier
        in this conversation so the agent can reference/revise them by name
        without the user re-uploading. No-op in the base."""
        return ""

    def _on_permission(self, ctx: Dict[str, Any], action: Dict[str, Any]) -> None:
        # Auto-approves UNLESS this workspace requires confirming commands —
        # a per-workspace Auto-run override (see AppContext.project_confirm_commands),
        # falling back to the global "confirm before running commands" setting.
        # Resolve on THIS turn's worker, never the latest — several turns may
        # be awaiting approval at once.
        if self.ctx.project_confirm_commands():
            from .permission_dialog import PermissionDialog

            approved, _remember = PermissionDialog.ask(action, parent=self)
            ctx["worker"].resolve_permission(approved)
            return
        ctx["worker"].resolve_permission(True)

    def _finalize_turn(self, ctx: Dict[str, Any]) -> None:
        """Merge one turn's new messages into its OWN conversation's history.

        "New" = everything the job appended after this turn's snapshot. Drop any
        system prompt the agent inserted when the history already carries one, so
        two turns started from an empty history don't leave a duplicate system
        message. Merges into ``home_messages`` (the list of the conversation the
        turn started in) so a background turn saves to the right chat even after the
        user switched away. Same object refs are reused, so _delete_turn's id-based
        removal still finds them."""
        home = ctx["home_messages"]
        local = ctx["messages"]
        new = local[ctx["snapshot_len"]:]
        if any(m.get("role") == "system" for m in home):
            new = [m for m in new if m.get("role") != "system"]
        home.extend(new)
        ctx["record"]["messages"] = new

    def _end_turn(self, ctx: Dict[str, Any]) -> None:
        """Shared teardown for a finished/failed turn: merge history, drop the
        worker, release the conversation once nothing else is running for it, and
        refresh the (global) running/capacity indicators."""
        self._finalize_turn(ctx)
        self._active.pop(ctx["worker"], None)
        home_id = ctx.get("home_id")
        if home_id and not any(c.get("home_id") == home_id for c in self._active.values()):
            self._sessions_live.pop(home_id, None)
        # Update the chat-box indicator for the CURRENT view: stop it once the viewed
        # conversation is idle (a live turn's own streaming manages it otherwise, so
        # we don't restart it here and disturb streaming).
        if not self._view_busy():
            self.thinking.stop()
        self.composer.set_running(bool(self._active))   # Stop shows while anything runs
        # Re-evaluate the per-conversation gate: sends dispatch again only when THIS
        # conversation is idle and the global cap allows.
        self.composer.set_busy(self._view_busy() or len(self._active) >= self._max_parallel())

    def _turn_is_live(self, ctx: Dict[str, Any]) -> bool:
        """True when the turn belongs to the currently-viewed conversation."""
        return ctx.get("home_id") == self.session_id and not ctx.get("detached")

    def _save_snapshot(self, session_id: str, messages: List[Dict[str, Any]],
                       title: str, inputs: Optional[List[str]] = None) -> None:
        """Persist a conversation by id (used both to register it in History the
        moment it starts and to save a finished background turn). No-op until it has
        a user message. Never raises into the UI."""
        if not self.ctx.config.history.get("autosave", True):
            return
        if not any(m.get("role") == "user" for m in messages):
            return
        try:
            from ..core.history import save_conversation
            save_conversation(
                self.ctx.config.history_dir(), self.kind, session_id,
                messages, title, inputs=list(inputs or []), outputs=[],
                # Only the CURRENT view knows its project for sure; a background
                # turn's save must not overwrite another conversation's project
                # with whatever the user is viewing now (save_conversation keeps
                # the stored value when '' is passed).
                project_id=self.project_id if session_id == self.session_id else "",
            )
        except Exception:
            pass  # persistence must never disrupt the UI

    def _persist_session(self, ctx: Dict[str, Any]) -> None:
        """Save a BACKGROUND turn's conversation (it isn't the current view, so the
        view-based _autosave can't). Outputs are rebuilt from disk on reopen."""
        self._save_snapshot(ctx["home_id"], ctx["home_messages"],
                            ctx.get("home_title", ""),
                            inputs=ctx.get("record", {}).get("inputs", []))
        self.history_changed.emit()

    def running_session_ids(self):
        """Set of conversation ids that currently have a turn running (for the
        History status markers)."""
        return set(self._sessions_live)

    def _finalize_plan(self, ctx: Dict[str, Any]) -> None:
        """On a successful finish, keep the plan visible with every step ticked
        'done' (so a completed plan can be reviewed) — it is cleared only when the
        NEXT message starts a fresh plan (see _start_turn)."""
        steps = ctx.get("plan_steps")
        if not steps:
            return
        changed = False
        for s in steps:
            if s.get("status") != "done":
                s["status"] = "done"
                changed = True
        if changed:
            self.on_plan(steps)   # re-render (Plan panel for Cowork / preview for Code)
            pb = ctx.get("plan_bubble")
            if pb is not None:
                pb.set_plain(_format_plan_steps(steps))

    # ---- token / cost accounting (shown in the chat, Claude-style) ----------
    def _usage_label(self) -> str:
        return self.title or self.session_id

    def _session_events(self):
        from ..core import usage_tracker as ut
        label = self._usage_label()
        return [e for e in ut.load_events()
                if e.get("source") == self.kind and e.get("label") == label]

    def _usage_snapshot(self) -> Dict[str, int]:
        """Cumulative in/out/cache tokens for THIS conversation so far."""
        snap = {"in": 0, "out": 0, "cache": 0}
        for e in self._session_events():
            snap["in"] += int(e.get("in", 0) or 0)
            snap["out"] += int(e.get("out", 0) or 0)
            snap["cache"] += int(e.get("cache", 0) or 0)
        return snap

    def _session_cost_usd(self) -> float:
        from ..core import model_pricing as mp
        return sum(mp.turn_cost_usd(e.get("model", ""), e.get("in", 0), e.get("out", 0),
                                    self.ctx.config) for e in self._session_events())

    def _show_usage(self, ctx: Dict[str, Any]) -> None:
        """Per-turn footer under the assistant message + the running conversation
        total (bottom-left). Cost uses the Monitoring model-price table and the
        display currency, and auto-updates when the model is switched."""
        from ..core import model_pricing as mp, usage_tracker as ut
        cur = self._usage_snapshot()
        base = ctx.get("usage_base") or {"in": 0, "out": 0, "cache": 0}
        d_in = max(0, cur["in"] - base.get("in", 0))
        d_out = max(0, cur["out"] - base.get("out", 0))
        d_cache = max(0, cur["cache"] - base.get("cache", 0))
        pricing = {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}
        # Condensed format (tight icon+value, single-space separators) — the
        # old 4-space-wide separators made this label wide enough that it got
        # crowded out of the composer's bottom row by the Local-folder button
        # sharing the same row.
        bub = ctx.get("last_assistant")
        if bub is not None and (d_in or d_out):
            turn_usd = mp.turn_cost_usd(self._model, d_in, d_out, self.ctx.config)
            bub.add_usage(f"↓{mp.format_tokens(d_in)} ↑{mp.format_tokens(d_out)} "
                          f"▤{mp.format_tokens(d_in + d_out + d_cache)} "
                          f"{ut.format_cost(turn_usd, pricing)}")
        self._usage_total_lbl.setText(
            f"↓{mp.format_tokens(cur['in'])} ↑{mp.format_tokens(cur['out'])} "
            f"▤{mp.format_tokens(cur['in'] + cur['out'] + cur['cache'])} "
            f"{ut.format_cost(self._session_cost_usd(), pricing)}")

    def _on_finished(self, ctx: Dict[str, Any], result: Dict[str, Any]) -> None:
        live = self._turn_is_live(ctx)
        self._end_turn(ctx)
        self._cleanup_turn(ctx, True)   # promote this turn's output folder, if any
        self.status_message.emit(tr("chatpanel.done", name=tr(f"app.tab.{self.kind}")))
        if live:
            self._finalize_plan(ctx)                       # keep the completed plan shown
            try:
                self._show_usage(ctx)                      # per-turn + conversation token/cost
            except Exception:  # noqa: BLE001 — usage display must never break a turn
                pass
            done = self.chat_view.add_success(tr("chat.done_marker"))   # green done marker in the chat box
            folder = self.workspace_dir()
            if folder:
                done.add_folder_link(str(folder), tr("chat.open_output_folder"))
            ctx["record"]["bubbles"].append(done)
            self._autosave()
        else:
            self._persist_session(ctx)   # save the background conversation by id
        self.turn_finished.emit(result)
        # Notify only once EVERYTHING is done (no running turns, empty queue).
        if not self._active and not self.composer.has_queue():
            self._maybe_notify_teams(result)
        self._drain_queue()

    def _on_failed(self, ctx: Dict[str, Any], err: str) -> None:
        live = self._turn_is_live(ctx)
        self._end_turn(ctx)
        self._cleanup_turn(ctx, False)   # discard this turn's output sandbox
        if live:
            self.chat_view.add_error(err)
            self.graph_event.emit(self.session_name, {"type": "error", "content": err})
            from ..providers.base import is_model_not_found_error

            if is_model_not_found_error(err) and ctx.get("display_text"):
                # A "soft" failure, not a crash: the selected model itself is
                # invalid/unavailable. Put the message back in the composer so
                # the user can just pick a different model in Settings and hit
                # Send again, instead of having to retype the whole prompt.
                self.composer.set_text(ctx["display_text"])
        else:
            self._persist_session(ctx)
        self.status_message.emit(tr("chatpanel.failed", name=tr(f"app.tab.{self.kind}")))
        self.turn_finished.emit({"error": err})
        self._drain_queue()

    def _drain_queue(self) -> None:
        # Start the NEXT queued message only while THIS conversation is idle (one
        # turn at a time here) and the global cap allows. Starting one flips
        # _view_busy() to True, so exactly one runs — the queue drains in order.
        while (not self._view_busy() and len(self._active) < self._max_parallel()
               and self.composer.has_queue()):
            nxt = self.composer.pop_next()
            if not nxt:
                break
            self._start_turn(nxt.get("text", ""), nxt.get("attachments", []))

    def stop(self) -> None:
        if not self._active:
            return
        for w in list(self._active):
            if w.isRunning():
                w.request_stop()
        self.composer.clear_queue()   # don't start anything still waiting
        self.status_message.emit(tr("chatpanel.stopping", name=tr(f"app.tab.{self.kind}")))

    # ---- Teams auto-notify ------------------------------------------
    def _last_assistant_text(self) -> str:
        for m in reversed(self.messages):
            if m.get("role") == "assistant" and m.get("content"):
                return m["content"]
        return ""

    def _maybe_notify_teams(self, result: Dict[str, Any]) -> None:
        teams = self.ctx.config.teams
        notifier = self.ctx.teams_notifier()
        if not (teams.get("notify_on_complete") and notifier.configured):
            return
        summary = self._last_assistant_text() or "Task completed."
        facts = {"Session": self.session_name, "Model": self.ctx.config.model_label()}
        wd = self.workspace_dir()
        if wd:
            facts["Folder"] = str(wd)
        if result.get("error"):
            facts["Status"] = "Error"

        def job(worker: AgentWorker):
            ok, detail = notifier.send(f"Cowork {self.session_name} — task done", summary[:1200], facts)
            return {"ok": ok, "detail": detail}

        w = AgentWorker(job)
        w.finished_ok.connect(lambda r: self.status_message.emit(r.get("detail", "")))
        self._teams_worker = w
        w.start()

    # ---- persistence -------------------------------------------------
    def _autosave(self) -> None:
        if not self.ctx.config.history.get("autosave", True):
            return
        if not any(m.get("role") == "user" for m in self.messages):
            return
        try:
            from ..core.history import save_conversation
            path = save_conversation(
                self.ctx.config.history_dir(), self.kind, self.session_id,
                self.messages, self.title,
                inputs=self.input_section.paths(),
                outputs=self.output_section.paths(),
                project_id=self.project_id,
            )
            # Remember this as the session to restore next launch (crash-safe).
            last = self.ctx.config.data.setdefault("last_session", {})
            if last.get(self.kind) != str(path):
                last[self.kind] = str(path)
                self.ctx.save()
        except Exception:
            pass  # autosave must never disrupt the UI

    def _busy(self) -> bool:
        """True while any turn is still running in this tab (any conversation)."""
        return bool(self._active)

    def _view_busy(self) -> bool:
        """True while the CURRENTLY-VIEWED conversation has a turn running."""
        return any(c.get("home_id") == self.session_id for c in self._active.values())

    def _sync_indicators(self) -> None:
        """Reflect the CURRENT conversation's agent status in the chat box + composer.
        Switching chats, or hitting History → Refresh, shows whether THIS chat is
        still processing (a background turn) or idle."""
        if self._view_busy():
            self.thinking.start("chat.running")   # this conversation is still working
        else:
            self.thinking.stop()
        self.composer.set_running(bool(self._active))   # Stop shows while anything runs
        self.composer.set_busy(self._view_busy() or len(self._active) >= self._max_parallel())

    def refresh_status(self) -> None:
        """Public: re-sync the on-screen agent status for the current conversation
        (used by the History Refresh button)."""
        self._sync_indicators()

    def _max_parallel(self) -> int:
        """Unlimited concurrent turns — no cap (the old Settings limit was removed).
        A large sentinel keeps the queue logic intact without ever gating."""
        return 100000

    def active_workers(self) -> List[AgentWorker]:
        """Workers for turns still running (used to stop them all on quit)."""
        return list(self._active)

    def _detach_live_turns(self) -> None:
        """Before switching away from the current conversation, turn its running
        turns into background jobs: they stop rendering into the (about-to-be-
        cleared) transcript but keep running and save to their own conversation."""
        for c in self._active.values():
            if c.get("home_id") == self.session_id:
                c["detached"] = True
                c["assistant"] = None   # its bubbles are about to be cleared

    def _running_ctx_for(self, session_id: str) -> Optional[Dict[str, Any]]:
        """The in-progress turn's context for a conversation (one at a time), or None."""
        for c in self._active.values():
            if c.get("home_id") == session_id:
                return c
        return None

    def _reattach_running_turn(self, ctx: Dict[str, Any]) -> None:
        """Re-render an in-progress turn into the current transcript and re-attach it
        so it keeps streaming live — used when reopening a running conversation, so
        the user sees the CURRENT task (message + steps so far + live plan), not just
        the last saved state."""
        record = ctx["record"]
        record["bubbles"] = []   # the old bubbles were cleared on the view switch
        # 1) the user's message that is being processed
        ub = self.chat_view.add_user(ctx.get("display_text") or "(attachment)")
        record["bubbles"].append(ub)
        # 2) steps already completed this turn (assistant text / tool results); found
        #    by identity after the user message (a system prompt may sit before it).
        #    Snapshot the list — the worker thread may still be appending to it.
        msgs = list(ctx.get("messages", []))
        ui = next((i for i, m in enumerate(msgs) if m is ctx.get("user_msg")), -1)
        for m in (msgs[ui + 1:] if ui >= 0 else []):
            role = m.get("role")
            if role == "assistant" and (m.get("content") or "").strip():
                b = self.chat_view.add_assistant(self.assistant_title())
                b.set_markdown(m["content"])
                record["bubbles"].append(b)
            elif role == "tool":
                b = self.chat_view.add_tool(m.get("name", "tool"), m.get("content", ""), True)
                record["bubbles"].append(b)
        # 3) the live plan checklist (if any) — inline, expandable
        steps = ctx.get("plan_steps") or []
        if steps:
            self.on_plan(steps)
            pb = self.chat_view.add_plan(_format_plan_steps(steps))
            record["bubbles"].append(pb)
            ctx["plan_bubble"] = pb
        # 4) the partial answer of the step currently streaming — re-attach so new
        #    deltas keep appending to this bubble.
        ctx["assistant"] = None
        ctx["reasoning"] = None
        if (ctx.get("partial") or "").strip():
            ab = self.chat_view.add_assistant(self.assistant_title())
            ab.set_markdown(ctx["partial"])
            record["bubbles"].append(ab)
            ctx["assistant"] = ab
        # 5) live again → future events render here
        ctx["detached"] = False
        self.chat_view.scroll_to_bottom()

    def new_session(self) -> None:
        from ..core.history import new_session_id

        # Allowed while work is running: current turns keep going in the background.
        self._detach_live_turns()
        self.messages = []
        self.session_id = new_session_id()
        self.title = ""
        self.turns = []
        self.chat_view.clear()
        self.composer.clear_queue()
        self.composer.reset_input()   # clear leftover text / "Attached: …" hint
        self.plan_section.clear()
        self.input_section.clear()
        self.output_section.clear()
        self.graph_event.emit(self.session_name, {"type": "reset"})
        self._sync_indicators()
        self.history_changed.emit()   # current view changed → refresh History highlight

    def load_conversation(self, conv: Dict[str, Any]) -> None:
        """Switch the view to a stored conversation. Allowed while work is running —
        the current turns keep going in the background."""
        sid = conv.get("session_id") or self.session_id
        # Clicking the conversation you're already viewing while it has a running
        # turn must NOT tear down its live rendering — just no-op.
        if sid == self.session_id and self._view_busy():
            return
        self._detach_live_turns()
        self.session_id = sid
        self.title = conv.get("title", "")
        self.project_id = conv.get("project_id", "") or "default"
        # If this conversation still has a turn running in the background, attach to
        # its LIVE message list (not a stale disk copy) so the two never race on save.
        if sid in self._sessions_live:
            self.messages = self._sessions_live[sid]
        else:
            self.messages = list(conv.get("messages", []))
        self.turns = []
        self.chat_view.clear()
        self.composer.clear_queue()
        self.composer.reset_input()   # clear leftover text / "Attached: …" hint
        self.plan_section.clear()
        self.input_section.clear()
        self.output_section.clear()
        self.graph_event.emit(self.session_name, {"type": "reset"})
        for m in self.messages:
            role = m.get("role")
            if role == "user":
                self.chat_view.add_user(m.get("content", ""))
                self.graph_event.emit(self.session_name, {"type": "user", "content": m.get("content", "")})
            elif role == "assistant":
                if m.get("content"):
                    self.chat_view.add_assistant(self.assistant_title()).set_markdown(m["content"])
                    self.graph_event.emit(self.session_name, {"type": "assistant_done", "content": m["content"]})
                for tc in m.get("tool_calls", []) or []:
                    self.graph_event.emit(self.session_name, {
                        "type": "tool_proposed", "name": tc.get("name", ""),
                        "args": tc.get("arguments", {}),
                        "preview": {"text": str(tc.get("arguments", {}))},
                    })
            elif role == "tool":
                self.chat_view.add_tool(m.get("name", "tool"), m.get("content", ""), True)
                self.graph_event.emit(self.session_name, {
                    "type": "tool_result", "name": m.get("name", ""),
                    "ok": True, "output": m.get("content", ""),
                })
        # Restore the Input/Output file lists too.
        for p in conv.get("inputs", []):
            self.input_section.add(p)
        for p in conv.get("outputs", []):
            self.output_section.add(p)
        # If this conversation has a turn running in the background, re-render the
        # in-progress task and re-attach it so it keeps streaming live here.
        running = self._running_ctx_for(sid)
        if running is not None:
            self._reattach_running_turn(running)
        elif self.messages:
            # A past (already finished) session — surface a link to its output
            # folder even though the live "done" marker isn't replayed.
            folder = self.workspace_dir()
            if folder:
                marker = self.chat_view.add_status(tr("chat.session_folder_marker"))
                marker.add_folder_link(str(folder), tr("chat.open_folder_short"))
        # Jump to the newest message after the transcript is rebuilt.
        self.chat_view.scroll_to_bottom()
        self._sync_indicators()
        self.history_changed.emit()   # current view changed → refresh History highlight
