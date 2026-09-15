"""Per-provider live model listing — used by Monitoring -> Agents Admin's
"Load models" button (originally built for the now-removed Preview tab's
"Fix with AI" panel, kept here since Agents Admin still depends on it)."""
from __future__ import annotations

from typing import Dict, List

from ..config import PROVIDER_LABELS


def fetch_live_models(ctx) -> Dict[str, List[str]]:
    """``{"openai_compat": [...], "anthropic": [...]}`` — each provider's own
    ``list_models()`` (same call Settings' "Load" button makes), so a picker
    can offer a specific model within a provider, not only its Settings
    default. Best-effort per provider: one failing gateway doesn't block the
    other's list."""
    result: Dict[str, List[str]] = {}
    for key in PROVIDER_LABELS:
        try:
            provider = ctx.build_provider_for(key)
            models = provider.list_models()
        except Exception:  # noqa: BLE001 — a broken provider config must not break the picker
            models = []
        if models:
            result[key] = models
    return result
