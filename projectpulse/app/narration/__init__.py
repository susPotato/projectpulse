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

The model's only job is to put findings it did not compute into sentences it is
not permitted to change.
"""

from app.narration.fallback import QUESTION_HEADINGS, render_narrative
from app.narration.validator import (
    ValidationIssue,
    ValidationResult,
    validate_draft,
)

__all__ = [
    "QUESTION_HEADINGS",
    "ValidationIssue",
    "ValidationResult",
    "render_narrative",
    "validate_draft",
]
