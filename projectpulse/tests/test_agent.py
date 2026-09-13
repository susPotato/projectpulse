"""Guards for the Agent chat module - the one generative surface with no gate.

Each vendor adapter (`_anthropic_chat`, `_gemini_chat`) is tested against a
stubbed SDK client rather than a real endpoint - `tests/test_narration.py`
already proves the wire format against a local HTTP server for the narration
path, and these reuse the same SDKs the same way. What is worth protecting
here: turns map to the right roles, every failure becomes `ChatUnavailable`
rather than an uncaught exception reaching a request handler, and - the
reason this module exists as a dispatcher at all - `chat()` actually routes
to the vendor `provider` names rather than defaulting to whichever one this
feature happened to be built against first (`CLAUDE.md` 2026-09-11: this
exact drift, silently defaulting to Gemini after the project switched to
Anthropic, is what broke the Agent tab in production)."""

from __future__ import annotations

import sys
import types

import pytest

from app.agent.chat import (
    SYSTEM_PROMPT,
    ChatTurn,
    ChatUnavailable,
    _anthropic_chat,
    _gemini_chat,
    chat,
)


# --------------------------------------------------------------------------
# The dispatcher: does `chat()` actually route on `provider`?
# --------------------------------------------------------------------------


def test_chat_dispatches_to_anthropic(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.agent.chat._anthropic_chat",
        lambda turns, **kw: calls.append(("anthropic", kw)) or "hi from claude",
    )
    monkeypatch.setattr(
        "app.agent.chat._gemini_chat",
        lambda turns, **kw: calls.append(("gemini", kw)) or "hi from gemini",
    )

    reply = chat([ChatTurn(role="user", content="hi")], provider="anthropic")

    assert reply == "hi from claude"
    assert len(calls) == 1
    vendor, kw = calls[0]
    assert vendor == "anthropic"
    assert kw["model"] == "claude-opus-5"
    assert kw["api_key"] is None and kw["base_url"] is None
    # No project was named, so the system prompt is the bare one - no brief.
    assert kw["system"] == SYSTEM_PROMPT


def test_chat_dispatches_to_gemini(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.agent.chat._gemini_chat",
        lambda turns, **kw: calls.append(("gemini", kw)) or "hi from gemini",
    )

    reply = chat([ChatTurn(role="user", content="hi")], provider="gemini")

    assert reply == "hi from gemini"
    assert len(calls) == 1
    vendor, kw = calls[0]
    assert vendor == "gemini"
    assert kw["model"] == "gemini-3.8-flash"
    assert kw["api_key"] is None
    assert kw["system"] == SYSTEM_PROMPT


def test_a_project_brief_reaches_the_system_prompt_and_not_the_transcript(monkeypatch):
    """Where the brief goes is the whole of this test.

    A user turn is something the person said. Folding project data into one
    would make "why did it answer that?" unanswerable, and the client resends
    the transcript every turn - so the next request would carry it back as
    though they had typed it.
    """
    captured: dict = {}
    monkeypatch.setattr(
        "app.agent.chat._anthropic_chat",
        lambda turns, **kw: captured.update(kw, turns=turns) or "ok",
    )

    chat(
        [ChatTurn(role="user", content="what is late?")],
        provider="anthropic",
        context="PROJECT: Demo\n6 past due.",
    )

    assert "6 past due." in captured["system"]
    assert "PROJECT BRIEF" in captured["system"]
    # The person's own words, unchanged.
    assert [t.content for t in captured["turns"]] == ["what is late?"]


def test_chat_names_the_provider_it_cannot_handle_yet():
    with pytest.raises(ChatUnavailable, match="openai"):
        chat([ChatTurn(role="user", content="hi")], provider="openai")


def test_chat_uses_an_explicit_model_over_the_providers_default(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.agent.chat._anthropic_chat",
        lambda turns, **kw: captured.update(kw) or "ok",
    )

    chat([ChatTurn(role="user", content="hi")], provider="anthropic", model="claude-haiku-4-5")

    assert captured["model"] == "claude-haiku-4-5"


# --------------------------------------------------------------------------
# Anthropic adapter
# --------------------------------------------------------------------------


class _FakeAnthropicResponse:
    def __init__(self, text="Hello there.", stop_reason="end_turn", refusal_category=None):
        self.content = [types.SimpleNamespace(type="text", text=text)] if text else []
        self.stop_reason = stop_reason
        self.stop_details = types.SimpleNamespace(category=refusal_category)


class _FakeAnthropicMessages:
    def __init__(self, response=None, raise_exc=None, capture=None):
        self._response = response or _FakeAnthropicResponse()
        self._raise = raise_exc
        self._capture = capture if capture is not None else {}

    def create(self, *, model, max_tokens, system, messages):
        self._capture["model"] = model
        self._capture["system"] = system
        self._capture["messages"] = messages
        if self._raise:
            raise self._raise
        return self._response


class _FakeAnthropicClient:
    def __init__(self, messages, **_kw):
        self.messages = messages


def _install_fake_anthropic(monkeypatch, messages: _FakeAnthropicMessages):
    fake_module = types.SimpleNamespace(Anthropic=lambda **kw: _FakeAnthropicClient(messages, **kw))
    monkeypatch.setitem(sys.modules, "anthropic", fake_module)


def test_anthropic_a_reply_comes_back_as_plain_text(monkeypatch):
    _install_fake_anthropic(
        monkeypatch, _FakeAnthropicMessages(_FakeAnthropicResponse("Sure, here's an idea."))
    )

    reply = _anthropic_chat(
        [ChatTurn(role="user", content="Any ideas?")], model="claude-opus-5", api_key=None, base_url=None
    )

    assert reply == "Sure, here's an idea."


