"""Guards for per-feature model choice, the encrypted key store and metering.

Three things are being protected, in rising order of how quietly they could go
wrong:

**A feature resolves to the model somebody chose**, and says where that choice
came from. The failure mode without this is a setting that appears to save and
changes nothing.

**A key is never stored in the clear, and never handed back.** The old store
kept it as plaintext JSON; this one seals it, and the difference is invisible
from the API - only a test that looks at the column can tell.

**Spend is recorded even when the call fails.** A refusal or a timeout still
burns input tokens, and a board that counted only successes would under-report
exactly when something is looping.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app import admin
from app.llm import features, keys, pricing, usage


@pytest.fixture
def clean():
    """No overrides, no stored keys, no usage rows, and a usable secret."""
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models.llm import LlmCredential, LlmUsage
    from app.models.uploads import AppSetting
    from app.narration import store

    def clear():
        with session_scope() as session:
            session.execute(delete(LlmUsage))
            session.execute(delete(LlmCredential))
            session.execute(
                delete(AppSetting).where(
                    AppSetting.key.in_(
                        [features.SETTING_KEY, pricing.SETTING_KEY, store.SETTING_KEY]
                    )
                )
            )

    clear()
    yield
    clear()


@pytest.fixture
def secret(monkeypatch):
    """A key-encryption key, so the store is usable inside a test."""
    monkeypatch.setenv(keys.SECRET_ENV, keys.generate_secret())


@pytest.fixture
def local():
    """A client that looks like it came from the machine the app runs on."""
    return TestClient(app, client=("127.0.0.1", 5000))


# --------------------------------------------------------------------------
# Which model a feature uses.
# --------------------------------------------------------------------------


def test_every_feature_resolves_without_any_configuration(clean):
    """The upgrade path: a deployment that sets none of this still works."""
    for key in features.FEATURES:
        chosen = features.resolve(key)
        assert chosen.provider in features.FEATURES[key].providers
        assert chosen.model, f"{key} resolved to an empty model"


def test_an_override_beats_the_global_default(clean):
    from app.narration import store

    store.update(enabled=True, provider="anthropic", model="claude-opus-5")
    assert features.resolve("narration").source == "global"

    features.set_override("narration", provider="gemini")
    chosen = features.resolve("narration")
    assert chosen.provider == "gemini"
    assert chosen.source == "override"
    # The model follows whoever chose the vendor. Carrying "claude-opus-5"
    # over to Gemini would send a model string that vendor has never heard of.
    assert chosen.model == "gemini-3.8-flash"


def test_features_can_use_different_models_at_the_same_time(clean):
    """The whole point: chat on one model, narration on another."""
    features.set_override("narration", provider="anthropic", model="claude-opus-5")
    features.set_override("chat", provider="gemini")

    assert features.resolve("narration").model == "claude-opus-5"
    assert features.resolve("chat").provider == "gemini"


def test_traceability_defaults_to_claude_and_offers_fpt(clean, secret, monkeypatch):
    from app import traceability_run as TR

    monkeypatch.delenv("TRACELINK_MODEL", raising=False)
    # Claude unless chosen otherwise - not whatever narration is set to.
    chosen = features.resolve("traceability")
    assert (chosen.provider, chosen.model) == ("anthropic", "claude-opus-5")
    assert TR._model_env({})["TRACELINK_MODEL"] == "claude-opus-5"

    # Picking FPT with no model gets the tracing-measured model, not the
    # narration one, and the run is handed the key saved on the page.
    keys.save("fpt", "sk-test")
    features.set_override("traceability", provider="fpt")
    assert features.resolve("traceability").model == "DeepSeek-V4-Flash"
    env = TR._model_env({})
    assert env["TRACELINK_MODEL"] == "fpt:DeepSeek-V4-Flash"
    assert env["FPT_API_KEY"] == "sk-test"
    # An explicit key in the environment is not overridden by the stored one.
    assert "FPT_API_KEY" not in TR._model_env({"FPT_API_KEY": "from-env"})

    view = [f for f in features.public_view()["features"] if f["key"] == "traceability"][0]
    assert view["model_options"]["fpt"][0]["id"] == "DeepSeek-V4-Flash"


def test_traceability_leaves_an_explicit_env_model_alone_until_overridden(clean, monkeypatch):
    from app import traceability_run as TR

    assert "TRACELINK_MODEL" not in TR._model_env({"TRACELINK_MODEL": "fpt:GLM-5.2"})
    features.set_override("traceability", provider="anthropic", model="claude-opus-5")
    assert TR._model_env({"TRACELINK_MODEL": "fpt:GLM-5.2"})["TRACELINK_MODEL"] == "claude-opus-5"


def test_a_feature_refuses_a_vendor_it_cannot_serve(clean):
    """`tile_agent` is an Anthropic tool loop; there is no OpenAI path."""
    with pytest.raises(ValueError, match="cannot run on"):
        features.set_override("tile_agent", provider="openai")


def test_a_known_vendor_saved_for_the_wrong_feature_falls_back(clean):
    """Saved before a feature narrowed, say. It degrades and says why."""
    from app.db import session_scope
    from app.models.uploads import AppSetting

    # Written directly, because `set_override` is what would have refused it.
    import json

    with session_scope() as session:
        session.merge(
            AppSetting(
                key=features.SETTING_KEY,
                value=json.dumps({"chat": {"provider": "openai"}}),
            )
        )

    chosen = features.resolve("chat")
    assert chosen.provider == "anthropic"
    assert "cannot run on openai" in chosen.unsupported


def test_an_unknown_vendor_is_left_alone_to_fail_loudly(clean):
    """A typo must disable narration, not silently switch vendor.

    `_narrator()` turns the resulting `ValueError` into the deterministic
    template. Quietly rewriting `llama` to `anthropic` would instead narrate
    against a vendor nobody chose - and bill for it.
    """
    from app.narration import store

    store.save(store.NarrationSettings(enabled=True, provider="llama"))
    assert features.resolve("narration").provider == "llama"

    from app.api.main import _narrator

    assert _narrator() is None


def test_a_feature_can_be_switched_off_on_its_own(clean):
    from app.narration import store

    store.update(enabled=True, provider="anthropic")
    assert features.resolve("narration").enabled is True

    features.set_override("narration", enabled=False)
    assert features.resolve("narration").enabled is False
    # `enabled=False` must survive as a stored value rather than being read as
    # "unset", which would inherit True and silently re-enable the feature.
    assert features.overrides()["narration"]["enabled"] is False


def test_turning_a_feature_off_actually_stops_the_narrator(clean):
    """Regression: `_narrator()` read the *global* flag, not the resolution.

    So "Turn off" on the settings page saved, displayed as off, and narration
    carried on - the one failure mode a per-feature switch exists to prevent.
    """
    from app.api.main import _narrator
    from app.narration import store

    store.update(enabled=True, provider="anthropic")
    assert _narrator() is not None

    features.set_override("narration", enabled=False)
    assert _narrator() is None


def test_the_self_test_reports_what_it_actually_ran(clean, local, monkeypatch):
    """Regression: this route raised `NameError` after a successful call.

    The response referenced a variable that per-feature resolution had
    replaced. Every line before it was exercised by the existing tests; this
    one is only reached once the model answers, so nothing without a live key
    could reach it - which is exactly why it shipped.
    """
    import app.api.main as main
    from app.narration import store

    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    store.update(enabled=True, provider="anthropic", model="claude-opus-5")

    class _Bundle:
        findings = ["something to narrate"]

    class _Outcome:
        source = "model"
        attempts = 1
        fallback_reason = ""
        narrative = "a narrative"

    monkeypatch.setattr(main, "check_connection", lambda: None)
    monkeypatch.setattr(main, "analyze_project", lambda *a, **k: _Bundle())
    monkeypatch.setattr("app.narration.client.narrate", lambda *a, **k: _Outcome())
    monkeypatch.setattr(
        "app.narration.providers.drafter_for", lambda *a, **k: (lambda s, u: "text")
    )

    body = local.post("/api/settings/test").json()
    assert body["narrative"] == "a narrative"
    assert body["provider"] == "anthropic"
    assert body["model"] == "claude-opus-5"


# --------------------------------------------------------------------------
# Keys.
# --------------------------------------------------------------------------


def test_a_key_round_trips_and_is_not_stored_in_the_clear(clean, secret):
    plaintext = "sk-ant-api03-notarealkey-abcdefghijklmnop-bQ4A"
    keys.save("anthropic", plaintext, actor="test")

    assert keys.get("anthropic") == plaintext

    from app.db import session_scope
    from app.models.llm import LlmCredential

    with session_scope() as session:
        row = session.get(LlmCredential, "anthropic")
        assert plaintext not in row.ciphertext
        # The hint identifies the key without disclosing it.
        assert row.hint == "sk-ant-...bQ4A"


def test_the_store_refuses_rather_than_falling_back_to_plaintext(clean, monkeypatch):
    """A secret store that silently degrades is worse than one that refuses."""
    monkeypatch.delenv(keys.SECRET_ENV, raising=False)

    assert keys.available() is False
    with pytest.raises(keys.KeyStoreUnavailable):
        keys.save("anthropic", "sk-ant-whatever-at-all")

    from app.db import session_scope
    from app.models.llm import LlmCredential

    with session_scope() as session:
        assert session.get(LlmCredential, "anthropic") is None


def test_a_passphrase_works_as_well_as_a_generated_key(clean, monkeypatch):
    """`fly secrets set PULSE_SECRET_KEY=$(openssl rand -hex 32)` is what people type."""
    monkeypatch.setenv(keys.SECRET_ENV, "a" * 64)
    keys.save("openai", "sk-openai-test-key-value")
    assert keys.get("openai") == "sk-openai-test-key-value"


def test_rotation_keeps_the_old_key_readable(clean, monkeypatch):
    first = keys.generate_secret()
    monkeypatch.setenv(keys.SECRET_ENV, first)
    keys.save("anthropic", "sk-ant-original-key-value-here")

    second = keys.generate_secret()
    monkeypatch.setenv(keys.SECRET_ENV, second)
    monkeypatch.setenv(keys.PREVIOUS_SECRET_ENV, first)

    assert keys.get("anthropic") == "sk-ant-original-key-value-here"
    assert keys.rotate() == 1

    # Re-sealed under the new key alone, so the old one can be dropped.
    monkeypatch.delenv(keys.PREVIOUS_SECRET_ENV)
    assert keys.get("anthropic") == "sk-ant-original-key-value-here"


def test_status_never_returns_a_key(clean, secret):
    keys.save("anthropic", "sk-ant-secret-value-that-must-not-leak")
    blob = repr(keys.status())
    assert "secret-value-that-must-not-leak" not in blob
    assert "sk-ant-...leak" in blob


def test_a_key_sealed_by_a_lost_secret_reports_itself_unreadable(clean, monkeypatch):
    """A row existing is not the same as a key that works.

    Reporting it as "saved" made the key list contradict the feature list
    beside it - one said the vendor was configured, the other said "no key" -
    which is the worst way to present a rotation gone wrong.
    """
    monkeypatch.setenv(keys.SECRET_ENV, keys.generate_secret())
    keys.save("anthropic", "sk-ant-sealed-with-the-old-secret")

    monkeypatch.setenv(keys.SECRET_ENV, keys.generate_secret())
    row = next(r for r in keys.status() if r["provider"] == "anthropic")

    assert row["stored"] is True
    assert row["readable"] is False
    assert row["effective_source"] == "none"
    assert keys.SECRET_ENV in row["problem"]
    # And the feature agrees, rather than claiming a credential it cannot use.
    assert features.resolve("narration").has_credential is False


def test_the_environment_is_reported_separately_from_the_store(clean, secret, monkeypatch):
    """"No key saved here" must not read as "no key"."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-from-the-environment")
    row = next(r for r in keys.status() if r["provider"] == "anthropic")
    assert row["stored"] is False
    assert row["from_environment"] is True
    assert row["env_var"] == "ANTHROPIC_API_KEY"
    assert row["effective_source"] == "environment"


