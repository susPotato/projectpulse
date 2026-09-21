"""Recording what every model call spent, and adding it up.

The adapters in `narration/providers.py` are closures with the signature
`(system, user) -> str`. They threw `response.usage` away, so this app had no
record of its own spend at all.

Rather than change that signature - which would have touched four adapters,
two agent modules and every caller - a call wraps itself in `track()`, which
times it, catches nothing, and writes one row either way. Which *feature* is
spending is ambient rather than a parameter, carried in a `ContextVar` set by
whichever entry point began the work. That keeps the accounting out of the
adapters' argument lists and means a new call site records correctly by being
inside the scope, rather than by remembering to pass a string down.

**Accounting never breaks a feature.** Every write is inside a `try`. A usage
row that cannot be stored is logged and dropped; the narrative still renders.
That is the right trade for a bookkeeping side effect on the path of a
user-visible request.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

#: Which feature the current call belongs to. Empty outside any scope, which
#: records as "unattributed" rather than being dropped - a call nobody labelled
#: still costs money, and a row with a vague label is more use than no row.
_FEATURE: ContextVar[str] = ContextVar("pulse_llm_feature", default="")

#: The call currently in flight, so an adapter can report its token counts
#: without the counts being threaded back through `Drafter`'s return type.
#: `None` outside `track()`, which makes every `report_*` helper a no-op - an
#: adapter called directly from a test does not need a usage row to exist.
_CURRENT: ContextVar["Call | None"] = ContextVar("pulse_llm_call", default=None)


@contextmanager
def for_feature(feature: str):
    """Attribute every model call inside this block to `feature`."""
    token = _FEATURE.set(feature)
    try:
        yield
    finally:
        _FEATURE.reset(token)


def current_feature() -> str:
    return _FEATURE.get() or "unattributed"


@dataclass
class Call:
    """A call in flight. The adapter reports its token counts through this."""

    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    #: Set by `track` when the body raised.
    error: str = ""
    _extra: dict = field(default_factory=dict)

    def tokens(
        self,
        *,
        input: int = 0,  # noqa: A002 - mirrors the vendor field name
        output: int = 0,
        cached: int = 0,
        cache_write: int = 0,
        reasoning: int = 0,
    ) -> None:
        """Report what the vendor said this cost. Safe to call more than once.

        Accumulates rather than replaces, so an adapter that retries inside one
        `track()` block - Anthropic's server-side fallback does exactly that -
        records the total rather than only the last attempt.
        """
        self.input_tokens += int(input or 0)
        self.output_tokens += int(output or 0)
        self.cached_input_tokens += int(cached or 0)
        self.cache_write_tokens += int(cache_write or 0)
        self.reasoning_tokens += int(reasoning or 0)

    def from_anthropic(self, response) -> None:
        """Read an Anthropic `Message.usage`, tolerating absent fields."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        self.tokens(
            input=getattr(usage, "input_tokens", 0) or 0,
            output=getattr(usage, "output_tokens", 0) or 0,
            cached=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        )
        # The model actually served, which is not always the one asked for -
        # a server-side fallback answers on a different model at its own rates.
        served = getattr(response, "model", "")
        if served:
            self.model = served

    def from_openai(self, response) -> None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details is not None:
            cached = getattr(details, "cached_tokens", 0) or 0
        reasoning = 0
        out_details = getattr(usage, "completion_tokens_details", None)
        if out_details is not None:
            reasoning = getattr(out_details, "reasoning_tokens", 0) or 0
        # `prompt_tokens` includes the cached ones, unlike Anthropic's
        # `input_tokens` - so subtract, or the cached tokens bill twice.
        prompt = getattr(usage, "prompt_tokens", 0) or 0
        self.tokens(
            input=max(prompt - cached, 0),
            output=getattr(usage, "completion_tokens", 0) or 0,
            cached=cached,
            reasoning=reasoning,
        )

    def from_gemini(self, response) -> None:
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return
        cached = getattr(usage, "cached_content_token_count", 0) or 0
        prompt = getattr(usage, "prompt_token_count", 0) or 0
        self.tokens(
            input=max(prompt - cached, 0),
            output=getattr(usage, "candidates_token_count", 0) or 0,
            cached=cached,
            reasoning=getattr(usage, "thoughts_token_count", 0) or 0,
        )

    def from_mapping(self, usage: dict | None) -> None:
        """An OpenAI-shaped `usage` object that arrived as plain JSON.

        The FPT gateway returns one of these inside its envelope; it has no SDK
        to give it attributes.
        """
        if not isinstance(usage, dict):
            return
        cached = 0
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
        prompt = int(usage.get("prompt_tokens") or 0)
        self.tokens(
            input=max(prompt - cached, 0),
            output=int(usage.get("completion_tokens") or 0),
            cached=cached,
        )


