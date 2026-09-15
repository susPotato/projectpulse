"""Icons: hand-painted panel-collapse toggles + a shared line-icon library
rendered from local SVG path data — no external image files, no network fetch,
so every icon stays crisp at any size and recolors for the light/dark theme.

The glyph set is ported 1:1 from the Nova Platform web app's shared icon
library (``nova-platform/apps/web/components/ui/icons.tsx``) so the desktop app
and the web platform show the SAME icons. Same Feather-style thin stroke, same
``viewBox 0 0 24 24``, same ``stroke-width`` (1.7) — the only difference is the
render path (Qt ``QSvgRenderer`` here vs. React ``<svg>`` there).

Entries fall into three groups below: (1) the full Nova set under Nova's own
names; (2) a few app-specific glyphs Nova doesn't define (save/new/attach/…),
drawn in the same thin-stroke style; (3) legacy aliases so this app's existing
``icon("chat"/"document"/"flask"/"sparkle"/"settings")`` call sites keep working
— each alias points at the matching Nova design (message/file/beaker/sparkles/
gear). Feather Icons and the derived Nova set are MIT-licensed."""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

_COLOR = "#8b8d98"  # neutral grey, visible on both light and dark buttons


def _hidpi_pixmap(size: int) -> QPixmap:
    """A transparent pixmap sized for the current display's pixel ratio (with the
    ratio set on it) so icons render crisply on HiDPI/scaled screens — a plain
    ``QPixmap(size, size)`` is only ``size`` device pixels and looks blurry when
    the OS scales it up. A QPainter on this draws in logical (size) coordinates."""
    # A plain fixed-size transparent pixmap — exactly ``size`` px, no supersample
    # (the earlier HiDPI supersampling made icons look oversized on scaled
    # displays). Icon display size is governed by each widget's iconSize.
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    return pm