# --------------------------------------------------------------------------
# Pricing and metering.
# --------------------------------------------------------------------------


def test_a_known_model_is_priced_and_an_unknown_one_is_flagged(clean):
    cost, priced = pricing.estimate("claude-opus-5", input_tokens=1_000_000)
    assert priced is True
    assert cost == pytest.approx(5.00)

    cost, priced = pricing.estimate("some-model-nobody-priced", input_tokens=1_000_000)
    assert priced is False
    assert cost == 0.0


def test_reasoning_tokens_are_not_billed_twice(clean):
    """They are inside `output_tokens` on every vendor.

    Adding them again would roughly double the cost of any reasoning model,
    which is the one case where the number matters most.
    """
    cost, _ = pricing.estimate("claude-opus-5", output_tokens=1_000_000)
    assert cost == pytest.approx(25.00)


def test_a_rate_override_wins_and_can_be_removed(clean):
    pricing.set_rate("gemini-3.8-flash", input=1.0, output=2.0)
    cost, priced = pricing.estimate("gemini-3.8-flash", input_tokens=1_000_000)
    assert priced is True and cost == pytest.approx(1.0)

    pricing.set_rate("gemini-3.8-flash", input=0, output=0)
    assert pricing.estimate("gemini-3.8-flash", input_tokens=10)[1] is False


