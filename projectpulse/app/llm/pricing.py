"""What a call costs, in US dollars per million tokens.

**Seeded only with rates that could actually be sourced.** The Anthropic rows
below are the published first-party API rates. Every other model this app can
be pointed at - the OpenAI and Gemini defaults, and the dozen models behind
FPT's gateway - ships *unpriced*, because a made-up rate is worse than no rate:
it produces a confident dollar figure that reconciles against nothing.

An unpriced call is still logged, still counted, and still shown. It lands in
the board's "unpriced" count instead of silently costing $0.00 and reading as
free, which is the failure mode this arrangement exists to avoid. Fill a rate
in on the settings page and calls priced from then on pick it up.

**Rates are applied when the call is made, not when the board is read.**
`LlmUsage.cost_usd` is stamped at write time - see that model's docstring for
why editing this table must not rewrite history.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

SETTING_KEY = "llm_pricing"


@dataclass(frozen=True)
class Rate:
    """US dollars per million tokens."""

    input: float
    output: float
    #: Cache reads bill at a fraction of the input rate; cache writes at a
    #: premium over it. Defaulted from `input` when a vendor publishes no
    #: separate number, which is the same as not modelling caching at all.
    cache_read: float = 0.0
    cache_write: float = 0.0

    def resolved(self) -> "Rate":
        return Rate(
            input=self.input,
            output=self.output,
            cache_read=self.cache_read or self.input,
            cache_write=self.cache_write or self.input,
        )


#: Published Anthropic first-party rates. Cache reads run at ~0.1x input and
#: cache writes at ~1.25x input, which is where the derived columns come from.
#:
#: Partner platforms (Bedrock, Vertex) bill separately and are not modelled -
#: this app talks to the first-party API.
BUILTIN_RATES: dict[str, Rate] = {
    "claude-opus-5": Rate(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-opus-4-8": Rate(input=5.00, output=25.00, cache_read=0.50, cache_write=6.25),
    "claude-sonnet-5": Rate(input=2.00, output=10.00, cache_read=0.20, cache_write=2.50),
    "claude-haiku-4-5": Rate(input=1.00, output=5.00, cache_read=0.10, cache_write=1.25),
    "claude-fable-5-1": Rate(input=10.00, output=50.00, cache_read=1.00, cache_write=12.50),
}

#: Deliberately empty of guesses. These are the models this app defaults to on
#: the other three vendors; each needs a rate typed in by somebody who has
#: looked at the actual bill. Listed here so the settings page can offer the
#: row rather than making someone discover the model string themselves.
UNPRICED_BY_DEFAULT: tuple[str, ...] = (
    "gpt-5.6-terra",
    "gemini-3.8-flash",
    "gemma-4-31B-it",
)


def _overrides() -> dict[str, Rate]:
    """Rates typed in on the settings page, which win over `BUILTIN_RATES`."""
    from app.db import session_scope
    from app.models.uploads import AppSetting

    try:
        with session_scope() as session:
            row = session.get(AppSetting, SETTING_KEY)
            if row is None:
                return {}
            raw = json.loads(row.value or "{}")
    except Exception as exc:  # noqa: BLE001 - no overrides is the ordinary state
        log.warning("ignoring unreadable pricing overrides: %s", exc)
        return {}

    out: dict[str, Rate] = {}
    for model, fields in (raw or {}).items():
        if not isinstance(fields, dict):
            continue
        try:
            out[model] = Rate(
                input=float(fields.get("input", 0) or 0),
                output=float(fields.get("output", 0) or 0),
                cache_read=float(fields.get("cache_read", 0) or 0),
                cache_write=float(fields.get("cache_write", 0) or 0),
            )
        except (TypeError, ValueError):
            log.warning("ignoring malformed rate for %s", model)
    return out


def rate_for(model: str) -> Rate | None:
    """The rate for a model, or `None` when nobody has supplied one."""
    if not model:
        return None
    overrides = _overrides()
    found = overrides.get(model) or BUILTIN_RATES.get(model)
    return found.resolved() if found else None


def set_rate(model: str, **fields) -> None:
    """Save a rate. `input=0` and `output=0` removes it again."""
    from app.db import session_scope
    from app.models.uploads import AppSetting

    model = model.strip()
    if not model:
        raise ValueError("a rate needs a model")

    with session_scope() as session:
        stored = {}
        row = session.get(AppSetting, SETTING_KEY)
        if row is not None:
            try:
                stored = json.loads(row.value or "{}")
            except Exception:  # noqa: BLE001 - a corrupt row is replaced
                stored = {}

        clean = {k: float(v or 0) for k, v in fields.items() if k in
                 {"input", "output", "cache_read", "cache_write"}}
        if not clean.get("input") and not clean.get("output"):
            stored.pop(model, None)
        else:
            stored[model] = clean

        session.merge(AppSetting(key=SETTING_KEY, value=json.dumps(stored)))


def estimate(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[float, bool]:
    """`(cost_in_usd, priced)` for one call.

    **Reasoning tokens are not a separate term.** Every vendor bills them
    inside `output_tokens`, so adding them again would roughly double the cost
    of any reasoning model. They are recorded on the row for explanation, not
    for arithmetic.

    `input_tokens` is expected to exclude anything served from cache, which is
    how Anthropic and OpenAI both report it - cached tokens are passed
    separately and priced at the cache-read rate.
    """
    rate = rate_for(model)
    if rate is None:
        return 0.0, False

    cost = (
        input_tokens * rate.input
        + output_tokens * rate.output
        + cached_input_tokens * rate.cache_read
        + cache_write_tokens * rate.cache_write
    ) / 1_000_000
    return cost, True


def table() -> list[dict]:
    """Every model with a rate, plus the ones known to need one.

    The settings page renders this directly, so an unpriced model appears as an
    empty row to fill in rather than being absent and unfindable.
    """
    overrides = _overrides()
    models = sorted(set(BUILTIN_RATES) | set(overrides) | set(UNPRICED_BY_DEFAULT))

    out = []
    for model in models:
        rate = overrides.get(model) or BUILTIN_RATES.get(model)
        resolved = rate.resolved() if rate else None
        out.append(
            {
                "model": model,
                "input": resolved.input if resolved else None,
                "output": resolved.output if resolved else None,
                "cache_read": resolved.cache_read if resolved else None,
                "cache_write": resolved.cache_write if resolved else None,
                "priced": resolved is not None,
                "source": (
                    "override"
                    if model in overrides
                    else "builtin"
                    if model in BUILTIN_RATES
                    else "unpriced"
                ),
            }
        )
    return out
