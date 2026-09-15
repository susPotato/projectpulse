"""RoutingService — the façade the UI (and the "REST-equivalent" API) talk to.

It wires the pieces together and holds the per-app state (assessment store +
pending-switch registry). It is deliberately **Qt-free and thread-safe** so it
can run from a chat worker thread, the scheduler, or a test. The UI layer adds
the toggle widget and the Manual-mode confirm dialog on top of these methods.

Logical API surface (mirrors the task's REST endpoints):

* :meth:`reassess`               ↔ ``POST /models/reassess``
* :meth:`best_for`               ↔ ``GET  /models/best``
* :meth:`assessments` / :meth:`status` ↔ ``GET /models/assessments``
* :meth:`add_candidate`          ↔ ``POST /models/add``
* :meth:`route`                  ↔ the decision half of ``POST /task/execute``
* :meth:`create_pending` / :meth:`resolve_pending` ↔ ``POST /task/confirm-switch``
* :meth:`get_routing_config` / :meth:`update_routing_config` ↔ ``/routing/config``
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .classifier import classify
from .clients import AppProbeClient, ProbeClient
from .models import (
    ModelAssessment,
    PendingSwitch,
    Policy,
    SwitchDecision,
    SwitchMode,
    TaskType,
    candidate_key,
    split_key,
)
from .orchestrator import Candidate, check_and_update
from .prober import make_judge
from .selector import Ranking, rank_models
from .store import AssessmentStore
from .switch_controller import Executor, PendingSwitchRegistry, decide

logger = logging.getLogger("cowork_local.routing")

# A sensible cheap judge model per known provider, used when the user hasn't
# pinned one in Settings. Falls back to the provider's own configured model.
_CHEAP_JUDGE_MODEL = {
    "anthropic": "claude-haiku-4-5-20251001",
    "codex": "gpt-4o-mini",
    "github_copilot": "gpt-4o-mini",
    "openai_compat": "",   # unknown gateway → use configured model
    "ollama": "",          # local → use configured model
}


@dataclass
class RouteResult:
    """Outcome of routing one turn (before any execution)."""

    mode: SwitchMode
    task_type: TaskType
    decision: SwitchDecision
    ranking: Optional[Ranking] = None

    @property
    def should_switch(self) -> bool:
        return self.decision.should_switch

    @property
    def needs_confirmation(self) -> bool:
        """Manual mode with a worthwhile switch → the UI must ask the user."""
        return self.mode == SwitchMode.MANUAL and self.decision.should_switch

    def target(self) -> Optional[Tuple[str, str]]:
        """The (provider, model_id) to switch to, or None."""
        if not self.decision.to_model:
            return None
        return split_key(self.decision.to_model)


class RoutingService:
    """Central routing coordinator, one per :class:`AppContext`."""

    def __init__(
        self,
        ctx: Any,
        *,
        store: Optional[AssessmentStore] = None,
        client: Optional[ProbeClient] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.ctx = ctx
        self.store = store or AssessmentStore()
        self._client = client  # None → lazily build AppProbeClient(ctx)
        import time as _time
        self.pending = PendingSwitchRegistry(clock=clock or _time.time)
        self._reassess_lock = threading.Lock()
        self._reassessing = False

    # -- config helpers ------------------------------------------------- #
    @property
    def _routing_cfg(self) -> Dict[str, Any]:
        return self.ctx.config.routing

    def get_routing_config(self) -> Dict[str, Any]:
        """Current routing behaviour config (for ``GET /routing/config``)."""
        return dict(self._routing_cfg)

    def update_routing_config(self, **changes) -> Dict[str, Any]:
        """Patch routing config (``PATCH /routing/config``) and persist.

        Only known keys are accepted; unknown keys are ignored so a typo can't
        silently poison the config.
        """
        cfg = self._routing_cfg
        allowed = {
            "switch_mode", "policy", "min_score_gain", "confirm_timeout_sec",
            "reassess_interval_hours", "per_provider_concurrency",
            "judge_provider", "judge_model", "auto_reassess_on_add",
        }
        for k, v in changes.items():
            if k in allowed:
                cfg[k] = v
        self.ctx.config.save()
        return dict(cfg)

    def _policy(self) -> Policy:
        raw = (self._routing_cfg.get("policy") or "balanced").lower()
        try:
            return Policy(raw)
        except ValueError:
            return Policy.BALANCED

    def _client_or_build(self) -> ProbeClient:
        if self._client is None:
            self._client = AppProbeClient(self.ctx)
        return self._client

    def _resolve_judge(self) -> Tuple[str, str]:
        """Which (provider, model) grades every probe.

        Uses the pinned judge from config when set, else a cheap default for
        the active provider (falling back to that provider's configured model).
        """
        cfg = self._routing_cfg
        provider = cfg.get("judge_provider") or self.ctx.config.active_provider
        model = cfg.get("judge_model") or ""
        if not model:
            model = _CHEAP_JUDGE_MODEL.get(provider, "")
        if not model:
            model = self.ctx.config.provider_conf(provider).get("model", "")
        return provider, model

    # -- candidates ----------------------------------------------------- #
    def candidates(self) -> List[Candidate]:
        """The models to assess: explicit ``routing.candidates`` plus each
        provider's currently-configured model (so the model in use is always
        scored). Deduplicated, order-stable."""
        out: List[Candidate] = []
        seen = set()

        def _add(provider: str, model_id: str, tier: Optional[str]) -> None:
            if not provider or not model_id:
                return
            key = candidate_key(provider, model_id)
            if key in seen:
                return
            seen.add(key)
            out.append((provider, model_id, tier))

        for c in self._routing_cfg.get("candidates") or []:
            if isinstance(c, dict):
                _add(c.get("provider", ""), c.get("model_id", ""), c.get("tier"))

        # Always include each configured provider's active model.
        for name, conf in (self.ctx.config.data.get("providers") or {}).items():
            _add(name, conf.get("model", ""), None)

        return out

    def add_candidate(
        self,
        provider: str,
        model_id: str,
        tier: Optional[str] = None,
        *,
        reassess: Optional[bool] = None,
    ) -> bool:
        """Add a model to the assessed set (``POST /models/add``).

        Returns True if it was newly added. When ``reassess`` (defaults to the
        ``auto_reassess_on_add`` config) is True, kicks off a background
        reassess so the new model gets scored right away.
        """
        cfg = self._routing_cfg
        cand = cfg.setdefault("candidates", [])
        key = candidate_key(provider, model_id)
        if any(candidate_key(c.get("provider", ""), c.get("model_id", "")) == key
               for c in cand if isinstance(c, dict)):
            return False
        cand.append({"provider": provider, "model_id": model_id, "tier": tier})
        self.ctx.config.save()

        do_reassess = cfg.get("auto_reassess_on_add", True) if reassess is None else reassess
        if do_reassess:
            self.reassess_background()
        return True

    # -- assessment run ------------------------------------------------- #
    def reassess(
        self,
        policy: Optional[Policy] = None,
        *,
        client: Optional[ProbeClient] = None,
    ) -> Dict[str, ModelAssessment]:
        """Run a full assessment (blocking). Safe to call from a worker thread.

        Guarded so two reassessments never run at once (a second call while one
        is in flight is a no-op returning the current store)."""
        with self._reassess_lock:
            if self._reassessing:
                logger.info("routing.reassess: already running — skipping duplicate")
                return self.store.load()
            self._reassessing = True
        try:
            policy = policy or self._policy()
            judge_provider, judge_model = self._resolve_judge()
            cli = client or self._client_or_build()
            if not judge_model:
                logger.warning("routing.reassess: no judge model resolved — aborting")
                return self.store.load()
            return check_and_update(
                self.candidates(), cli,
                judge_provider=judge_provider, judge_model=judge_model,
                store=self.store, config=self.ctx.config, policy=policy,
                per_provider_concurrency=int(self._routing_cfg.get("per_provider_concurrency", 2)),
            )
        finally:
            with self._reassess_lock:
                self._reassessing = False

    def reassess_background(
        self,
        policy: Optional[Policy] = None,
        on_done: Optional[Callable[[Dict[str, ModelAssessment]], None]] = None,
    ) -> threading.Thread:
        """Run :meth:`reassess` on a daemon thread (non-Qt, headless-safe)."""
        def _run() -> None:
            try:
                result = self.reassess(policy)
            except Exception:  # noqa: BLE001 — never let a reassess crash the app
                logger.exception("routing.reassess background run failed")
                result = {}
            if on_done is not None:
                try:
                    on_done(result)
                except Exception:  # noqa: BLE001
                    logger.exception("routing.reassess on_done callback failed")

        t = threading.Thread(target=_run, name="routing-reassess", daemon=True)
        t.start()
        return t

    def is_reassessing(self) -> bool:
        return self._reassessing

    # -- query ---------------------------------------------------------- #
    def assessments(self) -> Dict[str, ModelAssessment]:
        return self.store.load()

    def status(self) -> Dict[str, Any]:
        """``GET /models/assessments`` — last_updated + per-model summary."""
        assessments = self.store.load()
        return {
            "last_updated": self.store.last_updated(),
            "policy": self.store.policy(),
            "count": len(assessments),
            "models": sorted(assessments.keys()),
        }

    def best_for(
        self,
        task_type: TaskType,
        policy: Optional[Policy] = None,
        *,
        required_capabilities: Optional[List[str]] = None,
    ) -> Ranking:
        """Ranking + best model for a task type (``GET /models/best``)."""
        policy = policy or self._policy()
        return rank_models(
            self.store.load().values(), task_type, policy,
            required_capabilities=required_capabilities,
        )

    # -- routing decision ----------------------------------------------- #
    def route(
        self,
        surface: str,
        prompt: str,
        current_provider: str,
        current_model: str,
        *,
        mode_override: Optional[str] = None,
        required_capabilities: Optional[List[str]] = None,
        task_type: Optional[TaskType] = None,
    ) -> RouteResult:
        """Decide whether/how to switch models for one turn on ``surface``.

        Does NOT execute anything — returns a :class:`RouteResult` the caller
        acts on (Auto → switch & run; Manual+should_switch → confirm; else run
        as-is). Never raises: any internal failure yields an Off/no-switch
        result so a broken assessment store can't block chatting.
        """
        try:
            mode = (mode_override or self.ctx.config.routing_mode_for(surface) or "off").lower()
            mode_enum = SwitchMode(mode) if mode in ("off", "auto", "manual") else SwitchMode.OFF
            tt = task_type or classify(prompt)
            current_key = candidate_key(current_provider, current_model) if current_model else None

            if mode_enum == SwitchMode.OFF:
                decision = decide(current_key, rank_models([], tt), SwitchMode.OFF, 0.0, task_type=tt)
                return RouteResult(mode=mode_enum, task_type=tt, decision=decision)

            policy = self._policy()
            ranking = rank_models(
                self.store.load().values(), tt, policy,
                required_capabilities=required_capabilities,
            )
            min_gain = float(self._routing_cfg.get("min_score_gain", 0.05) or 0.0)
            decision = decide(current_key, ranking, mode_enum, min_gain, task_type=tt)
            return RouteResult(mode=mode_enum, task_type=tt, decision=decision, ranking=ranking)
        except Exception:  # noqa: BLE001 — routing must never break a chat turn
            logger.exception("routing.route failed — falling back to no-switch")
            tt = task_type or TaskType.QA
            current_key = candidate_key(current_provider, current_model) if current_model else None
            decision = decide(current_key, rank_models([], tt), SwitchMode.OFF, 0.0, task_type=tt)
            return RouteResult(mode=SwitchMode.OFF, task_type=tt, decision=decision)

    # -- manual pending switches ---------------------------------------- #
    def create_pending(self, decision: SwitchDecision, task_payload: Dict) -> PendingSwitch:
        """Register a Manual-mode proposal awaiting the user's confirm."""
        timeout = float(self._routing_cfg.get("confirm_timeout_sec", 60) or 60)
        return self.pending.create(decision, task_payload, timeout)

    def resolve_pending(self, request_id: str, approve: bool, run: Executor) -> Optional[Dict]:
        """Confirm/reject a pending switch (idempotent) — ``POST /task/confirm-switch``."""
        return self.pending.resolve(request_id, approve, run)

    def get_pending(self, request_id: str) -> Optional[PendingSwitch]:
        return self.pending.get(request_id)

    def sweep_pending(self) -> List[str]:
        """Expire overdue pending switches (called periodically by the scheduler)."""
        return self.pending.sweep_expired()


__all__ = ["RoutingService", "RouteResult"]
