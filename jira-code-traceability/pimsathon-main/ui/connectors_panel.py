"""Connectors (MCP / REST API) management — the setup UI.

Lives in Monitoring → Tools → "Connector" sub-tab (moved out of Settings). A
tree of the four categories (CAD / CAE / MS365 / Other); each connector has an
Enabled checkbox and can be added / edited / deleted (MCP-stdio or REST-API,
via ExtConnectorEditDialog). Built-in connectors (MS365 OneDrive / SharePoint,
and Jira under "Other") appear as rows in the tree with their checkbox bound to
config; double-clicking one opens its setup — nothing spills outside the tree.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.ext_connectors import CATEGORIES as EXT_CATEGORIES
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .ext_connector_dialog import ExtConnectorEditDialog
from .icons import icon


class JiraConnectDialog(QDialog):
    """Minimal Jira connect — paste any Jira link (it fills the base URL) + email
    + API token. Once connected, pasting a Jira link into Cowork / Co4E chat is
    read and processed automatically (no per-request setup)."""

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(tr("connectors.jira_group"))
        self.setMinimumWidth(460)
        jira = ctx.config.data.get("jira", {})
        form = QFormLayout(self)

        hint = QLabel(tr("connectors.jira_hint"))
        hint.setObjectName("hint"); hint.setWordWrap(True); hint.setOpenExternalLinks(True)
        form.addRow(hint)
        self.paste = QLineEdit()
        self.paste.setPlaceholderText(tr("connectors.jira_paste_placeholder"))
        self.paste.textChanged.connect(self._on_paste)
        form.addRow(tr("connectors.jira_paste"), self.paste)
        self.url = QLineEdit(jira.get("base_url", ""))
        self.url.setPlaceholderText("https://your-domain.atlassian.net")
        self.email = QLineEdit(jira.get("email", ""))
        self.token = QLineEdit(jira.get("api_token", ""))
        self.token.setEchoMode(QLineEdit.Password)
        form.addRow(tr("connectors.jira_url"), self.url)
        form.addRow(tr("connectors.jira_email"), self.email)
        form.addRow(tr("connectors.jira_token"), self.token)
        self.status = QLabel(); self.status.setObjectName("hint"); self.status.setWordWrap(True)
        form.addRow(self.status)

        row = QHBoxLayout()
        self.test_btn = QPushButton(tr("connectors.jira_test"))
        self.test_btn.clicked.connect(self._test)
        self.save_btn = QPushButton(tr("connectors.jira_save"))
        self.save_btn.setObjectName("primary"); self.save_btn.setIcon(icon("save"))
        self.save_btn.clicked.connect(self._save_close)
        row.addWidget(self.test_btn); row.addStretch(1); row.addWidget(self.save_btn)
        rw = QWidget(); rw.setLayout(row)
        form.addRow(rw)

    def _on_paste(self, text: str) -> None:
        from ..core import jira_tool
        base = jira_tool.base_url_from_link(text)
        if base:
            self.url.setText(base)

    def _save(self) -> None:
        j = self.ctx.config.data.setdefault("jira", {})
        j.update({"base_url": self.url.text().strip(), "email": self.email.text().strip(),
                  "api_token": self.token.text().strip()})
        j.setdefault("enabled", True)
        self.ctx.save()

    def _save_close(self) -> None:
        self._save()
        self.accept()

    def _test(self) -> None:
        from ..core import jira_tool
        self._save()
        cfg = self.ctx.config.data.get("jira", {})
        if not jira_tool.configured(cfg):
            self.status.setText(tr("connectors.jira_need_fields"))
            return
        self.status.setText(tr("connectors.jira_testing"))
        self.test_btn.setEnabled(False)

        def job(_w):
            return {"out": jira_tool.search(cfg, "order by created DESC", 1)}

        def done(r):
            self.test_btn.setEnabled(True)
            out = r.get("out", "")
            ok = not out.lower().startswith(("jira is not configured", "jira search failed"))
            self.status.setText(tr("connectors.jira_ok") if ok
                                else tr("connectors.jira_fail", err=out[:200]))

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda e: (self.test_btn.setEnabled(True),
                                    self.status.setText(tr("connectors.jira_fail", err=str(e)[:200]))))
        self._jira_worker = w
        w.start()


class ConnectorsPanel(QWidget):
    _EXT_CATEGORY_LABELS = {
        "cad": "CAD (NX / CATIA / SolidWorks / AutoCAD)",
        "cae": "CAE (ANSA / ABAQUS / HyperWorks / ANSYS)",
        "ms365": "MS365 (Microsoft 365 / OneDrive / SharePoint)",
        "other": "Other (any generic MCP server)",
    }
    _EXT_CATEGORY_ICONS = {"cad": "wrench", "cae": "ruler", "ms365": "cloud", "other": "plug"}
    # TEMPORARY: only OneDrive + SharePoint (auto-connect via locally-synced
    # OneDrive folders, no sign-in). Restore Outlook/Teams/Meeting when cloud
    # OAuth is re-enabled.
    _MS365_BUILTIN_LABELS = {"onedrive": "OneDrive", "sharepoint": "SharePoint"}

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        lay = QVBoxLayout(self)

        # Master switch: connect to external connectors at all (default ON).
        # Off = the agent connects to NO external connector/MCP (see
        # AppContext.build_mcp_tools), regardless of the per-connector checks below.
        self.connect_external_chk = QCheckBox(tr("connectors.connect_external"))
        self.connect_external_chk.setChecked(self.ctx.config.connect_external)
        self.connect_external_chk.setToolTip(tr("connectors.connect_external_tooltip"))
        self.connect_external_chk.toggled.connect(self._on_connect_external_toggled)
        lay.addWidget(self.connect_external_chk)

        hint = QLabel(tr("settings.ext_hint"))
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        self.ext_tree = QTreeWidget()
        self.ext_tree.setHeaderHidden(True)
        self.ext_tree.itemChanged.connect(self._on_ext_check)
        self.ext_tree.itemDoubleClicked.connect(lambda *_: self._ext_edit())
        lay.addWidget(self.ext_tree, 1)

        self.dbl_hint = QLabel(tr("connectors.dbl_configure"))
        self.dbl_hint.setObjectName("hint")
        lay.addWidget(self.dbl_hint)

        row = QHBoxLayout()
        self.add_btn = QPushButton(tr("settings.ext_add_btn"))
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._ext_add)
        self.edit_btn = QPushButton(tr("settings.ext_edit_btn"))
        self.edit_btn.setIcon(icon("edit"))
        self.edit_btn.clicked.connect(self._ext_edit)
        self.del_btn = QPushButton(tr("settings.ext_delete_btn"))
        self.del_btn.setIcon(icon("trash"))
        self.del_btn.clicked.connect(self._ext_delete)
        row.addWidget(self.add_btn)
        row.addWidget(self.edit_btn)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        lay.addLayout(row)

        self.ms365_local_status = QLabel()
        self.ms365_local_status.setObjectName("hint")
        self.ms365_local_status.setWordWrap(True)
        lay.addWidget(self.ms365_local_status)

        self._refresh_ms365_local_status()
        self._reload_ext_tree()
        on_language_changed(self._retranslate)

    # ---- rendering ------------------------------------------------------------
    def _reload_ext_tree(self) -> None:
        self.ext_tree.blockSignals(True)
        self.ext_tree.clear()
        ext = self.ctx.config.ext_connectors
        for cat in EXT_CATEGORIES:
            cat_item = QTreeWidgetItem([self._EXT_CATEGORY_LABELS.get(cat, cat)])
            cat_item.setIcon(0, icon(self._EXT_CATEGORY_ICONS.get(cat, "plug")))
            cat_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            cat_item.setData(0, Qt.UserRole, ("category", cat))
            self.ext_tree.addTopLevelItem(cat_item)
            if cat == "ms365":
                conns = self.ctx.config.ms365.get("connectors", {})
                for key, label in self._MS365_BUILTIN_LABELS.items():
                    b = QTreeWidgetItem([f"{label} — {tr('ext.mode_builtin')}"])
                    b.setFlags(b.flags() | Qt.ItemIsUserCheckable)
                    b.setCheckState(0, Qt.Checked if conns.get(key) else Qt.Unchecked)
                    b.setData(0, Qt.UserRole, ("ms365_builtin", "ms365", key))
                    cat_item.addChild(b)
            for idx, entry in enumerate(ext.get(cat, [])):
                mode_label = tr("ext.mode_mcp") if entry.get("mode") == "mcp_stdio" else tr("ext.mode_rest")
                child = QTreeWidgetItem([f"{entry.get('name', '')} — {mode_label}"])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if entry.get("enabled") else Qt.Unchecked)
                child.setData(0, Qt.UserRole, ("connector", cat, idx))
                cat_item.addChild(child)
            if cat == "other":
                # Jira is a built-in "Other" connector (like OneDrive under MS365):
                # checkbox = enabled; double-click opens its minimal setup dialog.
                jira = self.ctx.config.data.get("jira", {})
                configured = bool(jira.get("base_url") and jira.get("email")
                                  and jira.get("api_token"))
                jstate = tr("connectors.jira_connected") if configured else tr("connectors.jira_not_set")
                jrow = QTreeWidgetItem([f"Jira — {tr('ext.mode_builtin')} · {jstate}"])
                jrow.setIcon(0, icon("link"))
                jrow.setFlags(jrow.flags() | Qt.ItemIsUserCheckable)
                on = configured and jira.get("enabled", True)
                jrow.setCheckState(0, Qt.Checked if on else Qt.Unchecked)
                jrow.setToolTip(0, tr("connectors.jira_setup_hint"))
                jrow.setData(0, Qt.UserRole, ("jira_builtin", "other"))
                cat_item.addChild(jrow)
            cat_item.setExpanded(True)
        self.ext_tree.blockSignals(False)

    def _on_ext_check(self, item: QTreeWidgetItem, _col: int) -> None:
        data = item.data(0, Qt.UserRole)
        if data and data[0] == "ms365_builtin":
            self.ctx.config.ms365.setdefault("connectors", {})[data[2]] = (
                item.checkState(0) == Qt.Checked)
            self.ctx.save()
            return
        if data and data[0] == "jira_builtin":
            self.ctx.config.data.setdefault("jira", {})["enabled"] = (
                item.checkState(0) == Qt.Checked)
            self.ctx.save()
            return
        if not data or data[0] != "connector":
            return
        _, cat, idx = data
        entries = self.ctx.config.ext_connectors.get(cat, [])
        if 0 <= idx < len(entries):
            entries[idx]["enabled"] = item.checkState(0) == Qt.Checked
            self.ctx.save()

    def _current_ext_category(self) -> str:
        item = self.ext_tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        return data[1] if data else EXT_CATEGORIES[0]

    def _current_ext_connector(self):
        item = self.ext_tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        if not data or data[0] != "connector":
            return None
        _, cat, idx = data
        entries = self.ctx.config.ext_connectors.get(cat, [])
        return (cat, entries[idx]) if 0 <= idx < len(entries) else None

    # ---- CRUD -----------------------------------------------------------------
    def _ext_add(self) -> None:
        dlg = ExtConnectorEditDialog(self, category=self._current_ext_category())
        if dlg.exec():
            entry = dlg.result_connector()
            self.ctx.config.ext_connectors.setdefault(entry["category"], []).append(entry)
            self.ctx.save()
            self._reload_ext_tree()

    def _ext_edit(self) -> None:
        """Configure the selected row. Built-in Jira → its minimal dialog;
        a normal connector → the MCP/REST editor."""
        item = self.ext_tree.currentItem()
        data = item.data(0, Qt.UserRole) if item else None
        if data and data[0] == "jira_builtin":
            self._open_jira_dialog()
            return
        current = self._current_ext_connector()
        if current is None:
            return
        cat, entry = current
        dlg = ExtConnectorEditDialog(self, category=cat, connector=entry)
        if dlg.exec():
            entry.update(dlg.result_connector())
            self.ctx.save()
            self._reload_ext_tree()

    def _open_jira_dialog(self) -> None:
        JiraConnectDialog(self.ctx, self).exec()
        self._reload_ext_tree()

    def _ext_delete(self) -> None:
        current = self._current_ext_connector()
        if current is None:
            return
        cat, entry = current
        if QMessageBox.question(
                self, tr("settings.ext_delete_btn"),
                tr("settings.ext_delete_confirm", name=entry.get("name", ""))) != QMessageBox.Yes:
            return
        self.ctx.config.ext_connectors[cat].remove(entry)
        self.ctx.save()
        self._reload_ext_tree()

    def _refresh_ms365_local_status(self) -> None:
        from .. import paths
        root = paths.primary_onedrive_root()
        if root is not None:
            self.ms365_local_status.setText(tr("settings.ms365_local_connected", path=str(root)))
        else:
            self.ms365_local_status.setText(tr("settings.ms365_local_none"))

    def _on_connect_external_toggled(self, on: bool) -> None:
        self.ctx.config.set_connect_external(on)
        self._apply_connect_external_enabled(on)

    def _apply_connect_external_enabled(self, on: bool) -> None:
        """Grey out the per-connector setup when the master switch is off — the
        agent won't connect to any of them anyway."""
        for w in (self.ext_tree, self.add_btn, self.edit_btn, self.del_btn):
            w.setEnabled(on)

    def _retranslate(self) -> None:
        self.connect_external_chk.setText(tr("connectors.connect_external"))
        self.connect_external_chk.setToolTip(tr("connectors.connect_external_tooltip"))
        self.add_btn.setText(tr("settings.ext_add_btn"))
        self.edit_btn.setText(tr("settings.ext_edit_btn"))
        self.del_btn.setText(tr("settings.ext_delete_btn"))
        self.dbl_hint.setText(tr("connectors.dbl_configure"))
        self._refresh_ms365_local_status()
        self._reload_ext_tree()
        self._apply_connect_external_enabled(self.ctx.config.connect_external)
