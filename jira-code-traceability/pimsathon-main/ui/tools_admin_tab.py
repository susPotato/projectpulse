"""Tools — Monitoring tab (Admin) to govern every agent capability.

Two sub-tabs:
  * "Tool"      — built-in agent tools (read/write/edit files, run commands,
                  install packages, fetch URLs); toggling one OFF removes it
                  from the agent's toolset (persisted in ``config.tools_disabled``).
  * "Connector" — the full Connectors (MCP / REST API) setup, moved here from
                  Settings: add/edit/delete CAD/CAE/MS365/Other connectors and
                  enable/disable each (``ConnectorsPanel``).
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core.tools import TOOL_SPECS
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .connectors_panel import ConnectorsPanel
from .icons import icon


def _center_checkbox(checked: bool, on_toggle) -> QWidget:
    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setAlignment(Qt.AlignCenter)
    chk = QCheckBox()
    chk.setChecked(checked)
    chk.toggled.connect(on_toggle)
    lay.addWidget(chk)
    return box


class ToolsAdminTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        root = QVBoxLayout(self)

        self.subtabs = QTabWidget()
        root.addWidget(self.subtabs, 1)

        # ---- "Tool" sub-tab: built-in agent tools ------------------------
        tool_page = QWidget()
        tl = QVBoxLayout(tool_page)
        self._net_worker = None
        self._hint = QLabel()
        self._hint.setObjectName("hint")
        self._hint.setWordWrap(True)
        tl.addWidget(self._hint)

        self.table = QTableWidget(0, 3)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        # Description is the long column — IT stretches to fill remaining
        # width (was Name, leaving Description squeezed into whatever was
        # left over); Name/Enabled size to their own content.
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.setWordWrap(True)
        tl.addWidget(self.table, 1)

        # "Test Internet" self-test lives INSIDE the fetch_url tool row now (see
        # refresh) instead of a separate boxed section — persistent widgets so
        # they survive table rebuilds.
        self.test_internet_btn = QPushButton(tr("settings.test_internet"))
        self.test_internet_btn.setIcon(icon("globe"))
        self.test_internet_btn.setToolTip(tr("settings.test_internet_tooltip"))
        self.test_internet_btn.clicked.connect(self._test_internet)
        self.test_internet_status = QLabel("")
        self.test_internet_status.setWordWrap(True)

        btn_row = QHBoxLayout()
        self.refresh_btn = QPushButton()
        self.refresh_btn.clicked.connect(self.refresh)
        btn_row.addStretch(1)
        btn_row.addWidget(self.refresh_btn)
        tl.addLayout(btn_row)
        # Jira CONNECTION setup lives in the Connector sub-tab now; here the Tool
        # list just lets the admin turn the jira_* tools on/off. A pointer note:
        self.jira_note = QLabel()
        self.jira_note.setObjectName("hint")
        self.jira_note.setWordWrap(True)
        tl.addWidget(self.jira_note)
        self.subtabs.addTab(tool_page, "")

        # ---- "Connector" sub-tab: MCP / REST API setup (moved from Settings) --
        self.connectors_panel = ConnectorsPanel(ctx)
        self.subtabs.addTab(self.connectors_panel, "")

        on_language_changed(self._retranslate)
        self._retranslate()

    # ---- built-in tools table -------------------------------------------------
    def refresh(self) -> None:
        disabled = set(self.ctx.config.tools_disabled)
        specs = list(TOOL_SPECS)
        self.table.setRowCount(len(specs))
        for r, spec in enumerate(specs):
            self.table.setItem(r, 0, QTableWidgetItem(spec.name))
            # Full description (was truncated to 80 chars, hiding the rest) —
            # word-wraps inside the stretched column; resizeRowToContents
            # below grows the row to fit however many lines that takes.
            if spec.name == "fetch_url":
                # This tool's row carries the live "Test Internet" self-test
                # right below its description — no separate boxed section.
                self.table.setItem(r, 1, None)
                self.table.setCellWidget(r, 1, self._fetch_url_desc_cell(spec))
            else:
                desc_item = QTableWidgetItem(spec.description)
                desc_item.setToolTip(spec.description)
                self.table.setItem(r, 1, desc_item)
            self.table.setCellWidget(
                r, 2, _center_checkbox(spec.name not in disabled,
                                       lambda on, n=spec.name: self._toggle_builtin(n, on)))
        # Once ALL rows/columns are populated (so the stretched Description
        # column has its real width), grow each row to fit its wrapped text.
        self.table.resizeRowsToContents()

    def _fetch_url_desc_cell(self, spec) -> QWidget:
        cell = QWidget()
        cl = QVBoxLayout(cell)
        cl.setContentsMargins(6, 4, 6, 4)
        cl.setSpacing(4)
        desc = QLabel(spec.description)
        desc.setWordWrap(True)
        desc.setToolTip(spec.description)
        cl.addWidget(desc)
        net = QWidget()
        nl = QHBoxLayout(net)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.addWidget(self.test_internet_btn)
        nl.addWidget(self.test_internet_status, 1)
        cl.addWidget(net)
        return cell

    def _toggle_builtin(self, name: str, enabled: bool) -> None:
        self.ctx.config.set_tool_enabled(name, enabled)
        # For fetch_url, the Enabled toggle also governs the runtime web-access
        # gate (agent_security.allow_url_fetch) — one control for the capability.
        if name == "fetch_url":
            self.ctx.config.agent_security["allow_url_fetch"] = bool(enabled)
            self.ctx.config.save()

    def _test_internet(self) -> None:
        """Live-check the app's own outbound HTTPS path and report the concrete
        result. Respects the fetch_url toggle: when web access is OFF the agent
        cannot reach the internet, so the test reports that instead of probing."""
        disabled = ("fetch_url" in self.ctx.config.tools_disabled
                    or not bool(self.ctx.config.agent_security.get("allow_url_fetch", True)))
        if disabled:
            self.test_internet_status.setText(tr("tools_admin.internet_disabled"))
            self.test_internet_status.setStyleSheet("color: #c00;")
            return

        def job(worker):
            from ..core import tls_trust
            ok, message = tls_trust.diagnose_internet()
            return {"ok": ok, "message": message}

        def done(result):
            ok = result.get("ok")
            self.test_internet_status.setText(result.get("message", ""))
            self.test_internet_status.setStyleSheet("color: #090;" if ok else "color: #c00;")
            self.test_internet_btn.setEnabled(True)

        def failed(e):
            self.test_internet_status.setText(str(e))
            self.test_internet_status.setStyleSheet("color: #c00;")
            self.test_internet_btn.setEnabled(True)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._net_worker = w   # keep a ref so the thread isn't GC'd mid-run
        self.test_internet_btn.setEnabled(False)
        self.test_internet_status.setStyleSheet("")
        self.test_internet_status.setText(tr("settings.testing_internet"))
        w.start()

    # ---- i18n -----------------------------------------------------------------
    def _retranslate(self) -> None:
        self.subtabs.setTabText(0, tr("tools_admin.subtab_tool"))
        self.subtabs.setTabText(1, tr("tools_admin.subtab_connector"))
        self._hint.setText(tr("tools_admin.hint"))
        self.test_internet_btn.setText(tr("settings.test_internet"))
        self.test_internet_btn.setToolTip(tr("settings.test_internet_tooltip"))
        self.refresh_btn.setText(tr("tools_admin.refresh"))
        self.table.setHorizontalHeaderLabels([
            tr("tools_admin.col_name"), tr("tools_admin.col_desc"),
            tr("tools_admin.col_enabled"),
        ])
        self.jira_note.setText(tr("tools_admin.jira_note"))
        self.refresh()
