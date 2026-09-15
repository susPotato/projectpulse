"""Cowork tab — chat with the local Internal Agent."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..config import PROVIDER_LABELS
from ..core import agent_roles
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .chat_panel import ChatPanel
from .icons import icon

_FOLDER_LBL_MAX_CHARS = 42  # keep the composer's bottom row from crowding out Agent/Send


class CoworkTab(ChatPanel):
    def __init__(self, ctx: AppContext):
        super().__init__(ctx, "cowork", "Cowork", placeholder_key="composer.placeholder_cowork")

        self._title_lbl = QLabel()
        self._title_lbl.setStyleSheet("font-weight:700; font-size:15px;")
        self.model_lbl = QLabel("")
        self.model_lbl.setObjectName("hint")

        self.skills_btn = QPushButton()
        self.skills_btn.setIcon(icon("book"))
        self.skills_btn.clicked.connect(self._open_skills_manager)

        self._new_btn = QPushButton()
        self._new_btn.setIcon(icon("new"))
        self._new_btn.clicked.connect(self.new_session)

        self.toolbar_layout.addWidget(self._title_lbl)
        self.toolbar_layout.addWidget(self.model_lbl)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(self.skills_btn)
        self.toolbar_layout.addWidget(self._new_btn)

        # Where Cowork saves its deliverables — the project's sandbox workspace
        # (or, for a folder picked via the button, that folder). Only the
        # folder NAME shows beside the button (not the full path as a
        # link/hint text) — the full path is still available as a tooltip.
        self.folder_btn = QPushButton()
        self.folder_btn.setIcon(icon("folder"))
        self.folder_btn.clicked.connect(self._pick_output_folder)
        self.folder_lbl = QLabel("")
        self.folder_lbl.setObjectName("hint")
        folder_box = QWidget()
        _fbl = QHBoxLayout(folder_box)
        _fbl.setContentsMargins(0, 0, 0, 0)
        _fbl.setSpacing(6)
        _fbl.addWidget(self.folder_btn)
        _fbl.addWidget(self.folder_lbl)   # folder name BESIDE the button
        # Which project (workspace) the CURRENT thread belongs to. Cowork now
        # always lives INSIDE the Workspace screen for a single selected project,
        # so the project name beside the composer is redundant chrome — the label
        # is kept as a hidden member (its text still tracks the active project for
        # tooltips/tests) but no longer added to the layout.
        self.project_lbl = QLabel("")
        self.project_lbl.setObjectName("hint")
        self.project_lbl.setVisible(False)
        self.composer.add_bottom_left(folder_box)

        # Per-workspace "Auto-run" toggle (auto-approve commands in THIS
        # workspace) — sits next to the routing toggle the base class added.
        from .routing_toggle import AutoRunToggle
        self.autorun_toggle = AutoRunToggle(self.ctx)
        self.composer.add_bottom_right(self.autorun_toggle)

        self.refresh_header()
        # Start watching the output folder for new files
        wd = self.workspace_dir()
        if wd:
            self._start_watching(wd)
        # Cowork shows ONLY the final deliverables, refreshed from disk once a turn
        # succeeds (see _cleanup_turn) — never intermediate files or a folder name.
        on_language_changed(self._retranslate)

    def _retranslate(self) -> None:
        self._title_lbl.setText(tr("cowork.title"))
        self.skills_btn.setText(tr("cowork.skills_btn"))
        self.skills_btn.setToolTip(tr("cowork.skills_tooltip"))
        self._new_btn.setText(tr("cowork.new_chat"))
        self.folder_btn.setText(tr("cowork.pick_folder_btn"))
        self.folder_btn.setToolTip(tr("cowork.pick_folder_tooltip"))
        self._apply_output_folder_label()

    # ---- output folder --------------------------------------------------
    def _pick_output_folder(self) -> None:
        start = str(self._session_output_dir())
        chosen = QFileDialog.getExistingDirectory(self, tr("cowork.pick_folder_title"), start)
        if not chosen:
            return
        if self.project_id not in ("", "default"):
            # Inside a project, the picked folder becomes THAT project's
            # workspace (its sandbox + shared-knowledge root) — not the
            # global default-output setting.
            from ..core.projects import save_project

            project = self._project()
            if project is not None:
                project.output_dir = chosen
                save_project(project)
        else:
            self.ctx.config.cowork["output_dir"] = chosen
            self.ctx.save()
        self._apply_output_folder_label()
        self._refresh_outputs_from_disk()

    def _apply_output_folder_label(self) -> None:
        full = str(self._session_output_dir())
        # Only the folder NAME is shown beside the button — the full path
        # (still available on hover) reads as noisy clutter for a value the
        # user just picked and already knows the location of.
        name = Path(full).name or full
        if len(name) > _FOLDER_LBL_MAX_CHARS:
            name = name[:_FOLDER_LBL_MAX_CHARS - 1] + "…"
        self.folder_lbl.setText(name)
        self.folder_lbl.setToolTip(full)
        # Text still tracks the active project (tooltip/tests read it), but the
        # label stays hidden — see its creation note above.
        project = self._project()
        if project is not None:
            self.project_lbl.setText(tr("cowork.project_label", name=project.name))
            self.project_lbl.setToolTip(tr("cowork.project_tooltip", name=project.name))
        else:
            self.project_lbl.setText("")
        # Start watching the output folder for new files
        wd = self.workspace_dir()
        if wd:
            self._start_watching(wd)

    # ---- project (workspace) --------------------------------------------
    def _project(self):
        from ..core.projects import load_project

        return load_project(self.project_id)

    def set_project(self, project_id: str) -> None:
        """Assign the CURRENT thread to a project (called by the Workspace
        screen's 'New chat in project'). Output folder + shared context follow.

        Also makes this the ACTIVE workspace for per-workspace mode resolution
        and refreshes the routing/auto-run toggles to show THIS workspace's
        modes (so switching projects switches the visible modes)."""
        self.project_id = project_id or "default"
        self.ctx.active_project_id = self.project_id
        for t in (getattr(self, "routing_toggle", None), getattr(self, "autorun_toggle", None)):
            if t is not None:
                t.refresh()
        self._apply_output_folder_label()
        self._refresh_outputs_from_disk()

    def project_knowledge_dir(self):
        """A non-default project's shared-knowledge folder — its workspace
        ROOT. This is now the SAME folder every thread of the project already
        saves its deliverables into (no more per-session sub-folder — see
        _session_output_dir), so ChatPanel._augment's equality check skips the
        separate '[Project files]' scan and the '[Workspace files]' scan alone
        already covers everything in one shared place, Claude-Projects style."""
        if self.project_id in ("", "default"):
            return None
        project = self._project()
        return project.workspace_dir() if project else None

    # Generator/helper scripts are never a Cowork deliverable — keep them out of
    # the Output list entirely (only the final file is shown).
    _INTERMEDIATE_EXTS = {".py", ".pyw", ".js", ".mjs", ".cjs", ".ts",
                          ".sh", ".bat", ".ps1", ".rb", ".pl"}

    def assistant_title(self) -> str:
        return tr("cowork.assistant_title")

    def _session_output_dir(self):
        """Where this session's deliverables are saved.

        Default: a sub-folder of the configured Output root named by the
        session id (a timestamp — never the chat content), so same-named
        outputs from different sessions don't overwrite each other.

        Once a folder has been EXPLICITLY specified — either "Chọn thư mục
        khác" (see _pick_output_folder) for the default project, or a
        project's own workspace folder — files are saved DIRECTLY into it
        instead, no auto-created per-session sub-folder: the Output box/link
        always points at exactly that folder, and every thread that shares it
        sees the same files (deliverables AND project knowledge together,
        Claude-Projects style). Running turns still get isolated '.turns/<id>'
        sandboxes (unique across every session in this tab — see
        _turn_output_dir/_turn_seq — moved up on success by
        _promote_turn_outputs, which de-dupes by name via _unique_path), so
        concurrent turns/threads sharing one folder never clash.

        Threads of a NON-default project are sandboxed inside that project's
        own workspace: the agent's tools are confined there (ToolContext
        rejects any path escape), and it's a single shared folder for the
        whole project — not one sub-folder per thread."""
        if self.project_id not in ("", "default"):
            project = self._project()
            if project is not None:
                return project.workspace_dir()
        custom = (self.ctx.config.cowork.get("output_dir") or "").strip()
        if custom:
            return Path(custom).expanduser()
        return self.ctx.config.cowork_output_dir() / self.session_id

    def workspace_dir(self):
        return self._session_output_dir()

    def _turn_output_dir(self, turn_id: str):
        """Each running turn writes into its own '.turns/<id>' sandbox so parallel
        turns never clobber each other's files (run_cowork's cleanup diffs the
        folder before/after, which would misfire on a shared dir). On success the
        finished turn's deliverables are moved up to the session root — see
        _cleanup_turn. The dot-prefixed folder is ignored by the Output list
        (which shows only top-level files)."""
        return self._session_output_dir() / ".turns" / turn_id

    def _is_intermediate_output(self, path: str) -> bool:
        from pathlib import Path
        return Path(path).suffix.lower() in self._INTERMEDIATE_EXTS

    def _existing_output_names(self):
        """Deliverables already sitting in this session's output folder (from
        earlier, already-finished turns) — the current turn writes into its own
        '.turns/<id>' sandbox, so this never includes its own in-flight files."""
        try:
            return sorted(p.name for p in self._session_output_dir().iterdir()
                          if p.is_file() and not p.name.startswith(".")
                          and not self._is_intermediate_output(str(p)))
        except OSError:
            return []

    def _session_notes(self) -> str:
        # Lets the agent (and the user, without re-uploading) reference/revise a
        # file it made earlier in this same conversation — e.g. "sửa lại tiêu đề
        # trong file báo cáo vừa tạo" — since it can read_file/edit_file/write_file
        # it directly by the exact name listed here.
        names = self._existing_output_names()
        if not names:
            return ""
        listing = ", ".join(names)
        return (
            "[Session context] Files already created earlier in this conversation's output "
            "folder — read/revise them directly by their exact name with read_file/edit_file/"
            "write_file; the user does NOT need to re-upload them if they refer to \"the file "
            f"I made\" / \"file vừa tạo\" or similar: {listing}"
        )

    def _promote_turn_outputs(self, turn_dir, record, session_root) -> None:
        """Move a finished turn's files from its '.turns/<id>' sandbox up to its
        conversation's Output root (``session_root`` — the turn's HOME session, so a
        background turn lands in the right chat even after the user switched away),
        then remove the sandbox. run_cowork already flattened and stripped
        '.scratch'/generator scripts, so the sandbox root holds the final file(s).
        Every top-level file is moved (no extension filtering) so a file the user
        genuinely asked for is never dropped before the sandbox is removed — the
        Output list itself decides what to *show* (see _refresh_outputs_from_disk)."""
        import shutil
        from pathlib import Path

        from ..core.chat_agent import _unique_path

        turn_dir = Path(turn_dir)
        session_root = Path(session_root)
        try:
            if not turn_dir.is_dir() or turn_dir.resolve() == session_root.resolve():
                return
        except OSError:
            return
        session_root.mkdir(parents=True, exist_ok=True)
        remap = {}
        try:
            files = [p for p in turn_dir.iterdir() if p.is_file()]
        except OSError:
            files = []
        for p in files:
            try:
                dest = _unique_path(session_root, p.name)
                p.replace(dest)
                remap[str(p)] = str(dest)
            except OSError:
                pass
        shutil.rmtree(turn_dir, ignore_errors=True)
        # Keep this turn's recorded outputs pointing at the moved files so
        # deleting the message later still finds and removes them.
        if remap and record is not None:
            record["outputs"] = [remap.get(p, p) for p in record.get("outputs", [])]

    # ---- Output box: only the final, successful deliverables ----------
    def register_output(self, path: str) -> None:
        # Suppress live/intermediate updates during a run — the Output box is
        # rebuilt from the surviving files once the turn succeeds (see below).
        return

    def on_file_written(self, path: str) -> None:
        return  # no live file updates in Cowork

    def _refresh_outputs_from_disk(self) -> None:
        """Show only the final deliverable files now sitting in the session folder
        (no scripts/intermediate files, no hidden/`.scratch`, no folders)."""
        self.output_section.clear()
        try:
            files = sorted((p for p in self._session_output_dir().iterdir() if p.is_file()),
                           key=lambda p: p.name)
        except OSError:
            return
        for p in files:
            if not p.name.startswith(".") and not self._is_intermediate_output(str(p)):
                self.output_section.add(str(p))
        self.output_changed.emit(str(self._session_output_dir()))

    def new_session(self) -> None:
        # The new thread stays in the CURRENT project (Claude-style).
        super().new_session()
        # Refresh the project/folder labels + watch the new session's folder.
        self._apply_output_folder_label()

    def load_conversation(self, conv) -> None:
        super().load_conversation(conv)    # restores this conversation's project_id
        self._refresh_outputs_from_disk()  # show this session's deliverables from disk
        # Refresh the project/folder labels + watch this conversation's folder.
        self._apply_output_folder_label()

    def refresh_header(self) -> None:
        cfg = self.ctx.config
        label = PROVIDER_LABELS.get(cfg.active_provider, cfg.active_provider)
        self.model_lbl.setText(f"{label} · {cfg.model_label()}")
        self._apply_output_folder_label()  # picks up edits made via Settings too

    def build_job(self, text: str, messages, out_dir):
        # Each turn writes into its OWN isolated folder (out_dir) and works on its
        # OWN message list, so several turns can run in parallel without clobbering
        # each other's files or history. Deliverables are moved up to the session
        # Output root when the turn finishes (see _cleanup_turn).
        output_dir = out_dir or self._session_output_dir()
        title = self.title
        project_id = self.project_id
        # Captured at submit time (UI thread): the Admin-defined agent
        # preset's instructions, if one is selected in the Agent picker.
        agent_prompt = self.admin_agent_prompt()

        def job(worker: AgentWorker):
            from ..core.chat_agent import run_cowork
            from ..core.projects import load_project, project_context_text

            provider = self.build_provider()  # this tab's selected agent/model
            # 🔌 MCP Layer: every tool source flows through MCP now — the
            # external servers configured in Settings AND Microsoft 365 (a
            # built-in MCP server auto-registered while signed in, see
            # AppContext._ms365_builtin_connection / mcp_servers/ms365_server.py).
            extra_tools, extra_exec = self.ctx.build_mcp_tools()
            # Shared project instructions (Claude-Projects style) — refreshed
            # each turn so edits in the Workspace screen apply immediately.
            proj_ctx = project_context_text(load_project(project_id))
            if agent_prompt:
                proj_ctx = f"{proj_ctx}\n\n{agent_prompt}" if proj_ctx else agent_prompt
            # Permission Management (Sandbox Security Layer): off by default —
            # matches the pre-existing auto-run behavior. Now resolved PER
            # WORKSPACE: this project's Auto-run override wins, else the global
            # "confirm before running commands" setting (project_confirm_commands).
            gate = None
            if self.ctx.project_confirm_commands():
                gate = worker.new_gate("confirm", agent_role=agent_roles.COWORK)
            run_cowork(provider, messages, output_dir, worker.emit_event,
                       worker.is_cancelled, title=title,
                       extra_tools=extra_tools, extra_executor=extra_exec,
                       project_context=proj_ctx, security_config=self.ctx.config,
                       gate=gate)
            return {"messages": messages, "turn_dir": str(output_dir)}

        return job

    def _cleanup_turn(self, ctx, ok) -> None:
        """A turn ended (successfully or not): promote whatever files it
        produced up to ITS conversation's Output root, THEN discard the
        (by then empty, or intermediate-only) sandbox.

        This runs the SAME promotion on failure as on success — a turn can
        genuinely create a deliverable (e.g. save_file succeeds) and THEN hit
        an unrelated error later in the same turn (a follow-up tool call, a
        network drop, a rate limit that didn't recover) which raises and
        marks the whole turn as failed. Discarding the sandbox unconditionally
        in that case would silently delete a file the user actually got —
        exactly the "file đã tạo bị xóa" bug. Promoting first is always safe:
        an empty/intermediate-only sandbox just promotes zero files.

        Refresh the visible Output list only when that conversation is the
        one on screen."""
        turn_dir = ctx.get("out_dir")
        home_root = ctx.get("home_out_root")
        live = ctx.get("home_id") == self.session_id and not ctx.get("detached")
        if turn_dir and home_root:
            self._promote_turn_outputs(turn_dir, ctx.get("record"), home_root)
        elif turn_dir:
            import shutil
            shutil.rmtree(turn_dir, ignore_errors=True)
        if live:
            self._refresh_outputs_from_disk()
