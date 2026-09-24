"""Which model each feature uses.

There are four places in this app where a language model is called, and until
this module they all read one global setting - `PULSE_NARRATION_PROVIDER` -
through four separate `ModelConfig(...)` constructions in `app/api/main.py`.
That made one real question unanswerable: *the chat tab is chatty and cheap,
the tile agent runs a tool loop and is not, so why must they be the same
model?* They no longer must.

**Resolution order**, most specific first:

1. the per-feature override saved on the settings page (`app_settings.llm_features`),
2. the global default, which is the existing narration setting and so the
   environment (`PULSE_NARRATION_PROVIDER`, `PULSE_NARRATION_MODEL`),
3. the feature's own default in `FEATURES` below,
4. the vendor's default model from `narration/providers.py`.

Nothing here is required. A deployment that sets none of it behaves exactly as
it did before this module existed, which is what keeps it off the upgrade
checklist.

**Not every feature can run on every vendor.** `dashboard/agent.py` is an
Anthropic tool loop and `agent/chat.py` is wired for two vendors, so each
feature declares what it actually supports and a request to point one at a
vendor it cannot use is refused at the API rather than failing mid-call.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field, replace

log = logging.getLogger(__name__)

SETTING_KEY = "llm_features"

#: Guards read-modify-write so two saves cannot interleave into one row where
#: each wrote half the features. The same protection `narration/store.py` uses.
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Feature:
    """One place in the product that calls a model."""

    key: str
    label: str
    #: What it does, in the words the settings page shows.
    description: str
    #: Vendors whose adapter can actually serve this feature. A subset of
    #: `providers.PROVIDERS` wherever the call site is narrower than the
    #: narration adapters are.
    providers: tuple[str, ...]
    #: Generous where a truncated answer is discarded and the work is short;
    #: larger where a tool loop accumulates a transcript.
    max_tokens: int = 8000
    timeout_seconds: float = 60.0
    #: The switch that turns the whole feature off, if it has one. Features
    #: without one are always available when a key is.
    enable_flag: str = ""
    #: Which vendor this feature prefers when nothing else is configured.
    #: Empty means "follow the global default", which is what most should do.
    default_provider: str = ""
    #: Per-vendor default model and dropdown entries, where this feature's
    #: best model differs from the narration-measured `DEFAULT_MODELS` /
    #: `MODEL_OPTIONS`. Empty means "use those".
    default_models: dict = field(default_factory=dict)
    model_options: dict = field(default_factory=dict)


#: Every model call this app makes. Adding one here is what puts it on the
#: settings page and on the usage board - a feature absent from this table
#: still works, but spends money nobody can see, so add it.
FEATURES: dict[str, Feature] = {
    "narration": Feature(
        key="narration",
        label="Narration",
        description=(
            "Phrases findings the engine already computed. Runs behind a "
            "validator that checks the draft token by token, so the model "
            "asserts nothing of its own."
        ),
        providers=("anthropic", "openai", "gemini", "fpt"),
        max_tokens=8000,
        enable_flag="PULSE_NARRATION",
    ),
    "risk_drafts": Feature(
        key="risk_drafts",
        label="Risk drafts",
        description=(
            "Reads issue text and proposes risks a person then accepts or "
            "rejects. The one place a model is allowed to say something new, "
            "which is why it has its own switch."
        ),
        providers=("anthropic", "openai", "gemini", "fpt"),
        # Both numbers were measured, not chosen. Narration rephrases a
        # handful of findings already in hand; this reads a whole backlog and
        # writes structured JSON about it on a reasoning model. Sixty seconds
        # - the shared default - timed out on the first real project it met,
        # before the model had written a word.
        #
        # The ceiling has to be generous for the same reason: reasoning tokens
        # are billed *inside* it on every vendor, so a budget sized to the
        # answer starves it. At 4000, the budget went on thinking and the
        # reply was cut off before a single JSON object. The answer itself is
        # at most eight short objects; the rest is the model's working, and
        # refusing to pay for it buys nothing but a guaranteed truncation.
        max_tokens=16000,
        timeout_seconds=240.0,
        enable_flag="PULSE_RISK_DRAFTS",
    ),
    "tile_agent": Feature(
        key="tile_agent",
        label="Dashboard tile builder",
        description=(
            "A tool loop that turns 'show me slipping epics' into a real tile. "
            "Anthropic only - it is written against the Messages API's tool "
            "use, not against a shared abstraction."
        ),
        providers=("anthropic",),
        # A tool loop carries the whole transcript forward on every turn, so
        # the ceiling that suits a one-shot narrative is too tight here.
        max_tokens=16000,
        timeout_seconds=120.0,
        default_provider="anthropic",
    ),
    "chat": Feature(
        key="chat",
        label="Agent chat",
        description=(
            "The free-form Agent tab. Many short turns rather than a few long "
            "ones, which is the case for pointing it at a cheaper model than "
            "the rest of the app uses."
        ),
        providers=("anthropic", "gemini"),
        max_tokens=4000,
    ),
    "traceability": Feature(
        key="traceability",
        label="Traceability verdicts",
        description=(
            "Reads each ticket beside the code a retriever picked and decides "
            "whether the code supports it. Runs only when "
            "PULSE_TRACELINK_VERDICTS is on; answers are cached per model, so "
            "switching model re-runs every ticket once."
        ),
        providers=("anthropic", "fpt"),
        # Claude unless somebody chooses otherwise here - not the narration
        # default, which may be a vendor tracelink has no backend for.
        default_provider="anthropic",
        default_models={"anthropic": "claude-opus-5", "fpt": "DeepSeek-V4-Flash"},
        # Measured on 56 prompts byte-identical to cached Opus verdicts
        # (2026-09-24): agreement with Opus, and seconds per ticket. The
        # narration ranking does not carry over - gemma, fastest there, hit
        # the gateway's rate limit on a quarter of the tickets here.
        model_options={
            "anthropic": (
                {"id": "claude-opus-5", "label": "claude-opus-5 (recommended)"},
            ),
            "fpt": (
                {"id": "DeepSeek-V4-Flash",
                 "label": "DeepSeek-V4-Flash (recommended - 53/56 vs Opus, ~21s)"},
                {"id": "GLM-5.2", "label": "GLM-5.2 (52/56, ~8s)"},
                {"id": "Qwen3.6-27B", "label": "Qwen3.6-27B (51/56, ~11s)"},
                {"id": "Llama-3.3-70B-Instruct",
                 "label": "Llama-3.3-70B-Instruct (49/56, ~11s)"},
            ),
        },
    ),
    "codewiki": Feature(
        key="codewiki",
        label="CodeWiki docs",
        description=(
            "Writes a documentation tree from the code when a registered "
            "repository has no docs/ of its own, so the trace still has "
            "documents to read. Runs CodeWiki's agents - tens of minutes and "
            "many calls on the first run, resumed rather than repeated after."
        ),
        # CodeWiki speaks the OpenAI API, and these are the two vendors here
        # whose endpoint does. Tool calling on the gateway was checked for
        # all three FPT models below (2026-09-24) - the agents depend on it.
        providers=("fpt", "openai"),
        max_tokens=32768,
        timeout_seconds=300.0,
        default_provider="fpt",
        # Not the traceability pick, and measured rather than carried over
        # (2026-09-24, a 3-file repository): GLM-5.2 wrote all four pages in
        # 111s. DeepSeek-V4-Flash took 17 minutes for the first page - the
        # gateway queues it at 1-90s per request, and CodeWiki's agents make
        # many requests. Fine for a verdict per ticket; not for an agent loop.
        default_models={"fpt": "GLM-5.2"},
        model_options={
            "fpt": (
                {"id": "GLM-5.2", "label": "GLM-5.2 (recommended - 3 files in ~2 min)"},
                {"id": "DeepSeek-V4-Flash",
                 "label": "DeepSeek-V4-Flash (slow on the gateway - 17 min for 1 page)"},
                {"id": "Qwen3.6-27B", "label": "Qwen3.6-27B"},
            ),
        },
    ),
}


@dataclass(frozen=True)
class Resolution:
    """The answer to "what does this feature run on, and why that?"."""

    feature: str
    provider: str
    model: str
    max_tokens: int
    timeout_seconds: float
    api_key: str
    base_url: str
    enabled: bool
    #: "override" when the settings page chose it, "global" when it followed
    #: the narration default, "feature" when the table above decided. Shown on
    #: the page, so a setting that is not taking effect is visible rather than
    #: mysterious.
    source: str
    #: Set when the resolved provider cannot serve this feature - the caller
    #: falls back rather than failing, and the page shows the reason.
    unsupported: str = ""

    @property
    def has_credential(self) -> bool:
        """Whether a call could authenticate, from either source.

        An empty `api_key` is not "no credential": it is how a resolution says
        "let the vendor's SDK read the environment", which is the path a
        deployment injecting platform secrets takes. So the environment is
        checked too, rather than treating the stored row as the only answer.
        """
        return bool(self.api_key) or _env_key(self.provider)

    def model_config(self):
        """As the `ModelConfig` every adapter already takes."""
        from app.narration.providers import ModelConfig

        return ModelConfig(
            model=self.model,
            max_tokens=self.max_tokens,
            timeout_seconds=self.timeout_seconds,
            api_key=self.api_key,
            base_url=self.base_url,
        )


def _read() -> dict:
    """The saved overrides, or `{}`.

    Every failure means "nobody has overridden anything", which is the ordinary
    state and must not raise: this runs on the path of every model call.
    """
    from app.db import session_scope
    from app.models.uploads import AppSetting

    try:
        with session_scope() as session:
            row = session.get(AppSetting, SETTING_KEY)
            if row is None:
                return {}
            parsed = json.loads(row.value or "{}")
            return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("ignoring unreadable feature overrides: %s", exc)
        return {}


def overrides() -> dict:
    """Saved per-feature overrides, keyed by feature."""
    return {k: v for k, v in _read().items() if k in FEATURES and isinstance(v, dict)}


def set_override(feature: str, **changes) -> dict:
    """Change one feature's model settings and persist.

    A value of `None` clears that field back to the global default, which is
    how the page offers "follow the default" as a choice rather than making
    somebody guess which string means inherit.
    """
    from app.db import session_scope
    from app.models.uploads import AppSetting

    if feature not in FEATURES:
        raise ValueError(f"unknown feature {feature!r}")

    allowed = {
        "provider",
        "model",
        "max_tokens",
        "timeout_seconds",
        "base_url",
        "enabled",
    }
    unknown = set(changes) - allowed
    if unknown:
        raise ValueError(f"unknown setting(s) {sorted(unknown)}")

    spec = FEATURES[feature]
    provider = changes.get("provider")
    if provider and provider not in spec.providers:
        raise ValueError(
            f"{spec.label} cannot run on {provider!r}; it supports "
            f"{', '.join(spec.providers)}"
        )

    with _LOCK, session_scope() as session:
        stored = {}
        row = session.get(AppSetting, SETTING_KEY)
        if row is not None:
            try:
                stored = json.loads(row.value or "{}")
            except Exception:  # noqa: BLE001 - a corrupt row is replaced, not merged
                stored = {}

        current = dict(stored.get(feature) or {})
        for field, value in changes.items():
            # `enabled` is exempt from the clear-on-falsy rule below: False is
            # the whole point of setting it, and popping the key would silently
            # mean "inherit" - which for this field reads as True.
            if field == "enabled":
                current[field] = bool(value)
            elif value is None or value == "":
                current.pop(field, None)
            else:
                current[field] = value
        stored[feature] = current

        session.merge(AppSetting(key=SETTING_KEY, value=json.dumps(stored)))

    return current


def resolve(feature: str) -> Resolution:
    """What `feature` should call, with everything filled in.

    Never raises for a configuration problem. An override naming a vendor the
    feature cannot serve resolves to the feature's own default instead and says
    so in `unsupported`, because a bad setting must degrade the same way a
    missing key does - to the deterministic path - rather than 500 a page.
    """
    from app.llm import keys
    from app.narration.providers import DEFAULT_MODELS
    from app.narration.store import load as load_global

    spec = FEATURES.get(feature)
    if spec is None:
        raise ValueError(f"unknown feature {feature!r}")

    global_settings = load_global()
    saved = overrides().get(feature, {})

    provider = ""
    source = "feature"
    if saved.get("provider"):
        provider, source = str(saved["provider"]), "override"
    elif spec.default_provider:
        provider, source = spec.default_provider, "feature"
    elif global_settings.provider:
        provider, source = global_settings.provider, "global"

    # A vendor this app has no adapter for at all is left exactly as it is, so
    # `drafter_for` raises on it the way it always has. Quietly rewriting a
    # typo'd provider to a working one would turn "narration is off because
    # the setting is wrong" into "narration is on, against a vendor nobody
    # chose" - and the caller's own fallback to the deterministic template is
    # the behaviour a bad setting is supposed to get.
    from app.narration.providers import PROVIDERS

    unsupported = ""
    if provider and provider in PROVIDERS and provider not in spec.providers:
        # Known vendor, wrong feature - `chat` has no OpenAI path, say. Here a
        # fallback is right: the setting is meaningful, just not for this one.
        unsupported = (
            f"{spec.label} cannot run on {provider}; "
            f"using {spec.providers[0]} instead"
        )
        provider, source = spec.providers[0], "feature"
    if not provider:
        provider, source = spec.providers[0], "feature"

    # The model follows whoever chose the provider. An override that names a
    # provider but no model means "that vendor's default", not "the model
    # somebody picked for a different vendor" - which would be a model string
    # the new vendor has never heard of.
    model = ""
    if source == "override" and saved.get("model"):
        model = str(saved["model"])
    elif source == "global" and global_settings.model:
        model = str(global_settings.model)
    if not model:
        model = spec.default_models.get(provider) or DEFAULT_MODELS.get(provider, "")

    # The environment flag first, then an explicit per-feature choice on top -
    # so a deployment that has never opened the page behaves exactly as its
    # variables say, and the page can still turn one feature off without
    # touching the others or the environment.
    enabled = True
    if spec.enable_flag == "PULSE_NARRATION":
        enabled = global_settings.enabled
    elif spec.enable_flag == "PULSE_RISK_DRAFTS":
        from app.config import settings

        enabled = settings.risk_drafts_enabled
    if "enabled" in saved:
        enabled = bool(saved["enabled"])

    # The stored key wins over the environment, and an empty string tells the
    # adapter to let the vendor's SDK resolve credentials itself - which is how
    # a deployment injecting platform secrets keeps working with no row here.
    api_key = keys.get(provider) or global_settings.api_key or ""

    return Resolution(
        feature=feature,
        provider=provider,
        model=model,
        max_tokens=int(saved.get("max_tokens") or spec.max_tokens),
        timeout_seconds=float(saved.get("timeout_seconds") or spec.timeout_seconds),
        api_key=api_key,
        base_url=str(saved.get("base_url") or global_settings.base_url or ""),
        enabled=enabled,
        source=source,
        unsupported=unsupported,
    )


def attributed_drafter(feature: str):
    """`drafter_for(...)` for a feature, with its spend booked to that feature.

    Raises `ValueError` for an unknown provider exactly as `drafter_for` does,
    so callers keep the error handling they already had.

    The attribution is a wrapper rather than an argument because the adapters
    are `(system, user) -> str` closures several frames below the code that
    knows which feature is running - see `app/llm/usage.py` for why that
    signature was left alone.
    """
    from app.llm import usage
    from app.narration.providers import drafter_for

    chosen = resolve(feature)
    inner = drafter_for(chosen.provider, chosen.model_config())

    def attributed(system: str, user: str) -> str:
        with usage.for_feature(feature):
            return inner(system, user)

    return attributed


def public_view() -> dict:
    """Every feature, what it resolves to and where that came from.

    Never includes a key - only whether one is available, so the page can show
    a feature as ready or not without the server handing a secret to a browser.
    """
    from app.narration.providers import DEFAULT_MODELS, MODEL_OPTIONS

    saved = overrides()
    out = []
    for key, spec in FEATURES.items():
        resolution = resolve(key)
        out.append(
            {
                "key": key,
                "label": spec.label,
                "description": spec.description,
                "providers": list(spec.providers),
                "enable_flag": spec.enable_flag,
                "enabled": resolution.enabled,
                "provider": resolution.provider,
                "model": resolution.model,
                "max_tokens": resolution.max_tokens,
                "timeout_seconds": resolution.timeout_seconds,
                "source": resolution.source,
                "unsupported": resolution.unsupported,
                "has_credential": bool(resolution.api_key) or _env_key(resolution.provider),
                "override": saved.get(key, {}),
                "model_options": {
                    p: [dict(o) for o in
                        spec.model_options.get(p) or MODEL_OPTIONS.get(p, ())]
                    for p in spec.providers
                },
                "default_models": {
                    p: spec.default_models.get(p) or DEFAULT_MODELS.get(p, "")
                    for p in spec.providers
                },
            }
        )
    return {"features": out}


def _env_key(provider: str) -> bool:
    """Whether the environment carries a credential for this vendor."""
    import os

    from app.narration.store import ENV_KEYS

    return any(os.environ.get(n) for n in ENV_KEYS.get(provider, ()))
