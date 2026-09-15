"""Qt style sheets giving the app a modern, dark design-tool look (deep
ocean-blue surfaces, large rounded corners, a deep-sea gradient accent, and
colorful pill badges) inspired by contemporary dashboard UIs."""
from __future__ import annotations

# Brand accent — deep sea blue palette with teal-cyan gradients.
ACCENT = "#0096C7"
ACCENT_HOVER = "#48CAE4"
ACCENT2 = "#0077B6"          # deeper ocean blue for gradients
GRADIENT = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {ACCENT2}, stop:1 {ACCENT})"
GRADIENT_HOVER = f"qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #023E8A, stop:1 {ACCENT_HOVER})"

_DARK = f"""
* {{ font-family: "Segoe UI", "Helvetica Neue", "Arial", "Yu Gothic UI", "Meiryo", sans-serif; font-size: 13px; }}
QMainWindow, QWidget {{ background: #0A1628; color: #E0F0FF; }}
QMainWindow::separator {{ background: #0A1628; width: 4px; height: 4px; }}
QStatusBar {{ background: #0A1628; color: #5C8DB8; border-top: 1px solid #132240; }}
QSplitter::handle {{ background: #0A1628; }}
/* Separate the nav rail (its own panel + divider) from the content area. */
QWidget#navWrap {{ background: #0D1F35; border-right: 1px solid #17263f; }}
QWidget#navWrap QTreeWidget, QWidget#navWrap QListWidget {{ background: transparent; border: none; }}
QWidget#contentArea {{ background: #0A1628; }}
/* Compact icons app-wide (they looked oversized on scaled displays). */
QPushButton, QToolButton, QComboBox, QTabBar {{ qproperty-iconSize: 14px 14px; }}
QTreeWidget#navrail {{ qproperty-iconSize: 16px 16px; }}

/* Square the pane (tabs above stay rounded): a rounded pane lets its square
   child pages poke past the corners — the "rectangle behind the rounded box". */
QTabWidget::pane {{ background: #0D1F35; border: 1px solid #132240; border-radius: 0px; top: 2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: #111D32; color: #5C8DB8; padding: 9px 22px; margin: 0 6px 8px 0;
    border-radius: 10px; border: 1px solid #132240; font-weight: 500;
}}
QTabBar::tab:selected {{ background: {GRADIENT}; color: white; border: none; font-weight: 700; }}
QTabBar::tab:hover:!selected {{ background: #172A45; color: #B0D4F1; }}
/* Co4E flow "browser" tabs: sit FLUSH in their row (no floating bottom gap, so
   the icon/label is vertically centred) and use the app's panel/accent surfaces
   so the strip reads as part of the app. */
QTabBar#flowTabs::tab {{ background: #0D1F35; color: #8FB2D4; border: 1px solid #17263f;
    padding: 6px 10px; margin: 0 3px 0 0; min-height: 22px; border-radius: 8px; }}
QTabBar#flowTabs::tab:selected {{ background: {GRADIENT}; color: white; border: 1px solid transparent; }}
QTabBar#flowTabs::tab:hover:!selected {{ background: #172A45; color: #E0F0FF; }}
/* The "new flow" (＋) button — styled as the last tab in the strip. */
QPushButton#flowAddBtn {{ background: #0D1F35; color: #8FB2D4; border: 1px solid #17263f;
    border-radius: 8px; padding: 6px 0; font-size: 15px; font-weight: bold; min-height: 22px; }}
QPushButton#flowAddBtn:hover {{ background: #172A45; color: #E0F0FF; }}
/* Co4E sidebar (Workflows/Agents/Skills): transparent icon tabs with a subtle
   translucent selection + the normal text colour (matches the app's lists,
   not a bright fill). */
QTabBar#co4eSideTabs {{ qproperty-iconSize: 18px 18px; }}
QTabBar#co4eSideTabs::tab {{ background: transparent; color: #8FB2D4; border: none;
    border-radius: 6px; padding: 5px; margin: 0 6px 0 0; }}
QTabBar#co4eSideTabs::tab:selected {{ background: rgba(0,150,199,0.28); color: #E0F0FF; }}
QTabBar#co4eSideTabs::tab:hover:!selected {{ background: #172A45; color: #B0D4F1; }}
/* Co4E canvas frame — match the app's other framed surfaces (not a faint hairline). */
QGraphicsView#co4eCanvas {{ background: #0D1F35; border: 1px solid #1A2D4A; border-radius: 10px; }}

QGroupBox {{
    background: #0D1F35; border: 1px solid #132240; border-radius: 18px;
    margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; top: 2px;
    padding: 0 6px; color: #5C8DB8; letter-spacing: 0.5px;
}}

QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QTreeView, QListView, QTreeWidget, QListWidget {{
    background: #111D32; color: #E0F0FF; border: 1px solid #1A2D4A; border-radius: 10px;
    selection-background-color: {ACCENT}; selection-color: white;
}}
/* Scroll containers must NOT paint their own square panel behind rounded
   children (that square is what shows as a "rectangle under the rounded box").
   The corner where scrollbars meet is squared off too — keep it transparent. */
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border-radius: 6px; }}
QTreeView::item:hover, QListView::item:hover {{ background: #172A45; }}
QTreeView::item:selected, QListView::item:selected {{ background: rgba(0,150,199,0.28); color: #E0F0FF; }}
QHeaderView::section {{ background: #111D32; color: #5C8DB8; border: none; border-bottom: 1px solid #132240; padding: 6px; }}

QPushButton {{
    background: #132240; color: #E0F0FF; border: 1px solid #1A2D4A;
    border-radius: 10px; padding: 8px 16px;
}}
QPushButton:hover {{ background: #1A3050; border-color: #234070; }}
QPushButton:pressed {{ background: #111D32; }}
QPushButton:disabled {{ color: #3A5A78; background: #0F1A28; border-color: #132240; }}
QPushButton#primary {{ background: {GRADIENT}; color: white; border: none; font-weight: 700; padding: 8px 18px; }}
QPushButton#primary:hover {{ background: {GRADIENT_HOVER}; }}
QPushButton#primary:disabled {{ background: #1A2D4A; color: #3A5A78; }}
QPushButton#danger {{ background: #E5484D; color: white; border: none; font-weight: 600; }}
QPushButton#danger:hover {{ background: #EF6368; }}
QPushButton#navMenuBtn {{
    background: transparent; border: none; border-radius: 8px; padding: 5px 6px;
    font-weight: 700; font-size: 11px; letter-spacing: 1px; color: #5C8DB8; text-align: left;
}}
QPushButton#navMenuBtn:hover {{ background: #132240; color: #E0F0FF; }}
QPushButton#navMenuBtn:pressed {{ background: #111D32; }}

QLabel#badge {{ background: #0A2A3A; color: #48CAE4; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgeSuccess {{ background: #0A2A20; color: #48D9A0; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgePurple {{ background: #1A1A3A; color: #9B8FF7; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgePink {{ background: #2A1A30; color: #D980C0; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgeWarn {{ background: #0A2A3A; color: #48CAE4; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#hint {{ color: #5C8DB8; }}
QLabel#warning {{ color: #48CAE4; font-weight: 600; }}

QComboBox {{ background: #111D32; border: 1px solid #1A2D4A; border-radius: 10px; padding: 6px 10px; }}
QComboBox:hover {{ border-color: #234070; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: #111D32; border: 1px solid #1A2D4A; border-radius: 10px;
    selection-background-color: {ACCENT}; selection-color: white; outline: none;
}}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #1A2D4A; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #1A2D4A; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; background: #111D32; border: 1px solid #1A2D4A; border-radius: 5px;
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {GRADIENT}; border-color: {ACCENT};
}}
QCheckBox::indicator:disabled {{ border-color: #132240; background: #0F1A28; }}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background: #1A3050; border-color: #234070;
}}

QMenu {{ background: #111D32; color: #E0F0FF; border: 1px solid #1A2D4A; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 16px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}

QToolTip {{ background: #172A45; color: #E0F0FF; border: 1px solid #1A2D4A; border-radius: 6px; padding: 4px 8px; }}
"""