def test_a_successful_call_is_recorded_against_its_feature(clean):
    with usage.for_feature("narration"):
        with usage.track("anthropic", "claude-opus-5") as call:
            call.tokens(input=1000, output=500)

    summary = usage.summary(days=1)
    assert summary["totals"]["calls"] == 1
    assert summary["totals"]["input_tokens"] == 1000
    assert summary["totals"]["cost_usd"] == pytest.approx(
        (1000 * 5.00 + 500 * 25.00) / 1_000_000
    )
    assert summary["by_feature"][0]["feature"] == "narration"


def test_a_failed_call_is_still_recorded(clean):
    """A refusal burns input tokens. Hiding it under-reports the bill."""
    with usage.for_feature("risk_drafts"):
        with pytest.raises(RuntimeError):
            with usage.track("anthropic", "claude-opus-5") as call:
                call.tokens(input=800)
                raise RuntimeError("claude declined (cyber)")

    summary = usage.summary(days=1)
    assert summary["totals"]["calls"] == 1
    assert summary["totals"]["failed"] == 1
    assert summary["totals"]["input_tokens"] == 800
    assert "declined" in summary["recent_failures"][0]["error"]


def test_an_unpriced_call_is_counted_rather_than_shown_as_free(clean):
    with usage.for_feature("chat"):
        with usage.track("fpt", "gemma-4-31B-it") as call:
            call.tokens(input=2000, output=1000)

    summary = usage.summary(days=1)
    assert summary["totals"]["cost_usd"] == 0.0
    # The distinction that stops $0.00 reading as "this was free".
    assert summary["totals"]["unpriced_calls"] == 1


