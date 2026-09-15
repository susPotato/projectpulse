"""Enrich a candidate model with static metadata (price / context / caps).

Order of precedence when filling in a model's facts:

1. **Existing price table** — the app already lets users maintain a per-model
   USD price sheet (``core/model_pricing.py``, shown on the Monitoring
   Overview). If the model is in there, its real prices win.
2. **Built-in ``STATIC_METADATA``** — a small hard-coded table for well-known
   models (context window + capabilities + rough tier), since those rarely
   change and the price sheet may not carry them.
3. **Provider ``/models`` discovery** — used only to confirm the model is
   actually *available* on the provider right now.
4. **One-shot LLM self-report** — for a genuinely unknown model, an injected
   ``llm_declarer`` may be called ONCE to have the model describe its own
   capabilities; the result is cached by the caller.

Crucially, when a price is genuinely unknown we leave it ``None`` and set
``metadata_incomplete=True`` rather than inventing a number (per the task's
"KHÔNG hardcode giá đoán bừa" rule).
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, Optional

from .models import ModelMetadata

# Optional hook: given (provider, model_id) return a dict of self-reported
# facts (capabilities/max_context). Injected so tests never hit a real API.
LLMDeclarer = Callable[[str, str], Dict[str, Any]]


# --------------------------------------------------------------------------- #
# Built-in static table for well-known models.
#
# Keyed by a model-id PREFIX (longest match wins), so "claude-opus-4-8" is
# matched by the "claude-opus-4" entry. Prices here are deliberately absent for
# most rows — the user's own price sheet is the source of truth for cost, and a
# wrong hard-coded price is worse than a known-unknown. Context windows and
# capabilities, which are stable, are provided.
# --------------------------------------------------------------------------- #
STATIC_METADATA: Dict[str, Dict[str, Any]] = {
    # Anthropic Claude
    "claude-opus-4": {
        "tier": "powerful", "max_context": 200000,
        "capabilities": {"tools", "vision", "reasoning", "long_context"},
    },
    "claude-sonnet-4": {
        "tier": "balanced", "max_context": 200000,
        "capabilities": {"tools", "vision", "reasoning", "long_context"},
    },
    "claude-sonnet-5": {
        "tier": "balanced", "max_context": 200000,
        "capabilities": {"tools", "vision", "reasoning", "long_context"},
    },
    "claude-haiku-4": {
        "tier": "fast", "max_context": 200000,
        "capabilities": {"tools", "vision", "long_context"},
    },
    "claude-3-5-haiku": {
        "tier": "fast", "max_context": 200000,
        "capabilities": {"tools", "vision"},
    },
    # OpenAI / GPT
    "gpt-4o-mini": {
        "tier": "fast", "max_context": 128000,
        "capabilities": {"tools", "vision"},
    },
    "gpt-4o": {
        "tier": "balanced", "max_context": 128000,
        "capabilities": {"tools", "vision", "reasoning"},
    },
    "gpt-4-turbo": {
        "tier": "powerful", "max_context": 128000,
        "capabilities": {"tools", "vision", "reasoning"},
    },
    "o1": {
        "tier": "powerful", "max_context": 200000,
        "capabilities": {"reasoning", "long_context"},
    },
    "o3": {
        "tier": "powerful", "max_context": 200000,
        "capabilities": {"reasoning", "long_context", "tools"},
    },
    # Local / open models (Ollama)
    "llama3.1": {
        "tier": "fast", "max_context": 128000,
        "capabilities": {"tools"},
    },
    "qwen": {
        "tier": "fast", "max_context": 32000,
        "capabilities": {"tools", "reasoning"},
    },
    "deepseek": {
        "tier": "balanced", "max_context": 64000,
        "capabilities": {"reasoning", "tools"},
    },
    "gemma": {
        "tier": "fast", "max_context": 8192,
        "capabilities": set(),
    },
}


def _static_for(model_id: str) -> Dict[str, Any]:
    """Longest-prefix lookup in ``STATIC_METADATA`` (empty dict if no match)."""
    m = (model_id or "").lower()
    best_key = ""
    for key in STATIC_METADATA:
        if m.startswith(key) and len(key) > len(best_key):
            best_key = key
    return dict(STATIC_METADATA[best_key]) if best_key else {}


def _cost_from_price_table(model_id: str, config) -> tuple[Optional[float], Optional[float]]:
    """USD cost **per 1k tokens** from the app's price sheet, or ``(None, None)``.

    ``model_pricing.usd_rates_for`` returns USD per **1M** tokens, so we divide
    by 1000. A zero/absent entry is treated as unknown, not as free.
    """
    try:
        from .. import model_pricing
    except Exception:  # noqa: BLE001 — module optional in some contexts (tests)
        return None, None
    if config is None:
        return None, None
    rates = model_pricing.usd_rates_for(model_id, config)
    if not rates:
        return None, None
    ci = rates.get("in")
    co = rates.get("out")
    ci = (ci / 1000.0) if ci else None
    co = (co / 1000.0) if co else None
    return ci, co


def enrich(
    provider: str,
    model_id: str,
    *,
    config: Any = None,
    tier: Optional[str] = None,
    available_models: Optional[Iterable[str]] = None,
    llm_declarer: Optional[LLMDeclarer] = None,
) -> ModelMetadata:
    """Build a :class:`ModelMetadata` for one candidate.

    Parameters
    ----------
    provider, model_id:
        Identify the candidate.
    config:
        The app config, used to read the user's price sheet (optional).
    tier:
        User-declared tier from the provider config (e.g. "fast"); overrides
        any static-table tier when given.
    available_models:
        Model ids the provider currently lists. When provided, availability is
        set from membership; when ``None`` the model is assumed available (the
        prober will discover a truly-dead model via a failed probe anyway).
    llm_declarer:
        Optional one-shot capability self-report hook for unknown models.
    """
    static = _static_for(model_id)

    ci, co = _cost_from_price_table(model_id, config)

    max_context = static.get("max_context")
    capabilities = set(static.get("capabilities") or set())

    # Unknown model + a declarer available → ask it once to describe itself.
    if not static and llm_declarer is not None:
        try:
            declared = llm_declarer(provider, model_id) or {}
        except Exception:  # noqa: BLE001 — a failed self-report must not crash enrichment
            declared = {}
        if declared.get("max_context"):
            max_context = int(declared["max_context"])
        for cap in declared.get("capabilities") or []:
            capabilities.add(str(cap))

    available = True
    if available_models is not None:
        avail = {str(m) for m in available_models}
        available = model_id in avail

    # Price genuinely unknown → flag incomplete rather than guessing.
    metadata_incomplete = ci is None or co is None

    return ModelMetadata(
        provider=provider,
        model_id=model_id,
        tier=tier or static.get("tier"),
        cost_per_1k_input=ci,
        cost_per_1k_output=co,
        max_context=max_context,
        capabilities=capabilities,
        available=available,
        metadata_incomplete=metadata_incomplete,
    )


__all__ = ["STATIC_METADATA", "enrich", "LLMDeclarer"]
