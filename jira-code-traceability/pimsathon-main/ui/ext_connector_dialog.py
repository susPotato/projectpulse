"""Add/edit one External Connector entry (CAD/CAE/Office) — Settings'
"🏭 External Connectors" section (see settings_dialog.py for the list/CRUD).

Two connection modes, switched via ``mode_combo``:
  - MCP Server (stdio) — same shape as the plain "MCP Servers" section.
  - REST API — base URL + API key; the connector exposes ONE generic
    HTTP-request tool scoped to that base URL (see core/ext_connectors.py).
"""
from __future__ import annotations

import shlex
from typing import Optional

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core.ext_connectors import PRESETS
from ..i18n import tr


class ExtConnectorEditDialog(QDialog):
    def __init__(self, parent=None, category: str = "cad", connector: Optional[dict] = None):
        super().__init__(parent)
        connector = connector or {}
        self.category = connector.get("category", category)
        editing = bool(connector)
        self.setWindowTitle(tr("ext.edit_title") if editing else tr("ext.add_title"))
        self.setMinimumWidth(480)

        lay = QVBoxLayout(self)

        form = QFormLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItem(tr("ext.preset_custom"), "")
        for p in PRESETS.get(self.category, []):
            self.preset_combo.addItem(p["name"], p["id"])
        if editing:
            self.preset_combo.setEnabled(False)  # identity fixed once created
        form.addRow(tr("ext.preset_label"), self.preset_combo)

        self.name_edit = QLineEdit(connector.get("name", ""))
        self.name_edit.setPlaceholderText(tr("ext.name_placeholder"))
        form.addRow(tr("ext.name_label"), self.name_edit)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("ext.mode_mcp"), "mcp_stdio")
        self.mode_combo.addItem(tr("ext.mode_rest"), "rest_api")
        idx = self.mode_combo.findData(connector.get("mode", "mcp_stdio"))
        self.mode_combo.setCurrentIndex(max(0, idx))
        form.addRow(tr("ext.mode_label"), self.mode_combo)
        lay.addLayout(form)

        self.stack = QStackedWidget()

        mcp_page = QWidget()
        mcp_form = QFormLayout(mcp_page)
        self.command_edit = QLineEdit(connector.get("command", ""))
        self.command_edit.setPlaceholderText(tr("ext.command_placeholder"))
        mcp_form.addRow(tr("ext.command_label"), self.command_edit)
        self.args_edit = QLineEdit(" ".join(connector.get("args", []) or []))
        self.args_edit.setPlaceholderText(tr("ext.args_placeholder"))
        mcp_form.addRow(tr("ext.args_label"), self.args_edit)
        self.stack.addWidget(mcp_page)

        rest_page = QWidget()
        rest_form = QFormLayout(rest_page)
        self.base_url_edit = QLineEdit(connector.get("base_url", ""))
        self.base_url_edit.setPlaceholderText(tr("ext.base_url_placeholder"))
        rest_form.addRow(tr("ext.base_url_label"), self.base_url_edit)
        self.api_key_edit = QLineEdit(connector.get("api_key", ""))
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        rest_form.addRow(tr("ext.api_key_label"), self.api_key_edit)
        self.auth_header_edit = QLineEdit(connector.get("auth_header", "Authorization"))
        rest_form.addRow(tr("ext.auth_header_label"), self.auth_header_edit)
        self.auth_scheme_edit = QLineEdit(connector.get("auth_scheme", "Bearer"))
        rest_form.addRow(tr("ext.auth_scheme_label"), self.auth_scheme_edit)
        self.stack.addWidget(rest_page)

        lay.addWidget(self.stack)
        self.mode_combo.currentIndexChanged.connect(
            lambda i: self.stack.setCurrentIndex(self.mode_combo.currentData() != "mcp_stdio"))
        self.stack.setCurrentIndex(0 if self.mode_combo.currentData() == "mcp_stdio" else 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        self.status_label.setWordWrap(True)
        test_row = QHBoxLayout()
        test_btn = QPushButton(tr("ext.test_btn"))
        test_btn.clicked.connect(self._test_connection)
        test_row.addWidget(test_btn)
        test_row.addWidget(self.status_label, 1)
        lay.addLayout(test_row)

        self.preset_combo.currentIndexChanged.connect(self._apply_preset)
        idx = self.preset_combo.findData(connector.get("id", "") if editing else "")
        if idx > 0:
            self.preset_combo.setCurrentIndex(idx)

        self._enabled = bool(connector.get("enabled", False))

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _apply_preset(self) -> None:
        preset_id = self.preset_combo.currentData()
        if preset_id and not self.name_edit.text().strip():
            self.name_edit.setText(self.preset_combo.currentText())

    def _current_entry(self) -> dict:
        mode = self.mode_combo.currentData()
        preset_id = self.preset_combo.currentData()
        name = self.name_edit.text().strip()
        cid = preset_id or name.strip().lower().replace(" ", "_")
        args_text = self.args_edit.text().strip()
        return {
            "id": cid,
            "name": name,
            "category": self.category,
            "enabled": self._enabled,
            "mode": mode,
            "command": self.command_edit.text().strip(),
            "args": shlex.split(args_text) if args_text else [],
            "env": {},
            "base_url": self.base_url_edit.text().strip(),
            "api_key": self.api_key_edit.text(),
            "auth_header": self.auth_header_edit.text().strip() or "Authorization",
            "auth_scheme": self.auth_scheme_edit.text().strip(),
        }

    def _test_connection(self) -> None:
        entry = self._current_entry()
        if entry["mode"] == "rest_api":
            from ..core.ext_connectors import RestApiConnector

            ok, message = RestApiConnector(entry).test_connection()
        else:
            from ..core.mcp_client import McpServerConnection

            if not entry["command"]:
                ok, message = False, tr("ext.err_no_command")
            else:
                conn = McpServerConnection(entry["id"], entry["command"], entry["args"])
                try:
                    conn.start(timeout=10)
                    ok, message = True, tr("ext.test_mcp_ok")
                except Exception as exc:  # noqa: BLE001
                    ok, message = False, str(exc)
                finally:
                    conn.stop()
        prefix = "✓" if ok else "✗"
        self.status_label.setText(f"{prefix} {message}")

    def _on_accept(self) -> None:
        if not self.name_edit.text().strip():
            self.name_edit.setFocus()
            return
        entry = self._current_entry()
        if entry["mode"] == "mcp_stdio" and not entry["command"]:
            self.command_edit.setFocus()
            return
        if entry["mode"] == "rest_api" and not entry["base_url"]:
            self.base_url_edit.setFocus()
            return
        self.accept()

    def result_connector(self) -> dict:
        return self._current_entry()