@contextmanager
def track(provider: str, model: str):
    """Time a model call and record one row for it, success or failure.

    Re-raises whatever the body raised - this observes, it does not handle. A
    failed call is recorded with `ok = False` because a refusal or a timeout
    still burns input tokens on most vendors, and a board that hid them would
    under-report exactly when something is looping.
    """
    call = Call(provider=provider, model=model)
    token = _CURRENT.set(call)
    started = time.monotonic()
    try:
        yield call
    except Exception as exc:
        call.error = f"{type(exc).__name__}: {exc}"[:500]
        _write(call, time.monotonic() - started, ok=False)
        raise
    else:
        _write(call, time.monotonic() - started, ok=True)
    finally:
        _CURRENT.reset(token)


# --------------------------------------------------------------------------
# What the adapters call.
#
# One line each, and a no-op outside `track()`. The alternative was widening
# `Drafter` from `(system, user) -> str` to return usage alongside the text,
# which would have rewritten four adapters, both agent modules and every
# caller - to thread through a value only the bookkeeping cares about.
# --------------------------------------------------------------------------


def report_anthropic(response) -> None:
    call = _CURRENT.get()
    if call is not None:
        call.from_anthropic(response)


def report_openai(response) -> None:
    call = _CURRENT.get()
    if call is not None:
        call.from_openai(response)


def report_gemini(response) -> None:
    call = _CURRENT.get()
    if call is not None:
        call.from_gemini(response)


def report_mapping(payload) -> None:
    """For an adapter holding a parsed JSON body rather than an SDK object."""
    call = _CURRENT.get()
    if call is not None:
        call.from_mapping(payload)


def _write(call: Call, elapsed_seconds: float, *, ok: bool) -> None:
    """Persist one row. Never raises - see the module docstring."""
    from app.db import session_scope
    from app.llm.pricing import estimate
    from app.models.llm import LlmUsage

    try:
        cost, priced = estimate(
            call.model,
            input_tokens=call.input_tokens,
            output_tokens=call.output_tokens,
            cached_input_tokens=call.cached_input_tokens,
            cache_write_tokens=call.cache_write_tokens,
        )
        with session_scope() as session:
            session.add(
                LlmUsage(
                    feature=current_feature(),
                    provider=call.provider,
                    model=call.model,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    cached_input_tokens=call.cached_input_tokens,
                    reasoning_tokens=call.reasoning_tokens,
                    cost_usd=cost,
                    # A call that spent no tokens at all - an SDK that never
                    # left the process, say - is not "unpriced", it is free.
                    # Only a real call with no rate counts against the board's
                    # unpriced total.
                    priced=priced or (call.input_tokens + call.output_tokens) == 0,
                    latency_ms=int(elapsed_seconds * 1000),
                    ok=ok,
                    error=call.error or None,
                )
            )
    except Exception as exc:  # noqa: BLE001 - see the module docstring
        log.warning("could not record model usage: %s", exc)


# --------------------------------------------------------------------------
# Reading it back.
# --------------------------------------------------------------------------


