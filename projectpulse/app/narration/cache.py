"""Caches a model's phrasing so the same facts are never asked for twice.

The model's only job is phrasing (see `client.py`) - it changes no finding
and no figure. So if the deterministic template narrative a call would ask it
to rephrase is byte-identical to one already served, re-asking buys nothing
but latency: same rules, same brief, same answer. `narrate()` already builds
that template unconditionally as its fallback - it *is* the fingerprint of
every fact the model saw, so hashing it needs no second definition of "the
data changed" here.

Persisted in Postgres rather than kept in process memory on purpose: Fly
machines stop when idle (`fly.toml`'s `auto_stop_machines`) and a deployment
runs more than one, so an in-memory cache would miss on every cold start and
disagree between machines. A row here is visible to whichever machine answers
the next request.

**Only a successful model draft is cached.** A fallback never is: caching a
failure would make a transient network blip, or a credential about to be
fixed, stick for the cache entry's lifetime - and the failure path already
costs nothing beyond the one attempt `narrate` already made, so there is
nothing to save by remembering it.
"""

from __future__ import annotations

import hashlib
import logging

from app.api.schemas.insight import InsightBundle
from app.models.narration import NarrationCacheEntry
from app.narration.client import Drafter, NarrationOutcome, narrate
from app.narration.fallback import render_narrative

log = logging.getLogger(__name__)


def _fingerprint(project_id: str, bundle: InsightBundle) -> str:
    template = render_narrative(bundle)
    return hashlib.sha256(f"{project_id}\n{template}".encode()).hexdigest()


def cached_narrate(
    session,
    bundle: InsightBundle,
    *,
    project_id: str,
    drafter: Drafter | None,
) -> NarrationOutcome:
    """`narrate`, but a model is asked at most once per unique set of facts.

    `drafter is None` skips the cache entirely and goes straight to `narrate`
    - the template path is already instant, so there is no latency to save
    and nothing worth persisting for it.
    """
    if drafter is None:
        return narrate(bundle, drafter=None)

    key = _fingerprint(project_id, bundle)
    cached = session.get(NarrationCacheEntry, key)
    if cached is not None:
        # `attempts=0`: no attempt was made on this call. `fallback_reason`
        # stays None - as far as this page is concerned, the model answered.
        return NarrationOutcome(cached.narrative, "model", None, 0)

    outcome = narrate(bundle, drafter=drafter)
    if outcome.source == "model":
        session.merge(
            NarrationCacheEntry(
                cache_key=key, project_id=project_id, narrative=outcome.narrative
            )
        )
        log.info("narration cached for %s (%s)", project_id, key[:12])
    return outcome