def test_anthropic_turns_map_user_and_assistant_to_the_vendor_s_roles(monkeypatch):
    capture: dict = {}
    _install_fake_anthropic(monkeypatch, _FakeAnthropicMessages(capture=capture))

    _anthropic_chat(
        [
            ChatTurn(role="user", content="hi"),
            ChatTurn(role="assistant", content="hello"),
            ChatTurn(role="user", content="how are you"),
        ],
        model="claude-opus-5", api_key=None, base_url=None,
    )

    roles = [m["role"] for m in capture["messages"]]
    assert roles == ["user", "assistant", "user"]


def test_anthropic_a_refusal_raises_chat_unavailable(monkeypatch):
    _install_fake_anthropic(
        monkeypatch,
        _FakeAnthropicMessages(_FakeAnthropicResponse(stop_reason="refusal", refusal_category="SAFETY")),
    )

    with pytest.raises(ChatUnavailable, match="declined"):
        _anthropic_chat([ChatTurn(role="user", content="hi")], model="claude-opus-5", api_key=None, base_url=None)


def test_anthropic_an_empty_reply_raises_chat_unavailable(monkeypatch):
    _install_fake_anthropic(monkeypatch, _FakeAnthropicMessages(_FakeAnthropicResponse("")))

    with pytest.raises(ChatUnavailable, match="empty"):
        _anthropic_chat([ChatTurn(role="user", content="hi")], model="claude-opus-5", api_key=None, base_url=None)


def test_anthropic_a_vendor_exception_becomes_chat_unavailable(monkeypatch):
    _install_fake_anthropic(
        monkeypatch, _FakeAnthropicMessages(raise_exc=RuntimeError("connection reset"))
    )

    with pytest.raises(ChatUnavailable, match="connection reset"):
        _anthropic_chat([ChatTurn(role="user", content="hi")], model="claude-opus-5", api_key=None, base_url=None)


def test_anthropic_missing_package_raises_chat_unavailable_naming_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)

    with pytest.raises(ChatUnavailable, match=r'"\.\[llm\]"'):
        _anthropic_chat([ChatTurn(role="user", content="hi")], model="claude-opus-5", api_key=None, base_url=None)


# --------------------------------------------------------------------------
# Gemini adapter
# --------------------------------------------------------------------------


class _FakeGeminiResponse:
    def __init__(self, text="Hello there.", block_reason=None):
        self.text = text
        self.prompt_feedback = types.SimpleNamespace(block_reason=block_reason)


class _FakeGeminiModels:
    def __init__(self, response=None, raise_exc=None, capture=None):
        self._response = response or _FakeGeminiResponse()
        self._raise = raise_exc
        self._capture = capture if capture is not None else {}

    def generate_content(self, *, model, contents, config):
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        if self._raise:
            raise self._raise
        return self._response


class _FakeGeminiClient:
    def __init__(self, models):
        self.models = models


def _install_fake_genai(monkeypatch, models: _FakeGeminiModels):
    """Stand in for `google.genai` without installing/reaching the real SDK."""
    fake_types = types.SimpleNamespace(
        Content=lambda role, parts: {"role": role, "parts": parts},
        Part=lambda text: {"text": text},
        GenerateContentConfig=lambda **kw: kw,
    )
    fake_genai = types.SimpleNamespace(
        Client=lambda **kw: _FakeGeminiClient(models),
        types=fake_types,
    )
    google_pkg = types.ModuleType("google")
    google_pkg.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


def test_gemini_a_reply_comes_back_as_plain_text(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeGeminiModels(_FakeGeminiResponse("Sure, here's an idea.")))

    reply = _gemini_chat([ChatTurn(role="user", content="Any ideas?")], model="gemini-3.8-flash", api_key=None)

    assert reply == "Sure, here's an idea."


def test_gemini_turns_map_user_and_assistant_to_the_vendor_s_roles(monkeypatch):
    capture: dict = {}
    _install_fake_genai(monkeypatch, _FakeGeminiModels(capture=capture))

    _gemini_chat(
        [
            ChatTurn(role="user", content="hi"),
            ChatTurn(role="assistant", content="hello"),
            ChatTurn(role="user", content="how are you"),
        ],
        model="gemini-3.8-flash", api_key=None,
    )

    roles = [c["role"] for c in capture["contents"]]
    assert roles == ["user", "model", "user"]


def test_gemini_a_blocked_prompt_raises_chat_unavailable(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeGeminiModels(_FakeGeminiResponse(block_reason="SAFETY")))

    with pytest.raises(ChatUnavailable, match="blocked"):
        _gemini_chat([ChatTurn(role="user", content="hi")], model="gemini-3.8-flash", api_key=None)


def test_gemini_an_empty_reply_raises_chat_unavailable(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeGeminiModels(_FakeGeminiResponse("")))

    with pytest.raises(ChatUnavailable, match="empty"):
        _gemini_chat([ChatTurn(role="user", content="hi")], model="gemini-3.8-flash", api_key=None)


def test_gemini_a_vendor_exception_becomes_chat_unavailable(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeGeminiModels(raise_exc=RuntimeError("connection reset")))

    with pytest.raises(ChatUnavailable, match="connection reset"):
        _gemini_chat([ChatTurn(role="user", content="hi")], model="gemini-3.8-flash", api_key=None)


def test_gemini_missing_package_raises_chat_unavailable_naming_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)
    monkeypatch.delitem(sys.modules, "google", raising=False)

    with pytest.raises(ChatUnavailable, match="llm-gemini"):
        _gemini_chat([ChatTurn(role="user", content="hi")], model="gemini-3.8-flash", api_key=None)
