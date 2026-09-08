"""Turning findings into sentences - and refusing to let that change them.

Two modules, in the order they must be built:

`fallback.py`
    Deterministic prose assembled from the bundle. Built first on purpose: it is
    the offline demo insurance and the answer to "what happens when the model is
    down". Nothing here can fail in a way that empties the page.

`validator.py`
    The gate every model draft passes before it is allowed near a reader. It
    assumes the model is careless rather than malicious, and it is the reason a
    model can be used at all in a product whose thesis is determinism.

`client.py`
    The fence, built last because it is the only optional part. It assembles a
    prompt that contains no digit, sends it, puts the answer through the gate,
    and substitutes the numbers afterwards. Every failure lands back on
    `fallback.py` with a reason attached, so the model can be absent, broken or
    wrong without the page being any of those. It names no vendor.

`providers.py`
    One adapter per vendor - Claude, GPT, Gemini - each reduced to
    `(system, user) -> str`. Nothing in the fence changes when the vendor does,
    which is the whole reason the seam is that narrow.

The model's only job is to put findings it did not compute into sentences it is
not permitted to change.
"""

from app.narration.client import (
    BriefLeak,
    Drafter,
    NarrationBrief,
    NarrationOutcome,
    NarrationUnavailable,
    build_brief,
    narrate,
)
from app.narration.providers import (
    DEFAULT_MODELS,
    PROVIDERS,
    ModelConfig,
    drafter_for,
)
from app.narration.fallback import QUESTION_HEADINGS, render_narrative
from app.narration.validator import (
    ValidationIssue,
    ValidationResult,
    contains_quantity,
    validate_draft,
)

__all__ = [
    "DEFAULT_MODELS",
    "PROVIDERS",
    "QUESTION_HEADINGS",
    "BriefLeak",
    "Drafter",
    "ModelConfig",
    "NarrationBrief",
    "NarrationOutcome",
    "NarrationUnavailable",
    "ValidationIssue",
    "ValidationResult",
    "build_brief",
    "contains_quantity",
    "drafter_for",
    "narrate",
    "render_narrative",
    "validate_draft",
]
