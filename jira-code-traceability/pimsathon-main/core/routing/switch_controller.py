"""Decide whether to switch models, and orchestrate Auto vs Manual execution.

Two independent pieces:

* :func:`decide` — a **pure** function turning ``(current model, ranking, mode,
  threshold)`` into a :class:`SwitchDecision`. No I/O, no state; trivially
  testable.
* :class:`PendingSwitchRegistry` — an in-memory, TTL'd, thread-safe store of
  Manual-mode switch proposals awaiting user confirmation, with an
  **idempotent** ``resolve`` (confirming the same ``request_id`` twice never
  runs the task twice).

Flow (from the task spec)::

    task arrives → classify task_type → selector.best_model()
                 → decide() compares best vs current
                 → gain < min_score_gain          → keep current model
                 → gain ok, mode == AUTO           → switch now, run task
                 → gain ok, mode == MANUAL         → create PendingSwitch, ask user
                 → gain ok, mode == OFF            → never switch (keep current)
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Callable, Dict, List, Optional, Set

from .models import (
    PendingSwitch,
    SwitchDecision,
    SwitchMode,
    SwitchStatus,
    TaskType,
)
from .selector import Ranking

# Executor signature used by the registry: given the resolved model key and
# whether that represents a switch away from the original, run the task and
# return a JSON-serializable result dict.
Executor = Callable[[str, bool], Dict]


def decide(
    current_key: Optional[str],
    ranking: Ranking,
    mode: SwitchMode,
    min_score_gain: float,
    *,
    task_type: Optional[TaskType] = None,
) -> SwitchDecision:
    """Compare the current model against the ranking's best under ``mode``.

    Returns a :class:`SwitchDecision` whose ``should_switch`` is True only when
    routing is enabled, a better candidate exists, and it beats the current
    model by at least ``min_score_gain``. ``reason`` always explains the call
    in words (e.g. *"coding fit 0.82 > current 0.71, gain 0.11"*).
    """
    tt = task_type or ranking.task_type
    best = ranking.best
    tt_name = tt.value if tt else "?"

    base = dict(
        from_model=current_key,
        to_model=best.key if best else None,
        mode=mode,
        task_type=tt.value if tt else None,
    )

    # Routing disabled → never switch.
    if mode == SwitchMode.OFF:
        return SwitchDecision(
            should_switch=False, score_gain=0.0,
            reason="routing off — keeping current model", **base,
        )

    # Nothing assessed / nothing usable → cannot switch.
    if best is None:
        return SwitchDecision(
            should_switch=False, score_gain=0.0,
            reason="no assessed candidate available for this task", **base,
        )

    to_score = best.score
    from_score = ranking.score_of(current_key) if current_key else 0.0
    best_name = best.assessment.metadata.model_id

    # No current model yet (fresh surface) → adopt the best outright.
    if not current_key:
        return SwitchDecision(
            should_switch=to_score > 0.0,
            from_score=0.0, to_score=to_score, score_gain=to_score,
            reason=f"no current model — selecting best-fit {best_name} ({tt_name} fit {to_score:.2f})",
            **base,
        )

    # Current model is already the best-fit → stay put.
    if best.key == current_key:
        return SwitchDecision(
            should_switch=False,
            from_score=from_score, to_score=to_score, score_gain=0.0,
            reason=f"current model is already best-fit for {tt_name} (fit {to_score:.2f})",
            **base,
        )

    gain = round(to_score - from_score, 6)
    if gain < min_score_gain:
        return SwitchDecision(
            should_switch=False,
            from_score=from_score, to_score=to_score, score_gain=gain,
            reason=(
                f"best {best_name} fit {to_score:.2f} vs current {from_score:.2f}, "
                f"gain {gain:.2f} < threshold {min_score_gain:.2f} — keeping current"
            ),
            **base,
        )

    return SwitchDecision(
        should_switch=True,
        from_score=from_score, to_score=to_score, score_gain=gain,
        reason=(
            f"{tt_name} fit {to_score:.2f} > current {from_score:.2f}, "
            f"gain {gain:.2f} — switch to {best_name}"
        ),
        **base,
    )


class PendingSwitchRegistry:
    """Thread-safe, TTL'd store of Manual-mode switch proposals.

    A proposal is created when Manual mode wants to switch; the UI shows it and
    later calls :meth:`resolve` with the user's approve/reject. Idempotency:
    resolving the same ``request_id`` more than once runs the task exactly once
    and returns the cached result to every caller.

    ``clock`` is injectable so tests can drive expiry deterministically.
    """

    # A short cap so a wedged executor can't hang a waiting confirm forever.
    _RESOLVE_WAIT_SEC = 600.0

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._items: Dict[str, PendingSwitch] = {}
        self._events: Dict[str, threading.Event] = {}
        self._running: Set[str] = set()
        self._lock = threading.Lock()
        self._clock = clock

    # -- creation ------------------------------------------------------- #
    def create(
        self,
        decision: SwitchDecision,
        task_payload: Dict,
        timeout_sec: float,
    ) -> PendingSwitch:
        """Register a new pending switch and return it (with a fresh id)."""
        rid = uuid.uuid4().hex
        now = self._clock()
        ps = PendingSwitch(
            request_id=rid,
            task_payload=task_payload,
            decision=decision,
            created_at=now,
            expires_at=now + max(0.0, float(timeout_sec)),
            status=SwitchStatus.PENDING,
        )
        with self._lock:
            self._items[rid] = ps
            self._events[rid] = threading.Event()
        return ps

    # -- lookup --------------------------------------------------------- #
    def get(self, request_id: str) -> Optional[PendingSwitch]:
        """Fetch a pending switch, lazily marking it EXPIRED if its TTL passed."""
        with self._lock:
            ps = self._items.get(request_id)
            if ps is not None:
                self._maybe_expire_locked(ps)
            return ps

    def _maybe_expire_locked(self, ps: PendingSwitch) -> None:
        if ps.status == SwitchStatus.PENDING and self._clock() >= ps.expires_at:
            ps.status = SwitchStatus.EXPIRED

    # -- resolution ----------------------------------------------------- #
    def resolve(self, request_id: str, approve: bool, run: Executor) -> Optional[Dict]:
        """Confirm (``approve=True``) or reject (``approve=False``) a proposal.

        On the FIRST resolution: runs ``run(model_key, switched)`` where
        ``model_key`` is the proposed model when approved, else the current
        model; caches and returns its result. Subsequent resolutions of the
        same id return the cached result **without** re-running (idempotent).

        An already-EXPIRED proposal is forced down the reject path (run with the
        current model) — matching "timeout → keep current model".

        Returns ``None`` if ``request_id`` is unknown.
        """
        with self._lock:
            ps = self._items.get(request_id)
            if ps is None:
                return None
            self._maybe_expire_locked(ps)
            event = self._events[request_id]

            # Already executed → idempotent replay, no matter who asks.
            if ps.result is not None:
                return ps.result

            expired = ps.status == SwitchStatus.EXPIRED
            effective_approve = bool(approve) and not expired

            # First caller to arrive wins the right to execute exactly once.
            i_run = request_id not in self._running
            if i_run:
                self._running.add(request_id)
                ps.status = (
                    SwitchStatus.CONFIRMED if effective_approve else SwitchStatus.REJECTED
                )

        if not i_run:
            # Another thread is executing — wait for it, then replay its result.
            event.wait(timeout=self._RESOLVE_WAIT_SEC)
            with self._lock:
                return self._items[request_id].result

        # Execute outside the lock (the network/LLM call may be slow).
        decision = ps.decision
        model_key = decision.to_model if effective_approve else decision.from_model
        try:
            result = run(model_key or "", bool(effective_approve))
        finally:
            with self._lock:
                self._running.discard(request_id)
        with self._lock:
            ps.result = result
            event.set()
        return result

    # -- maintenance ---------------------------------------------------- #
    def sweep_expired(self) -> List[str]:
        """Mark all overdue PENDING proposals EXPIRED; return their ids."""
        expired: List[str] = []
        with self._lock:
            for rid, ps in self._items.items():
                if ps.status == SwitchStatus.PENDING and self._clock() >= ps.expires_at:
                    ps.status = SwitchStatus.EXPIRED
                    expired.append(rid)
        return expired

    def purge(self, keep_resolved: bool = False) -> int:
        """Drop resolved/expired entries to free memory. Returns count removed.

        With ``keep_resolved=True``, entries that carry a cached ``result`` are
        retained so their idempotent replay still works.
        """
        removed = 0
        with self._lock:
            for rid in list(self._items):
                ps = self._items[rid]
                terminal = ps.status in (
                    SwitchStatus.CONFIRMED, SwitchStatus.REJECTED, SwitchStatus.EXPIRED
                )
                if terminal and not (keep_resolved and ps.result is not None):
                    self._items.pop(rid, None)
                    self._events.pop(rid, None)
                    self._running.discard(rid)
                    removed += 1
        return removed

    def pending_ids(self) -> List[str]:
        with self._lock:
            return [
                rid for rid, ps in self._items.items()
                if ps.status == SwitchStatus.PENDING
            ]


__all__ = ["decide", "PendingSwitchRegistry", "Executor"]
