"""LLM provider abstraction.

All providers translate a *canonical* message list into their own API shape and
expose a single ``chat()`` method that streams assistant text via a callback and
returns the final assistant message (including any tool calls).
"""
from .base import Provider, ProviderError, ToolSpec
from .factory import build_provider

__all__ = ["Provider", "ProviderError", "ToolSpec", "build_provider"]
