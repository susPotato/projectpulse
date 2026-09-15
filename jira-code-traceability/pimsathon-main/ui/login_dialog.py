"""Login screen shown once at startup, before ``MainWindow`` is ever built
(see ``app.py::run()``). Three paths, chosen automatically:

1. **Bootstrap** — no shared folder configured yet, or it's configured but
   still empty: a short setup form creates the shared folder path + the
   first Admin account, shows its generated 12-character code once, then
   logs straight in as that Admin.
2. **Normal login** — Account (auto-lowercased as typed) + 12-character code.
   An optional Department field (e.g. "FA.PDS") is offered on this page for
   filling it auto-creates/joins a Group of that exact name (see
   ``core/groups.py::find_or_create_by_name``), a convenience the user may
   skip entirely.
3. **Offline fallback** — the shared folder is configured but unreachable
   (VPN off, share down): if a previous login on this machine succeeded, its
   (username, role) — never the code — was cached locally and can be reused
   so the app stays usable off-network; a freshly revoked/edited account
   only takes effect once the shared folder is reachable again.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from ..core import accounts
from ..core.accounts import Account
from ..i18n import tr
from ..state import AppContext


class _AccountEdit(QLineEdit):
    """Account field: alnum/./- only, auto-lowercased as the user types."""

    def __init__(self):
        super().__init__()
        self.setMaxLength(64)
        self.textChanged.connect(self._normalize)

    def _normalize(self, text: str) -> None:
        cleaned = re.sub(r"[^\w.\-]", "", text.lower())
        if cleaned == text:
            return
        cur = self.cursorPosition()
        self.blockSignals(True)
        self.setText(cleaned)
        self.setCursorPosition(min(cur, len(cleaned)))
        self.blockSignals(False)


class LoginDialog(QDialog):
    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.account: Optional[Account] = None
        self.setWindowTitle(tr("login.title"))
        self.setMinimumWidth(380)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        root = QVBoxLayout(self)
        header = QLabel(tr("login.header"))
        header.setStyleSheet("font-weight:700; font-size:16px;")
        root.addWidget(header)

        self._stack = QStackedWidget()
        root.addWidget(self._stack)

        shared_dir = self.ctx.config.shared_dir
        reachable = self._is_reachable(shared_dir)
        needs_bootstrap = (not shared_dir) or (
            reachable and not accounts.list_accounts(accounts.accounts_dir(shared_dir)))

        if needs_bootstrap:
            self._stack.addWidget(self._build_bootstrap_page())
        elif not reachable:
            self._stack.addWidget(self._build_offline_page(shared_dir))
        else:
            self._stack.addWidget(self._build_login_page(shared_dir))
        self._stack.setCurrentIndex(0)

        exit_btn = QPushButton(tr("login.exit_btn"))
        exit_btn.clicked.connect(self.reject)
        root.addWidget(exit_btn, alignment=Qt.AlignRight)

    # ---- helpers -----------------------------------------------------
    @staticmethod
    def _is_reachable(shared_dir: str) -> bool:
        if not shared_dir:
            return False
        try:
            return Path(shared_dir).expanduser().exists()
        except OSError:
            return False

    def _finish_login(self, account: Account) -> None:
        self.account = account
        accounts.save_last_login(account.username, account.role)
        self.ctx.config.auth["last_account"] = account.username
        self.ctx.save()
        if account.code:   # offline fallback has no real code — never cache an empty one
            try:
                import keyring

                keyring.set_password("cowork_local_login", account.username, account.code)
            except Exception:  # noqa: BLE001 — no OS credential store available
                pass
        self.accept()

    # ---- bootstrap (no shared folder / no accounts yet) ---------------
    def _build_bootstrap_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(tr("login.bootstrap_hint")))
        form = QFormLayout()
        self.bs_dir_edit = QLineEdit(self.ctx.config.shared_dir)
        browse_btn = QPushButton(tr("login.browse"))
        from .icons import icon
        browse_btn.setIcon(icon("folder"))
        browse_btn.clicked.connect(self._bs_browse)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self.bs_dir_edit, 1)
        dir_row.addWidget(browse_btn)
        form.addRow(tr("login.shared_dir"), dir_row)
        self.bs_user_edit = _AccountEdit()
        form.addRow(tr("login.account"), self.bs_user_edit)
        lay.addLayout(form)
        self.bs_error = QLabel("")
        self.bs_error.setObjectName("warning")
        self.bs_error.setWordWrap(True)
        lay.addWidget(self.bs_error)
        create_btn = QPushButton(tr("login.create_admin"))
        create_btn.setObjectName("primary")
        create_btn.clicked.connect(self._bs_create_admin)
        lay.addWidget(create_btn)
        return page

    def _bs_browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("login.shared_dir"))
        if chosen:
            self.bs_dir_edit.setText(chosen)

    def _bs_create_admin(self) -> None:
        shared_dir = self.bs_dir_edit.text().strip()
        username = self.bs_user_edit.text().strip()
        if not shared_dir or not username:
            self.bs_error.setText(tr("login.err_missing_fields"))
            return
        directory = accounts.accounts_dir(shared_dir)
        try:
            if accounts.admin_exists(directory) or not accounts.claim_admin_slot(directory):
                self.bs_error.setText(tr("login.err_admin_exists"))
                self.ctx.config.auth["shared_dir"] = shared_dir
                self.ctx.save()
                self._retry()
                return
            existing = {a.code for a in accounts.list_accounts(directory)}
            account = accounts.new_account(
                username, "admin", created_by="bootstrap", existing_codes=existing)
            accounts.save_account(account, directory)
        except OSError as exc:
            self.bs_error.setText(tr("login.err_shared_dir", error=str(exc)))
            return
        self.ctx.config.auth["shared_dir"] = shared_dir
        self.ctx.save()
        QMessageBox.information(
            self, tr("login.code_shown_title"),
            tr("login.code_shown_body", username=account.username, code=account.code))
        self._finish_login(account)

    # ---- normal login ---------------------------------------------------
    def _build_login_page(self, shared_dir: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        form = QFormLayout()
        self.user_edit = _AccountEdit()
        last_account = self.ctx.config.auth.get("last_account", "")
        if last_account:
            self.user_edit.setText(last_account)
        form.addRow(tr("login.account"), self.user_edit)
        self.code_edit = QLineEdit()
        self.code_edit.setEchoMode(QLineEdit.Password)
        self.code_edit.setMaxLength(accounts.CODE_LENGTH)
        if last_account:
            try:
                import keyring

                cached_code = keyring.get_password("cowork_local_login", last_account)
                if cached_code:
                    self.code_edit.setText(cached_code)
            except Exception:  # noqa: BLE001
                pass
        form.addRow(tr("login.code"), self.code_edit)
        self.department_edit = QLineEdit(self.ctx.config.auth.get("last_department", ""))
        self.department_edit.setPlaceholderText(tr("login.department_placeholder"))
        form.addRow(tr("login.department"), self.department_edit)
        lay.addLayout(form)

        self.login_error = QLabel("")
        self.login_error.setObjectName("warning")
        self.login_error.setWordWrap(True)
        lay.addWidget(self.login_error)

        login_btn = QPushButton(tr("login.login_btn"))
        login_btn.setObjectName("primary")
        login_btn.clicked.connect(lambda: self._do_login(shared_dir))
        lay.addWidget(login_btn)

        return page

    def _do_login(self, shared_dir: str) -> None:
        username = self.user_edit.text().strip()
        code = self.code_edit.text().strip()
        directory = accounts.accounts_dir(shared_dir)
        account = accounts.verify_login(username, code, directory)
        if account is None:
            self.login_error.setText(tr("login.err_invalid"))
            return
        self._apply_department(account, shared_dir, self.department_edit.text())
        self._finish_login(account)

    def _apply_department(self, account: Account, shared_dir: str, department: str) -> None:
        """Optional, login-time-only convenience: record the typed Department
        on the account and auto-create/join a same-named Group — skipped
        entirely when left blank. Never touches role/admin state."""
        department = (department or "").strip()
        self.ctx.config.auth["last_department"] = department
        if not department:
            return
        from ..core import groups

        acc_dir = accounts.accounts_dir(shared_dir)
        if account.department != department:
            account.department = department
            accounts.save_account(account, acc_dir)
        group = groups.find_or_create_by_name(department, groups.groups_dir(shared_dir))
        if account.group_id != group.group_id:
            account.group_id = group.group_id
            accounts.save_account(account, acc_dir)
        groups.ensure_member(group, account.username, groups.groups_dir(shared_dir))

    # ---- offline fallback (shared folder configured but unreachable) ----
    def _build_offline_page(self, shared_dir: str) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel(tr("login.unreachable", path=shared_dir)))
        cached = accounts.load_last_login()
        if cached:
            username, role = cached
            lay.addWidget(QLabel(tr("login.offline_hint", username=username, role=role)))
            offline_btn = QPushButton(tr("login.offline_btn", role=role))
            offline_btn.setObjectName("primary")
            offline_btn.clicked.connect(lambda: self._finish_login(
                Account(username=username, role=role, code="")))
            lay.addWidget(offline_btn)
        else:
            lay.addWidget(QLabel(tr("login.no_offline_cache")))
        retry_btn = QPushButton(tr("login.retry_btn"))
        retry_btn.clicked.connect(self._retry)
        lay.addWidget(retry_btn)
        return page

    def _retry(self) -> None:
        self._stack.removeWidget(self._stack.currentWidget())
        shared_dir = self.ctx.config.shared_dir
        reachable = self._is_reachable(shared_dir)
        needs_bootstrap = (not shared_dir) or (
            reachable and not accounts.list_accounts(accounts.accounts_dir(shared_dir)))
        if needs_bootstrap:
            self._stack.addWidget(self._build_bootstrap_page())
        elif not reachable:
            self._stack.addWidget(self._build_offline_page(shared_dir))
        else:
            self._stack.addWidget(self._build_login_page(shared_dir))
        self._stack.setCurrentIndex(0)


def show_login(ctx: AppContext) -> Optional[Account]:
    """Run the login flow; ``None`` means the user cancelled (caller must not
    proceed to build ``MainWindow``)."""
    dlg = LoginDialog(ctx)
    if dlg.exec():
        return dlg.account
    return None