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
        store.NarrationSettings(enabled=True, provider="openai", model="gpt-4.1")
    )
    loaded = store.load()

    assert loaded.enabled is True
    assert loaded.provider == "openai"
    assert loaded.model == "gpt-4.1"


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
    """Changing the model id must not disturb anything else."""
    store.save(store.NarrationSettings(provider="openai"))
    store.update(model="gpt-4.1")

    assert store.load().provider == "openai"
    assert store.load().model == "gpt-4.1"


def test_a_key_handed_to_this_store_is_never_written(state):
    """The plaintext path is closed, not merely unused by the UI.

    This row is plaintext JSON. Keys live encrypted in `llm_credentials`
    (`app/llm/keys.py`), and a caller that still passes one here - an old
    client, a replayed form post - must not succeed in putting it back.
    """
    from app.db import session_scope
    from app.models.uploads import AppSetting

    store.save(store.NarrationSettings(provider="openai", api_key="sk-must-not-land"))
    store.update(api_key="sk-must-not-land-either")

    assert store.load().api_key == ""
    with session_scope() as session:
        row = session.get(AppSetting, store.SETTING_KEY)
        assert "must-not-land" not in (row.value or "")


def test_the_public_view_never_carries_a_key(state):
    store.save(store.NarrationSettings(provider="anthropic"))
    view = store.public_view()

    assert "api_key" not in view
    # Nothing is stored here to report any more; `/api/llm/keys` is where a
    # key's presence is shown, from the encrypted store.
    assert view["api_key_set"] is False


def test_a_legacy_plaintext_key_is_migrated_and_blanked(state, monkeypatch):
    """The row somebody wrote before keys moved must not stay in the clear."""
    import json as _json

    from app.db import session_scope
    from app.llm import keys
    from app.models.uploads import AppSetting

    monkeypatch.setenv(keys.SECRET_ENV, keys.generate_secret())
    # Written past `save()`, because `save()` is what now strips it - this is
    # the shape a row written by the old code actually has on disk.
    with session_scope() as session:
        session.merge(
            AppSetting(
                key=store.SETTING_KEY,
                value=_json.dumps(
                    {"enabled": True, "provider": "anthropic", "api_key": "sk-ant-legacy-value"}
                ),
            )
        )

    assert keys.adopt_legacy() == "anthropic"

    assert keys.get("anthropic") == "sk-ant-legacy-value"
    assert store.load().api_key == ""
    with session_scope() as session:
        row = session.get(AppSetting, store.SETTING_KEY)
        assert "legacy-value" not in (row.value or "")
    keys.delete("anthropic")


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
    # Accepted and discarded rather than refused, so an old client does not
    # 500 - but it does not get a key into this row either.
    assert body["api_key_set"] is False
    assert "api_key" not in body
    assert store.load().api_key == ""


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
