"""📊 Monitoring Dashboard — live view over the Sandbox / MCP / Agent Core
layers, built entirely from data those layers already produce:

- **Overview**       — a card-based dashboard (Token Usage & Cost, Resource
  Usage, Recent Activity, Sandbox Details, Permissions, Audit Log), using
  only real state from the panels below (no fabricated numbers).
- **Security Events**  — the audit log (``core/audit_log.py``) filtered to
  ``kind="security_block"``.
- **MCP Call History** — the audit log filtered to ``kind="mcp_call"``.
- **Action Logs**      — the full audit log, newest first.
- **Agent Status**     — which ``agent_roles`` (see ``core/agent_roles.py``)
  are currently running, read from the existing ``ChatPanel._active``/
  ``TaskScheduler._workers``/GraphRAG-ask-worker state — no new runtime
  tracking of its own.

Each panel is a thin, read-only VIEW — this module owns no state that
outlives a refresh tick (besides a one-sample I/O cache used to compute
instantaneous disk/network rates between ticks).
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import List

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from ..core import agent_roles, audit_log
from ..core import usage_tracker as ut
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import icon, DOT_GREEN, DOT_RED, DOT_AMBER
from .widgets import BudgetCard, StatCard, fmt_tokens

_REFRESH_MS = 3000
_MAX_ROWS = 300


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _relative_time(ts: str) -> str:
    """A short "Xm ago"-style string for an audit-log ``ts`` (naive local
    ISO timestamp, see ``audit_log.record``); "" if unparsable."""
    try:
        then = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return ""
    delta = (datetime.now() - then).total_seconds()
    if delta < 60:
        return tr("monitoring.time_just_now")
    if delta < 3600:
        return tr("monitoring.time_minutes_ago", n=int(delta // 60))
    if delta < 86400:
        return tr("monitoring.time_hours_ago", n=int(delta // 3600))
    return tr("monitoring.time_days_ago", n=int(delta // 86400))


class _EventTable(QTableWidget):
    """A read-only table of audit-log events — newest-first by default, and
    every column header is click-to-sort (ascending/descending toggle; the
    Time column's ISO timestamps sort correctly as text)."""

    def __init__(self):
        super().__init__(0, 7)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.verticalHeader().setVisible(False)
        self.setSortingEnabled(True)
        header = self.horizontalHeader()
        header.setStretchLastSection(True)
        for col in range(6):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)

    def retranslate(self) -> None:
        self.setHorizontalHeaderLabels([
            tr("monitoring.col_time"), tr("monitoring.col_role"),
            tr("monitoring.col_account"), tr("monitoring.col_machine"),
            tr("monitoring.col_name"), tr("monitoring.col_result"),
            tr("monitoring.col_detail"),
        ])

    def set_events(self, events: List[dict]) -> None:
        events = sorted(events, key=lambda e: e.get("ts", ""), reverse=True)[:_MAX_ROWS]
        self.setSortingEnabled(False)
        self.setRowCount(len(events))
        for row, ev in enumerate(events):
            is_admin_violation = ev.get("role") == "admin" and not ev.get("ok", True)
            cells = [
                ev.get("ts", ""), agent_roles.label_for(ev.get("agent_role", "")),
                ev.get("account", "") or "—", ev.get("machine", "") or "—",
                ev.get("name", ""), "",
                (ev.get("detail") or "")[:300],
            ]
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 5:   # Result — green check / red close icon (no emoji)
                    item.setIcon(icon("check", color=DOT_GREEN) if ev.get("ok")
                                 else icon("close", color=DOT_RED))
                if is_admin_violation:
                    item.setBackground(QBrush(QColor(229, 72, 77, 60)))
                self.setItem(row, col, item)
        self.setSortingEnabled(True)
        self.apply_filter(getattr(self, "_filter_needle", ""))

    def apply_filter(self, needle: str) -> None:
        self._filter_needle = (needle or "").strip().lower()
        for row in range(self.rowCount()):
            if not self._filter_needle:
                self.setRowHidden(row, False)
                continue
            match = any(
                self._filter_needle in (self.item(row, col).text().lower()
                                        if self.item(row, col) else "")
                for col in range(self.columnCount()))
            self.setRowHidden(row, not match)


