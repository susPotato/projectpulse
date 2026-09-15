"""Periodic reassessment scheduler (Qt layer).

No APScheduler dependency — this mirrors the app's existing ``TaskScheduler``:
a lightweight ``QTimer`` ticks periodically and, when the configured interval
has elapsed since the last assessment, launches a background reassess on a
daemon thread (so the UI never blocks). It also expires stale Manual-mode
pending switches on each tick.

Reassessment is expensive (it spends real tokens), so the cadence is
deliberately coarse — default every 24h, configurable via
``routing.reassess_interval_hours`` (0 disables the periodic run entirely).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("cowork_local.routing")

# How often the timer wakes to CHECK whether a reassess is due. The actual
# reassess cadence is governed by reassess_interval_hours; this is just the
# polling granularity (cheap — it only reads a timestamp).
_TICK_MS = 30 * 60 * 1000  # 30 minutes


class RoutingScheduler(QObject):
    """Drives periodic reassessment + pending-switch expiry for a service."""

    reassess_started = Signal()
    reassess_finished = Signal(int)  # number of models assessed

    def __init__(self, ctx: Any, service: Any, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.service = service
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self.tick)

    # -- lifecycle ------------------------------------------------------ #
    def start(self) -> None:
        """Begin periodic checks. Does NOT force an immediate reassess — the
        first one happens when the interval is genuinely due (or never, if the
        store is fresh), to avoid a burst of API calls at every app launch."""
        self.tick()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    # -- tick ----------------------------------------------------------- #
    def _interval_hours(self) -> float:
        try:
            return float(self.ctx.config.routing.get("reassess_interval_hours", 24) or 0)
        except Exception:  # noqa: BLE001
            return 24.0

    def _hours_since_last(self) -> Optional[float]:
        last = self.service.store.last_updated()
        if not last:
            return None  # never assessed
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
        except (ValueError, TypeError):
            return None

    def _routing_enabled_anywhere(self) -> bool:
        """Is routing actually in use? True if the global mode is auto/manual OR
        any chat surface overrides to auto/manual. When everything is Off, the
        assessment scores would never be consulted — so we don't spend tokens
        probing for them (no surprise cost on a fresh install)."""
        try:
            routing = self.ctx.config.routing
            if (routing.get("switch_mode") or "off") in ("auto", "manual"):
                return True
            for m in (routing.get("surface_modes") or {}).values():
                if m in ("auto", "manual"):
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def is_due(self) -> bool:
        if not self._routing_enabled_anywhere():
            return False  # routing off everywhere → don't probe (would be wasted cost)
        interval = self._interval_hours()
        if interval <= 0:
            return False  # periodic reassess disabled
        since = self._hours_since_last()
        if since is None:
            return True  # never assessed → due once routing is actually enabled
        return since >= interval

    def tick(self) -> None:
        """Expire stale pending switches; reassess if the interval is due."""
        try:
            self.service.sweep_pending()
        except Exception:  # noqa: BLE001
            logger.exception("routing.scheduler: sweep_pending failed")

        if not self.is_due() or self.service.is_reassessing():
            return

        logger.info("routing.scheduler: reassess is due — starting background run")
        self.reassess_started.emit()

        def _done(result) -> None:
            self.reassess_finished.emit(len(result or {}))

        self.service.reassess_background(on_done=_done)

    def trigger_now(self) -> None:
        """Force an out-of-band reassess (e.g. Settings' 'Reassess now' button)."""
        if self.service.is_reassessing():
            return
        self.reassess_started.emit()
        self.service.reassess_background(on_done=lambda r: self.reassess_finished.emit(len(r or {})))


__all__ = ["RoutingScheduler"]
