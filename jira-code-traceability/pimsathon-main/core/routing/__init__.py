"""Auto Model Assessment & Routing.

Reads the configured providers/models, assesses each model (static metadata +
dynamic probes judged by a fixed cheap judge model), scores them per task type
under a policy, and routes each chat/agent turn to the best-fit model — either
silently (Auto), after user confirmation (Manual), or not at all (Off).

Public entry point for the app is :class:`service.RoutingService`, wired into
``AppContext`` and driven from the Off/Auto/Manual toggle on each chat screen.

Sub-modules
-----------
* ``models``            — Pydantic data models shared by everything here.
* ``scorer``            — fit-score formula + policy weights.
* ``store``             — persist/version assessments (atomic write + history).
* ``metadata``          — static metadata table + enrich() with fallbacks.
* ``clients``           — thin adapter over the app's existing Provider layer.
* ``prober``            — benchmark prompts, probe_model(), judge().
* ``scorer``/``selector`` — score and rank candidates per task type.
* ``switch_controller`` — Auto/Manual/Off switch decisions + pending confirms.
* ``classifier``        — classify a prompt into a TaskType.
* ``orchestrator``      — check_and_update(): the full assess→score→store loop.
* ``service``           — façade the UI talks to.
* ``scheduler``         — periodic + on-model-add reassess triggers.
"""
from .models import (
    ModelAssessment,
    ModelMetadata,
    PendingSwitch,
    Policy,
    ProbeResult,
    SwitchDecision,
    SwitchMode,
    SwitchStatus,
    TaskType,
    candidate_key,
    split_key,
)

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