_LIGHT = f"""
* {{ font-family: "Segoe UI", "Helvetica Neue", "Arial", "Yu Gothic UI", "Meiryo", sans-serif; font-size: 13px; }}
QMainWindow, QWidget {{ background: #E8F4FD; color: #1A2332; }}
QStatusBar {{ background: #E8F4FD; color: #5C8DB8; border-top: 1px solid #B8D4E8; }}
/* Separate the nav rail (its own panel + divider) from the content area. */
QWidget#navWrap {{ background: #EDF5FB; border-right: 1px solid #C4DBEC; }}
QWidget#navWrap QTreeWidget, QWidget#navWrap QListWidget {{ background: transparent; border: none; }}
QWidget#contentArea {{ background: #E8F4FD; }}
/* Compact icons app-wide (they looked oversized on scaled displays). */
QPushButton, QToolButton, QComboBox, QTabBar {{ qproperty-iconSize: 14px 14px; }}
QTreeWidget#navrail {{ qproperty-iconSize: 16px 16px; }}

QTabWidget::pane {{ background: #FFFFFF; border: 1px solid #B8D4E8; border-radius: 0px; top: 2px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{
    background: #D0E8F5; color: #3A6B8C; padding: 9px 22px; margin: 0 6px 8px 0;
    border-radius: 10px; border: 1px solid #B8D4E8; font-weight: 500;
}}
QTabBar::tab:selected {{ background: {GRADIENT}; color: white; border: none; font-weight: 700; }}
QTabBar::tab:hover:!selected {{ background: #B8D4E8; color: #1A2332; }}
/* Co4E flow "browser" tabs — light-theme counterpart (see dark block). */
QTabBar#flowTabs::tab {{ background: #EDF5FB; color: #3A6B8C; border: 1px solid #C4DBEC;
    padding: 6px 10px; margin: 0 3px 0 0; min-height: 22px; border-radius: 8px; }}
QTabBar#flowTabs::tab:selected {{ background: {GRADIENT}; color: white; border: 1px solid transparent; }}
QTabBar#flowTabs::tab:hover:!selected {{ background: #D0E8F5; color: #1A2332; }}
/* The "new flow" (＋) button (light) — styled as the last tab in the strip. */
QPushButton#flowAddBtn {{ background: #EDF5FB; color: #3A6B8C; border: 1px solid #C4DBEC;
    border-radius: 8px; padding: 6px 0; font-size: 15px; font-weight: bold; min-height: 22px; }}
QPushButton#flowAddBtn:hover {{ background: #D0E8F5; color: #1A2332; }}
/* Co4E sidebar (light) — transparent tabs, translucent selection, dark text. */
QTabBar#co4eSideTabs {{ qproperty-iconSize: 18px 18px; }}
QTabBar#co4eSideTabs::tab {{ background: transparent; color: #3A6B8C; border: none;
    border-radius: 6px; padding: 5px; margin: 0 6px 0 0; }}
QTabBar#co4eSideTabs::tab:selected {{ background: rgba(0,150,199,0.20); color: #1A2332; }}
QTabBar#co4eSideTabs::tab:hover:!selected {{ background: #D0E8F5; color: #1A2332; }}
/* Co4E canvas frame (light). */
QGraphicsView#co4eCanvas {{ background: #FFFFFF; border: 1px solid #B8D4E8; border-radius: 10px; }}

QGroupBox {{
    background: #FFFFFF; border: 1px solid #B8D4E8; border-radius: 18px;
    margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 700;
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; top: 2px;
    padding: 0 6px; color: #5C8DB8; letter-spacing: 0.5px;
}}

QPlainTextEdit, QTextEdit, QTextBrowser, QLineEdit, QTreeView, QListView, QTreeWidget, QListWidget {{
    background: white; color: #1A2332; border: 1px solid #C0D8EC; border-radius: 10px;
    selection-background-color: {ACCENT}; selection-color: white;
}}
QScrollArea {{ background: transparent; border: none; }}
QAbstractScrollArea::corner {{ background: transparent; }}
QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; border-radius: 6px; }}
QTreeView::item:hover, QListView::item:hover {{ background: #E0EFFA; }}
QTreeView::item:selected, QListView::item:selected {{ background: rgba(0,150,199,0.18); color: #1A2332; }}
QHeaderView::section {{ background: #E8F4FD; color: #5C8DB8; border: none; border-bottom: 1px solid #B8D4E8; padding: 6px; }}

QPushButton {{
    background: white; color: #1A2332; border: 1px solid #C0D8EC;
    border-radius: 10px; padding: 8px 16px;
}}
QPushButton:hover {{ background: #E0EFFA; }}
QPushButton:disabled {{ color: #8AA8C0; background: #F0F8FC; }}
QPushButton#primary {{ background: {GRADIENT}; color: white; border: none; font-weight: 700; padding: 8px 18px; }}
QPushButton#primary:hover {{ background: {GRADIENT_HOVER}; }}
QPushButton#danger {{ background: #E5484D; color: white; border: none; font-weight: 600; }}
QPushButton#danger:hover {{ background: #EF6368; }}
QPushButton#navMenuBtn {{
    background: transparent; border: none; border-radius: 8px; padding: 5px 6px;
    font-weight: 700; font-size: 11px; letter-spacing: 1px; color: #5C8DB8; text-align: left;
}}
QPushButton#navMenuBtn:hover {{ background: #D0E8F5; color: #1A2332; }}
QPushButton#navMenuBtn:pressed {{ background: #B8D4E8; }}

QLabel#badge {{ background: #D0ECF8; color: #0077B6; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgeSuccess {{ background: #D0F5E8; color: #1B7A3D; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgePurple {{ background: #E8E0FF; color: #6238C9; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgePink {{ background: #F8E0F0; color: #B93A85; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#badgeWarn {{ background: #D0ECF8; color: #0077B6; border-radius: 11px; padding: 2px 12px; font-weight: 600; }}
QLabel#hint {{ color: #5C8DB8; }}
QLabel#warning {{ color: #0077B6; font-weight: 600; }}

QComboBox {{ background: white; border: 1px solid #C0D8EC; border-radius: 10px; padding: 6px 10px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{
    background: white; border: 1px solid #C0D8EC; border-radius: 10px;
    selection-background-color: {ACCENT}; selection-color: white; outline: none;
}}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C0D8EC; border-radius: 5px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C0D8EC; border-radius: 5px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; border: none; background: none; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px; height: 16px; background: white; border: 1px solid #C0D8EC; border-radius: 5px;
}}
QRadioButton::indicator {{ border-radius: 9px; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {GRADIENT}; border-color: {ACCENT};
}}
QCheckBox::indicator:disabled {{ border-color: #D0E8F5; background: #F0F8FC; }}
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background: #B8D4E8; border-color: #8AB8D8;
}}

QMenu {{ background: white; color: #1A2332; border: 1px solid #C0D8EC; border-radius: 10px; padding: 4px; }}
QMenu::item {{ padding: 6px 16px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}

QToolTip {{ background: #FFFFFF; color: #1A2332; border: 1px solid #C0D8EC; border-radius: 6px; padding: 4px 8px; }}
"""

# Node colors used by the Graph view (shared light/dark).
NODE_COLORS = {
    "user": "#3B82F6",
    "assistant": ACCENT,
    "tool": "#8B5CF6",
    "result": "#22A06B",
    "error": "#E5484D",
}


def resolve_theme(theme: str) -> str:
    """Resolve 'system' to 'dark'/'light' based on the OS color scheme."""
    if theme != "system":
        return theme
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            scheme = app.styleHints().colorScheme()
            return "light" if scheme == Qt.ColorScheme.Light else "dark"
    except Exception:
        pass
    return "dark"


def stylesheet(theme: str) -> str:
    return _LIGHT if resolve_theme(theme) == "light" else _DARK