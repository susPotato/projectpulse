"""A small, dependency-free smooth-spline line chart (QWidget).

Given a series of ``(label, value)`` points it draws a Catmull-Rom spline with a
soft area fill, a highlighted endpoint, y-grid + value labels, and a few x-axis
labels — theme-aware (light/dark). Used by the Dashboard's token/cost-over-time
chart; kept generic so any screen can reuse it.
"""
from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ..theme import ACCENT


def _endpoint_label_rect(point_x: float, point_y: float, text_width: float,
                         widget_width: float, top: float) -> Tuple[float, float]:
    """Top-left (x, y) for the endpoint value label's bounding box, given its
    ACTUAL text width (not a hardcoded guess — that used to clip/hide longer
    formatted amounts, since ``QPainter.drawText(rect, ...)`` clips to the
    rect it's given). Clamped so the box never runs off either horizontal
    edge; flips BELOW the point instead of above when the point sits too
    close to the title to avoid overlapping it."""
    box_w = text_width + 6
    x = max(2, min(point_x - box_w, widget_width - 2 - box_w))
    y = point_y - 22
    if y < top + 2:            # too close to the title — flip below the dot
        y = point_y + 8
    return x, y


def _catmull_rom(points: List[QPointF]) -> QPainterPath:
    """A smooth spline through ``points`` (Catmull-Rom → cubic Bézier)."""
    path = QPainterPath()
    if not points:
        return path
    path.moveTo(points[0])
    if len(points) == 1:
        return path
    n = len(points)
    for i in range(n - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < n else points[i + 1]
        c1 = QPointF(p1.x() + (p2.x() - p0.x()) / 6.0, p1.y() + (p2.y() - p0.y()) / 6.0)
        c2 = QPointF(p2.x() - (p3.x() - p1.x()) / 6.0, p2.y() - (p3.y() - p1.y()) / 6.0)
        path.cubicTo(c1, c2, p2)
    return path


class SplineChart(QWidget):
    def __init__(self):
        super().__init__()
        self._points: List[Tuple[str, float]] = []
        self._fmt: Callable[[float], str] = lambda v: f"{v:.2f}"
        self._title = ""
        self._refs: List[Tuple[float, str, str]] = []   # (value, label, color hex)
        self.setMinimumHeight(200)

    def set_data(self, points: List[Tuple[str, float]],
                 value_fmt: Optional[Callable[[float], str]] = None, title: str = "") -> None:
        self._points = list(points or [])
        if value_fmt is not None:
            self._fmt = value_fmt
        self._title = title
        self.update()

    def set_reference_lines(self, refs: List[Tuple[float, str, str]]) -> None:
        """Dashed horizontal comparison lines: ``[(value, label, color_hex), …]``
        (e.g. last week / last month with a % delta). Included in the y-scale."""
        self._refs = list(refs or [])
        self.update()

    def _dark(self) -> bool:
        from .chat_view import _app_theme
        return _app_theme() == "dark"

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        dark = self._dark()
        grid = QColor("#1A2D4A" if dark else "#C7DEEE")
        text = QColor("#8FB2D4" if dark else "#5C7A94")
        accent = QColor(ACCENT)
        w, h = self.width(), self.height()

        pts = self._points
        if len(pts) < 1:
            p.setPen(text)
            p.drawText(self.rect(), Qt.AlignCenter, "—")
            return
        vals = [v for _l, v in pts]
        scale_vals = vals + [r[0] for r in self._refs]   # refs must fit on-scale too
        vmax = max(scale_vals) or 1.0
        vmin = min(min(scale_vals), 0.0)
        span = (vmax - vmin) or 1.0

        # The left margin fits the y-axis grid labels' ACTUAL widest rendering
        # — a fixed 54px used to clip (hide) longer formatted amounts, since
        # QPainter.drawText(rect, ...) clips to the rect it's given.
        grid_labels = [self._fmt(vmax - span * i / 4) for i in range(5)]
        label_w = max((p.fontMetrics().horizontalAdvance(s) for s in grid_labels), default=0)
        left, right, top, bottom = max(54, label_w + 10), 14, 24, 26
        self._last_left = left    # exposed for tests — the margin actually used
        plot_w = max(1, w - left - right)
        plot_h = max(1, h - top - bottom)

        if self._title:
            p.setPen(text)
            f = p.font(); f.setBold(True); p.setFont(f)
            p.drawText(left, 4, plot_w, 18, Qt.AlignLeft | Qt.AlignVCenter, self._title)
            f.setBold(False); p.setFont(f)

        # y grid + labels (4 lines)
        p.setPen(QPen(grid, 1))
        for i in range(5):
            y = top + plot_h * i / 4
            p.drawLine(left, int(y), left + plot_w, int(y))
            p.setPen(text)
            p.drawText(0, int(y) - 8, left - 6, 16, Qt.AlignRight | Qt.AlignVCenter, grid_labels[i])
            p.setPen(QPen(grid, 1))

        # dashed comparison lines (last week / last month) — drawn under the
        # spline so the curve stays readable; label sits at the left.
        for value, label, color in self._refs:
            y = top + plot_h * (1 - (value - vmin) / span)
            c = QColor(color)
            p.setPen(QPen(c, 1, Qt.DashLine))
            p.drawLine(left, int(y), left + plot_w, int(y))
            if label:
                p.setPen(c)
                p.drawText(left + 4, int(y) - 15, plot_w - 8, 14,
                           Qt.AlignLeft | Qt.AlignVCenter, label)

        n = len(pts)
        xs = [left + (plot_w * i / (n - 1) if n > 1 else plot_w / 2) for i in range(n)]
        screen = [QPointF(xs[i], top + plot_h * (1 - (vals[i] - vmin) / span)) for i in range(n)]
        spline = _catmull_rom(screen)

        # area fill under the curve
        area = QPainterPath(spline)
        area.lineTo(screen[-1].x(), top + plot_h)
        area.lineTo(screen[0].x(), top + plot_h)
        area.closeSubpath()
        grad = QLinearGradient(0, top, 0, top + plot_h)
        c0 = QColor(accent); c0.setAlpha(80)
        c1 = QColor(accent); c1.setAlpha(10)
        grad.setColorAt(0, c0); grad.setColorAt(1, c1)
        p.fillPath(area, QBrush(grad))
        # the spline line
        p.setPen(QPen(accent, 2))
        p.drawPath(spline)
        # endpoint dot + latest value
        p.setBrush(QBrush(accent)); p.setPen(Qt.NoPen)
        p.drawEllipse(screen[-1], 4, 4)
        p.setPen(accent)
        f = p.font(); f.setBold(True); p.setFont(f)
        # QPainter.drawText(rect, ...) CLIPS to the given rect — a hardcoded
        # 60px box used to silently cut off (hide) longer formatted amounts
        # (currency symbol + thousands separators easily exceed 60px).
        label = self._fmt(vals[-1])
        tw = p.fontMetrics().horizontalAdvance(label)
        label_x, label_y = _endpoint_label_rect(screen[-1].x(), screen[-1].y(), tw, w, top)
        p.drawText(int(label_x), int(label_y), int(tw + 6), 16,
                   Qt.AlignRight | Qt.AlignVCenter, label)
        f.setBold(False); p.setFont(f)

        # x labels: first, middle, last
        p.setPen(text)
        idxs = sorted(set([0, n // 2, n - 1]))
        for i in idxs:
            p.drawText(int(xs[i]) - 40, h - bottom + 4, 80, 18,
                       Qt.AlignCenter, pts[i][0])