# Inner content of a 24x24 stroke-based SVG (viewBox/stroke attrs added by
# `icon()` below). One entry per semantic action, reused across every tab/
# dialog so the same concept always gets the same glyph.
_PATHS = {
    # ---- Nova set: navigation / areas ---------------------------------
    "dashboard": '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/>'
                 '<rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/>',
    "schedule": '<rect x="3" y="4" width="18" height="17" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/>'
                '<line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    "workspaces": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "cowork": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "flow": '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/>'
            '<circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    "graph": '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>'
             '<line x1="8.6" y1="13.5" x2="15.4" y2="17.5"/><line x1="15.4" y1="6.5" x2="8.6" y2="10.5"/>',
    "book": '<path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z"/>'
            '<path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z"/>',
    "monitoring": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "list": '<line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/>'
            '<line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/>'
            '<line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/>',
    "server": '<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/>'
              '<line x1="6" y1="6" x2="6.01" y2="6"/><line x1="6" y1="18" x2="6.01" y2="18"/>',
    "box": '<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>'
           '<polyline points="3.3 7 12 12 20.7 7"/><line x1="12" y1="22" x2="12" y2="12"/>',
    "shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/>'
               '<line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/>'
               '<line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/>'
               '<line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/>'
               '<line x1="17" y1="16" x2="23" y2="16"/>',
    "users": '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "user": '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
    "briefcase": '<rect x="2" y="7" width="20" height="14" rx="2"/>'
                 '<path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
    "award": '<circle cx="12" cy="8" r="7"/><polyline points="8.2 13.9 7 22 12 19 17 22 15.8 13.9"/>',
    "gear": '<circle cx="12" cy="12" r="3"/>'
            '<path d="M12 1v4M12 19v4M4.2 4.2l2.8 2.8M17 17l2.8 2.8M1 12h4M19 12h4M4.2 19.8L7 17M17 7l2.8-2.8"/>',
    "globe": '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/>'
             '<path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>'
              '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/>',

    # ---- Nova set: actions / objects ----------------------------------
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "edit": '<path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>'
            '<path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4z"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/>'
             '<path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
    "play": '<polygon points="5 3 19 12 5 21 5 3"/>',
    "pause": '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "sparkles": '<path d="M12 3l1.8 4.8L18.5 9.5 13.8 11.2 12 16l-1.8-4.8L5.5 9.5l4.7-1.7z"/>'
                '<path d="M19 15l.7 1.9L21.5 17.5l-1.8.7L19 20l-.7-1.8L16.5 17.5l1.8-.6z"/>',
    "refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>'
               '<path d="M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "file": '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>'
            '<polyline points="13 2 13 9 20 9"/>',
    "code": '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/>',
    "robot": '<rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 8V4M9 4h6"/>'
             '<line x1="2" y1="14" x2="4" y2="14"/><line x1="20" y1="14" x2="22" y2="14"/>'
             '<circle cx="9" cy="13" r="1"/><circle cx="15" cy="13" r="1"/><line x1="9" y1="17" x2="15" y2="17"/>',
    "puzzle": '<path d="M20.5 11H19V7a2 2 0 0 0-2-2h-4V3.5a2.5 2.5 0 0 0-5 0V5H4a2 2 0 0 0-2 2v3.8h1.5a2.2 2.2 0 0 1 0 4.4H2V19a2 2 0 0 0 2 2h3.8v-1.5a2.2 2.2 0 0 1 4.4 0V21H17a2 2 0 0 0 2-2v-4h1.5a2.5 2.5 0 0 0 0-5z"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "close": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "upload": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
              '<polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
                '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/>'
            '<path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
    "lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
    "bell": '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
    "tag": '<path d="M20.6 13.4 12 22l-9-9V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 16 14"/>',
    "filter": '<polygon points="22 3 2 3 10 12.5 10 19 14 21 14 12.5 22 3"/>',
    "eye": '<path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z"/><circle cx="12" cy="12" r="3"/>',
    "chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/>'
             '<line x1="6" y1="20" x2="6" y2="14"/>',
    "database": '<ellipse cx="12" cy="5" rx="9" ry="3"/>'
                '<path d="M21 5v6c0 1.7-4 3-9 3s-9-1.3-9-3V5"/>'
                '<path d="M21 11v6c0 1.7-4 3-9 3s-9-1.3-9-3v-6"/>',
    "cpu": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>'
           '<line x1="9" y1="1" x2="9" y2="4"/><line x1="15" y1="1" x2="15" y2="4"/>'
           '<line x1="9" y1="20" x2="9" y2="23"/><line x1="15" y1="20" x2="15" y2="23"/>'
           '<line x1="20" y1="9" x2="23" y2="9"/><line x1="20" y1="14" x2="23" y2="14"/>'
           '<line x1="1" y1="9" x2="4" y2="9"/><line x1="1" y1="14" x2="4" y2="14"/>',
    "cloud": '<path d="M18 10h-1.3A7 7 0 1 0 4 15h13a4 4 0 0 0 1-7.9z"/>',
    "beaker": '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/>'
              '<line x1="7" y1="15" x2="17" y2="15"/>',
    "compass": '<circle cx="12" cy="12" r="10"/><polygon points="16.2 7.8 14.1 14.1 7.8 16.2 9.9 9.9 16.2 7.8"/>',
    "bolt": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "star": '<polygon points="12 2 15.1 8.6 22 9.3 17 14.1 18.2 21 12 17.6 5.8 21 7 14.1 2 9.3 8.9 8.6 12 2"/>',
    "flag": '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>'
            '<line x1="4" y1="22" x2="4" y2="15"/>',
    "wrench": '<path d="M14.7 6.3a4 4 0 0 0-5.3 5.3L3 18l3 3 6.4-6.4a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.8-.7-.7-2.8z"/>',
    "message": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-4.1A8.4 8.4 0 0 1 3.7 12a8.4 8.4 0 0 1 8.4-8.4h.5A8.4 8.4 0 0 1 21 11.5z"/>',
    "send": '<line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    "branch": '<line x1="6" y1="3" x2="6" y2="15"/><circle cx="18" cy="6" r="3"/>'
              '<circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>',
    "alert": '<path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>'
             '<line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "plug": '<path d="M12 22v-5"/><path d="M9 8V2"/><path d="M15 8V2"/>'
            '<path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"/>',
    "factory": '<path d="M2 20a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V8l-7 5V8l-7 5V4a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/>'
               '<line x1="17" y1="18" x2="18" y2="18"/><line x1="12" y1="18" x2="13" y2="18"/>'
               '<line x1="7" y1="18" x2="8" y2="18"/>',
    "ruler": '<path d="M21.3 15.3a2.4 2.4 0 0 1 0 3.4l-2.6 2.6a2.4 2.4 0 0 1-3.4 0L2.7 8.7a2.4 2.4 0 0 1 0-3.4'
             'l2.6-2.6a2.4 2.4 0 0 1 3.4 0z"/><path d="M14.5 12.5 16 11"/><path d="M11.5 9.5 13 8"/>'
             '<path d="M8.5 6.5 10 5"/><path d="M17.5 15.5 19 14"/>',
    "pin": '<line x1="12" y1="17" x2="12" y2="22"/>'
           '<path d="M5 17h14v-1.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V6h1a2 2 0 0 0 0-4H8a2 2 0 0 0 0 4h1v4.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24z"/>',

    # ---- App-specific glyphs Nova doesn't define (same thin-stroke) ----
    "save": '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
            '<polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/>',
    "new": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
           '<polyline points="14 2 14 8 20 8"/><line x1="12" y1="18" x2="12" y2="12"/>'
           '<line x1="9" y1="15" x2="15" y2="15"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "shuffle": '<polyline points="16 3 21 3 21 8"/><line x1="4" y1="20" x2="21" y2="3"/>'
               '<polyline points="21 16 21 21 16 21"/><line x1="15" y1="15" x2="21" y2="21"/>'
               '<line x1="4" y1="4" x2="9" y2="9"/>',
    "unlock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 9.9-1"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="M21 2l-9.6 9.6"/><path d="M15.5 7.5l3 3L22 7l-3-3"/>',
    "attach": '<path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/>',
    "compress": '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/>'
                '<line x1="14" y1="10" x2="21" y2="3"/><line x1="3" y1="21" x2="10" y2="14"/>',
    "network": '<path d="M5 12.55a11 11 0 0 1 14.08 0"/><path d="M1.42 9a16 16 0 0 1 21.16 0"/>'
               '<path d="M8.53 16.11a6 6 0 0 1 6.95 0"/><line x1="12" y1="20" x2="12" y2="20"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/>'
               '<line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/>'
           '<line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>'
           '<line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/>'
           '<line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>'
           '<line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "chevron-left": '<polyline points="15 18 9 12 15 6"/>',
    "chevron-right": '<polyline points="9 18 15 12 9 6"/>',
    "chevron-down": '<polyline points="6 9 12 15 18 9"/>',
    "chevron-up": '<polyline points="18 15 12 9 6 15"/>',
    # A plain checkmark (kept as-is): the app uses "check" for a run-the-check
    # action button, where a bare tick reads better than Nova's box+check.
    "check": '<polyline points="20 6 9 17 4 12"/>',

    # ---- Legacy aliases → matching Nova design (keep old call sites working) --
    "chat": '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-4.1A8.4 8.4 0 0 1 3.7 12a8.4 8.4 0 0 1 8.4-8.4h.5A8.4 8.4 0 0 1 21 11.5z"/>',  # = message
    "document": '<path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>'
                '<polyline points="13 2 13 9 20 9"/>',  # = file
    "flask": '<path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/>'
             '<line x1="7" y1="15" x2="17" y2="15"/>',  # = beaker
    "sparkle": '<path d="M12 3l1.8 4.8L18.5 9.5 13.8 11.2 12 16l-1.8-4.8L5.5 9.5l4.7-1.7z"/>'
               '<path d="M19 15l.7 1.9L21.5 17.5l-1.8.7L19 20l-.7-1.8L16.5 17.5l1.8-.6z"/>',  # = sparkles
    "settings": '<circle cx="12" cy="12" r="3"/>'
                '<path d="M12 1v4M12 19v4M4.2 4.2l2.8 2.8M17 17l2.8 2.8M1 12h4M19 12h4M4.2 19.8L7 17M17 7l2.8-2.8"/>',  # = gear
}


