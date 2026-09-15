"""Agents Admin — Monitoring tab visible to the Admin role ONLY.

CRUD over the shared admin-agent catalog (``core/admin_agents.py``): each
agent has a name, an app function from a fixed droplist (search / monitor /
cowork / graphrag / schedule / security), optional extra instructions and a
model (blank = the machine's Settings model). Saved straight into the shared
accounts folder, so every machine pointed at the same share picks changes up
automatically (OneDrive/network sync) — non-admin machines only ever READ the
catalog (their pickers in Cowork / Schedule Task list the enabled agents).

The "Check" button probes each agent's effective provider (``check_agent``)
and shows an operational-status column (🟢 reachable / 🔴 error) separate from
the Enabled config flag.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..config import PROVIDER_LABELS
from ..core import admin_agents, preview_ai
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import icon, dot_icon, DOT_GREEN, DOT_RED, DOT_AMBER, DOT_GREY

_PROVIDER_DEFAULT = ""   # "" = each machine's own active provider (unchanged default)


class AgentEditDialog(QDialog):
    """Add/Edit one admin agent. The provider/model pickers are drop-lists,
    not free text — ``provider_combo`` offers the app's built-in providers
    (plus "machine default"), ``model_combo`` offers that provider's REAL
    model list once fetched via "Load models" (same on-demand fetch the
    Preview tab and Settings' own "Load" button use) — editable so an admin
    can still pin an exact model string that isn't in the fetched list yet."""

    def __init__(self, parent=None, ctx: Optional[AppContext] = None,
                 agent: Optional[admin_agents.AdminAgent] = None,
                 default_model_hint: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        self._existing = agent
        self._live_models: Dict[str, List[str]] = {}
        self._workers: List[AgentWorker] = []
        self.setWindowTitle(tr("agents_admin.edit_title") if agent
                            else tr("agents_admin.add_title"))
        self.resize(420, 400)
        form = QFormLayout(self)
        self.name_edit = QLineEdit(agent.name if agent else "")
        form.addRow(tr("agents_admin.f_name"), self.name_edit)
        self.kind_combo = QComboBox()
        for kind in admin_agents.TASK_KINDS:
            self.kind_combo.addItem(tr(f"agents_admin.kind.{kind}"), kind)
        if agent:
            idx = self.kind_combo.findData(agent.task_kind)
            if idx >= 0:
                self.kind_combo.setCurrentIndex(idx)
        form.addRow(tr("agents_admin.f_kind"), self.kind_combo)
        self.prompt_edit = QPlainTextEdit(agent.prompt if agent else "")
        self.prompt_edit.setPlaceholderText(tr("agents_admin.f_prompt_placeholder"))
        self.prompt_edit.setMaximumHeight(110)
        form.addRow(tr("agents_admin.f_prompt"), self.prompt_edit)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem(tr("agents_admin.provider_default"), _PROVIDER_DEFAULT)
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, key)
        if agent and agent.provider:
            idx = self.provider_combo.findData(agent.provider)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.currentIndexChanged.connect(self._refresh_model_combo)
        form.addRow(tr("agents_admin.f_provider"), self.provider_combo)

        model_row = QHBoxLayout()
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        if agent and agent.model:
            self.model_combo.addItem(agent.model)
        self.model_combo.setEditText(agent.model if agent else "")
        self.model_combo.lineEdit().setPlaceholderText(
            tr("agents_admin.f_model_placeholder", model=default_model_hint or "—"))
        self.load_models_btn = QPushButton()
        self.load_models_btn.setIcon(icon("download"))
        self.load_models_btn.setToolTip(tr("agents_admin.load_models_tooltip"))
        self.load_models_btn.clicked.connect(self._load_live_models)
        self.load_models_btn.setEnabled(self.ctx is not None)
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.load_models_btn)
        form.addRow(tr("agents_admin.f_model"), model_row)

        self.enabled_chk = QCheckBox(tr("agents_admin.f_enabled"))
        self.enabled_chk.setChecked(agent.enabled if agent else True)
        form.addRow("", self.enabled_chk)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _load_live_models(self) -> None:
        if self.ctx is None:
            return
        self.load_models_btn.setEnabled(False)
        ctx = self.ctx

        def job(_worker: AgentWorker):
            return preview_ai.fetch_live_models(ctx)

        def done(result: dict) -> None:
            self.load_models_btn.setEnabled(True)
            self._live_models = result or {}
            self._refresh_model_combo()
            if not self._live_models:
                QMessageBox.information(self, tr("agents_admin.add_title"),
                                        tr("agents_admin.load_models_empty"))

        def failed(err: str) -> None:
            self.load_models_btn.setEnabled(True)
            QMessageBox.warning(self, tr("agents_admin.add_title"), err)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._workers.append(w)
        w.start()

    def _refresh_model_combo(self) -> None:
        provider_key = self.provider_combo.currentData()
        current_text = self.model_combo.currentText().strip()
        models = self._live_models.get(provider_key, []) if provider_key else []
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setEditText(current_text)
        self.model_combo.blockSignals(False)

    def result_fields(self) -> Dict[str, str]:
        return {
            "name": self.name_edit.text().strip(),
            "task_kind": self.kind_combo.currentData(),
            "prompt": self.prompt_edit.toPlainText().strip(),
            "provider": self.provider_combo.currentData() or "",
            "model": self.model_combo.currentText().strip(),
            "enabled": self.enabled_chk.isChecked(),
        }


class AgentsAdminTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        # Last operational-health result per agent_id → (ok, message). Populated
        # on demand by the "Check" button (see _check_all); survives refresh().
        self._status: Dict[str, tuple] = {}
        self._check_workers: List[AgentWorker] = []

        root = QVBoxLayout(self)
        self._hint = QLabel("")
        self._hint.setObjectName("hint")
        self._hint.setWordWrap(True)
        root.addWidget(self._hint)

        self.table = QTableWidget(0, 6)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSortingEnabled(True)
        root.addWidget(self.table, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton()
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add)
        self.edit_btn = QPushButton()
        self.edit_btn.setIcon(icon("edit"))
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn = QPushButton()
        self.del_btn.setIcon(icon("trash"))
        self.del_btn.clicked.connect(self._delete)
        self.check_btn = QPushButton()
        self.check_btn.setIcon(icon("check"))
        self.check_btn.clicked.connect(self._check_all)
        for b in (self.add_btn, self.edit_btn, self.del_btn):
            btns.addWidget(b)
        btns.addStretch(1)
        btns.addWidget(self.check_btn)
        root.addLayout(btns)

        on_language_changed(self._retranslate)
        self._retranslate()

    # ---- storage ---------------------------------------------------------
    def _dir(self):
        return admin_agents.agents_admin_dir(self.ctx.config.shared_dir)

    def _default_model_hint(self) -> str:
        conf = self.ctx.config.provider_conf(self.ctx.config.active_provider)
        return conf.get("model", "")

    def _selected_agent(self) -> Optional[admin_agents.AdminAgent]:
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        from PySide6.QtCore import Qt

        return admin_agents.load_agent(item.data(Qt.UserRole), self._dir())

    # ---- CRUD -------------------------------------------------------------
    def _add(self) -> None:
        dlg = AgentEditDialog(self, ctx=self.ctx, default_model_hint=self._default_model_hint())
        if not dlg.exec():
            return
        fields = dlg.result_fields()
        if not fields["name"]:
            return
        agent = admin_agents.new_agent(
            fields["name"], fields["task_kind"], fields["prompt"],
            provider=fields.get("provider", ""), model=fields["model"],
            updated_by=(getattr(self.ctx, "account", None).username if getattr(self.ctx, "account", None) else ""))
        agent.enabled = bool(fields["enabled"])
        admin_agents.save_agent(agent, self._dir())
        self.refresh()

    def _edit(self) -> None:
        agent = self._selected_agent()
        if agent is None:
            return
        dlg = AgentEditDialog(self, ctx=self.ctx, agent=agent,
                              default_model_hint=self._default_model_hint())
        if not dlg.exec():
            return
        fields = dlg.result_fields()
        if not fields["name"]:
            return
        from datetime import datetime

        agent.name = fields["name"]
        agent.task_kind = fields["task_kind"]
        agent.prompt = fields["prompt"]
        agent.provider = fields.get("provider", "")
        agent.model = fields["model"]
        agent.enabled = bool(fields["enabled"])
        agent.updated = datetime.now().isoformat(timespec="seconds")
        agent.updated_by = (getattr(self.ctx, "account", None).username if getattr(self.ctx, "account", None) else "")
        admin_agents.save_agent(agent, self._dir())
        self.refresh()

    def _delete(self) -> None:
        agent = self._selected_agent()
        if agent is None:
            return
        if QMessageBox.question(
                self, tr("agents_admin.delete_title"),
                tr("agents_admin.delete_confirm", name=agent.name)) != QMessageBox.Yes:
            return
        admin_agents.delete_agent(agent.agent_id, self._dir())
        self.refresh()

    # ---- view --------------------------------------------------------------
    def _status_cell(self, agent_id: str) -> tuple:
        """(status_icon | None, display_text, tooltip) for the operational-status
        column — a colored LED dot instead of the old 🟢/🔴 emoji."""
        res = self._status.get(agent_id)
        if res is None:
            return None, tr("agents_admin.status_unchecked"), tr("agents_admin.status_unchecked_tip")
        ok, msg = res
        if msg == "checking":
            return dot_icon(DOT_AMBER), tr("agents_admin.status_checking"), ""
        ic = dot_icon(DOT_GREEN if ok else DOT_RED)
        return ic, (tr("agents_admin.status_ok") if ok else tr("agents_admin.status_bad")), msg

    def refresh(self) -> None:
        from PySide6.QtCore import Qt

        # Make sure the built-in in-app Help assistant exists, so the Admin can
        # manage its provider/model here (the floating Help widget uses it).
        admin_agents.ensure_help_agent(self._dir())
        agents = admin_agents.list_agents(self._dir())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(agents))
        default_model = self._default_model_hint()
        for row, agent in enumerate(agents):
            if agent.model:
                provider_lbl = PROVIDER_LABELS.get(agent.provider, "") if agent.provider else ""
                model = f"{provider_lbl} — {agent.model}" if provider_lbl else agent.model
            else:
                model = tr("agents_admin.default_model", model=default_model or "—")
            status_icon, status_text, status_tip = self._status_cell(agent.agent_id)
            cells = [agent.name, tr(f"agents_admin.kind.{agent.task_kind}"),
                     model, "", status_text, agent.updated]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 0:
                    item.setData(Qt.UserRole, agent.agent_id)
                if col == 3:   # Enabled — green check / grey minus icon (no emoji)
                    item.setIcon(icon("check", color=DOT_GREEN) if agent.enabled
                                 else icon("minus", color=DOT_GREY))
                if col == 4:
                    if status_icon:
                        item.setIcon(status_icon)
                    if status_tip:
                        item.setToolTip(status_tip)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)

    def _check_all(self) -> None:
        """Health-check every agent's effective provider off the UI thread and
        update the Status column with the result (🟢 reachable / 🔴 error)."""
        agents = admin_agents.list_agents(self._dir())
        if not agents:
            return
        for a in agents:
            self._status[a.agent_id] = (False, "checking")
        self.check_btn.setEnabled(False)
        self.refresh()
        ctx = self.ctx

        def job(_worker: AgentWorker) -> dict:
            return {a.agent_id: admin_agents.check_agent(ctx, a) for a in agents}

        def done(result: dict) -> None:
            self.check_btn.setEnabled(True)
            self._status.update(result or {})
            self.refresh()

        def failed(err: str) -> None:
            self.check_btn.setEnabled(True)
            for a in agents:
                self._status[a.agent_id] = (False, err[:200])
            self.refresh()

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._check_workers.append(w)
        w.start()

    def _retranslate(self) -> None:
        self._hint.setText(tr("agents_admin.hint"))
        self.table.setHorizontalHeaderLabels([
            tr("agents_admin.col_name"), tr("agents_admin.col_kind"),
            tr("agents_admin.col_model"), tr("agents_admin.col_enabled"),
            tr("agents_admin.col_status"), tr("agents_admin.col_updated"),
        ])
        self.add_btn.setText(tr("agents_admin.add_btn"))
        self.edit_btn.setText(tr("agents_admin.edit_btn"))
        self.del_btn.setText(tr("agents_admin.delete_btn"))
        self.check_btn.setText(tr("agents_admin.check_btn"))
        self.check_btn.setToolTip(tr("agents_admin.check_tooltip"))
        self.refresh()
