"""Narration settings a person can change while the app is running.

`app.config.Settings` is a frozen dataclass built at import, which is right for
deployment configuration and useless for a key someone wants to paste into a
form. This is the mutable half: which vendor, which model, whether narration is
on at all, and the credential.

**The key does not live here any more.** It used to, in plaintext, in this
same JSON row. It is now sealed in `llm_credentials` - see `app/llm/keys.py`,
which explains what that buys and what it does not. This module still *reads*
the old field so a row written before the move can be migrated
(`keys.adopt_legacy`), and `save()` blanks it on the way past, but nothing
writes a key here again. Re-opening that path would undo the encryption for
anybody who happened to use this form instead of the other one.

It used to be a JSON file under `PULSE_STATE_DIR`, which is fine on a laptop
and wrong on a host: a container's filesystem does not survive a deploy, so a
provider chosen in the browser silently reverted on the next release. The
database is where the rest of this app's mutable state already lives, and it
is the same security boundary the connection string is.

Environment still wins where it is set: `ANTHROPIC_API_KEY` and friends are read
by the SDKs when no key is stored here, so a deployment that injects secrets
properly - Fly secrets, say - never needs this row to exist. That remains the
recommended path for a deployment; this is for configuring a running app by
hand.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, replace

log = logging.getLogger(__name__)

#: Guards read-modify-write, so two saves arriving together cannot interleave
#: into one row where each wrote half the fields.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class NarrationSettings:
    """What a person can change without restarting anything."""

    enabled: bool = False
    provider: str = "anthropic"
    #: Empty means the provider's default from `narration.providers`.
    model: str = ""
    #: **Legacy, read-only.** Rows written before keys moved to
    #: `llm_credentials` still carry one here; `keys.adopt_legacy()` re-seals
    #: it and `save()` blanks it. Always empty on anything written since.
    api_key: str = ""
    #: Point the vendor's SDK elsewhere - a self-hosted open model behind an
    #: OpenAI-compatible server, or an internal gateway. Empty = the vendor.
    base_url: str = ""


def _mask(key: str) -> str:
    """A hint that identifies a key without disclosing it.

    Enough to tell two keys apart when someone wonders which one is loaded;
    not enough to use. Short strings are hidden entirely rather than mostly
    shown, because a short secret is all suffix.
    """
    if not key:
        return ""
    if len(key) < 12:
        return "*" * len(key)
    return f"{key[:7]}...{key[-4:]}"


def load() -> NarrationSettings:
    """Current settings, falling back to `app.config` then to the defaults.

    A missing or unreadable row is not an error: it means nobody has
    configured narration here, which is the ordinary state.
    """
    from app.config import settings

    base = NarrationSettings(
        enabled=settings.narration_model_enabled,
        provider=settings.narration_provider,
        model=settings.narration_model,
    )

    stored = _read()
    if not isinstance(stored, dict):
        return base

    return replace(
        base,
        enabled=bool(stored.get("enabled", base.enabled)),
        provider=str(stored.get("provider") or base.provider),
        model=str(stored.get("model", base.model) or ""),
        api_key=str(stored.get("api_key", "") or ""),
        base_url=str(stored.get("base_url", "") or ""),
    )


SETTING_KEY = "narration"


def _read() -> dict | None:
    """The stored settings, or `None` if there are none to read.

    Every failure - no row, no table, no database - means "nobody has
    configured narration", which is the ordinary state and must fall back to
    `app.config` rather than raise. This runs on the path of every narrated
    request.
    """
    from app.db import session_scope
    from app.models.uploads import AppSetting

    try:
        with session_scope() as session:
            row = session.get(AppSetting, SETTING_KEY)
            if row is None:
                return None
            return json.loads(row.value or "{}")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("ignoring unreadable narration settings: %s", exc)
        return None


def save(current: NarrationSettings) -> NarrationSettings:
    """Persist settings and return what was written.

    **The key is stripped before writing, always.** This row is plaintext JSON
    and a credential has no business in it; `app/llm/keys.py` owns keys now.
    Stripping here rather than refusing means a caller holding a legacy value
    - `adopt_legacy()` does - can persist the rest of the settings without
    first having to know about the field.

    In the database rather than a file under `PULSE_STATE_DIR`: a container's
    disk does not survive a deploy, so a provider chosen in the browser reset
    itself on every release.
    """
    from app.db import session_scope
    from app.models.uploads import AppSetting

    stored = replace(current, api_key="")
    with _LOCK, session_scope() as session:
        session.merge(
            AppSetting(key=SETTING_KEY, value=json.dumps(asdict(stored)))
        )
    return stored


def update(**changes) -> NarrationSettings:
    """Change some fields and persist.

    `api_key` is accepted and discarded rather than rejected: callers that
    still pass it - an old client, a stored form post - should not 500, they
    should simply not succeed in putting a key here. `save()` strips it too,
    so there is no path through this module that writes one.
    """
    changes.pop("api_key", None)
    return save(replace(load(), **changes))


def public_view() -> dict:
    """What a browser is allowed to know. Never the key itself."""
    from app.narration.providers import DEFAULT_MODELS, MODEL_OPTIONS, PROVIDERS

    current = load()
    return {
        "enabled": current.enabled,
        "provider": current.provider,
        "model": current.model,
        "base_url": current.base_url,
        "effective_model": current.model or DEFAULT_MODELS.get(current.provider, ""),
        "api_key_set": bool(current.api_key),
        "api_key_hint": _mask(current.api_key),
        "api_key_from_environment": _env_key_present(current.provider),
        "providers": list(PROVIDERS),
        "default_models": dict(DEFAULT_MODELS),
        "model_options": {k: [dict(o) for o in v] for k, v in MODEL_OPTIONS.items()},
        # Where the settings live, so the page can say so rather than
        # implying a file somebody could go and edit.
        "stored_at": "database (app_settings.narration)",
    }


#: Which environment variable each vendor's SDK reads when no key is stored.
ENV_KEYS = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    # Read by our own adapter rather than by an SDK - `fpt` has none.
    "fpt": ("FPT_API_KEY",),
}


def _env_key_present(provider: str) -> bool:
    """Whether the environment already carries a credential for this vendor.

    Shown on the page so "no key saved here" does not read as "no key", which
    would send someone pasting a secret into a file they did not need."""
    return any(os.environ.get(name) for name in ENV_KEYS.get(provider, ()))