class MonitoringTab(QWidget):
    status_message = Signal(str)

    def __init__(self, ctx: AppContext, cowork=None, structure=None, task_scheduler=None):
        super().__init__()
        self.ctx = ctx
        self._cowork = cowork
        self._structure = structure
        self._task_scheduler = task_scheduler
        self._last_io_sample = None

        root = QVBoxLayout(self)
        head = QHBoxLayout()
        self._title = QLabel()
        self._title.setStyleSheet("font-weight:700; font-size:15px;")
        head.addWidget(self._title)
        head.addStretch(1)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(icon("refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        root.addLayout(head)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        # ---- Overview (card dashboard) ----------------------------------
        self.tabs.addTab(self._build_overview_page(), "")

        visible = self._tab_visible
        self.security_table = _EventTable()
        self.security_page = self._wrap_with_filter(self.security_table)
        if visible("security_events"):
            self.tabs.addTab(self.security_page, "")
        self.mcp_table = _EventTable()
        if visible("mcp_history"):
            self.tabs.addTab(self.mcp_table, "")
        self.action_table = _EventTable()
        self.action_page = self._wrap_with_filter(self.action_table)
        if visible("action_logs"):
            self.tabs.addTab(self.action_page, "")

        # ---- Agent Status -------------------------------------------------
        self.status_table = QTableWidget(0, 3)
        self.status_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.status_table.verticalHeader().setVisible(False)
        self.status_table.horizontalHeader().setStretchLastSection(True)
        if visible("agent_status"):
            self.tabs.addTab(self.status_table, "")

        # ---- Agents Admin (catalog: assign a role + pinned model per agent) --
        # The system-management agents (Security, GraphRAG/Knowledge, Monitor…)
        # are defined here — each gets a task_kind (role) and an optional pinned
        # provider/model. Admin-only; since the app runs with full admin access
        # this is always shown.
        from .agents_admin_tab import AgentsAdminTab
        self.agents_admin_tab = AgentsAdminTab(ctx)
        if visible("agents_admin"):
            self.tabs.addTab(self.agents_admin_tab, "")

        # ---- Tools (govern built-in tools + Connectors/MCP in one place) -----
        from .tools_admin_tab import ToolsAdminTab
        self.tools_admin_tab = ToolsAdminTab(ctx)
        if visible("tools_admin"):
            self.tabs.addTab(self.tools_admin_tab, "")

        # ---- Icons (browse built-in icons + add custom icons for agents/flows) --
        from .icons_admin_tab import IconsAdminTab
        self.icons_admin_tab = IconsAdminTab(ctx)
        self.tabs.addTab(self.icons_admin_tab, "")

        self.tabs.setCurrentIndex(0)

        self._timer = QTimer(self)
        # (nav integration methods defined below)
        self._timer.setInterval(_REFRESH_MS)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

        on_language_changed(self._retranslate)
        self.refresh()

    # ---- nav integration: sub-tabs driven from the left nav rail ------------
    def nav_subtabs(self):
        """(label, index, icon_name) for each sub-tab — the left nav lists these
        as children under 'Monitoring'. Icons are keyed by widget identity so
        they're correct in every language."""
        by_widget = {self.agents_admin_tab: "robot", self.tools_admin_tab: "wrench",
                     self.icons_admin_tab: "star"}
        for attr, name in (("security_page", "shield"), ("mcp_table", "plug"),
                           ("action_page", "bolt"), ("status_table", "monitor")):
            w = getattr(self, attr, None)
            if w is not None:
                by_widget[w] = name
        out = []
        for i in range(self.tabs.count()):
            out.append((self.tabs.tabText(i), i, by_widget.get(self.tabs.widget(i), "dashboard")))
        return out

    def select_subtab(self, index: int) -> None:
        if 0 <= index < self.tabs.count():
            self.tabs.setCurrentIndex(index)

    def hide_tab_bar(self) -> None:
        """Hide the in-content tab strip; the nav rail drives the sub-tabs."""
        self.tabs.tabBar().hide()

    # ---- model pricing list (Overview) --------------------------------------
    def _reload_pricing_table(self, *_a) -> None:
        from ..core import model_pricing as mp
        to_ccy = self.ov_pricing_ccy.currentData() or "USD"
        entries = mp.list_entries(self.ctx.config)
        t = self.ov_pricing_table
        t.setRowCount(len(entries))
        for r, e in enumerate(entries):
            in_v = mp.convert(e.get("input_price", 0), e.get("input_ccy", "USD"), to_ccy, self.ctx.config)
            out_v = mp.convert(e.get("output_price", 0), e.get("output_ccy", "USD"), to_ccy, self.ctx.config)
            vals = [e.get("model", ""), e.get("context_length", ""), e.get("max_output", ""),
                    f"{mp.format_price(in_v, to_ccy)} / {e.get('input_unit', '')}",
                    f"{mp.format_price(out_v, to_ccy)} / {e.get('output_unit', '')}"]
            for c, v in enumerate(vals):
                t.setItem(r, c, QTableWidgetItem(str(v)))

    def _import_pricing(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from ..core import model_pricing as mp
        path, _ = QFileDialog.getOpenFileName(
            self, tr("monitoring.pricing_import"), "", "Pricing (*.xlsx *.csv)")
        if not path:
            return
        default_ccy = self.ov_pricing_ccy.currentData() or "USD"
        try:
            imported = mp.import_table(path, default_ccy=default_ccy)
        except ValueError as exc:
            QMessageBox.warning(self, tr("monitoring.pricing_title"), str(exc))
            return
        merged = {e["model"]: e for e in mp.list_entries(self.ctx.config)}
        for e in imported:
            merged[e["model"]] = e
        mp.save_entries(self.ctx.config, list(merged.values()))
        self.ctx.save()
        self._reload_pricing_table()
        self.status_message.emit(tr("monitoring.pricing_imported", n=len(imported)))

    def _export_pricing(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from ..core import model_pricing as mp
        path, _ = QFileDialog.getSaveFileName(
            self, tr("monitoring.pricing_export"), "model_pricing_template.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        mp.export_template(path)
        self.status_message.emit(tr("monitoring.pricing_exported"))

    def _add_pricing_row(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        from ..core import model_pricing as mp
        name, ok = QInputDialog.getText(self, tr("monitoring.pricing_add"),
                                        tr("monitoring.pricing_add_prompt"))
        name = (name or "").strip()
        if not ok or not name:
            return
        ccy = self.ov_pricing_ccy.currentData() or "USD"
        mp.add_entry(self.ctx.config, mp.entry_from_row(
            [name, "", "", "0", "Million tokens", "0", "Million tokens"], default_ccy=ccy))
        self.ctx.save()
        self._reload_pricing_table()

    def _autolink_pricing(self) -> None:
        from ..core import model_pricing as mp
        from ..core.worker import AgentWorker
        if getattr(self, "_pricing_worker", None) is not None:
            return
        self.ov_price_link_btn.setEnabled(False)
        ctx = self.ctx
        ccy = self.ov_pricing_ccy.currentData() or "USD"

        def job(_w):
            return {"entries": mp.auto_link(ctx, default_ccy=ccy)}

        def done(r):
            self._pricing_worker = None
            self.ov_price_link_btn.setEnabled(True)
            self.ctx.save()
            self._reload_pricing_table()
            self.status_message.emit(tr("monitoring.pricing_linked", n=len(r.get("entries", []))))

        def failed(_e):
            self._pricing_worker = None
            self.ov_price_link_btn.setEnabled(True)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._pricing_worker = w
        w.start()

    def _delete_pricing_row(self) -> None:
        from ..core import model_pricing as mp
        row = self.ov_pricing_table.currentRow()
        entries = mp.list_entries(self.ctx.config)
        if 0 <= row < len(entries):
            del entries[row]
            mp.save_entries(self.ctx.config, entries)
            self.ctx.save()
            self._reload_pricing_table()

    def _wrap_with_filter(self, table: "_EventTable") -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        search = QLineEdit()
        search.setPlaceholderText(tr("monitoring.filter_placeholder"))
        search.textChanged.connect(table.apply_filter)
        ai_btn = QPushButton(tr("monitoring.ai_filter_btn"))
        ai_btn.setIcon(icon("sparkle"))
        ai_btn.setToolTip(tr("monitoring.ai_filter_tooltip"))
        ai_btn.clicked.connect(lambda: self._ai_filter(search, ai_btn))
        row.addWidget(search, 1)
        row.addWidget(ai_btn)
        lay.addLayout(row)
        lay.addWidget(table, 1)
        page.filter_edit = search
        page.ai_filter_btn = ai_btn
        return page

    def _ai_filter(self, search: QLineEdit, ai_btn: QPushButton) -> None:
        query = search.text().strip()
        if not query or getattr(self, "_ai_filter_worker", None) is not None:
            return
        ai_btn.setEnabled(False)
        ctx = self.ctx

        def job(worker):
            provider = ctx.build_active_provider()
            reply = provider.chat([
                {"role": "system", "content":
                    "Turn the user's natural-language question about an audit/security event "
                    "log into ONE short search keyword. Reply with ONLY the keyword."},
                {"role": "user", "content": query},
            ], cancel=worker.is_cancelled)
            return {"keyword": (reply.get("content") or "").strip().splitlines()[0][:60]}

        def done(result: dict) -> None:
            self._ai_filter_worker = None
            ai_btn.setEnabled(True)
            search.setText(result.get("keyword") or query)

        def failed(_err: str) -> None:
            self._ai_filter_worker = None
            ai_btn.setEnabled(True)

        from ..core.worker import AgentWorker
        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._ai_filter_worker = w
        w.start()

    # ---- Overview page construction ------------------------------------
    def _build_overview_page(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        root = QHBoxLayout(content)
        root.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)
        right = QVBoxLayout()
        right.setSpacing(12)
        root.addLayout(left, 2)
        root.addLayout(right, 1)

        # ---- Token Usage & Cost --------------------------------------------
        self.ov_usage_group = QGroupBox()
        usage_lay = QGridLayout(self.ov_usage_group)
        usage_lay.setSpacing(8)
        self.ov_usage_total = StatCard()
        self.ov_usage_in = StatCard()
        self.ov_usage_out = StatCard()
        self.ov_usage_cache = StatCard()
        self.ov_usage_cost = StatCard()
        for i, card in enumerate((self.ov_usage_total, self.ov_usage_in, self.ov_usage_out,
                                  self.ov_usage_cache, self.ov_usage_cost)):
            usage_lay.addWidget(card, 0, i)
        # Budget: remaining/budget, direct entry, auto-warns red past 85% used —
        # same box (and same usage.budget_* config) as the Dashboard's.
        self.ov_budget_card = BudgetCard()
        self.ov_budget_card.apply_btn.setIcon(icon("check"))
        self.ov_budget_card.apply_btn.clicked.connect(self._apply_budget)
        usage_lay.addWidget(self.ov_budget_card, 0, 5, 2, 1)
        # Equal stretch on every column — otherwise the grid sizes each column
        # to its widest cell's natural content (Budget's longer "$X / $Y" value
        # + entry row makes its column wider than the plain stat cards).
        for col in range(6):
            usage_lay.setColumnStretch(col, 1)

        # Unit prices are NOT entered here anymore — the cost total is computed
        # straight from the model pricing table (below). The display-currency
        # picker moved to the Dashboard (beside its refresh button) — both
        # screens still read/write the SAME usage.currency config key.
        left.addWidget(self.ov_usage_group)

        # ---- Recent Activity ----------------------------------------------
        self.ov_activity_group = QGroupBox()
        act_lay = QVBoxLayout(self.ov_activity_group)
        self.ov_activity_lbl = QLabel()
        self.ov_activity_lbl.setWordWrap(True)
        self.ov_activity_lbl.setTextFormat(Qt.RichText)
        act_lay.addWidget(self.ov_activity_lbl)
        left.addWidget(self.ov_activity_group)

        # ---- Resource Usage -------------------------------------------------
        self.ov_resource_group = QGroupBox()
        res_lay = QVBoxLayout(self.ov_resource_group)

        def _bar_row():
            row = QHBoxLayout()
            lbl = QLabel(); lbl.setFixedWidth(70)
            bar = QProgressBar(); bar.setFixedHeight(8); bar.setTextVisible(False)
            val = QLabel(); val.setFixedWidth(90); val.setAlignment(Qt.AlignRight)
            row.addWidget(lbl); row.addWidget(bar, 1); row.addWidget(val)
            res_lay.addLayout(row)
            return lbl, bar, val

        def _text_row():
            row = QHBoxLayout()
            lbl = QLabel(); val = QLabel(); val.setAlignment(Qt.AlignRight)
            row.addWidget(lbl); row.addStretch(1); row.addWidget(val)
            res_lay.addLayout(row)
            return lbl, val

        self.ov_cpu_lbl, self.ov_cpu_bar, self.ov_cpu_val = _bar_row()
        self.ov_mem_lbl, self.ov_mem_bar, self.ov_mem_val = _bar_row()
        self.ov_disk_lbl, self.ov_disk_val = _text_row()
        self.ov_network_lbl, self.ov_network_val = _text_row()
        res_lay.addStretch(1)

        # ---- Model pricing (beside the CPU/resource group) ------------------
        self.ov_pricing_group = QGroupBox()
        pg = QVBoxLayout(self.ov_pricing_group)
        phdr = QHBoxLayout()
        self.ov_pricing_ccy_lbl = QLabel(); self.ov_pricing_ccy_lbl.setObjectName("hint")
        self.ov_pricing_ccy = QComboBox()
        for cur in ut.SUPPORTED_CURRENCIES:
            self.ov_pricing_ccy.addItem(cur, cur)
        pidx = self.ov_pricing_ccy.findData(
            (self.ctx.config.data.get("usage") or {}).get("currency", "USD"))
        self.ov_pricing_ccy.setCurrentIndex(max(0, pidx))
        self.ov_pricing_ccy.currentIndexChanged.connect(self._reload_pricing_table)
        phdr.addWidget(self.ov_pricing_ccy_lbl)
        phdr.addWidget(self.ov_pricing_ccy)
        phdr.addStretch(1)
        self.ov_price_import_btn = QPushButton(); self.ov_price_import_btn.setIcon(icon("download"))
        self.ov_price_import_btn.clicked.connect(self._import_pricing)
        self.ov_price_export_btn = QPushButton(); self.ov_price_export_btn.setIcon(icon("upload"))
        self.ov_price_export_btn.clicked.connect(self._export_pricing)
        self.ov_price_add_btn = QPushButton(); self.ov_price_add_btn.setIcon(icon("plus"))
        self.ov_price_add_btn.clicked.connect(self._add_pricing_row)
        self.ov_price_link_btn = QPushButton(); self.ov_price_link_btn.setIcon(icon("refresh"))
        self.ov_price_link_btn.clicked.connect(self._autolink_pricing)
        self.ov_price_del_btn = QPushButton(); self.ov_price_del_btn.setIcon(icon("trash"))
        self.ov_price_del_btn.clicked.connect(self._delete_pricing_row)
        for b in (self.ov_price_import_btn, self.ov_price_export_btn, self.ov_price_add_btn,
                  self.ov_price_link_btn, self.ov_price_del_btn):
            phdr.addWidget(b)
        pg.addLayout(phdr)
        self.ov_pricing_table = QTableWidget(0, 5)
        self.ov_pricing_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.ov_pricing_table.verticalHeader().setVisible(False)
        self.ov_pricing_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ov_pricing_table.setSelectionBehavior(QTableWidget.SelectRows)
        pg.addWidget(self.ov_pricing_table, 1)

        res_row = QHBoxLayout()
        res_row.setSpacing(12)
        res_row.addWidget(self.ov_resource_group, 1)
        res_row.addWidget(self.ov_pricing_group, 2)   # pricing sits beside CPU/resources
        left.addLayout(res_row)
        left.addStretch(1)
        self._reload_pricing_table()

        # ---- Sandbox Details --------------------------------------------
        self.ov_sandbox_details_group = QGroupBox()
        sbx_lay = QVBoxLayout(self.ov_sandbox_details_group)

        def _kv():
            row = QHBoxLayout()
            lbl = QLabel(); lbl.setObjectName("hint")
            val = QLabel()
            row.addWidget(lbl); row.addStretch(1); row.addWidget(val)
            sbx_lay.addLayout(row)
            return lbl, val

        self.ov_sbx_id_lbl, self.ov_sbx_id_val = _kv()
        self.ov_sbx_status_lbl, self.ov_sbx_status_val = _kv()
        self.ov_sbx_status_val.setObjectName("badgeSuccess")
        self.ov_sbx_created_lbl, self.ov_sbx_created_val = _kv()
        self.ov_sbx_uptime_lbl, self.ov_sbx_uptime_val = _kv()

        limits_row = QHBoxLayout()
        self.ov_sbx_limits_lbl = QLabel()
        self.ov_sbx_limits_lbl.setObjectName("hint")
        self.ov_sbx_limits_lbl.setWordWrap(True)
        self.ov_sbx_edit_btn = QPushButton()
        self.ov_sbx_edit_btn.setFlat(True)
        self.ov_sbx_edit_btn.clicked.connect(self._open_settings_and_refresh)
        limits_row.addWidget(self.ov_sbx_limits_lbl, 1)
        limits_row.addWidget(self.ov_sbx_edit_btn)
        sbx_lay.addLayout(limits_row)

        self.ov_sbx_net_lbl, self.ov_sbx_net_val = _kv()
        right.addWidget(self.ov_sandbox_details_group)

        # ---- Permissions -----------------------------------------------
        self.ov_permissions_group = QGroupBox()
        perm_lay = QVBoxLayout(self.ov_permissions_group)

        def _pkv():
            row = QHBoxLayout()
            lbl = QLabel(); lbl.setObjectName("hint")
            val = QLabel()
            row.addWidget(lbl); row.addStretch(1); row.addWidget(val)
            perm_lay.addLayout(row)
            return lbl, val

        self.ov_perm_fs_lbl, self.ov_perm_fs_val = _pkv()
        self.ov_perm_network_lbl, self.ov_perm_network_val = _pkv()
        self.ov_perm_process_lbl, self.ov_perm_process_val = _pkv()
        self.ov_perm_env_lbl, self.ov_perm_env_val = _pkv()
        self.ov_perm_edit_btn = QPushButton()
        self.ov_perm_edit_btn.setFlat(True)
        self.ov_perm_edit_btn.clicked.connect(self._open_settings_and_refresh)
        perm_lay.addWidget(self.ov_perm_edit_btn, 0, Qt.AlignRight)
        right.addWidget(self.ov_permissions_group)

        # ---- Audit Log ----------------------------------------------------
        self.ov_audit_group = QGroupBox()
        audit_lay = QVBoxLayout(self.ov_audit_group)
        self.ov_audit_lbl = QLabel()
        self.ov_audit_lbl.setWordWrap(True)
        self.ov_audit_lbl.setTextFormat(Qt.RichText)
        audit_lay.addWidget(self.ov_audit_lbl)
        self.ov_view_all_btn = QPushButton()
        self.ov_view_all_btn.setFlat(True)
        self.ov_view_all_btn.clicked.connect(
            lambda: self.tabs.setCurrentIndex(self.tabs.indexOf(self.action_page)))
        audit_lay.addWidget(self.ov_view_all_btn, 0, Qt.AlignRight)
        self.ov_audit_group.setVisible(self._tab_visible("action_logs"))
        right.addWidget(self.ov_audit_group)
        right.addStretch(1)

        return page

    def _open_settings_and_refresh(self) -> None:
        from .settings_dialog import SettingsDialog
        dlg = SettingsDialog(self.ctx, self)
        dlg.exec()
        self.refresh()

    @staticmethod
    def _set_badge(label: QLabel, object_name: str) -> None:
        label.setObjectName(object_name)
        label.style().unpolish(label)
        label.style().polish(label)

    def _tab_visible(self, key: str) -> bool:
        return self.ctx.role == "admin" or bool(self.ctx.config.monitoring_visibility.get(key, True))

    def _set_tab_text_if_present(self, widget, text: str) -> None:
        idx = self.tabs.indexOf(widget)
        if idx >= 0:
            self.tabs.setTabText(idx, text)

    # ---- i18n ------------------------------------------------------------
    def _retranslate(self) -> None:
        self._title.setText(tr("monitoring.title"))
        self.refresh_btn.setText(tr("monitoring.refresh"))
        if self.tabs.count():
            self.tabs.setTabText(0, tr("monitoring.tab_overview"))
        self._set_tab_text_if_present(self.security_page, tr("monitoring.tab_security"))
        self._set_tab_text_if_present(self.mcp_table, tr("monitoring.tab_mcp"))
        self._set_tab_text_if_present(self.action_page, tr("monitoring.tab_actions"))
        self._set_tab_text_if_present(self.status_table, tr("monitoring.tab_agents"))
        self._set_tab_text_if_present(self.agents_admin_tab, tr("monitoring.tab_agents_admin"))
        self._set_tab_text_if_present(self.tools_admin_tab, tr("monitoring.tab_tools"))
        self._set_tab_text_if_present(self.icons_admin_tab, tr("monitoring.tab_icons"))
        self.security_table.retranslate()
        self.mcp_table.retranslate()
        self.action_table.retranslate()
        self.status_table.setHorizontalHeaderLabels([
            tr("monitoring.col_agent"), tr("monitoring.col_active"), tr("monitoring.col_source"),
        ])

        self.ov_usage_group.setTitle(tr("monitoring.overview_usage_title"))
        self.ov_budget_card.apply_btn.setToolTip(tr("usage.budget_apply_tooltip"))
        self.ov_budget_card.budget_spin.setToolTip(tr("usage.budget_spin_tooltip"))

        self.ov_activity_group.setTitle(tr("monitoring.overview_activity_title"))
        self.ov_resource_group.setTitle(tr("monitoring.overview_resource_title"))
        self.security_page.filter_edit.setPlaceholderText(tr("monitoring.filter_placeholder"))
        self.action_page.filter_edit.setPlaceholderText(tr("monitoring.filter_placeholder"))
        self.ov_cpu_lbl.setText(tr("monitoring.overview_res_cpu"))
        self.ov_mem_lbl.setText(tr("monitoring.overview_res_mem"))
        self.ov_disk_lbl.setText(tr("monitoring.overview_res_disk"))
        self.ov_network_lbl.setText(tr("monitoring.overview_res_network"))
        # model pricing panel
        self.ov_pricing_group.setTitle(tr("monitoring.pricing_title"))
        self.ov_pricing_ccy_lbl.setText(tr("monitoring.pricing_currency"))
        self.ov_price_import_btn.setText(tr("monitoring.pricing_import"))
        self.ov_price_export_btn.setText(tr("monitoring.pricing_export"))
        self.ov_price_add_btn.setText(tr("monitoring.pricing_add"))
        self.ov_price_link_btn.setText(tr("monitoring.pricing_autolink"))
        self.ov_price_del_btn.setText(tr("monitoring.pricing_delete"))
        self.ov_pricing_table.setHorizontalHeaderLabels([
            tr("monitoring.pricing_col_model"), tr("monitoring.pricing_col_context"),
            tr("monitoring.pricing_col_maxout"), tr("monitoring.pricing_col_input"),
            tr("monitoring.pricing_col_output")])

        self.ov_sandbox_details_group.setTitle(tr("monitoring.overview_sandbox_details_title"))
        self.ov_sbx_id_lbl.setText(tr("monitoring.overview_sandbox_id"))
        self.ov_sbx_status_lbl.setText(tr("monitoring.overview_status"))
        self.ov_sbx_created_lbl.setText(tr("monitoring.overview_created"))
        self.ov_sbx_uptime_lbl.setText(tr("monitoring.overview_uptime"))
        self.ov_sbx_edit_btn.setText(tr("monitoring.overview_edit"))
        self.ov_sbx_net_lbl.setText(tr("monitoring.overview_network_label"))

        self.ov_permissions_group.setTitle(tr("monitoring.overview_permissions_title"))
        self.ov_perm_fs_lbl.setText(tr("monitoring.overview_perm_fs"))
        self.ov_perm_fs_val.setText(tr("monitoring.overview_perm_fs_value"))
        self.ov_perm_network_lbl.setText(tr("monitoring.overview_perm_network"))
        self.ov_perm_process_lbl.setText(tr("monitoring.overview_perm_process"))
        self.ov_perm_process_val.setText(tr("monitoring.overview_perm_process_value"))
        self.ov_perm_env_lbl.setText(tr("monitoring.overview_perm_env"))
        self.ov_perm_env_val.setText(tr("monitoring.overview_perm_env_value"))
        self.ov_perm_edit_btn.setText(tr("monitoring.overview_edit"))

        self.ov_audit_group.setTitle(tr("monitoring.overview_audit_title"))
        self.ov_view_all_btn.setText(tr("monitoring.overview_view_all"))

        self.refresh()

    # ---- refresh -----------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_resource_usage()
        events = self._load_events()
        self.security_table.set_events([e for e in events if e.get("kind") == "security_block"])
        self.mcp_table.set_events([e for e in events if e.get("kind") == "mcp_call"])
        self.action_table.set_events(events)
        self._refresh_agent_status()
        self._refresh_overview(events)

    def _load_events(self) -> List[dict]:
        shared_dir = self.ctx.config.shared_dir
        if shared_dir:
            from ..core import telemetry_shared
            shared_events = telemetry_shared.load_shared_audit_events(shared_dir)
            if shared_events:
                return shared_events
        return audit_log.load_events()

    def _refresh_resource_usage(self) -> None:
        try:
            import psutil
        except ImportError:
            self._set_overview_resource_na()
            return

        try:
            own = psutil.Process()
            own_cpu = own.cpu_percent(interval=None)
            own_mem = own.memory_info().rss
        except Exception:
            own, own_cpu, own_mem = None, 0.0, 0

        self.ov_cpu_bar.setValue(int(min(own_cpu, 100)))
        self.ov_cpu_val.setText(f"{own_cpu:.0f}%")
        try:
            total_mem = psutil.virtual_memory().total
            mem_pct = int(own_mem * 100 / total_mem) if total_mem else 0
        except Exception:
            mem_pct = 0
        self.ov_mem_bar.setValue(min(mem_pct, 100))
        self.ov_mem_val.setText(_fmt_bytes(own_mem))

        now = time.monotonic()
        try:
            io = own.io_counters() if own is not None else None
            disk_bytes = (io.read_bytes + io.write_bytes) if io is not None else None
        except Exception:
            disk_bytes = None
        try:
            net = psutil.net_io_counters()
            net_bytes = net.bytes_sent + net.bytes_recv
        except Exception:
            net_bytes = None

        prev = self._last_io_sample
        self._last_io_sample = (now, disk_bytes, net_bytes)
        na = tr("monitoring.na")
        if prev and disk_bytes is not None and prev[1] is not None and now > prev[0]:
            rate = max(0.0, (disk_bytes - prev[1]) / (now - prev[0]))
            self.ov_disk_val.setText(f"{_fmt_bytes(rate)}/s")
        else:
            self.ov_disk_val.setText(na)
        if prev and net_bytes is not None and prev[2] is not None and now > prev[0]:
            rate = max(0.0, (net_bytes - prev[2]) / (now - prev[0]))
            self.ov_network_val.setText(f"{_fmt_bytes(rate)}/s")
        else:
            self.ov_network_val.setText(na)

    def _set_overview_resource_na(self) -> None:
        na = tr("monitoring.na")
        self.ov_cpu_bar.setValue(0)
        self.ov_cpu_val.setText(na)
        self.ov_mem_bar.setValue(0)
        self.ov_mem_val.setText(na)
        self.ov_disk_val.setText(na)
        self.ov_network_val.setText(na)

    def _refresh_agent_status(self) -> None:
        cowork_n = len(self._cowork.active_workers()) if self._cowork is not None else 0
        task_n = self._task_scheduler.running_count() if self._task_scheduler is not None else 0
        ask_worker = getattr(self._structure, "_ask_worker", None)
        knowledge_n = 1 if (ask_worker is not None and ask_worker.isRunning()) else 0

        # Security is a system-management agent that runs INLINE on the active
        # turn (agent_security prompt/command validation) — there is no separate
        # worker to count, so its "active" cell shows On/Off from Settings
        # instead of a live count.
        sec_on = bool(self.ctx.config.agent_security.get("enabled"))

        rows = [
            (agent_roles.COWORK, cowork_n, tr("monitoring.source_cowork")),
            (agent_roles.TASK, task_n, tr("monitoring.source_task")),
            (agent_roles.KNOWLEDGE, knowledge_n, tr("monitoring.source_knowledge")),
            (agent_roles.PLANNER, None, tr("monitoring.source_planner")),
            (agent_roles.REASONING, None, tr("monitoring.source_reasoning")),
            (agent_roles.SECURITY, None, tr("monitoring.source_security")),
        ]
        self.status_table.setRowCount(len(rows))
        for row, (role_key, count, source) in enumerate(rows):
            self.status_table.setItem(row, 0, QTableWidgetItem(agent_roles.label_for(role_key)))
            if role_key == agent_roles.SECURITY:
                active_text = tr("monitoring.on") if sec_on else tr("monitoring.off")
            else:
                active_text = tr("monitoring.active_n", n=count) if count is not None else "—"
            self.status_table.setItem(row, 1, QTableWidgetItem(active_text))
            self.status_table.setItem(row, 2, QTableWidgetItem(source))

    def _activity_line(self, event: dict) -> str:
        # Monochrome colored mark (no emoji) — an HTML label can't host a QIcon,
        # so a thin ✓ / ✗ / ! tinted by state is the line-style equivalent.
        ok = event.get("ok", True)
        if ok:
            mark = f"<span style='color:{DOT_GREEN};'>✓</span>"
        elif event.get("kind") == "security_block":
            mark = f"<span style='color:{DOT_AMBER};'>!</span>"
        else:
            mark = f"<span style='color:{DOT_RED};'>✗</span>"
        name = event.get("name", "") or event.get("kind", "")
        rel = _relative_time(event.get("ts", ""))
        suffix = f" <span style='color:#8b8d98;'>— {rel}</span>" if rel else ""
        return f"{mark} {name}{suffix}"

    def _refresh_usage_cards(self) -> None:
        from ..core import model_pricing as mp
        mp.sync_to_usage(self.ctx.config)   # cost total comes straight from the price table
        pricing = {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}
        events = ut.load_events()
        s = ut.summarize(events)
        costs = ut.cost_usd_events(events, pricing)
        self.ov_usage_total.set(tr("dashboard.card_total"), fmt_tokens(s["total"]),
                                tr("dashboard.card_turns", n=s["turns"]))
        self.ov_usage_in.set(tr("dashboard.card_in"), fmt_tokens(s["in"]),
                             ut.format_cost(costs["in"], pricing))
        self.ov_usage_out.set(tr("dashboard.card_out"), fmt_tokens(s["out"]),
                              ut.format_cost(costs["out"], pricing))
        self.ov_usage_cache.set(tr("dashboard.card_cache"), fmt_tokens(s["cache"]),
                                ut.format_cost(costs["cache"], pricing))
        self.ov_usage_cost.set(tr("dashboard.card_cost"),
                               ut.format_cost(sum(costs.values()), pricing, digits=2))
        self._refresh_budget()

    def _apply_budget(self) -> None:
        """Persist the spin box's value as the new budget — starts a fresh
        remaining-balance window (spend before now is no longer counted)."""
        ccy = (self.ctx.config.data.get("usage") or {}).get("currency", "USD")
        ut.set_budget(self.ctx.config, self.ov_budget_card.budget_spin.value(), ccy)
        self.ctx.save()
        self._refresh_budget()

    def _refresh_budget(self) -> None:
        from ..core import model_pricing as mp
        pricing = {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}
        status = ut.budget_status(self.ctx.config)
        if status is None:
            self.ov_budget_card.set(tr("usage.budget_title"), "—", tr("usage.budget_no_budget"))
            self.ov_budget_card.budget_spin.setValue(0.0)
            return
        amount_disp = mp.convert(status["amount_usd"], "USD",
                                 pricing.get("currency", "USD"), self.ctx.config)
        value = (f"{ut.format_cost(status['remaining_usd'], pricing, digits=2)}"
                 f" / {ut.format_cost(status['amount_usd'], pricing, digits=2)}")
        pct = int(round(status["pct_used"] * 100))
        sub = tr("usage.budget_over_warning") if status["over_85"] else tr("usage.budget_used_pct", pct=pct)
        self.ov_budget_card.set(tr("usage.budget_title"), value, sub, warn=status["over_85"])
        if not self.ov_budget_card.budget_spin.hasFocus():
            self.ov_budget_card.budget_spin.setValue(round(amount_disp, 2))

    def _refresh_overview(self, events: List[dict]) -> None:
        sec = self.ctx.config.agent_security
        self._refresh_usage_cards()
        net_blocked = bool(sec.get("block_network"))

        recent = sorted(events, key=lambda e: e.get("ts", ""), reverse=True)
        if recent:
            self.ov_activity_lbl.setText("<br>".join(self._activity_line(e) for e in recent[:6]))
            self.ov_audit_lbl.setText("<br>".join(
                f"{e.get('ts', '')} — {e.get('name', '')}" for e in recent[:4]))
        else:
            self.ov_activity_lbl.setText(tr("monitoring.overview_no_activity"))
            self.ov_audit_lbl.setText(tr("monitoring.overview_no_activity"))

        self.ov_sbx_id_val.setText(f"sbx_{os.getpid():x}")
        self.ov_sbx_status_val.setText(tr("monitoring.overview_status_running"))
        self.ov_sbx_created_val.setText(datetime.fromtimestamp(self.ctx.started_at).strftime("%H:%M:%S"))
        uptime_s = max(0, int(time.time() - self.ctx.started_at))
        h, rem = divmod(uptime_s, 3600)
        m, s = divmod(rem, 60)
        self.ov_sbx_uptime_val.setText(f"{h}h {m}m {s}s" if h else f"{m}m {s}s")
        limit_parts = []
        if sec.get("resource_limit_cpu_percent"):
            limit_parts.append(f"CPU {sec['resource_limit_cpu_percent']}%")
        if sec.get("resource_limit_memory_mb"):
            limit_parts.append(f"MEM {sec['resource_limit_memory_mb']}MB")
        if sec.get("resource_limit_disk_mb"):
            limit_parts.append(f"DISK {sec['resource_limit_disk_mb']}MB")
        self.ov_sbx_limits_lbl.setText(
            tr("monitoring.overview_resource_limits") + ": "
            + (", ".join(limit_parts) if limit_parts else tr("monitoring.na")))
        self.ov_sbx_net_val.setText(
            tr("monitoring.overview_network_disabled") if net_blocked
            else tr("monitoring.overview_network_enabled"))
        self._set_badge(self.ov_sbx_net_val, "badgeWarn" if net_blocked else "badgeSuccess")

        self.ov_perm_network_val.setText(
            tr("monitoring.overview_perm_network_blocked") if net_blocked
            else tr("monitoring.overview_perm_network_allowed"))
        self._set_badge(self.ov_perm_network_val, "badgeWarn" if net_blocked else "badgeSuccess")