"""A collapsible CLI terminal panel — runs commands directly on the host OS
(Windows cmd / POSIX sh) via QProcess, streaming output live.

User-operated (the person types the commands themselves), so it deliberately
runs in the real shell with no agent sandbox — it's a convenience terminal, not
an agent tool.

Terminal conveniences implemented in-panel:
  * ``cd`` / ``cd D:`` / ``cd /d X:\\path`` (drive + relative + absolute), ``clear``
  * **Tab completion** of files/folders in the current directory
  * **Up/Down** command history
  * UTF-8 output (Windows ``chcp 65001``) so non-ASCII — e.g. Japanese — shows
    correctly instead of mojibake.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QVBoxLayout, QWidget,
)

from ..i18n import on_language_changed, tr
from .icons import icon

_IS_WIN = sys.platform == "win32"


class _TermInput(QLineEdit):
    """Command input with Tab-completion and Up/Down history (like a real shell)."""

    complete_requested = Signal()
    history_prev = Signal()
    history_next = Signal()

    def keyPressEvent(self, e):  # noqa: N802 - Qt override
        if e.key() == Qt.Key_Tab:
            self.complete_requested.emit()
            e.accept()
            return
        if e.key() == Qt.Key_Up:
            self.history_prev.emit()
            e.accept()
            return
        if e.key() == Qt.Key_Down:
            self.history_next.emit()
            e.accept()
            return
        super().keyPressEvent(e)


class TerminalPanel(QWidget):
    """Collapsible terminal: a header (toggle) + output console + command input."""

    expanded = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = True
        self._cwd = str(Path.home())
        self._proc: QProcess | None = None
        self._history: list[str] = []
        self._hist_idx = 0          # points one past the last entry when idle

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- header (always visible; click to expand/collapse) --------------
        self._header = QFrame()
        self._header.setObjectName("termHeader")
        self._header.setStyleSheet(
            "#termHeader { background: rgba(0,0,0,0.06); border-radius: 6px; }")
        hb = QHBoxLayout(self._header)
        hb.setContentsMargins(8, 4, 8, 4)
        self._toggle_btn = QPushButton()
        self._toggle_btn.setFlat(True)
        self._toggle_btn.setFixedSize(22, 22)
        self._toggle_btn.setCursor(Qt.PointingHandCursor)
        self._toggle_btn.clicked.connect(self.toggle)
        hb.addWidget(self._toggle_btn)
        self._title = QLabel(tr("terminal.title"))
        self._title.setStyleSheet("font-weight:600;")
        hb.addWidget(self._title)
        hb.addStretch(1)
        self._cwd_lbl = QLabel("")
        self._cwd_lbl.setObjectName("hint")
        hb.addWidget(self._cwd_lbl)
        root.addWidget(self._header)

        # ---- body (hidden while collapsed) ----------------------------------
        self._body = QWidget()
        bl = QVBoxLayout(self._body)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(4)
        self.output = QPlainTextEdit()
        self.output.setObjectName("termOutput")
        self.output.setReadOnly(True)
        self.output.setMaximumBlockCount(5000)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.output.setFont(mono)
        self.output.setStyleSheet(
            "#termOutput { background: #1e1e1e; color: #d4d4d4; border: none; }")
        self.output.setMinimumHeight(160)
        bl.addWidget(self.output, 1)

        row = QHBoxLayout()
        self._prompt = QLabel("$")
        self._prompt.setFont(mono)
        row.addWidget(self._prompt)
        self.input = _TermInput()
        self.input.setObjectName("termInput")
        self.input.setFont(mono)
        self.input.setStyleSheet(
            "#termInput { background: #1e1e1e; color: #d4d4d4; border: 1px solid #3c3c3c; "
            "border-radius: 6px; padding: 4px 8px; }")
        self.input.returnPressed.connect(self._run_current)
        self.input.complete_requested.connect(self._complete)
        self.input.history_prev.connect(lambda: self._history_move(-1))
        self.input.history_next.connect(lambda: self._history_move(1))
        row.addWidget(self.input, 1)
        self._run_btn = QPushButton()
        self._run_btn.setObjectName("primary")
        self._run_btn.clicked.connect(self._run_current)
        row.addWidget(self._run_btn)
        bl.addLayout(row)
        root.addWidget(self._body)

        self._body.setVisible(False)
        on_language_changed(self._retranslate)
        self._retranslate()
        self._apply_collapsed()

    # ---- public API ----------------------------------------------------------
    def set_cwd(self, path: str) -> None:
        if path and os.path.isdir(path):
            self._cwd = os.path.normpath(str(path))
            self._cwd_lbl.setText(self._cwd)
            self._prompt.setText(_prompt_for(self._cwd))

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._apply_collapsed()
        if not self._collapsed:
            self.expanded.emit()
            self.input.setFocus()

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self._apply_collapsed()

    def _apply_collapsed(self) -> None:
        self._body.setVisible(not self._collapsed)
        self._toggle_btn.setIcon(icon("chevron-right" if self._collapsed else "chevron-down"))
        self._toggle_btn.setToolTip(
            tr("terminal.expand_tooltip") if self._collapsed else tr("terminal.collapse_tooltip"))

    # ---- history -------------------------------------------------------------
    def _history_move(self, direction: int) -> None:
        if not self._history:
            return
        self._hist_idx = max(0, min(len(self._history), self._hist_idx + direction))
        self.input.setText(self._history[self._hist_idx] if self._hist_idx < len(self._history) else "")

    # ---- Tab completion ------------------------------------------------------
    def _complete(self) -> None:
        text = self.input.text()
        head, sep, token = text.rpartition(" ")
        norm = token.replace("\\", "/")
        if "/" in norm:
            dir_part, _, name = norm.rpartition("/")
            base = dir_part if os.path.isabs(dir_part) else os.path.join(self._cwd, dir_part)
            rebuilt_prefix = token[: len(token) - len(name)]   # keep the dir + separator as typed
        else:
            base, name, rebuilt_prefix = self._cwd, token, ""
        try:
            entries = sorted(os.listdir(base or self._cwd))
        except OSError:
            return
        low = name.lower()
        matches = [e for e in entries if e.lower().startswith(low)]
        if not matches:
            return

        def _decorate(entry: str) -> str:
            full = os.path.join(base or self._cwd, entry)
            return entry + (os.sep if os.path.isdir(full) else "")

        if len(matches) == 1:
            completed = _decorate(matches[0])
            self.input.setText((head + sep) + rebuilt_prefix + completed)
        else:
            common = os.path.commonprefix(matches)
            if len(common) > len(name):
                self.input.setText((head + sep) + rebuilt_prefix + common)
            # List the candidates (like bash's double-Tab) so the user can see them.
            self._append("  ".join(_decorate(m) for m in matches) + "\n", role="out")

    # ---- running commands ----------------------------------------------------
    def _run_current(self) -> None:
        cmd = self.input.text().strip()
        if not cmd:
            return
        self.input.clear()
        self._history.append(cmd)
        self._hist_idx = len(self._history)
        self.run_command(cmd)

    def run_command(self, cmd: str) -> None:
        self._append(f"\n{_prompt_for(self._cwd)} {cmd}\n", role="cmd")
        stripped = cmd.strip()
        if stripped in ("clear", "cls"):
            self.output.clear()
            return
        if stripped == "cd" or stripped.lower().startswith(("cd ", "cd\t")):
            self._change_dir(stripped[2:].strip())
            return
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._append(tr("terminal.busy") + "\n", role="err")
            return
        self._start_process(cmd)

    def _change_dir(self, target: str) -> None:
        target = target.strip()
        if target.lower().startswith("/d "):     # cmd's "cd /d X:\path" flag
            target = target[3:].strip()
        target = target.strip('"').strip("'")
        if not target:
            self.set_cwd(str(Path.home()))
            return
        # A bare drive letter ("D:") means that drive's root ("D:\").
        if _IS_WIN and re.fullmatch(r"[A-Za-z]:", target):
            target = target + os.sep
        new = target if os.path.isabs(target) else os.path.join(self._cwd, target)
        new = os.path.normpath(new)
        if os.path.isdir(new):
            self.set_cwd(new)
        else:
            self._append(tr("terminal.cd_error", path=target) + "\n", role="err")

    def _start_process(self, cmd: str) -> None:
        proc = QProcess(self)
        proc.setWorkingDirectory(self._cwd)
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        proc.readyReadStandardOutput.connect(
            lambda: self._append(_decode(bytes(proc.readAllStandardOutput()))))
        proc.readyReadStandardError.connect(
            lambda: self._append(_decode(bytes(proc.readAllStandardError())), role="err"))
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(
            lambda _e: self._append(tr("terminal.launch_error") + "\n", role="err"))
        self._proc = proc
        self._set_running(True)
        if _IS_WIN:
            # ``chcp 65001`` switches cmd to the UTF-8 codepage so non-ASCII
            # (e.g. Japanese) output isn't mojibake. (No ``/u`` — that would emit
            # UTF-16 and fight the UTF-8 decode.)
            proc.start("cmd.exe", ["/c", f"chcp 65001>nul & {cmd}"])
        else:
            proc.start(os.environ.get("SHELL", "/bin/sh"), ["-c", cmd])

    def _on_finished(self, code: int, _status=None) -> None:
        self._append(tr("terminal.exit", code=code) + "\n",
                     role="ok" if code == 0 else "err")
        self._set_running(False)

    def _set_running(self, running: bool) -> None:
        self.input.setEnabled(not running)
        self._run_btn.setEnabled(not running)
        if not running:
            self.input.setFocus()

    def _append(self, text: str, role: str = "out") -> None:
        if not text:
            return
        from PySide6.QtGui import QColor, QTextCursor
        colors = {"cmd": "#4ec9b0", "err": "#f48771", "ok": "#6a9955", "out": "#d4d4d4"}
        cursor = self.output.textCursor()
        cursor.movePosition(QTextCursor.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QColor(colors.get(role, "#d4d4d4")))
        cursor.setCharFormat(fmt)
        cursor.insertText(text)
        self.output.setTextCursor(cursor)
        self.output.ensureCursorVisible()

    # ---- lifecycle -----------------------------------------------------------
    def stop(self) -> None:
        if self._proc is not None and self._proc.state() != QProcess.NotRunning:
            self._proc.kill()

    def _retranslate(self) -> None:
        self._title.setText(tr("terminal.title"))
        self._run_btn.setText(tr("terminal.run"))
        self.input.setPlaceholderText(tr("terminal.placeholder"))
        self._apply_collapsed()


def _prompt_for(cwd: str) -> str:
    name = Path(cwd).name or cwd
    return f"{name} >" if _IS_WIN else f"{name} $"


def _decode(data: bytes) -> str:
    import locale
    encs = ["utf-8"]
    try:
        encs.append(locale.getpreferredencoding(False))
    except Exception:  # noqa: BLE001
        pass
    encs += ["cp932", "cp1252", "latin-1"]   # cp932 = Japanese Windows console
    for enc in encs:
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")