def all_icon_names() -> list:
    """Every icon name usable by :func:`icon` — the built-in line-icon set plus
    any custom icons added via Monitoring's Icon Management (icons_admin_tab).
    Backs the icon-picker dropdown offered wherever a step/agent/flow icon is
    chosen, so users pick from this SAME registry instead of typing a name."""
    from ..core import custom_icons
    try:
        custom = custom_icons.list_custom()
    except Exception:  # noqa: BLE001 — the picker must never crash on a bad read
        custom = []
    return sorted(set(_PATHS) | set(custom))


def icon_picker_combo(current: str = "") -> QComboBox:
    """An editable dropdown of every icon name (see :func:`all_icon_names`),
    each row previewing its actual glyph — so choosing a step/agent icon is a
    quick pick from Monitoring's icon registry instead of typing a name from
    memory. Still editable: a name not yet in the list (e.g. a custom icon
    about to be added) can be typed directly, same as before."""
    combo = QComboBox()
    combo.setEditable(True)
    for name in all_icon_names():
        combo.addItem(icon(name, size=14), name)
    idx = combo.findText(current) if current else -1
    if idx >= 0:
        combo.setCurrentIndex(idx)
    else:
        combo.setCurrentText(current or "")
    return combo


def icon(name: str, size: int = 16, color: str = _COLOR) -> QIcon:
    """A flat thin-line icon for ``name`` (see ``_PATHS`` for the full list),
    tinted ``color`` — rendered from local SVG data, no image files/network.
    Stroke width 1.7 matches the Nova Platform web app's shared icon set."""
    # A user-added custom icon (full SVG under ~/.cowork_local/icons) is rendered
    # as-is (keeps its own colours). Then built-in glyphs; then a neutral fallback.
    if name not in _PATHS:
        try:
            from ..core import custom_icons
            custom_svg = custom_icons.get_svg(name)
        except Exception:  # noqa: BLE001 — icon lookup must never crash the UI
            custom_svg = None
        if custom_svg:
            renderer = QSvgRenderer(QByteArray(custom_svg.encode("utf-8")))
            pm = _hidpi_pixmap(size)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            renderer.render(p)
            p.end()
            return QIcon(pm)
    # Unknown names must never crash the UI — fall back to a neutral glyph.
    body = _PATHS.get(name) or _PATHS.get("sparkle") or ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
           f'fill="none" stroke="{color}" stroke-width="1.7" stroke-linecap="round" '
           f'stroke-linejoin="round">{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pm = _hidpi_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    return QIcon(pm)


