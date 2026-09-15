"""Pydantic v2 data models for Auto Model Assessment & Routing.

These are the provider-agnostic shapes shared by every routing module — the
enricher, prober, scorer, selector and switch-controller all speak in terms of
these. They serialize cleanly to/from JSON so the assessment store and the
app config (``~/.cowork_local/…``) can round-trip them.

Terminology
-----------
* A **candidate** is a ``(provider, model_id)`` pair the app can call.
* An **assessment** is what we learned about one candidate: its static
  metadata, the dynamic probe results per task type, and the derived
  ``fit_scores`` per task type.
* A **task type** is the kind of work a message represents (qa / coding / …).
* A **policy** is how we weigh quality vs cost vs latency when scoring.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class TaskType(str, Enum):
    """The kinds of work a chat/agent turn can represent.

    A message is classified into exactly one of these before routing (see
    ``classifier.py``). ``BENCHMARK_TASKS`` in the prober has one fixed prompt
    per value so every candidate model is compared on the same yardstick.
    """

    QA = "qa"
    CODING = "coding"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    CREATIVE = "creative"


class Policy(str, Enum):
    """How to trade off quality, cost and latency when scoring a model."""

    QUALITY = "quality"
    COST = "cost"
    LATENCY = "latency"
    BALANCED = "balanced"


class SwitchMode(str, Enum):
    """Per-surface routing behaviour, driven by the Off/Auto/Manual toggle.

    * ``OFF``    — routing disabled; always use the manually-selected model.
    * ``AUTO``   — silently switch to the best model when it clears the gain
                   threshold, then run the task.
    * ``MANUAL`` — propose the switch and wait for the user to confirm before
                   running with the new model.
    """

    OFF = "off"
    AUTO = "auto"
    MANUAL = "manual"


class SwitchStatus(str, Enum):
    """Lifecycle of a :class:`PendingSwitch` awaiting user confirmation."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


# --------------------------------------------------------------------------- #
# Static metadata + dynamic probe
# --------------------------------------------------------------------------- #
class ModelMetadata(BaseModel):
    """Static, mostly-price/capability facts about one candidate model.

    ``cost_per_1k_*`` are USD per 1,000 tokens. They are ``None`` — not a
    guess — when the price is genuinely unknown; ``metadata_incomplete`` is
    then set True so the scorer/UI can flag it rather than silently trusting a
    fabricated number (see ``metadata.py``).
    """

    provider: str
    model_id: str
    tier: Optional[str] = None  # e.g. "fast" | "powerful" — free-form, user-supplied
    cost_per_1k_input: Optional[float] = None
    cost_per_1k_output: Optional[float] = None
    max_context: Optional[int] = None
    capabilities: Set[str] = Field(default_factory=set)  # e.g. {"vision", "tools"}
    available: bool = True
    metadata_incomplete: bool = False

    @property
    def key(self) -> str:
        """Stable ``provider/model_id`` identity used as a dict key everywhere."""
        return candidate_key(self.provider, self.model_id)

    @property
    def avg_cost_per_1k(self) -> Optional[float]:
        """Blended input/output price, or None if either side is unknown.

        A rough 1:3 input:output ratio (typical chat workload) is used so a
        single scalar can feed the cost term of the fit score.
        """
        ci, co = self.cost_per_1k_input, self.cost_per_1k_output
        if ci is None or co is None:
            return None
        return (ci + 3.0 * co) / 4.0


class ProbeResult(BaseModel):
    """Outcome of running one benchmark task against one model.

    ``success=False`` means the call itself failed (network/auth/model error);
    ``error`` then holds a human-readable reason and ``quality_score`` stays 0.
    """

    latency_ms: float = 0.0
    success: bool = False
    quality_score: float = 0.0  # 0..1, from the judge model
    tokens_out: int = 0
    error: Optional[str] = None


class ModelAssessment(BaseModel):
    """Everything we know about one candidate after an assessment run."""

    metadata: ModelMetadata
    # Keyed by TaskType.value (JSON-friendly string keys).
    probes: Dict[str, ProbeResult] = Field(default_factory=dict)
    fit_scores: Dict[str, float] = Field(default_factory=dict)
    assessed_at: Optional[str] = None  # ISO-8601 UTC timestamp

    @property
    def key(self) -> str:
        return self.metadata.key

    def fit_for(self, task_type: TaskType) -> float:
        """Fit score for ``task_type`` (0.0 if this model was never scored for it)."""
        return float(self.fit_scores.get(task_type.value, 0.0))


# --------------------------------------------------------------------------- #
# Switch decision + pending confirmation
# --------------------------------------------------------------------------- #
class SwitchDecision(BaseModel):
    """The verdict of comparing the current model against the selector's best.

    ``should_switch`` is False when routing is Off, when the best candidate IS
    the current model, or when the score gain is below ``min_score_gain``.
    """

    should_switch: bool
    from_model: Optional[str] = None  # candidate key, or None if nothing active yet
    to_model: Optional[str] = None
    from_score: float = 0.0
    to_score: float = 0.0
    score_gain: float = 0.0
    reason: str = ""
    mode: SwitchMode = SwitchMode.OFF
    task_type: Optional[str] = None


class PendingSwitch(BaseModel):
    """A Manual-mode switch proposal held until the user confirms/rejects.

    Stored in-memory with a TTL; ``result`` caches the executed task output so
    a repeated confirm of the same ``request_id`` is idempotent (returns the
    cached result instead of running the task twice).
    """

    request_id: str
    task_payload: Dict[str, Any] = Field(default_factory=dict)
    decision: SwitchDecision
    created_at: float  # epoch seconds (monotonic wall clock at creation)
    expires_at: float
    status: SwitchStatus = SwitchStatus.PENDING
    result: Optional[Dict[str, Any]] = None  # cached task result once executed


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def candidate_key(provider: str, model_id: str) -> str:
    """The canonical ``provider/model_id`` string used as a dict key."""
    return f"{provider}/{model_id}"


def split_key(key: str) -> tuple[str, str]:
    """Inverse of :func:`candidate_key`. Splits on the first ``/`` only, so a
    model id that itself contains ``/`` (some gateways use ``org/model``) is
    preserved intact."""
    provider, _, model_id = key.partition("/")
    return provider, model_id


__all__ = [
    "TaskType",
    "Policy",
    "SwitchMode",
    "SwitchStatus",
    "ModelMetadata",
    "ProbeResult",
    "ModelAssessment",
    "SwitchDecision",
    "PendingSwitch",
    "candidate_key",
    "split_key",
]
