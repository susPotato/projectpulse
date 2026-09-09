"""Narration settings a person can change while the app is running.

`app.config.Settings` is a frozen dataclass built at import, which is right for
deployment configuration and useless for a key someone wants to paste into a
form. This is the mutable half: which vendor, which model, whether narration is
on at all, and the credential.

**The key is stored in plaintext on this machine**, in `PULSE_STATE_DIR`
(`<repo>/.pulse/` by default, gitignored). That is the same bargain as a `.env`
file and it is stated here, in the API response and on the settings page rather
than left for someone to discover. It is never logged, and never returned to a
browser - `public_view()` returns a hint like `sk-ant-...bQ4A` and a boolean, so
a page can show that a key is set without being able to read it back.

Environment still wins where it is set: `ANTHROPIC_API_KEY` and friends are read
by the SDKs when no key is stored here, so a deployment that injects secrets
properly never needs this file to exist.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path

log = logging.getLogger(__name__)

#: Guards read-modify-write. Uvicorn serves requests from a thread pool, so two
#: saves arriving together could otherwise interleave into a half-written file.
_LOCK = threading.Lock()

FILENAME = "narration.json"


@dataclass(frozen=True)
class NarrationSettings:
    """What a person can change without restarting anything."""

    enabled: bool = False
    provider: str = "anthropic"
    #: Empty means the provider's default from `narration.providers`.
    model: str = ""
    #: Empty means "let the SDK read the environment", which is the deployment
    #: path. Never leaves the process except to the vendor's client.
    api_key: str = ""
    #: Point the vendor's SDK elsewhere - a self-hosted open model behind an
    #: OpenAI-compatible server, or an internal gateway. Empty = the vendor.
    base_url: str = ""


def state_path() -> Path:
    from app.config import settings

    return Path(settings.state_dir) / FILENAME


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

    A missing or unreadable file is not an error: it means nobody has configured
    narration on this machine, which is the ordinary state.
    """
    from app.config import settings

    base = NarrationSettings(
        enabled=settings.narration_model_enabled,
        provider=settings.narration_provider,
        model=settings.narration_model,
    )

    path = state_path()
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return base
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("ignoring unreadable narration settings at %s: %s", path, exc)
        return base

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


def save(current: NarrationSettings) -> NarrationSettings:
    """Write settings to disk and return what was written."""
    path = state_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(current), indent=2), encoding="utf-8")
        try:
            # Owner-only where the platform honours it. Windows ignores the
            # mode bits, so this is best effort and not a security boundary -
            # which is why the page says the key is stored in plaintext.
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
    return current


def update(**changes) -> NarrationSettings:
    """Change some fields and persist.

    `api_key=None` leaves the stored key alone, which is what a form submits
    when the user did not retype it - the page never had the real one to send
    back. `api_key=""` is an explicit clear.
    """
    with_key = dict(changes)
    keep_key = with_key.pop("api_key", None) is None and "api_key" in changes

    current = load()
    if keep_key:
        changes = {k: v for k, v in changes.items() if k != "api_key"}

    return save(replace(current, **changes))


def public_view() -> dict:
    """What a browser is allowed to know. Never the key itself."""
    from app.narration.providers import DEFAULT_MODELS, PROVIDERS

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
        "stored_at": str(state_path()),
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
