"""Guards for the Agent chat module - the one generative surface with no gate.

`gemini_chat` is tested against a stubbed `google.genai` client rather than a
real endpoint - `tests/test_narration.py` already proves the wire format
against a local HTTP server for the narration path, and this reuses the same
SDK the same way. What is worth protecting here is simpler: turns map to the
right roles, and every failure becomes `ChatUnavailable` rather than an
uncaught exception reaching a request handler.
"""

from __future__ import annotations

import sys
import types

import pytest

from app.agent.chat import ChatTurn, ChatUnavailable, gemini_chat


class _FakeResponse:
    def __init__(self, text="Hello there.", block_reason=None):
        self.text = text
        self.prompt_feedback = types.SimpleNamespace(block_reason=block_reason)


class _FakeModels:
    def __init__(self, response=None, raise_exc=None, capture=None):
        self._response = response or _FakeResponse()
        self._raise = raise_exc
        self._capture = capture if capture is not None else {}

    def generate_content(self, *, model, contents, config):
        self._capture["model"] = model
        self._capture["contents"] = contents
        self._capture["config"] = config
        if self._raise:
            raise self._raise
        return self._response


class _FakeClient:
    def __init__(self, models):
        self.models = models


def _install_fake_genai(monkeypatch, models: _FakeModels):
    """Stand in for `google.genai` without installing/reaching the real SDK."""
    fake_types = types.SimpleNamespace(
        Content=lambda role, parts: {"role": role, "parts": parts},
        Part=lambda text: {"text": text},
        GenerateContentConfig=lambda **kw: kw,
    )
    fake_genai = types.SimpleNamespace(
        Client=lambda **kw: _FakeClient(models),
        types=fake_types,
    )
    google_pkg = types.ModuleType("google")
    google_pkg.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google_pkg)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)


def test_a_reply_comes_back_as_plain_text(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeModels(_FakeResponse("Sure, here's an idea.")))

    reply = gemini_chat([ChatTurn(role="user", content="Any ideas?")])

    assert reply == "Sure, here's an idea."


def test_turns_map_user_and_assistant_to_the_vendor_s_roles(monkeypatch):
    capture: dict = {}
    _install_fake_genai(monkeypatch, _FakeModels(capture=capture))

    gemini_chat(
        [
            ChatTurn(role="user", content="hi"),
            ChatTurn(role="assistant", content="hello"),
            ChatTurn(role="user", content="how are you"),
        ]
    )

    roles = [c["role"] for c in capture["contents"]]
    assert roles == ["user", "model", "user"]


def test_a_blocked_prompt_raises_chat_unavailable(monkeypatch):
    _install_fake_genai(
        monkeypatch, _FakeModels(_FakeResponse(block_reason="SAFETY"))
    )

    with pytest.raises(ChatUnavailable, match="blocked"):
        gemini_chat([ChatTurn(role="user", content="hi")])


def test_an_empty_reply_raises_chat_unavailable(monkeypatch):
    _install_fake_genai(monkeypatch, _FakeModels(_FakeResponse("")))

    with pytest.raises(ChatUnavailable, match="empty"):
        gemini_chat([ChatTurn(role="user", content="hi")])


def test_a_vendor_exception_becomes_chat_unavailable(monkeypatch):
    _install_fake_genai(
        monkeypatch, _FakeModels(raise_exc=RuntimeError("connection reset"))
    )

    with pytest.raises(ChatUnavailable, match="connection reset"):
        gemini_chat([ChatTurn(role="user", content="hi")])


def test_missing_package_raises_chat_unavailable_naming_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "google.genai", None)
    monkeypatch.delitem(sys.modules, "google", raising=False)

    with pytest.raises(ChatUnavailable, match="llm-gemini"):
        gemini_chat([ChatTurn(role="user", content="hi")])
