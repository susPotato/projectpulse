"""Guards for the runtime narration settings and the page that edits them.

Two things are being protected here, and the second matters more than the first.

**The key must not leak.** It is never returned to a browser, never logged, and
a save that did not retype it must not wipe it - a page that cannot read the key
back would otherwise clear it on every unrelated edit.

**Writing settings is loopback-only.** The endpoint accepts an API key, so it is
restricted to the machine the app runs on. That is not a permission system; it
is the smallest boundary that is honest, and it means a deployment behind a
proxy is read-only with nothing to configure.
"""

from __future__ import annotations

import dataclasses
import json

import pytest
from fastapi.testclient import TestClient

from app.narration import store


@pytest.fixture
def state():
    """Start every test with no stored settings.

    These live in the database now rather than a file under `PULSE_STATE_DIR`
    - a container's disk does not survive a deploy, so a provider chosen in
    the browser used to revert on every release. The fixture therefore has to
    clear a *row* rather than hand out a temp directory: the suite shares one
    SQLite file (see `tests/conftest.py`), so a settings row left behind by
    one test is the next test's starting state.
    """
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models.uploads import AppSetting

    def clear():
        with session_scope() as session:
            session.execute(delete(AppSetting).where(AppSetting.key == store.SETTING_KEY))

    clear()
    yield
    clear()


def _local(app):
    """A client the app will treat as running on this machine."""
    return TestClient(app, client=("127.0.0.1", 5000))


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


def test_settings_survive_a_round_trip(state):
    store.save(
        store.NarrationSettings(
            enabled=True, provider="openai", model="gpt-4.1", api_key="sk-secret"
        )
    )
    loaded = store.load()

    assert loaded.enabled is True
    assert loaded.provider == "openai"
    assert loaded.api_key == "sk-secret"


def test_no_stored_settings_is_the_ordinary_state_not_an_error(state):
    assert store.load().enabled is False


def test_an_unreadable_row_falls_back_rather_than_raising(state):
    """Corrupt JSON means "nobody configured narration", not a 500 on every
    narrated request - `load()` runs on that path."""
    from app.db import session_scope
    from app.models.uploads import AppSetting

    with session_scope() as session:
        session.merge(AppSetting(key=store.SETTING_KEY, value="{ not json"))

    assert store.load().provider == "anthropic"


def test_an_unreachable_database_falls_back_rather_than_raising(state, monkeypatch):
    """The same courtesy when the database itself is the problem. Narration
    settings are not worth failing a page render over."""
    import app.db

    def boom():
        raise RuntimeError("no database")

    monkeypatch.setattr(app.db, "session_scope", boom)

    assert store.load().provider == "anthropic"


def test_updating_without_a_key_keeps_the_stored_one(state):
    """The page never receives the key, so it cannot send it back.

    Without this, changing the model id would silently clear the credential.
    """
    store.save(store.NarrationSettings(api_key="sk-keep-me"))
    store.update(model="gpt-4.1")

    assert store.load().api_key == "sk-keep-me"
    assert store.load().model == "gpt-4.1"


def test_an_empty_key_is_an_explicit_clear(state):
    store.save(store.NarrationSettings(api_key="sk-goodbye"))
    store.update(api_key="")

    assert store.load().api_key == ""


def test_the_public_view_never_carries_the_key(state):
    store.save(store.NarrationSettings(api_key="sk-ant-api03-abcdefghijklmnop"))
    view = store.public_view()

    assert "api_key" not in view
    assert "abcdefghijklmnop" not in json.dumps(view)
    assert view["api_key_set"] is True
    assert view["api_key_hint"].startswith("sk-ant-")


def test_a_short_key_is_hidden_entirely_rather_than_mostly_shown(state):
    """A short secret is all suffix, so the usual last-four hint discloses it."""
    store.save(store.NarrationSettings(api_key="short"))

    assert store.public_view()["api_key_hint"] == "*****"


# --------------------------------------------------------------------------
# The endpoints
# --------------------------------------------------------------------------


def test_settings_cannot_be_changed_from_off_this_machine(state):
    from app.api.main import app

    response = TestClient(app).put("/api/settings", json={"enabled": True})

    assert response.status_code == 403
    assert store.load().enabled is False


def test_the_test_endpoint_is_local_only_too(state):
    from app.api.main import app

    assert TestClient(app).post("/api/settings/test").status_code == 403


def test_a_local_client_can_change_them(state):
    from app.api.main import app

    body = _local(app).put(
        "/api/settings",
        json={"enabled": True, "provider": "openai", "api_key": "sk-test-abcdefghijkl"},
    ).json()

    assert body["enabled"] is True
    assert body["provider"] == "openai"
    assert body["api_key_set"] is True
    assert "api_key" not in body


def test_an_unknown_provider_is_refused_before_it_is_stored(state):
    from app.api.main import app

    response = _local(app).put("/api/settings", json={"provider": "llama"})

    assert response.status_code == 400
    assert store.load().provider == "anthropic"


def test_the_effective_model_names_the_default_when_none_is_set(state):
    from app.api.main import app

    body = _local(app).get("/api/settings").json()

    assert body["model"] == ""
    assert body["effective_model"] == "claude-opus-5"


def test_the_narrator_is_none_until_narration_is_switched_on(state):
    from app.api.main import _narrator

    assert _narrator() is None

    store.update(enabled=True, provider="anthropic")
    assert _narrator() is not None


def test_a_corrupt_provider_costs_the_model_and_not_the_page(state):
    """An unknown provider in the stored file must not 500 the insight route."""
    from app.api.main import _narrator

    store.save(store.NarrationSettings(enabled=True, provider="llama"))

    assert _narrator() is None


def test_a_base_url_reaches_the_stored_settings(state):
    """The self-hosted path: an open model behind an OpenAI-compatible server.

    This is what turns "run a 26B model on our own hardware" into a form field
    rather than a fork, so it has to survive the round trip like anything else.
    """
    from app.api.main import app

    body = _local(app).put(
        "/api/settings",
        json={
            "enabled": True,
            "provider": "openai",
            "model": "qwen3-32b-local",
            "base_url": "http://127.0.0.1:9911/v1",
            "api_key": "local-no-key-needed",
        },
    ).json()

    assert body["base_url"] == "http://127.0.0.1:9911/v1"
    assert store.load().base_url == "http://127.0.0.1:9911/v1"


def test_the_base_url_reaches_the_drafter_config(state, monkeypatch):
    """A stored endpoint is useless if `_narrator` drops it on the way through."""
    import app.narration.providers as providers
    from app.api.main import _narrator

    store.save(
        store.NarrationSettings(
            enabled=True, provider="openai", base_url="http://example.invalid/v1"
        )
    )

    seen = {}
    monkeypatch.setattr(
        providers, "_openai_drafter", lambda cfg: seen.setdefault("cfg", cfg)
    )

    _narrator()
    assert seen["cfg"].base_url == "http://example.invalid/v1"
