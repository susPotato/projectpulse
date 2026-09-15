"""Build a provider instance from the application config."""
from __future__ import annotations

from typing import Any, Dict

from .anthropic import AnthropicProvider
from .base import Provider, ProviderError
from .openai_compat import OpenAICompatProvider

_REGISTRY = {
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
    # All OpenAI-compatible endpoints (Ollama's /v1 server, the Copilot chat API,
    # and OpenAI itself) speak the same Chat Completions protocol.
    "ollama": OpenAICompatProvider,
    "github_copilot": OpenAICompatProvider,
    "codex": OpenAICompatProvider,
}


def build_provider(name: str, conf: Dict[str, Any]) -> Provider:
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ProviderError(f"Unsupported provider: {name}")
    return cls(conf)
