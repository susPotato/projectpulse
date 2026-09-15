"""Settings dialog: AI provider, Sandbox, the unified Connectors (MCP) group
(CAD / CAE / MS365 / Other — MCP servers + REST connectors in one place),
and the merged "Parameter" group (Cowork / Attachments / GraphRAG caps)."""
from __future__ import annotations

from typing import Dict

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget,
)

from ..config import PROVIDER_LABELS
from ..core.ext_connectors import CATEGORIES as EXT_CATEGORIES
from ..core.worker import AgentWorker
from ..i18n import LANGUAGES, tr
from ..state import AppContext
from .icons import icon, IconLabel
from .ext_connector_dialog import ExtConnectorEditDialog


class SettingsDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(tr("settings.title"))
        self.setMinimumWidth(560)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMinimizeButtonHint
            | Qt.WindowMaximizeButtonHint
        )
        self.setSizeGripEnabled(True)
        self.setStyleSheet(
            "QGroupBox { background: transparent;"
            " border: 1px solid rgba(140,146,152,0.35); }")
        data = ctx.config.data

        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._content = QWidget()
        root = QVBoxLayout(self._content)

        # --- language + tray ---
        top = QFormLayout()
        self.language_combo = QComboBox()
        for key, label in LANGUAGES.items():
            self.language_combo.addItem(label, key)
        self._select_combo(self.language_combo, ctx.config.language)
        top.addRow(tr("settings.language"), self.language_combo)

        self.tray_chk = QCheckBox(tr("settings.tray_keep"))
        self.tray_chk.setChecked(bool(data.get("tray", {}).get("minimize_on_close", True)))
        top.addRow("", self.tray_chk)
        self.notify_chk = QCheckBox(tr("settings.tray_notify"))
        self.notify_chk.setChecked(bool(data.get("tray", {}).get("notify_on_done", True)))
        top.addRow("", self.notify_chk)
        root.addLayout(top)

        self._load_workers = []

        # --- AI Provider ---
        self._prov_staging: Dict[str, dict] = {
            key: dict(conf) for key, conf in data["providers"].items()
        }
        self.provider_combo = QComboBox()
        for key, label in PROVIDER_LABELS.items():
            self.provider_combo.addItem(label, key)
        self._select_combo(self.provider_combo, ctx.config.active_provider)
        self._prov_current_key = self.provider_combo.currentData()

        conf = self._prov_staging.get(self._prov_current_key, {})
        self.prov_base = QLineEdit(conf.get("base_url", ""))
        self.prov_key = self._secret(conf.get("api_key", ""))
        self.prov_model = self._model_combo(conf.get("model", ""))
        self.prov_status = QLabel("")
        self.prov_status.setObjectName("hint")
        self.prov_status.setWordWrap(True)
        prov_group = self._group(tr("settings.group.provider"), [
            (tr("settings.active_provider"), self.provider_combo),
            (tr("settings.base_url"), self.prov_base),
            (tr("settings.api_key"), self.prov_key),
            (tr("settings.model"), self._with_load(self.prov_model, self.prov_status)),
        ])
        prov_group.layout().addRow("", self.prov_status)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_edit_changed)
        root.addWidget(prov_group)

        # --- Sandbox Security Layer ---
        sec = ctx.config.agent_security
        self.sandbox_group = QGroupBox(tr("settings.group.sandbox"))
        sbl = QVBoxLayout(self.sandbox_group)

        # --- Password protection for Sandbox Security (at top) ---
        self.sandbox_pw_label = IconLabel("lock", "Sandbox Security Password")
        sbl.addWidget(self.sandbox_pw_label)

        pw_row = QHBoxLayout()
        self.sandbox_pw_edit = QLineEdit("")
        self.sandbox_pw_edit.setPlaceholderText("Enter password to edit sandbox settings")
        self.sandbox_pw_edit.setEchoMode(QLineEdit.Password)
        pw_row.addWidget(self.sandbox_pw_edit, 1)
        self.sandbox_unlock_btn = QPushButton("Unlock")
        self.sandbox_unlock_btn.clicked.connect(self._sandbox_unlock)
        pw_row.addWidget(self.sandbox_unlock_btn)
        self.sandbox_locked_status = IconLabel("lock", "Locked (changes disabled)", color="#c00")
        self.sandbox_locked_status.text_label().setStyleSheet("color: #c00; font-weight: bold;")
        pw_row.addWidget(self.sandbox_locked_status)
        sbl.addLayout(pw_row)
        self._sandbox_unlocked = False  # Start LOCKED — must enter password first
        self._sandbox_pw = sec.get("sandbox_pw", "quandh14")

        # Separator line between pw section and sandbox settings
        pw_sep = QLabel("────────────────")
        sbl.addWidget(pw_sep)

        self.sandbox_confirm = QCheckBox(tr("settings.sandbox_confirm_commands"))
        self.sandbox_confirm.setChecked(bool(sec.get("cowork_confirm_commands", False)))
        self.sandbox_confirm.setToolTip(tr("settings.sandbox_confirm_commands_tooltip"))
        sbl.addWidget(self.sandbox_confirm)

        self.sandbox_block_network = QCheckBox(tr("settings.sandbox_block_network"))
        self.sandbox_block_network.setChecked(bool(sec.get("block_network", True)))
        self.sandbox_block_network.setToolTip(tr("settings.sandbox_block_network_tooltip"))
        sbl.addWidget(self.sandbox_block_network)

        # "Allow the agent to fetch URLs" + the live "Test Internet" self-test
        # moved to Monitoring → Tools → Tool (they govern a tool capability, so
        # they belong with the other tool toggles — see ToolsAdminTab).

        # --- Enable/Disable Agent Security ---
        self.sec_enabled = QCheckBox("Enable Agent Security (command validation)")
        self.sec_enabled.setChecked(bool(sec.get("enabled", True)))
        self.sec_enabled.setToolTip("Bật/tắt toàn bộ Agent Security")
        sbl.addWidget(self.sec_enabled)

        # --- AI Command Check toggle ---
        self.ai_check = QCheckBox("AI check commands")
        self.ai_check.setChecked(bool(sec.get("command_ai_check", False)))
        self.ai_check.setToolTip("Cho AI control-agent xét lệnh trước khi chạy")
        sbl.addWidget(self.ai_check)

        # Resource limits (CPU/Memory/Disk I/O) moved to the Parameter group
        # below — see _param_section("settings.group.sandbox_limits").

        # Collect all sandbox-editable widgets and lock them until unlocked
        self._sandbox_widgets = [
            self.sandbox_confirm, self.sandbox_block_network,
            self.ai_check, self.sec_enabled,
        ]
        for _w in self._sandbox_widgets:
            _w.setEnabled(False)

        root.addWidget(self.sandbox_group)

        # Connectors (MCP / REST API) are managed entirely in Monitoring → Tools
        # → Connector now — no connector UI in Settings. (_ms365_workers is kept
        # for the dead-but-retained MS365 OAuth sign-in handlers below.)
        self._ms365_workers = []

        # --- Parameter ---
        param_group = QGroupBox(tr("settings.group.parameter"))
        pgl = QFormLayout(param_group)

        def _param_section(key: str) -> None:
            lbl = QLabel(tr(key))
            lbl.setStyleSheet("font-weight:600; margin-top:6px;")
            pgl.addRow(lbl)

        # Parallel-conversation limit removed — conversations and flows now run
        # unlimited in parallel (no cap, no Settings row).
        att = data.get("attachments", {})
        _param_section("settings.group.attachments")
        self.attach_files = QSpinBox()
        self.attach_files.setRange(1, 50)
        self.attach_files.setSuffix(tr("settings.max_files_suffix"))
        self.attach_files.setValue(max(1, int(att.get("max_files", 20))))
        self.attach_files.setToolTip(tr("settings.max_files_tooltip"))
        self.attach_tokens = QSpinBox()
        self.attach_tokens.setRange(1, 1000)
        self.attach_tokens.setSingleStep(5)
        self.attach_tokens.setSuffix(tr("settings.max_per_file_suffix"))
        self.attach_tokens.setValue(max(1, int(att.get("max_tokens", 500000)) // 1000))
        self.attach_tokens.setToolTip(tr("settings.max_per_file_tooltip"))
        pgl.addRow(tr("settings.max_files"), self.attach_files)
        pgl.addRow(tr("settings.max_per_file"), self.attach_tokens)

        st = data.get("structure", {})
        _param_section("settings.group.structure")
        self.struct_nodes = QSpinBox()
        self.struct_nodes.setRange(0, 100000)
        self.struct_nodes.setSpecialValueText(tr("settings.unlimited"))
        self.struct_nodes.setSuffix(tr("settings.nodes_suffix"))
        self.struct_nodes.setValue(max(0, int(st.get("max_nodes", 500))))
        self.struct_nodes.setToolTip(tr("settings.nodes_tooltip"))
        self.struct_edges = QSpinBox()
        self.struct_edges.setRange(0, 200000)
        self.struct_edges.setSpecialValueText(tr("settings.unlimited"))
        self.struct_edges.setSuffix(tr("settings.edges_suffix"))
        self.struct_edges.setValue(max(0, int(st.get("max_edges", 500))))
        self.struct_edges.setToolTip(tr("settings.edges_tooltip"))
        pgl.addRow(tr("settings.max_nodes"), self.struct_nodes)
        pgl.addRow(tr("settings.max_edges"), self.struct_edges)

        # Sandbox resource limits (CPU / Memory / Disk I/O) — moved here from
        # the Sandbox Security group; still stored under agent_security.*.
        _param_section("settings.group.sandbox_limits")
        self.sandbox_cpu = QSpinBox()
        self.sandbox_cpu.setRange(0, 100_000)
        self.sandbox_cpu.setSuffix(" %")
        self.sandbox_cpu.setSpecialValueText(tr("settings.sandbox_unlimited"))
        self.sandbox_cpu.setValue(int(sec.get("resource_limit_cpu_percent", 0) or 0))
        pgl.addRow(tr("settings.sandbox_cpu_label"), self.sandbox_cpu)

        self.sandbox_memory = QSpinBox()
        self.sandbox_memory.setRange(0, 1_000_000)
        self.sandbox_memory.setSuffix(" MB")
        self.sandbox_memory.setSpecialValueText(tr("settings.sandbox_unlimited"))
        self.sandbox_memory.setValue(int(sec.get("resource_limit_memory_mb", 2048) or 2048))
        pgl.addRow(tr("settings.sandbox_memory_label"), self.sandbox_memory)

        self.sandbox_disk = QSpinBox()
        self.sandbox_disk.setRange(0, 1_000_000)
        self.sandbox_disk.setSuffix(" MB")
        self.sandbox_disk.setSpecialValueText(tr("settings.sandbox_unlimited"))
        self.sandbox_disk.setValue(int(sec.get("resource_limit_disk_mb", 2048) or 2048))
        pgl.addRow(tr("settings.sandbox_disk_label"), self.sandbox_disk)

        root.addWidget(param_group)

        # ---- Auto Model Routing ------------------------------------------
        routing = self.ctx.config.routing
        routing_group = QGroupBox(tr("routing.settings_group"))
        rgl = QFormLayout(routing_group)

        self.routing_mode = QComboBox()
        for value, key in (("off", "routing.mode_off"), ("auto", "routing.mode_auto"),
                           ("manual", "routing.mode_manual")):
            self.routing_mode.addItem(tr(key), value)
        self._select_combo(self.routing_mode, routing.get("switch_mode", "off"))
        rgl.addRow(tr("routing.settings_mode"), self.routing_mode)

        self.routing_policy = QComboBox()
        for value, key in (("quality", "routing.policy_quality"), ("cost", "routing.policy_cost"),
                           ("latency", "routing.policy_latency"), ("balanced", "routing.policy_balanced")):
            self.routing_policy.addItem(tr(key), value)
        self._select_combo(self.routing_policy, routing.get("policy", "balanced"))
        rgl.addRow(tr("routing.settings_policy"), self.routing_policy)

        # Min score gain stored as a fraction (0..1); shown as a percentage.
        self.routing_min_gain = QSpinBox()
        self.routing_min_gain.setRange(0, 100)
        self.routing_min_gain.setSuffix(" %")
        self.routing_min_gain.setValue(int(round(float(routing.get("min_score_gain", 0.05)) * 100)))
        rgl.addRow(tr("routing.settings_min_gain"), self.routing_min_gain)

        self.routing_timeout = QSpinBox()
        self.routing_timeout.setRange(5, 600)
        self.routing_timeout.setSuffix(" s")
        self.routing_timeout.setValue(int(routing.get("confirm_timeout_sec", 60) or 60))
        rgl.addRow(tr("routing.settings_timeout"), self.routing_timeout)

        self.routing_interval = QSpinBox()
        self.routing_interval.setRange(0, 720)
        self.routing_interval.setSpecialValueText(tr("routing.mode_off"))  # 0 = disabled
        self.routing_interval.setSuffix(" h")
        self.routing_interval.setValue(int(routing.get("reassess_interval_hours", 24) or 0))
        rgl.addRow(tr("routing.settings_interval"), self.routing_interval)

        self.routing_concurrency = QSpinBox()
        self.routing_concurrency.setRange(1, 16)
        self.routing_concurrency.setValue(int(routing.get("per_provider_concurrency", 2) or 2))
        rgl.addRow(tr("routing.settings_concurrency"), self.routing_concurrency)

        self.routing_judge = QLineEdit(routing.get("judge_model", ""))
        rgl.addRow(tr("routing.settings_judge"), self.routing_judge)

        self.routing_reassess_btn = QPushButton(tr("routing.settings_reassess_now"))
        self.routing_reassess_btn.clicked.connect(self._routing_reassess_now)
        rgl.addRow("", self.routing_reassess_btn)

        rhint = QLabel(tr("routing.settings_hint"))
        rhint.setObjectName("hint")
        rhint.setWordWrap(True)
        rgl.addRow(rhint)
        root.addWidget(routing_group)

        note = QLabel(tr("settings.tip"))
        note.setObjectName("hint")
        root.addWidget(note)

        scroll.setWidget(self._content)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        from .widgets import guard_wheel
        guard_wheel(self)

        screen = QGuiApplication.primaryScreen()
        if screen:
            avail = screen.availableGeometry()
            self.resize(640, min(740, avail.height() - 80))
            self.setMaximumHeight(avail.height())

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def _secret(value: str) -> QLineEdit:
        edit = QLineEdit(value)
        edit.setEchoMode(QLineEdit.Password)
        return edit

    @staticmethod
    def _select_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    @staticmethod
    def _group(title: str, rows) -> QGroupBox:
        box = QGroupBox(title)
        form = QFormLayout(box)
        for label, widget in rows:
            form.addRow(label, widget)
        return box

    def _routing_reassess_now(self) -> None:
        """Kick off a manual model reassessment in the background."""
        try:
            service = self.ctx.routing()
            if service.is_reassessing():
                return
            self.routing_reassess_btn.setEnabled(False)
            self.routing_reassess_btn.setText(tr("routing.reassessing"))

            def _done(result) -> None:
                # Re-enable from the (worker) callback; label reflects the count.
                self.routing_reassess_btn.setEnabled(True)
                self.routing_reassess_btn.setText(
                    tr("routing.reassess_done", count=len(result or {})))

            service.reassess_background(on_done=_done)
        except Exception:  # noqa: BLE001 — a reassess click must never crash Settings
            self.routing_reassess_btn.setEnabled(True)
            self.routing_reassess_btn.setText(tr("routing.settings_reassess_now"))

    @staticmethod
    def _model_combo(value: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        if value:
            combo.addItem(value)
            combo.setCurrentText(value)
        return combo

    def _with_load(self, combo: QComboBox, status: QLabel) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(combo, 1)
        btn = QPushButton(tr("settings.load"))
        btn.setIcon(icon("download"))
        btn.setToolTip(tr("settings.load_tooltip"))
        btn.clicked.connect(
            lambda: self._load_models(self.provider_combo.currentData(), combo, status))
        lay.addWidget(btn)
        test_btn = QPushButton(tr("settings.test_connection"))
        test_btn.setIcon(icon("flask"))
        test_btn.setToolTip(tr("settings.test_connection_tooltip"))
        test_btn.clicked.connect(
            lambda: self._test_connection(self.provider_combo.currentData(), status))
        lay.addWidget(test_btn)
        return row

    def _stash_provider_fields(self) -> None:
        staged = self._prov_staging.setdefault(self._prov_current_key, {})
        staged.update({
            "base_url": self.prov_base.text().strip(),
            "api_key": self.prov_key.text(),
            "model": self.prov_model.currentText().strip(),
        })

    def _on_provider_edit_changed(self) -> None:
        self._stash_provider_fields()
        self._prov_current_key = self.provider_combo.currentData()
        conf = self._prov_staging.get(self._prov_current_key, {})
        self.prov_base.setText(conf.get("base_url", ""))
        self.prov_key.setText(conf.get("api_key", ""))
        self.prov_model.clear()
        if conf.get("model"):
            self.prov_model.addItem(conf["model"])
            self.prov_model.setCurrentText(conf["model"])
        else:
            self.prov_model.setCurrentText("")
        self.prov_status.setText("")

    def _current_conf(self, provider: str) -> dict:
        if provider == self._prov_current_key:
            return {"base_url": self.prov_base.text().strip(), "api_key": self.prov_key.text(),
                    "model": self.prov_model.currentText().strip()}
        conf = self._prov_staging.get(provider, {})
        return {"base_url": conf.get("base_url", ""), "api_key": conf.get("api_key", ""),
                "model": conf.get("model", "")}

    # ---- MS365 zero-config sign-in ("connect like Claude") ---------------
    def _refresh_ms365_status(self) -> None:
        from ..core.ms365_auth import current_identity
        who = current_identity(self.ctx.config)
        if who:
            self.ms365_status.setText(tr("settings.ms365_signed_in", who=who))
            self.ms365_signin_btn.setEnabled(False)
            self.ms365_signout_btn.setEnabled(True)
        else:
            self.ms365_status.setText(tr("settings.ms365_signed_out"))
            self.ms365_signin_btn.setEnabled(True)
            self.ms365_signout_btn.setEnabled(False)
        self.ms365_signin_btn.setText(tr("settings.ms365_signin_btn"))
        self.ms365_signout_btn.setText(tr("settings.ms365_signout_btn"))

    def _ms365_sign_in(self) -> None:
        from ..core.ms365_auth import current_identity, sign_in
        self.ms365_signin_btn.setEnabled(False)
        self.ms365_status.setText(tr("settings.ms365_signing_in"))
        cfg = self.ctx.config

        def job(worker):
            # on_code fires (worker thread) with the MSAL device-flow dict —
            # marshal it to the UI thread via the worker's event signal.
            return sign_in(lambda flow: worker.event.emit({"device_flow": flow}), cfg)

        def on_event(ev: dict) -> None:
            if "device_flow" in ev:
                self._show_ms365_device_code(ev["device_flow"])

        def done(_result) -> None:
            self._close_ms365_code_dialog()
            self.ctx.save()
            self._refresh_ms365_status()
            QMessageBox.information(
                self, tr("settings.ms365_signin_btn"),
                tr("settings.ms365_signed_in", who=current_identity(cfg)))

        def failed(err: str) -> None:
            self._close_ms365_code_dialog()
            self._refresh_ms365_status()
            QMessageBox.warning(self, tr("settings.ms365_signin_btn"), err)

        w = AgentWorker(job)
        w.event.connect(on_event)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._ms365_workers.append(w)
        w.start()

    def _close_ms365_code_dialog(self) -> None:
        dlg = getattr(self, "_ms365_code_dialog", None)
        if dlg is not None:
            dlg.close()
            self._ms365_code_dialog = None

    def _show_ms365_device_code(self, flow: dict) -> None:
        """Auto-open the sign-in page + show the one-time code in a COPYABLE,
        non-modal dialog (so the worker keeps polling and can auto-close it on
        success). The code is also copied to the clipboard immediately."""
        import webbrowser

        code = flow.get("user_code", "")
        url = flow.get("verification_uri", "https://microsoft.com/devicelogin")
        # Auto-copy the code so the user can just paste it.
        QGuiApplication.clipboard().setText(code)
        # Auto-open the browser to the (code-prefilled, if available) sign-in page.
        try:
            webbrowser.open(flow.get("verification_uri_complete") or url)
        except Exception:  # noqa: BLE001 — a headless box just shows the link to click
            pass

        self._close_ms365_code_dialog()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("settings.ms365_signin_btn"))
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        info = QLabel(tr("settings.ms365_code_hint", url=url))
        info.setWordWrap(True)
        info.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextBrowserInteraction)
        info.setOpenExternalLinks(True)
        lay.addWidget(info)

        code_row = QHBoxLayout()
        code_edit = QLineEdit(code)
        code_edit.setReadOnly(True)
        f = code_edit.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        code_edit.setFont(f)
        code_edit.setCursorPosition(0)
        copy_btn = QPushButton(tr("settings.ms365_copy_code"))
        copy_btn.setIcon(icon("document"))
        copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(code))
        open_btn = QPushButton(tr("settings.ms365_open_link"))
        open_btn.setIcon(icon("link"))
        open_btn.clicked.connect(lambda: webbrowser.open(flow.get("verification_uri_complete") or url))
        code_row.addWidget(code_edit, 1)
        code_row.addWidget(copy_btn)
        code_row.addWidget(open_btn)
        lay.addLayout(code_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)

        self._ms365_code_dialog = dlg
        dlg.show()   # non-modal — sign-in polling continues; done() closes it

    def _ms365_sign_out(self) -> None:
        from ..core.ms365_auth import sign_out_default
        sign_out_default(self.ctx.config)
        self._refresh_ms365_status()

    def _load_models(self, provider: str, combo: QComboBox, status: QLabel) -> None:
        conf = self._current_conf(provider)

        def job(worker):
            from ..providers import build_provider
            prov = build_provider(provider, conf)
            models = prov.list_models()
            return {"models": models, "error": getattr(prov, "last_error", "")}

        def done(result):
            models = result.get("models") or []
            current = combo.currentText().strip()
            combo.clear()
            if current:
                combo.addItem(current)
            for m in models:
                if m != current:
                    combo.addItem(m)
            combo.setCurrentText(current)
            error = result.get("error", "")
            if models:
                status.setText(tr("settings.loaded_models", n=len(models),
                    provider=PROVIDER_LABELS.get(provider, provider)))
            else:
                status.setText(tr("settings.load_models_error", err=error or
                                  tr("settings.load_models_error_unknown")))

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(lambda e: status.setText(tr("settings.load_failed", err=e)))
        self._load_workers.append(w)
        status.setText(tr("settings.loading_models"))
        w.start()

    def _test_connection(self, provider: str, status: QLabel) -> None:
        conf = self._current_conf(provider)

        def job(worker):
            from ..providers import build_provider
            ok, message = build_provider(provider, conf).test_connection()
            return {"ok": ok, "message": message}

        def done(result):
            ok = result.get("ok")
            status.setText(result.get("message", ""))
            status.setStyleSheet("color: #090;" if ok else "color: #c00;")

        def failed(e):
            status.setText(str(e))
            status.setStyleSheet("color: #c00;")

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._load_workers.append(w)
        status.setText(tr("settings.testing_connection"))
        w.start()

    def _sandbox_unlock(self) -> None:
        pw = self.sandbox_pw_edit.text()
        if pw == self._sandbox_pw:
            self._sandbox_unlocked = True
            self.sandbox_locked_status.setText("Unlocked")
            self.sandbox_locked_status.set_icon("unlock", "#090")
            self.sandbox_locked_status.text_label().setStyleSheet("color: #090; font-weight: bold;")
            # Enable all sandbox widgets
            for w in self._sandbox_widgets:
                w.setEnabled(True)
            QMessageBox.information(self, "Sandbox Security", "Sandbox settings unlocked.")
        else:
            QMessageBox.warning(self, "Wrong Password", "Password incorrect. Sandbox settings remain locked.")

    def _save(self) -> None:
        data = self.ctx.config.data
        data["active_provider"] = self.provider_combo.currentData()
        data["language"] = self.language_combo.currentData()

        self._stash_provider_fields()
        for key, staged in self._prov_staging.items():
            data["providers"].setdefault(key, {}).update({
                "base_url": staged.get("base_url", ""),
                "api_key": staged.get("api_key", ""),
                "model": staged.get("model", ""),
            })

        # NOTE: allow_url_fetch is managed in Monitoring → Tools → Tool now
        # (persisted there directly), so it is intentionally not written here.
        data.setdefault("agent_security", {}).update({
            "enabled": self.sec_enabled.isChecked(),
            "cowork_confirm_commands": self.sandbox_confirm.isChecked(),
            "block_network": self.sandbox_block_network.isChecked(),
            "command_ai_check": self.ai_check.isChecked(),
            "command_whitelist": [],
            "resource_limit_cpu_percent": self.sandbox_cpu.value(),
            "resource_limit_memory_mb": self.sandbox_memory.value(),
            "resource_limit_disk_mb": self.sandbox_disk.value(),
        })
        att = data.setdefault("attachments", {})
        att["max_tokens"] = self.attach_tokens.value() * 1000
        att["max_files"] = self.attach_files.value()
        st = data.setdefault("structure", {})
        st["max_nodes"] = self.struct_nodes.value()
        st["max_edges"] = self.struct_edges.value()
        tray = data.setdefault("tray", {})
        tray["minimize_on_close"] = self.tray_chk.isChecked()
        tray["notify_on_done"] = self.notify_chk.isChecked()

        r = data.setdefault("routing", {})
        r["switch_mode"] = self.routing_mode.currentData()
        r["policy"] = self.routing_policy.currentData()
        r["min_score_gain"] = self.routing_min_gain.value() / 100.0
        r["confirm_timeout_sec"] = self.routing_timeout.value()
        r["reassess_interval_hours"] = self.routing_interval.value()
        r["per_provider_concurrency"] = self.routing_concurrency.value()
        r["judge_model"] = self.routing_judge.text().strip()

        self.ctx.save()

        # Force-reload config so all parts of the app pick up the new settings immediately
        self.ctx.config._data = None  # invalidate cache
        self.ctx.config._agent_security = None

        self.accept()