def test_unpriced_calls_are_counted_per_model_not_just_in_total(clean):
    """Regression: this was `sum(<boolean>)`, which does not survive a move.

    SQLite quietly returned 1 for eighteen unpriced calls, and Postgres - the
    deployment target - refuses to sum a boolean at all. The blanket `except`
    around `summary()` would have turned that into an empty board rather than
    an error, so nothing would have pointed at this.
    """
    for _ in range(3):
        with usage.for_feature("chat"):
            with usage.track("fpt", "gemma-4-31B-it") as call:
                call.tokens(input=100, output=50)
    with usage.for_feature("narration"):
        with usage.track("anthropic", "claude-opus-5") as call:
            call.tokens(input=100, output=50)

    by_model = {r["model"]: r for r in usage.summary(days=1)["by_model"]}
    assert by_model["gemma-4-31B-it"]["unpriced_calls"] == 3
    assert by_model["claude-opus-5"]["unpriced_calls"] == 0


def test_an_unattributed_call_is_kept_not_dropped(clean):
    """A call nobody labelled still costs money."""
    with usage.track("anthropic", "claude-opus-5") as call:
        call.tokens(input=10, output=10)

    assert usage.summary(days=1)["by_feature"][0]["feature"] == "unattributed"


def test_accounting_never_breaks_the_feature(clean, monkeypatch):
    """A usage row that cannot be written is dropped, not raised."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(usage, "_write", boom)
    with pytest.raises(RuntimeError):
        with usage.track("anthropic", "claude-opus-5"):
            pass

    # And with the real `_write`, a broken session still must not propagate.
    monkeypatch.undo()
    monkeypatch.setattr("app.db.session_scope", boom)
    with usage.track("anthropic", "claude-opus-5") as call:
        call.tokens(input=1)


# --------------------------------------------------------------------------
# Who may change any of it.
# --------------------------------------------------------------------------


def test_a_remote_write_is_refused_when_no_token_is_configured(clean, monkeypatch):
    """Default-deny: an existing deployment does not loosen by upgrading."""
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)

    response = TestClient(app).put(
        "/api/llm/features/narration", json={"provider": "gemini"}
    )
    assert response.status_code == 403
    assert admin.TOKEN_ENV in response.json()["detail"]


def test_a_remote_write_succeeds_with_the_token(clean, monkeypatch):
    monkeypatch.setenv(admin.TOKEN_ENV, "s3cret-admin-token")

    client = TestClient(app)
    assert client.put("/api/llm/features/chat", json={"provider": "gemini"}).status_code == 403

    response = client.put(
        "/api/llm/features/chat",
        json={"provider": "gemini"},
        headers={admin.HEADER: "s3cret-admin-token"},
    )
    assert response.status_code == 200
    assert features.resolve("chat").provider == "gemini"


def test_a_wrong_token_is_refused_differently_from_an_absent_one(clean, monkeypatch):
    """The two cases send someone to different places, so they read differently.

    "No token is configured" is a deployment that has not opted in and needs a
    secret set; "the token is wrong" is a request that tried and failed.
    Collapsing them sends the first case hunting for a typo in a value that
    does not exist.
    """
    def refusal():
        return (
            TestClient(app)
            .put(
                "/api/llm/features/chat",
                json={"provider": "gemini"},
                headers={admin.HEADER: "not-the-real-token"},
            )
            .json()["detail"]
        )

    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    absent = refusal()
    monkeypatch.setenv(admin.TOKEN_ENV, "the-real-token")
    wrong = refusal()

    assert absent != wrong
    # Nothing to paste yet - go and create one.
    assert admin.TOKEN_ENV in absent
    # There is one; this is not it.
    assert "/llm" in wrong


def test_loopback_still_writes_without_a_token(clean, local, monkeypatch):
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    assert local.put("/api/llm/features/chat", json={"provider": "gemini"}).status_code == 200


def test_writable_reflects_the_real_answer(clean, local, monkeypatch):
    """It was hardcoded true, which told a deployed browser it could save."""
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    assert local.get("/api/settings").json()["writable"] is True
    assert TestClient(app).get("/api/settings").json()["writable"] is False


def test_the_usage_board_is_readable_without_a_token(clean):
    """It discloses no credential, and spend nobody can see is spend nobody controls."""
    assert TestClient(app).get("/api/llm/usage").status_code == 200


def test_saving_a_key_through_the_api_never_returns_it(clean, local, secret):
    body = local.put(
        "/api/llm/keys/anthropic", json={"api_key": "sk-ant-round-trip-test-bQ4A"}
    ).json()

    assert "round-trip-test" not in repr(body)
    row = next(r for r in body["keys"] if r["provider"] == "anthropic")
    assert row["stored"] is True
    assert row["hint"] == "sk-ant-...bQ4A"
    assert keys.get("anthropic") == "sk-ant-round-trip-test-bQ4A"
