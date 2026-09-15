"""A thin, unified calling surface over the app's existing Provider layer.

The task asks for a ``clients.py`` abstraction that talks to Anthropic / OpenAI
behind one interface. This app **already has** that — ``providers/`` with
``build_provider`` and a canonical ``chat()`` that streams text and returns the
final assistant message. Rather than duplicate it (and re-solve TLS trust,
429-retry, gateway config…), this module adapts it to the shape the prober
wants: a single blocking ``complete()`` that returns text + token estimate.

Tests inject a fake :class:`ProbeClient` so assessment never hits a real API.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol


@dataclass
class CompletionResult:
    """Outcome of one non-streaming completion used for probing."""

    text: str = ""
    tokens_out: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class ProbeClient(Protocol):
    """Minimal interface the prober/judge depend on (so they're mockable)."""

    def complete(
        self,
        provider: str,
        model_id: str,
        messages: List[Dict[str, Any]],
    ) -> CompletionResult:
        ...


def _estimate_tokens(text: str) -> int:
    """Rough output-token count. Uses the app's estimator when importable
    (keeps the number consistent with the usage tracker), else ~4 chars/token."""
    try:
        from ..usage_tracker import estimate_tokens
        return int(estimate_tokens(text or ""))
    except Exception:  # noqa: BLE001
        return max(0, len(text or "") // 4)


class AppProbeClient:
    """Real :class:`ProbeClient` backed by :class:`AppContext`.

    Builds a fresh provider per call via ``ctx.build_provider_for`` — the same
    path interactive chat uses — so the internal gateway, per-host TLS trust and
    rate-limit retry all apply to assessment calls too.
    """

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx

    def complete(
        self,
        provider: str,
        model_id: str,
        messages: List[Dict[str, Any]],
    ) -> CompletionResult:
        try:
            prov = self.ctx.build_provider_for(provider, model_id or None)
            # Non-streaming: no on_text/on_reasoning callbacks. cancel=None.
            result = prov.chat(messages, tools=None, on_text=None, cancel=None)
        except Exception as exc:  # noqa: BLE001 — surfaced as a failed probe
            return CompletionResult(error=str(exc))
        content = ""
        if isinstance(result, dict):
            content = result.get("content") or ""
            # Strip any inline <think> block a reasoning model may have inlined.
            try:
                from ...providers.base import Provider
                content = Provider.strip_think(content)
            except Exception:  # noqa: BLE001
                pass
        return CompletionResult(text=content, tokens_out=_estimate_tokens(content))


__all__ = ["CompletionResult", "ProbeClient", "AppProbeClient"]
