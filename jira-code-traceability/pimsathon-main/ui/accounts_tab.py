"""Accounts panel — lives inside Monitoring, Admin/Sub-admin only.

CRUD on accounts + groups (a small org tree: Group -> Sub-admin -> members),
plus a per-account usage/cost table sourced from the shared cross-machine
telemetry store (``core/telemetry_shared.py``) — no Microsoft Graph API, see
that module's docstring.

Sub-admin sees the exact same UI as Admin, just pre-filtered to their own
group (``groups.group_for_user``); they cannot create/delete other
sub-admins or reassign roles/groups outside their own group.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import accounts, groups, telemetry_shared
from ..core import usage_tracker as ut
from ..core.worker import AgentWorker
from ..i18n import on_language_changed, tr
from ..state import AppContext
from .icons import icon
from .widgets import fmt_tokens

_PERIODS = ("day", "week", "month", "year")
_PERIOD_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}


class _OrgTree(QTreeWidget):
    """Org tree with drag-and-drop member moving: drag an account item onto a
    group item to move it there. No confirm button — Qt's own drag gesture
    (press, drag onto a visibly-highlighted target, release) is already a
    deliberate action that can't happen from a stray single click, unlike the
    earlier explicit "enable move" button flow this replaces."""
    member_dropped = Signal(str, str)   # username, target_group_id ("" = ungrouped)

    def __init__(self):
        super().__init__()
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QTreeWidget.DragDrop)

    def set_drag_enabled(self, enabled: bool) -> None:
        self.setDragEnabled(enabled)
        self.setAcceptDrops(enabled)

    def _drop_kinds(self, event):
        """(target_kind, source_kind) tuples for the drop TARGET under the
        cursor and the drag SOURCE (``self.currentItem()``), used by all
        three drag/drop overrides so they gate identically."""
        target = self.itemAt(event.position().toPoint())
        source = self.currentItem()
        target_kind = target.data(0, Qt.UserRole) if target else None
        source_kind = source.data(0, Qt.UserRole) if source else None
        return target_kind, source_kind

    def _drop_is_valid(self, event) -> bool:
        target_kind, source_kind = self._drop_kinds(event)
        return bool(target_kind and target_kind[0] == "group"
                    and source_kind and source_kind[0] == "account")

    def dragEnterEvent(self, event) -> None:
        if self.dragEnabled() and self._drop_is_valid(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._drop_is_valid(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        """Handled entirely ourselves (data model + a full refresh()) —
        never delegates to Qt's own default reparenting, since the tree is
        always rebuilt from ``accounts``/``groups`` storage anyway."""
        if self._drop_is_valid(event):
            target_kind, source_kind = self._drop_kinds(event)
            event.acceptProposedAction()
            self.member_dropped.emit(source_kind[1], target_kind[1])
        else:
            event.ignore()


class AccountEditDialog(QDialog):
    """Add/Edit one Account. ``locked_role``/``locked_group_id`` (Sub-admin
    editing a member of their own group) disable the role/group pickers so a
    Sub-admin can't promote someone or move them out of their group."""

    def __init__(self, parent=None, account: Optional[accounts.Account] = None,
                available_groups: Optional[List[groups.Group]] = None,
                allow_role_edit: bool = True, allow_group_edit: bool = True,
                fixed_group_id: str = ""):
        super().__init__(parent)
        self._existing = account
        self.setWindowTitle(tr("accounts.edit_title") if account else tr("accounts.add_title"))
        self.resize(360, 280)
        form = QFormLayout(self)
        self.user_edit = QLineEdit(account.username if account else "")
        self.user_edit.setEnabled(account is None)   # username is the identity key — no rename
        form.addRow(tr("accounts.f_username"), self.user_edit)
        self.name_edit = QLineEdit(account.display_name if account else "")
        form.addRow(tr("accounts.f_display_name"), self.name_edit)
        self.email_edit = QLineEdit(account.email if account else "")
        self.email_edit.setPlaceholderText(tr("accounts.f_email_placeholder"))
        form.addRow(tr("accounts.f_email"), self.email_edit)
        self.role_combo = QComboBox()
        for r in accounts.ROLES:
            self.role_combo.addItem(tr(f"accounts.role.{r}"), r)
        if account:
            idx = self.role_combo.findData(account.role)
            if idx >= 0:
                self.role_combo.setCurrentIndex(idx)
        self.role_combo.setEnabled(allow_role_edit)
        form.addRow(tr("accounts.f_role"), self.role_combo)
        self.dept_edit = QLineEdit(account.department if account else "")
        form.addRow(tr("accounts.f_department"), self.dept_edit)
        self.group_combo = QComboBox()
        self.group_combo.addItem(tr("accounts.no_group"), "")
        for g in (available_groups or []):
            self.group_combo.addItem(g.name, g.group_id)
        preset_group = fixed_group_id or (account.group_id if account else "")
        idx = self.group_combo.findData(preset_group)
        if idx >= 0:
            self.group_combo.setCurrentIndex(idx)
        self.group_combo.setEnabled(allow_group_edit)
        form.addRow(tr("accounts.f_group"), self.group_combo)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_fields(self) -> Dict[str, str]:
        return {
            "username": self.user_edit.text().strip(),
            "display_name": self.name_edit.text().strip(),
            "email": self.email_edit.text().strip(),
            "role": self.role_combo.currentData(),
            "department": self.dept_edit.text().strip(),
            "group_id": self.group_combo.currentData() or "",
        }