def summary(days: int = 30) -> dict:
    """Everything the usage board shows, in one query pass.

    Grouped server-side rather than by shipping rows to the browser: this table
    grows by a row per model call and a month of a busy demo is not something
    to serialize into a page.
    """
    from sqlalchemy import case, func

    from app.db import session_scope
    from app.llm.features import FEATURES
    from app.models.llm import LlmUsage

    since = datetime.now(timezone.utc) - timedelta(days=max(days, 1))

    empty = {
        "days": days,
        "since": since.isoformat(),
        "totals": {
            "calls": 0,
            "failed": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_input_tokens": 0,
            "reasoning_tokens": 0,
            "cost_usd": 0.0,
            "unpriced_calls": 0,
        },
        "by_feature": [],
        "by_model": [],
        "by_day": [],
        "recent_failures": [],
        "available": False,
    }

    try:
        with session_scope() as session:
            base = session.query(LlmUsage).filter(LlmUsage.created_at >= since)

            totals = base.with_entities(
                func.count(LlmUsage.id),
                func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                func.coalesce(func.sum(LlmUsage.cached_input_tokens), 0),
                func.coalesce(func.sum(LlmUsage.reasoning_tokens), 0),
                func.coalesce(func.sum(LlmUsage.cost_usd), 0.0),
            ).one()

            failed = base.filter(LlmUsage.ok.is_(False)).count()
            unpriced = base.filter(LlmUsage.priced.is_(False)).count()

            by_feature = (
                base.with_entities(
                    LlmUsage.feature,
                    func.count(LlmUsage.id),
                    func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.cost_usd), 0.0),
                    func.coalesce(func.avg(LlmUsage.latency_ms), 0),
                )
                .group_by(LlmUsage.feature)
                .all()
            )

            by_model = (
                base.with_entities(
                    LlmUsage.provider,
                    LlmUsage.model,
                    func.count(LlmUsage.id),
                    func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.cost_usd), 0.0),
                    # `sum(<boolean>)` is not portable - SQLite quietly
                    # miscounts it and Postgres, which is the deployment
                    # target, rejects summing a boolean outright. That would
                    # have been caught by the blanket `except` below and
                    # served an empty board rather than an error.
                    func.coalesce(
                        func.sum(case((LlmUsage.priced.is_(False), 1), else_=0)), 0
                    ),
                )
                .group_by(LlmUsage.provider, LlmUsage.model)
                .all()
            )

            # `date()` rather than a driver-specific truncation, so the same
            # query serves Postgres in production and SQLite in the tests.
            day = func.date(LlmUsage.created_at)
            by_day = (
                base.with_entities(
                    day,
                    func.count(LlmUsage.id),
                    func.coalesce(func.sum(LlmUsage.input_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.output_tokens), 0),
                    func.coalesce(func.sum(LlmUsage.cost_usd), 0.0),
                )
                .group_by(day)
                .order_by(day)
                .all()
            )

            failures = (
                base.filter(LlmUsage.ok.is_(False))
                .order_by(LlmUsage.created_at.desc())
                .limit(20)
                .all()
            )

            labels = {k: f.label for k, f in FEATURES.items()}
            return {
                "days": days,
                "since": since.isoformat(),
                "totals": {
                    "calls": int(totals[0] or 0),
                    "failed": int(failed),
                    "input_tokens": int(totals[1] or 0),
                    "output_tokens": int(totals[2] or 0),
                    "cached_input_tokens": int(totals[3] or 0),
                    "reasoning_tokens": int(totals[4] or 0),
                    "cost_usd": float(totals[5] or 0.0),
                    "unpriced_calls": int(unpriced),
                },
                "by_feature": [
                    {
                        "feature": row[0],
                        "label": labels.get(row[0], row[0]),
                        "calls": int(row[1] or 0),
                        "input_tokens": int(row[2] or 0),
                        "output_tokens": int(row[3] or 0),
                        "cost_usd": float(row[4] or 0.0),
                        "avg_latency_ms": int(row[5] or 0),
                    }
                    for row in sorted(by_feature, key=lambda r: -float(r[4] or 0))
                ],
                "by_model": [
                    {
                        "provider": row[0],
                        "model": row[1],
                        "calls": int(row[2] or 0),
                        "input_tokens": int(row[3] or 0),
                        "output_tokens": int(row[4] or 0),
                        "cost_usd": float(row[5] or 0.0),
                        "unpriced_calls": int(row[6] or 0),
                    }
                    for row in sorted(by_model, key=lambda r: -float(r[5] or 0))
                ],
                "by_day": [
                    {
                        "day": str(row[0]),
                        "calls": int(row[1] or 0),
                        "input_tokens": int(row[2] or 0),
                        "output_tokens": int(row[3] or 0),
                        "cost_usd": float(row[4] or 0.0),
                    }
                    for row in by_day
                ],
                "recent_failures": [
                    {
                        "at": row.created_at.isoformat(),
                        "feature": row.feature,
                        "provider": row.provider,
                        "model": row.model,
                        "error": row.error or "",
                    }
                    for row in failures
                ],
                "available": True,
            }
    except Exception as exc:  # noqa: BLE001 - an absent table is "nothing yet"
        log.warning("could not read model usage: %s", exc)
        return empty


def purge(older_than_days: int) -> int:
    """Delete rows older than `older_than_days`. Returns how many went.

    The log grows without bound otherwise. Nothing calls this automatically -
    retention is a decision, not a default.
    """
    from app.db import session_scope
    from app.models.llm import LlmUsage

    cutoff = datetime.now(timezone.utc) - timedelta(days=max(older_than_days, 0))
    with session_scope() as session:
        return (
            session.query(LlmUsage)
            .filter(LlmUsage.created_at < cutoff)
            .delete(synchronize_session=False)
        )
