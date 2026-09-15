"""Floating in-app Help assistant — the app icon pinned to the bottom-right of
the main window, on every screen. Click it to expand a compact chat panel that
greets the user (in the display language) and answers how-to-use-the-app
questions only. A chevron on its left collapses it to a thin tab at the screen
edge when the user doesn't want it visible.

It is deliberately minimal: no tools, no file access, no agent loop — just a
single ``provider.chat`` per message (same pattern as the AI-draft helpers),
scoped by the built-in "help" admin agent's system prompt (see
``core.admin_agents``: task_kind "help"). The agent is managed in Monitoring →
Agents Admin, so the Admin can pick which provider/model answers.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from ..core import admin_agents
from ..core.worker import AgentWorker
from ..i18n import tr
from .icons import icon

_ASSETS = Path(__file__).resolve().parent.parent / "assets"

_MARGIN = 18                       # gap from the window's bottom-right corner
_LAUNCHER = 64                     # collapsed app-icon badge size (a clean rounded card, like image 2)
_LAUNCHER_ICON = 52                # the icon inside it, inset so the light badge frames it
_COLLAPSE_W, _COLLAPSE_H = 18, 44  # the "hide to the edge" chevron beside it
_GAP = 2
_TAB_W, _TAB_H = 16, 48            # the thin "show" tab when hidden at the edge
_PANEL_W, _PANEL_H = 340, 460      # expanded chat panel size

# The three states the floating assistant cycles through.
_HIDDEN, _LAUNCHER_ST, _PANEL = "hidden", "launcher", "panel"


def _current_user() -> str:
    return (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()


def _app_icon() -> QIcon:
    """The app's own icon.png (falls back to the generic robot glyph if the
    asset is somehow missing)."""
    p = _ASSETS / "icon.png"
    return QIcon(str(p)) if p.exists() else icon("robot")


def _app_pixmap(size: int) -> QPixmap:
    """icon.png scaled to ``size`` (smooth), for the launcher badge label."""
    p = _ASSETS / "icon.png"
    if p.exists():
        return QPixmap(str(p)).scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return icon("robot").pixmap(size, size)


class _IconTap(QLabel):
    """A QLabel that behaves like a button (click → signal) — used for the
    launcher badge so it carries NO QPushButton chrome/box, just the icon on a
    clean rounded card."""

    clicked = Signal()

    def mousePressEvent(self, e):  # noqa: N802 - Qt override
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
            e.accept()
            return
        super().mousePressEvent(e)


class HelpAgentWidget(QWidget):
    """Overlay child of the main window; anchors itself bottom-right and cycles
    hidden-tab → launcher icon → expanded chat panel."""

    status_message = Signal(str)

    def __init__(self, ctx, parent=None, user_name: str = ""):
        super().__init__(parent)
        self.ctx = ctx
        self._user_name = user_name or _current_user()
        self._state = _LAUNCHER_ST
        self._busy = False
        self._worker: Optional[AgentWorker] = None
        # Conversation history (excludes the system prompt, prepended per call).
        # Seeded with the greeting so the panel always opens on a friendly hello.
        self._history: List[Dict[str, str]] = [
            {"role": "assistant", "content": self._greeting()}
        ]
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._pal = self._compute_palette()
        self._build_edge_tab()
        self._build_launcher()
        self._build_panel()
        self._apply_style()
        self._apply_state()

    # ---- theming ----------------------------------------------------------
    def _compute_palette(self) -> Dict[str, str]:
        """Chat-body colours that FOLLOW the app's light/dark theme. The header
        is intentionally NOT themed here (it stays a fixed light bar — see
        _apply_style), only the conversation area adapts."""
        from ..theme import resolve_theme
        dark = resolve_theme(getattr(self.ctx.config, "theme", "system")) == "dark"
        if dark:
            return {
                "panel_bg": "#16202b", "text": "#e3ebf5", "log_bg": "#0f1720",
                "input_bg": "#1b2733", "border": "#33404d",
                "user_bg": "#123a52", "user_label": "#58c0ee",
                "bot_bg": "#232f3b", "bot_label": "#6fe3a4",
            }
        return {
            "panel_bg": "#ffffff", "text": "#14212b", "log_bg": "#f7f9fb",
            "input_bg": "#ffffff", "border": "#d5d9de",
            "user_bg": "#dceff8", "user_label": "#0077B6",
            "bot_bg": "#eef1f4", "bot_label": "#2f7d55",
        }

    def apply_theme(self) -> None:
        """Re-style + re-render when the app theme switches (called from
        MainWindow._apply_theme). Header stays fixed; chat body re-colours."""
        self._pal = self._compute_palette()
        self._apply_style()
        self._render()

    def _apply_style(self) -> None:
        # The HEADER bar is a FIXED light strip in both themes (per request); only
        # the chat body below follows the app's light/dark palette (self._pal).
        from ..theme import ACCENT, ACCENT2, GRADIENT
        p = self._pal
        self.setStyleSheet(f"""
            /* Clean rounded app-icon badge (like image 2): a fixed light card
               framing the icon — no QPushButton box. */
            #helpLauncher {{ background: #e8f2fb; border: 1px solid #d3e3f2;
                border-radius: 16px; }}
            #helpLauncher:hover {{ background: #dcedfb; }}
            #helpCollapseBtn, #helpEdgeTab {{ background: rgba(0,0,0,0.06); border: none;
                border-radius: 6px; }}
            #helpCollapseBtn:hover, #helpEdgeTab:hover {{ background: rgba(0,0,0,0.14); }}
            #helpPanel {{ background: {p['panel_bg']}; border: 1px solid {p['border']};
                border-radius: 14px; color: {p['text']}; }}
            /* Faint-blue header bar — LOCKED light, dark title, in both themes.
               The header AND its child labels set fixed backgrounds so the dark
               theme never bleeds into the App-Assistant title strip. */
            #helpHeader {{ background: #e8f2fb; border-bottom: 1px solid #d9e6f2;
                border-top-left-radius: 14px; border-top-right-radius: 14px; }}
            #helpHeader QLabel {{ background: transparent; color: #14212b; }}
            #helpTitle {{ color: #14212b; font-weight: 700; font-size: 13px; background: transparent; }}
            #helpMinBtn {{ background: transparent; border: none; }}
            #helpMinBtn:hover {{ background: rgba(0,0,0,0.10); border-radius: 6px; }}
            #helpLog {{ background: {p['log_bg']}; border: none; color: {p['text']}; padding: 4px 6px; }}
            #helpInputRow {{ background: {p['panel_bg']}; border-bottom-left-radius: 14px;
                border-bottom-right-radius: 14px; }}
            #helpInput {{ border: 1px solid {p['border']}; border-radius: 8px; padding: 5px 8px;
                background: {p['input_bg']}; color: {p['text']}; }}
            #helpInput:focus {{ border: 1px solid {ACCENT}; }}
            #helpSendBtn {{ background: {GRADIENT}; border: none; border-radius: 8px; }}
            #helpSendBtn:hover {{ background: {ACCENT2}; }}
            #helpSendBtn:disabled {{ background: #b7c0c9; }}
        """)

    # ---- greeting / labels ------------------------------------------------
    def _greeting(self) -> str:
        name = self._user_name or tr("help_agent.default_user")
        return tr("help_agent.greeting", name=name)

    # ---- construction -----------------------------------------------------
    def _build_edge_tab(self) -> None:
        # Shown only while hidden: a thin tab at the right edge to bring the
        # assistant back (chevron points left = "slide out").
        self.edge_tab = QPushButton(self)
        self.edge_tab.setObjectName("helpEdgeTab")
        self.edge_tab.setIcon(icon("chevron-left", color="#5a6570"))
        self.edge_tab.setCursor(Qt.PointingHandCursor)
        self.edge_tab.setToolTip(tr("help_agent.show_tooltip"))
        self.edge_tab.clicked.connect(self._show_launcher)

    def _build_launcher(self) -> None:
        # A left-side chevron collapses the assistant to the edge…
        self.collapse_btn = QPushButton(self)
        self.collapse_btn.setObjectName("helpCollapseBtn")
        self.collapse_btn.setIcon(icon("chevron-right", color="#5a6570"))
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.setToolTip(tr("help_agent.hide_tooltip"))
        self.collapse_btn.clicked.connect(self._hide_to_edge)
        # …and the app icon itself opens the chat — a clean rounded badge (like
        # image 2), NOT a QPushButton (which added a pale box around the icon).
        self.launcher = _IconTap(self)
        self.launcher.setObjectName("helpLauncher")
        self.launcher.setFixedSize(_LAUNCHER, _LAUNCHER)
        self.launcher.setAlignment(Qt.AlignCenter)
        self.launcher.setPixmap(_app_pixmap(_LAUNCHER_ICON))
        self.launcher.setCursor(Qt.PointingHandCursor)
        self.launcher.setToolTip(tr("help_agent.open_tooltip"))
        self.launcher.clicked.connect(self._expand)

    def _build_panel(self) -> None:
        self.panel = QFrame(self)
        self.panel.setObjectName("helpPanel")

        v = QVBoxLayout(self.panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        # Header: app icon + title + minimize (plain white bar, no colour fill)
        header = QFrame(self.panel)
        header.setObjectName("helpHeader")
        hb = QHBoxLayout(header)
        hb.setContentsMargins(12, 8, 8, 8)
        self.title_icon = QLabel(header)
        self.title_icon.setPixmap(_app_icon().pixmap(20, 20))
        hb.addWidget(self.title_icon)
        self.title = QLabel(tr("help_agent.title"), header)
        self.title.setObjectName("helpTitle")
        hb.addWidget(self.title, 1)
        self.min_btn = QPushButton(header)
        self.min_btn.setObjectName("helpMinBtn")
        self.min_btn.setIcon(icon("minus", color="#5a6570"))
        self.min_btn.setFixedSize(24, 24)
        self.min_btn.setCursor(Qt.PointingHandCursor)
        self.min_btn.setToolTip(tr("help_agent.collapse_tooltip"))
        self.min_btn.clicked.connect(self._collapse)
        hb.addWidget(self.min_btn)
        v.addWidget(header)

        # Conversation log
        self.log = QTextBrowser(self.panel)
        self.log.setObjectName("helpLog")
        self.log.setOpenExternalLinks(False)
        v.addWidget(self.log, 1)

        # Input row
        row = QFrame(self.panel)
        row.setObjectName("helpInputRow")
        rb = QHBoxLayout(row)
        rb.setContentsMargins(8, 8, 8, 8)
        rb.setSpacing(6)
        self.input = QLineEdit(row)
        self.input.setObjectName("helpInput")
        self.input.setPlaceholderText(tr("help_agent.placeholder"))
        self.input.returnPressed.connect(self._send)
        rb.addWidget(self.input, 1)
        self.send_btn = QPushButton(row)
        self.send_btn.setObjectName("helpSendBtn")
        self.send_btn.setIcon(icon("send"))
        self.send_btn.setFixedSize(32, 30)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._send)
        rb.addWidget(self.send_btn)
        v.addWidget(row)

        self._render()

    # ---- state transitions ------------------------------------------------
    def _expand(self) -> None:
        self._state = _PANEL
        self._apply_state()
        self.input.setFocus()

    def _collapse(self) -> None:
        self._state = _LAUNCHER_ST
        self._apply_state()

    def _hide_to_edge(self) -> None:
        self._state = _HIDDEN
        self._apply_state()

    def _show_launcher(self) -> None:
        self._state = _LAUNCHER_ST
        self._apply_state()

    def _apply_state(self) -> None:
        st = self._state
        self.edge_tab.setVisible(st == _HIDDEN)
        self.collapse_btn.setVisible(st == _LAUNCHER_ST)
        self.launcher.setVisible(st == _LAUNCHER_ST)
        self.panel.setVisible(st == _PANEL)
        if st == _PANEL:
            self.resize(_PANEL_W, _PANEL_H)
            self.panel.setGeometry(0, 0, _PANEL_W, _PANEL_H)
        elif st == _LAUNCHER_ST:
            w = _LAUNCHER + _GAP + _COLLAPSE_W
            self.resize(w, _LAUNCHER)
            # Icon on the left, the collapse chevron on the RIGHT (toward the
            # screen edge it tucks into).
            self.launcher.setGeometry(0, 0, _LAUNCHER, _LAUNCHER)
            self.collapse_btn.setGeometry(_LAUNCHER + _GAP, (_LAUNCHER - _COLLAPSE_H) // 2,
                                          _COLLAPSE_W, _COLLAPSE_H)
        else:  # hidden
            self.resize(_TAB_W, _TAB_H)
            self.edge_tab.setGeometry(0, 0, _TAB_W, _TAB_H)
        self.reposition()
        self.raise_()

    def reposition(self) -> None:
        """Pin to the parent's bottom-right corner (called on parent resize)."""
        p = self.parentWidget()
        if p is None:
            return
        x = max(0, p.width() - self.width() - _MARGIN)
        y = max(0, p.height() - self.height() - _MARGIN)
        self.move(x, y)

    # ---- rendering --------------------------------------------------------
    def _bubble_html(self, who: str, content: str) -> str:
        """One message as a clearly-separated, labelled bubble: the user's turns
        sit right-aligned with an accent tint, the assistant's left-aligned on a
        neutral fill, each headed by its speaker name — so who said what is never
        ambiguous. (QTextDocument has no border-radius, so filled table cells do
        the bubble work.)"""
        p = self._pal
        text = (content or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = text.replace("\n", "<br>")
        if who == "user":
            align, bg, label_color = "right", p["user_bg"], p["user_label"]
            label = tr("chat.you")
        else:
            align, bg, label_color = "left", p["bot_bg"], p["bot_label"]
            label = tr("help_agent.title")
        return (
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td align="{align}">'
            f'<table width="80%" cellspacing="0" cellpadding="7" bgcolor="{bg}"><tr>'
            f'<td style="color:{p["text"]};">'
            f'<b style="color:{label_color};">{label}</b><br>{text}'
            f'</td></tr></table></td></tr></table>'
            '<div style="line-height:6px;">&nbsp;</div>'   # gap between turns
        )

    def _render(self, pending: bool = False) -> None:
        parts = [self._bubble_html(m["role"], m["content"]) for m in self._history]
        if pending:
            parts.append(self._bubble_html("assistant", "…"))
        self.log.setHtml("".join(parts))
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    # ---- send a message ---------------------------------------------------
    def _send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self._history.append({"role": "user", "content": text})
        self._set_busy(True)
        self._render(pending=True)

        agent = admin_agents.ensure_help_agent(
            admin_agents.agents_admin_dir(self.ctx.config.shared_dir))
        history = list(self._history)

        def job(worker):
            provider = admin_agents.build_agent_provider(self.ctx, agent)
            messages = [{"role": "system", "content": agent.effective_prompt()}] + history
            result = provider.chat(messages, tools=None, cancel=worker.is_cancelled)
            content = result.get("content", "") if isinstance(result, dict) else str(result)
            return {"content": provider.strip_think(content) or ""}

        worker = AgentWorker(job)
        worker.finished_ok.connect(self._on_reply)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        worker.start()

    def _on_reply(self, result: Dict[str, Any]) -> None:
        content = (result or {}).get("content", "").strip() or tr("help_agent.empty_reply")
        self._history.append({"role": "assistant", "content": content})
        self._set_busy(False)
        self._render()

    def _on_failed(self, err: str) -> None:
        self._history.append({"role": "assistant",
                              "content": tr("help_agent.error", error=err)})
        self._set_busy(False)
        self._render()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)

    def retranslate(self) -> None:
        self.title.setText(tr("help_agent.title"))
        self.input.setPlaceholderText(tr("help_agent.placeholder"))
        self.launcher.setToolTip(tr("help_agent.open_tooltip"))
        self.min_btn.setToolTip(tr("help_agent.collapse_tooltip"))
        self.collapse_btn.setToolTip(tr("help_agent.hide_tooltip"))
        self.edge_tab.setToolTip(tr("help_agent.show_tooltip"))