class AccountsTab(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._current_username = ""

        root = QVBoxLayout(self)
        self._shared_hint = QLabel("")
        self._shared_hint.setObjectName("hint")
        self._shared_hint.setWordWrap(True)
        root.addWidget(self._shared_hint)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)

        # ---- left: org tree + CRUD buttons --------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)

        # Smart search: instant substring filter over the org tree, plus an
        # AI button that turns a natural-language query into keywords first.
        search_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.textChanged.connect(self._apply_tree_filter)
        self.ai_search_btn = QPushButton()
        self.ai_search_btn.setIcon(icon("sparkle"))
        self.ai_search_btn.clicked.connect(self._ai_search)
        search_row.addWidget(self.search_edit, 1)
        search_row.addWidget(self.ai_search_btn)
        ll.addLayout(search_row)

        # Scope the tree to just one Group (on top of the free-text search
        # above, which already searches within whichever groups are shown) —
        # handy once there are many groups and you only want to look at one.
        self.group_filter_combo = QComboBox()
        self.group_filter_combo.currentIndexChanged.connect(lambda _i: self._apply_tree_filter())
        ll.addWidget(self.group_filter_combo)

        self.tree = _OrgTree()
        self.tree.setHeaderHidden(True)
        self.tree.currentItemChanged.connect(self._on_tree_select)
        # Only Admin may drag a member into a different group — Sub-admin's
        # tree stays plain (they're scoped to their own group anyway).
        self.tree.set_drag_enabled(self._is_admin())
        self.tree.member_dropped.connect(self._on_member_dropped)
        ll.addWidget(self.tree, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton()
        self.add_btn.setIcon(icon("plus"))
        self.add_btn.clicked.connect(self._add_account)
        self.edit_btn = QPushButton()
        self.edit_btn.setIcon(icon("edit"))
        self.edit_btn.clicked.connect(self._edit_account)
        self.del_btn = QPushButton()
        self.del_btn.setIcon(icon("trash"))
        self.del_btn.clicked.connect(self._delete_account)
        self.code_btn = QPushButton()
        self.code_btn.setIcon(icon("key"))
        self.code_btn.clicked.connect(self._regenerate_code)
        for b in (self.add_btn, self.edit_btn, self.del_btn, self.code_btn):
            btns.addWidget(b)
        ll.addLayout(btns)

        # Group creation is an Admin-only capability — the button is HIDDEN
        # for every other role (a visible button whose click silently no-ops
        # was the old, confusing behaviour).
        self.group_btn = QPushButton()
        self.group_btn.setIcon(icon("folder"))
        self.group_btn.clicked.connect(self._add_group)
        self.group_btn.setVisible(self._is_admin())
        ll.addWidget(self.group_btn)

        # Bulk import from Excel (Admin only): download the template, fill a
        # row per person, import — groups + accounts are created together.
        excel_row = QHBoxLayout()
        self.excel_template_btn = QPushButton()
        self.excel_template_btn.setIcon(icon("download"))
        self.excel_template_btn.clicked.connect(self._export_excel_template)
        self.excel_import_btn = QPushButton()
        self.excel_import_btn.setIcon(icon("upload"))
        self.excel_import_btn.clicked.connect(self._import_excel)
        self.excel_template_btn.setVisible(self._is_admin())
        self.excel_import_btn.setVisible(self._is_admin())
        excel_row.addWidget(self.excel_template_btn)
        excel_row.addWidget(self.excel_import_btn)
        ll.addLayout(excel_row)
        split.addWidget(left)

        # ---- right: per-account usage/cost table --------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self._usage_title = QLabel("")
        self._usage_title.setStyleSheet("font-weight:700;")
        head.addWidget(self._usage_title, 1)
        self.period_combo = QComboBox()
        for p in _PERIODS:
            self.period_combo.addItem(tr(f"accounts.period.{p}"), p)
        self.period_combo.currentIndexChanged.connect(self.refresh)
        head.addWidget(self.period_combo)
        self.refresh_btn = QPushButton()
        self.refresh_btn.setIcon(icon("refresh"))
        self.refresh_btn.clicked.connect(self.refresh)
        head.addWidget(self.refresh_btn)
        rl.addLayout(head)

        self.usage_table = QTableWidget(0, 6)
        self.usage_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.usage_table.verticalHeader().setVisible(False)
        self.usage_table.horizontalHeader().setStretchLastSection(True)
        rl.addWidget(self.usage_table, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([260, 640])

        on_language_changed(self._retranslate)
        self._retranslate()
        self.refresh()

    # ---- role-scoped repository access ---------------------------------
    def _shared_dir(self) -> str:
        return self.ctx.config.shared_dir

    def _accounts_dir(self):
        return accounts.accounts_dir(self._shared_dir())

    def _groups_dir(self):
        return groups.groups_dir(self._shared_dir())

    def _is_admin(self) -> bool:
        return self.ctx.role == "admin"

    def _my_group(self) -> Optional[groups.Group]:
        acc = self.ctx.account
        if acc is None:
            return None
        return groups.group_for_user(acc.username, self._groups_dir())

    def _visible_groups(self) -> List[groups.Group]:
        all_groups = groups.list_groups(self._groups_dir())
        if self._is_admin():
            return all_groups
        mine = self._my_group()
        return [mine] if mine else []

    def _visible_accounts(self) -> List[accounts.Account]:
        all_accounts = accounts.list_accounts(self._accounts_dir())
        if self._is_admin():
            return all_accounts
        mine = self._my_group()
        if mine is None:
            return []
        members = set(mine.member_usernames) | {mine.subadmin_username}
        return [a for a in all_accounts if a.username in members]

    # ---- tree ------------------------------------------------------------
    def refresh(self) -> None:
        shared_dir = self._shared_dir()
        if not shared_dir:
            self._shared_hint.setText(tr("accounts.no_shared_dir"))
            self.tree.clear()
            self.usage_table.setRowCount(0)
            return
        self._shared_hint.setText(tr("accounts.shared_dir_hint", path=shared_dir))
        self._reload_tree()
        self._reload_usage_table()

    def _reload_group_filter_combo(self) -> None:
        """Repopulate the "scope to one group" combo, keeping whichever
        group_id was selected before (falls back to "All groups" if that
        group no longer exists — e.g. it was just deleted)."""
        current = self.group_filter_combo.currentData()
        self.group_filter_combo.blockSignals(True)
        self.group_filter_combo.clear()
        self.group_filter_combo.addItem(tr("accounts.filter_all_groups"), "")
        for g in self._visible_groups():
            self.group_filter_combo.addItem(g.name, g.group_id)
        idx = self.group_filter_combo.findData(current)
        self.group_filter_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_filter_combo.blockSignals(False)

    def _reload_tree(self) -> None:
        self._reload_group_filter_combo()
        self.tree.clear()
        my_accounts = {a.username: a for a in self._visible_accounts()}
        for g in self._visible_groups():
            g_item = QTreeWidgetItem([g.name])
            g_item.setIcon(0, icon("folder"))
            g_item.setData(0, Qt.UserRole, ("group", g.group_id))
            self.tree.addTopLevelItem(g_item)
            if g.subadmin_username and g.subadmin_username in my_accounts:
                acc = my_accounts.pop(g.subadmin_username)
                self._add_account_item(g_item, acc, is_subadmin=True)
            for uname in g.member_usernames:
                acc = my_accounts.pop(uname, None)
                if acc is not None:
                    self._add_account_item(g_item, acc)
            g_item.setExpanded(True)
        if my_accounts:
            ungrouped = QTreeWidgetItem([tr("accounts.ungrouped")])
            ungrouped.setIcon(0, icon("folder"))
            ungrouped.setData(0, Qt.UserRole, ("group", ""))
            self.tree.addTopLevelItem(ungrouped)
            for acc in my_accounts.values():
                self._add_account_item(ungrouped, acc)
            ungrouped.setExpanded(True)
        self._apply_tree_filter()   # a rebuilt tree must respect the active search

    def _add_account_item(self, parent: QTreeWidgetItem, acc: accounts.Account,
                          is_subadmin: bool = False) -> None:
        label = f"{acc.display_name or acc.username} ({acc.username}) — {tr(f'accounts.role.{acc.role}')}"
        item = QTreeWidgetItem([label])
        if is_subadmin:   # subadmin badge → star icon instead of a ★ glyph
            item.setIcon(0, icon("star", color="#f59e0b"))
        item.setData(0, Qt.UserRole, ("account", acc.username))
        if acc.email:
            item.setToolTip(0, acc.email)
        parent.addChild(item)

    def _on_tree_select(self, *_a) -> None:
        item = self.tree.currentItem()
        kind_id = item.data(0, Qt.UserRole) if item else None
        self._current_username = kind_id[1] if kind_id and kind_id[0] == "account" else ""
        self.refresh()

    # ---- account CRUD ------------------------------------------------------
    def _add_account(self) -> None:
        shared_dir = self._shared_dir()
        if not shared_dir:
            return
        fixed_group = "" if self._is_admin() else (self._my_group().group_id if self._my_group() else "")
        dlg = AccountEditDialog(
            self, available_groups=self._visible_groups(),
            allow_role_edit=self._is_admin(), allow_group_edit=self._is_admin(),
            fixed_group_id=fixed_group)
        if not dlg.exec():
            return
        fields = dlg.result_fields()
        if not fields["username"]:
            return
        directory = self._accounts_dir()
        # Single-admin invariant: the app has exactly ONE admin account —
        # creating a second one is refused outright, whoever asks.
        if fields["role"] == "admin" and accounts.admin_exists(directory):
            QMessageBox.warning(self, tr("accounts.add_title"),
                                tr("accounts.err_admin_exists"))
            return
        existing_codes = {a.code for a in accounts.list_accounts(directory)}
        account = accounts.new_account(
            fields["username"], fields["role"] if self._is_admin() else "user",
            display_name=fields["display_name"], department=fields["department"],
            email=fields["email"], group_id=fields["group_id"] or fixed_group,
            created_by=self.ctx.account.username if self.ctx.account else "",
            existing_codes=existing_codes)
        accounts.save_account(account, directory)
        if account.group_id:
            self._add_member_to_group(account.group_id, account.username)
        QMessageBox.information(
            self, tr("login.code_shown_title"),
            tr("login.code_shown_body", username=account.username, code=account.code))
        self.refresh()

    def _selected_account(self) -> Optional[accounts.Account]:
        if not self._current_username:
            return None
        return accounts.find_by_username(self._current_username, self._accounts_dir())

    def _edit_account(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        fixed_group = "" if self._is_admin() else account.group_id
        dlg = AccountEditDialog(
            self, account=account, available_groups=self._visible_groups(),
            allow_role_edit=self._is_admin(), allow_group_edit=self._is_admin(),
            fixed_group_id=fixed_group)
        if not dlg.exec():
            return
        fields = dlg.result_fields()
        # Single-admin invariant also on PROMOTION: an account may only be
        # made admin when no OTHER account already holds that role.
        if (self._is_admin() and fields["role"] == "admin"
                and accounts.admin_exists(self._accounts_dir(),
                                          exclude_username=account.username)):
            QMessageBox.warning(self, tr("accounts.edit_title"),
                                tr("accounts.err_admin_exists"))
            return
        old_group_id = account.group_id
        account.display_name = fields["display_name"]
        account.department = fields["department"]
        account.email = fields["email"]
        if self._is_admin():
            account.role = fields["role"]
            account.group_id = fields["group_id"]
        accounts.save_account(account, self._accounts_dir())
        if self._is_admin() and account.group_id != old_group_id:
            if old_group_id:
                self._remove_member_from_group(old_group_id, account.username)
            if account.group_id:
                self._add_member_to_group(account.group_id, account.username)
        self.refresh()

    def _delete_account(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        if not self._is_admin() and account.role != "user":
            return   # Sub-admin can only remove ordinary members, never another sub-admin
        if QMessageBox.question(
                self, tr("accounts.delete_title"),
                tr("accounts.delete_confirm", username=account.username)) != QMessageBox.Yes:
            return
        accounts.delete_account(account.username, self._accounts_dir())
        if account.group_id:
            self._remove_member_from_group(account.group_id, account.username)
        self._current_username = ""
        self.refresh()

    def _regenerate_code(self) -> None:
        account = self._selected_account()
        if account is None:
            return
        directory = self._accounts_dir()
        existing_codes = {a.code for a in accounts.list_accounts(directory) if a.username != account.username}
        account.code = accounts.generate_code(existing_codes)
        accounts.save_account(account, directory)
        QMessageBox.information(
            self, tr("login.code_shown_title"),
            tr("login.code_shown_body", username=account.username, code=account.code))

    # ---- smart search over the org tree ------------------------------------
    def _apply_tree_filter(self, text: str = "") -> None:
        """Instant substring filter: hide accounts whose label doesn't match;
        a group stays visible while any of its children match (or itself
        matches). Empty text shows everything. On top of that, the "scope to
        one group" combo can hide every OTHER group's top-level item outright
        (its own members are never even substring-checked in that case)."""
        needle = (text or self.search_edit.text()).strip().lower()
        group_scope = self.group_filter_combo.currentData() or ""
        for gi in range(self.tree.topLevelItemCount()):
            g_item = self.tree.topLevelItem(gi)
            kind_id = g_item.data(0, Qt.UserRole)
            g_id = kind_id[1] if kind_id else ""
            if group_scope and g_id != group_scope:
                g_item.setHidden(True)
                continue
            any_child = False
            for ci in range(g_item.childCount()):
                child = g_item.child(ci)
                match = not needle or needle in child.text(0).lower()
                child.setHidden(not match)
                any_child = any_child or match
            g_match = not needle or needle in g_item.text(0).lower()
            g_item.setHidden(not (g_match or any_child))
            if g_match and needle:
                for ci in range(g_item.childCount()):
                    g_item.child(ci).setHidden(False)

    def _ai_search(self) -> None:
        """AI-assisted search: the typed text is treated as a natural-language
        question ("ai trong nhóm CAE chưa có phòng ban?") — the model turns it
        into plain search keywords, which then run through the same substring
        filter. Falls back to using the raw text on any provider error."""
        query = self.search_edit.text().strip()
        if not query or getattr(self, "_ai_search_worker", None) is not None:
            return
        self.ai_search_btn.setEnabled(False)
        ctx = self.ctx

        def job(worker: AgentWorker):
            provider = ctx.build_active_provider()
            reply = provider.chat([
                {"role": "system", "content":
                    "Turn the user's natural-language people-search question into 1-3 SHORT "
                    "search keywords (a name, username, department, group or role word) that "
                    "would appear in an account list. Reply with ONLY the single best keyword, "
                    "no explanation."},
                {"role": "user", "content": query},
            ], cancel=worker.stop_event)
            return {"keyword": (reply.get("content") or "").strip().splitlines()[0][:60]}

        def done(result: dict) -> None:
            self._ai_search_worker = None
            self.ai_search_btn.setEnabled(True)
            keyword = result.get("keyword") or query
            self.search_edit.setText(keyword)   # textChanged re-applies the filter

        def failed(_err: str) -> None:
            self._ai_search_worker = None
            self.ai_search_btn.setEnabled(True)
            self._apply_tree_filter(query)

        w = AgentWorker(job)
        w.finished_ok.connect(done)
        w.failed.connect(failed)
        self._ai_search_worker = w
        w.start()

    # ---- bulk import from Excel (Admin only) --------------------------------
    def _export_excel_template(self) -> None:
        if not self._is_admin():
            return
        from ..core import account_excel

        path, _ = QFileDialog.getSaveFileName(
            self, tr("accounts.excel_template_btn"), "accounts_template.xlsx",
            "Excel (*.xlsx)")
        if not path:
            return
        try:
            account_excel.export_template(path)
        except Exception as exc:  # noqa: BLE001 — a locked/unwritable target file
            QMessageBox.warning(self, tr("accounts.excel_template_btn"), str(exc))

    def _import_excel(self) -> None:
        if not self._is_admin() or not self._shared_dir():
            return
        from ..core import account_excel

        path, _ = QFileDialog.getOpenFileName(
            self, tr("accounts.excel_import_btn"), "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            created, warnings = account_excel.import_accounts(
                path, self._shared_dir(),
                created_by=self.ctx.account.username if self.ctx.account else "")
        except ValueError as exc:
            QMessageBox.warning(self, tr("accounts.excel_import_btn"), str(exc))
            return
        codes_path = account_excel.export_issued_codes(
            created, Path(path).with_name(Path(path).stem + "_codes.xlsx"))
        lines = [tr("accounts.excel_imported", n=len(created))]
        if codes_path is not None:
            lines.append(tr("accounts.excel_codes_saved", path=str(codes_path)))
        lines += [f"• {a.username}: {a.code}" for a in created[:20]]
        if warnings:
            lines.append("")
            lines += warnings[:10]
        QMessageBox.information(self, tr("accounts.excel_import_btn"), "\n".join(lines))
        self.refresh()

    # ---- move a member between groups (Admin only, drag-and-drop) ---------
    def _on_member_dropped(self, username: str, target_group_id: str) -> None:
        if not self._is_admin():
            return
        account = accounts.find_by_username(username, self._accounts_dir())
        if account is None:
            return
        old_group_id = account.group_id
        if target_group_id == old_group_id:
            return
        account.group_id = target_group_id
        accounts.save_account(account, self._accounts_dir())
        if old_group_id:
            self._remove_member_from_group(old_group_id, account.username)
        if target_group_id:
            self._add_member_to_group(target_group_id, account.username)
        self.refresh()

    # ---- group CRUD (Admin only) -------------------------------------------
    def _add_group(self) -> None:
        if not self._is_admin():
            return
        name, ok = QInputDialog.getText(self, tr("accounts.new_group_title"), tr("accounts.f_group_name"))
        if not (ok and name.strip()):
            return
        group = groups.new_group(name.strip())
        groups.save_group(group, self._groups_dir())
        self.refresh()

    def _add_member_to_group(self, group_id: str, username: str) -> None:
        g = groups.load_group(group_id, self._groups_dir())
        if g is None:
            return
        if username not in g.member_usernames:
            g.member_usernames.append(username)
            groups.save_group(g, self._groups_dir())

    def _remove_member_from_group(self, group_id: str, username: str) -> None:
        g = groups.load_group(group_id, self._groups_dir())
        if g is None:
            return
        g.member_usernames = [m for m in g.member_usernames if m != username]
        # A departing Sub-admin must not stay listed as the group's subadmin
        # (e.g. when moved to a different group or deleted) — otherwise the
        # group would keep "pointing" at someone no longer in it.
        if g.subadmin_username == username:
            g.subadmin_username = ""
        groups.save_group(g, self._groups_dir())

    # ---- usage/cost table --------------------------------------------------
    def _pricing(self) -> Dict:
        return {**ut.DEFAULT_PRICING, **(self.ctx.config.data.get("usage") or {})}

    def _reload_usage_table(self) -> None:
        shared_dir = self._shared_dir()
        period = self.period_combo.currentData() or "day"
        start = date.today() - timedelta(days=_PERIOD_DAYS.get(period, 1) - 1)
        events = telemetry_shared.load_shared_usage_events(shared_dir, start=start)
        by_account: Dict[str, List[dict]] = {}
        for ev in events:
            by_account.setdefault(ev.get("account", ""), []).append(ev)

        accounts_by_username = {a.username: a for a in self._visible_accounts()}
        pricing = self._pricing()
        rows = []
        for username, acc in accounts_by_username.items():
            acc_events = by_account.get(username, [])
            s = ut.summarize(acc_events)
            costs = ut.cost_usd_events(acc_events, pricing)   # honors the per-model price table
            machines = sorted({e.get("machine", "") for e in acc_events if e.get("machine")})
            rows.append((
                acc.display_name or acc.username, acc.username, ", ".join(machines) or "—",
                acc.department or "—", fmt_tokens(s["total"]), ut.format_cost(sum(costs.values()), pricing),
            ))
        rows.sort(key=lambda r: r[0].lower())
        self.usage_table.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for col, text in enumerate(cells):
                self.usage_table.setItem(row, col, QTableWidgetItem(str(text)))

    # ---- i18n --------------------------------------------------------------
    def _retranslate(self) -> None:
        self._shared_hint.setText(tr("accounts.no_shared_dir"))
        self.add_btn.setText(tr("accounts.add_btn"))
        self.edit_btn.setText(tr("accounts.edit_btn"))
        self.del_btn.setText(tr("accounts.delete_btn"))
        self.code_btn.setText(tr("accounts.generate_code_btn"))
        self.group_btn.setText(tr("accounts.new_group_btn"))
        if self._is_admin():
            self.tree.setToolTip(tr("accounts.drag_move_hint"))
        self.search_edit.setPlaceholderText(tr("accounts.search_placeholder"))
        self.ai_search_btn.setText(tr("accounts.ai_search_btn"))
        self.ai_search_btn.setToolTip(tr("accounts.ai_search_tooltip"))
        self.excel_template_btn.setText(tr("accounts.excel_template_btn"))
        self.excel_import_btn.setText(tr("accounts.excel_import_btn"))
        self._usage_title.setText(tr("accounts.usage_title"))
        self.refresh_btn.setText(tr("monitoring.refresh"))
        self.usage_table.setHorizontalHeaderLabels([
            tr("accounts.col_name"), tr("accounts.col_account"), tr("accounts.col_machine"),
            tr("accounts.col_department"), tr("accounts.col_tokens"), tr("accounts.col_cost"),
        ])
        self.refresh()