def _panel_icon(fill_left: bool, size: int = 16, color: str = _COLOR) -> QIcon:
    """A rounded panel split by a divider, with one narrow side filled solid
    (the 'sidebar' toggle look)."""
    pm = _hidpi_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    col = QColor(color)
    p.setPen(QPen(col, 1.5))

    rect = QRectF(2.0, 3.0, size - 4.0, size - 6.0)
    p.drawRoundedRect(rect, 3.0, 3.0)

    col_w = rect.width() * 0.34
    if fill_left:
        bar_x = rect.left() + col_w
        fill = QRectF(rect.left() + 1.2, rect.top() + 1.2, col_w - 1.6, rect.height() - 2.4)
    else:
        bar_x = rect.right() - col_w
        fill = QRectF(bar_x + 0.4, rect.top() + 1.2, col_w - 1.6, rect.height() - 2.4)

    p.drawLine(int(bar_x), int(rect.top() + 1), int(bar_x), int(rect.bottom() - 1))
    p.fillRect(fill, QBrush(col))
    p.end()
    return QIcon(pm)


def collapse_left_icon() -> QIcon:
    """Panel with the left strip filled — collapse toward the left. Rendered
    at the same 16px as ``icon()`` so it sits flush with the nav-rail icons
    (Dashboard etc.) instead of looking one size up."""
    return _panel_icon(fill_left=True)


def collapse_right_icon() -> QIcon:
    """Panel with the right strip filled — collapse toward the right."""
    return _panel_icon(fill_left=False)


def pixmap(name: str, size: int = 16, color: str = _COLOR) -> QPixmap:
    """The line-icon ``name`` as a QPixmap (for QLabel.setPixmap — QLabel has no
    setIcon). Same glyph/renderer as ``icon()``."""
    return icon(name, size, color).pixmap(size, size)


# Status-LED colors — a filled dot, the one place a solid glyph (not a line
# icon) is the right metaphor for an on/off/running indicator.
DOT_GREEN = "#22c55e"
DOT_RED = "#ef4444"
DOT_AMBER = "#f59e0b"
DOT_BLUE = "#3b82f6"
DOT_GREY = "#9ca3af"


def dot_icon(color: str = DOT_GREY, size: int = 12) -> QIcon:
    """A small filled status dot (LED). Used for on/off/running indicators where
    a colored dot reads better than a line glyph."""
    pm = _hidpi_pixmap(size)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color)))
    m = size * 0.22
    p.drawEllipse(QRectF(m, m, size - 2 * m, size - 2 * m))
    p.end()
    return QIcon(pm)


class IconLabel(QWidget):
    """A line-icon shown immediately to the left of a text label — the standard
    replacement for the old "🔒 Some text" emoji-prefixed QLabels. ``set_text``
    updates just the text; ``set_icon`` swaps the glyph/color, so dynamic
    status labels (lock/unlock, …) keep working."""

    def __init__(self, name: str, text: str = "", *, size: int = 16,
                 color: str = _COLOR, gap: int = 6, parent=None):
        super().__init__(parent)
        self._size = size
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(gap)
        self._icon = QLabel()
        self._icon.setPixmap(pixmap(name, size, color))
        self._text = QLabel(text)
        lay.addWidget(self._icon)
        lay.addWidget(self._text)
        lay.addStretch(1)

    def set_text(self, text: str) -> None:
        self._text.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 — QLabel-compatible alias
        self._text.setText(text)

    def set_icon(self, name: str, color: str = _COLOR) -> None:
        self._icon.setPixmap(pixmap(name, self._size, color))

    def text_label(self) -> QLabel:
        """The inner text QLabel (for styling — setStyleSheet, etc.)."""
        return self._text
