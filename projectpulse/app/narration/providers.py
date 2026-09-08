"""One `Drafter` per vendor, and nothing else.

`client.py` is the fence: it builds a prompt with no digit in it, validates what
comes back, and substitutes the numbers afterwards. None of that is
vendor-specific, so none of it lives here - and this file is the demonstration
that it does not have to. A `Drafter` is `(system, user) -> str`. Everything the
product cares about happens on either side of that signature.

Three consequences worth stating, because they are the reason the seam was drawn
here rather than deeper:

**Switching vendor changes nothing that matters.** The validator, the token
substitution, the retry-with-objections and the template fallback are identical
whichever adapter runs. A vendor cannot be trusted more or less than another by
this design, because none of them is trusted at all.

**A missing SDK is not an error.** Each adapter imports its package on first
call, so `app.narration` stays importable with none of the three installed -
which is how a judge runs the demo. `narrate` turns the ImportError into a
fallback with a reason.

**Every adapter raises rather than returns bad prose.** A refusal, a truncation
or an empty completion all become `NarrationUnavailable`, because a partial
draft that happens to parse is more dangerous than no draft: the template is
known-good, so there is never a reason to accept something worse.

⚠️ **No adapter has called a commercial endpoint.** There are no API credentials
on the machine this was written on. What *is* verified: the OpenAI and Gemini
adapters complete a full round trip - request, response parsing, gate,
substitution - against local servers speaking each vendor's wire format
(`tests/test_narration.py`), so the only unproven part is the credential itself.
The Anthropic adapter's kwargs are checked against the installed SDK but its
response parsing has not been exercised.

**The model ids in `DEFAULT_MODELS` were checked against each vendor's current
model list on 2026-09-08.** They still age: `PULSE_NARRATION_MODEL` and the
settings page override any of them without a code change, which is the point of
keeping them in one table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from app.narration.client import Drafter, NarrationUnavailable

log = logging.getLogger(__name__)

#: The vendors with an adapter here. `drafter_for` is the only way to pick one,
#: so an unknown name fails at configuration time rather than mid-request.
PROVIDERS = ("anthropic", "openai", "gemini")

#: Default model per vendor, checked against each vendor's current model list
#: on 2026-09-08. Not placeholders - but model names move, so
#: `PULSE_NARRATION_MODEL` and the settings page override any of them without a
#: code change.
#:
#: The two non-Anthropic defaults are the cost-balanced tier rather than the
#: flagship, deliberately: this job is phrasing findings the engine already
#: computed, under a rule the validator enforces. It rewards instruction-
#: following, not reasoning depth, and a rejected draft costs a retry rather
#: than a wrong number.
DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "openai": "gpt-5.6-terra",
    "gemini": "gemini-3.8-flash",
}

#: What to install for each, quoted in the message a failed import produces.
EXTRAS = {
    "anthropic": 'pip install -e ".[llm]"',
    "openai": 'pip install -e ".[llm-openai]"',
    "gemini": 'pip install -e ".[llm-gemini]"',
}


@dataclass(frozen=True)
class ModelConfig:
    """What to ask, and how patiently.

    `max_tokens` is generous because reasoning tokens are billed inside it on
    every vendor and the narrative itself is short. A truncated draft is
    discarded, so a tight ceiling buys nothing but a guaranteed fallback.

    `model` empty means "this vendor's default", resolved in `drafter_for` -
    which keeps the default in one table instead of one per adapter.
    """

    model: str = ""
    max_tokens: int = 8000
    timeout_seconds: float = 60.0
    #: Passed to the vendor client explicitly when set. Empty means "let the
    #: SDK read the environment", which is how a real deployment injects it -
    #: so the settings page is an addition to that path, never a replacement.
    #: Never logged; `store.public_view()` will not return it to a browser.
    api_key: str = ""
    #: Point a vendor's SDK somewhere else: a self-hosted open model behind an
    #: OpenAI-compatible server (vLLM, Ollama, LM Studio), an internal FPT
    #: gateway, or a proxy. Empty means the vendor's own endpoint. This is what
    #: makes "run a 26B model on our hardware" a setting rather than a fork.
    base_url: str = ""


def drafter_for(provider: str, config: ModelConfig | None = None) -> Drafter:
    """The adapter for one vendor, with its default model filled in.

    The single entry point on purpose. A typo in `PULSE_NARRATION_PROVIDER`
    raises here, at startup, instead of silently disabling narration and
    presenting as "the model never worked".
    """
    name = (provider or "").strip().lower()
    if name not in PROVIDERS:
        raise ValueError(
            f"unknown narration provider {provider!r}; "
            f"expected one of {', '.join(PROVIDERS)}"
        )

    cfg = config or ModelConfig()
    if not cfg.model:
        cfg = replace(cfg, model=DEFAULT_MODELS[name])

    return {
        "anthropic": _anthropic_drafter,
        "openai": _openai_drafter,
        "gemini": _gemini_drafter,
    }[name](cfg)


def _import(module: str, provider: str):
    """Import a vendor SDK, or say which extra installs it."""
    try:
        return __import__(module, fromlist=["*"])
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise NarrationUnavailable(
            f"the {provider} SDK is not installed; {EXTRAS[provider]}"
        ) from exc


def _key(cfg: ModelConfig) -> dict:
    """The `api_key=` kwarg, or nothing at all.

    Omitted rather than passed as empty or None: every SDK treats an absent
    argument as "resolve from the environment" and an explicit empty string as
    a credential that happens to be blank, which fails with a confusing message
    instead of falling back.
    """
    return {"api_key": cfg.api_key} if cfg.api_key else {}


def _base(cfg: ModelConfig) -> dict:
    """The `base_url=` kwarg, or nothing.

    Omitted when empty for the same reason as the key: an explicit empty string
    is a URL that does not resolve, where an absent argument means "the
    vendor's own endpoint". Gemini takes it differently - see `_gemini_drafter`.
    """
    return {"base_url": cfg.base_url} if cfg.base_url else {}


def _nonempty(text: str, provider: str) -> str:
    """Guard the one failure every vendor can produce silently.

    An empty completion is a successful HTTP response with nothing in it. It
    would fail the validator's `shape` stage anyway, but as "draft is empty" -
    which sends a reader looking at the prompt instead of at the vendor.
    """
    if not text.strip():
        raise NarrationUnavailable(f"{provider} returned an empty completion")
    return text


# --------------------------------------------------------------------------
# Anthropic. The path this project was built against.
# --------------------------------------------------------------------------


def _anthropic_drafter(cfg: ModelConfig) -> Drafter:
    """Claude, via the Messages API.

    Credentials resolve the way the SDK resolves them - `ANTHROPIC_API_KEY`,
    `ANTHROPIC_AUTH_TOKEN`, or an `ant auth login` profile. Nothing here checks
    for a key: an auth failure is one more reason to serve the template, and the
    SDK's own message says more than a guess made here would.
    """
    holder: dict[str, object] = {}

    def draft(system: str, user: str) -> str:
        if "client" not in holder:
            anthropic = _import("anthropic", "anthropic")
            holder["client"] = anthropic.Anthropic(
                timeout=cfg.timeout_seconds, **_key(cfg), **_base(cfg)
            )

        response = holder["client"].beta.messages.create(  # type: ignore[union-attr]
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            thinking={"type": "adaptive"},
            # A policy decline is re-run on a fallback model inside the same
            # call. Cheap insurance: without it a refusal costs the narrative
            # outright, and this text is read aloud in a live demo.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
        )

        # Both arrive as a successful HTTP response, so neither raises on its
        # own. A refusal has no text to read at all.
        if response.stop_reason == "refusal":
            detail = getattr(response.stop_details, "category", None)
            raise NarrationUnavailable(f"claude declined ({detail})")
        if response.stop_reason == "max_tokens":
            raise NarrationUnavailable("the draft was truncated at max_tokens")

        return _nonempty(
            "".join(b.text for b in response.content if b.type == "text"),
            "anthropic",
        )

    return draft


# --------------------------------------------------------------------------
# OpenAI.
# --------------------------------------------------------------------------


def _openai_drafter(cfg: ModelConfig) -> Drafter:
    """GPT, via Chat Completions.

    Chat Completions rather than the Responses API deliberately: it is the
    shape every OpenAI-compatible endpoint implements, so this adapter also
    covers Azure OpenAI, a local vLLM or an internal FPT gateway by setting
    `OPENAI_BASE_URL` - no second adapter, no second thing to keep working.

    Credentials come from `OPENAI_API_KEY`.
    """
    holder: dict[str, object] = {}

    def draft(system: str, user: str) -> str:
        if "client" not in holder:
            openai = _import("openai", "openai")
            holder["client"] = openai.OpenAI(
                timeout=cfg.timeout_seconds, **_key(cfg), **_base(cfg)
            )

        response = holder["client"].chat.completions.create(  # type: ignore[union-attr]
            model=cfg.model,
            max_completion_tokens=cfg.max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )

        choice = response.choices[0]
        # A content filter stops generation with a successful response, and a
        # refusal is a separate field from the text rather than an exception.
        if choice.finish_reason == "content_filter":
            raise NarrationUnavailable("openai stopped on a content filter")
        if choice.finish_reason == "length":
            raise NarrationUnavailable("the draft was truncated at max_tokens")
        refusal = getattr(choice.message, "refusal", None)
        if refusal:
            raise NarrationUnavailable(f"openai declined: {refusal}")

        return _nonempty(choice.message.content or "", "openai")

    return draft


# --------------------------------------------------------------------------
# Gemini.
# --------------------------------------------------------------------------


def _gemini_drafter(cfg: ModelConfig) -> Drafter:
    """Gemini, via the `google-genai` SDK.

    The system prompt is `system_instruction` rather than a first turn, so the
    instructions carry the authority the vendor gives them instead of arriving
    as something the user said.

    Credentials come from `GOOGLE_API_KEY` or `GEMINI_API_KEY`.
    """
    holder: dict[str, object] = {}

    def draft(system: str, user: str) -> str:
        if "client" not in holder:
            genai = _import("google.genai", "gemini")
            options = (
                {"http_options": genai.types.HttpOptions(base_url=cfg.base_url)}
                if cfg.base_url
                else {}
            )
            holder["client"] = genai.Client(**_key(cfg), **options)
            holder["types"] = genai.types

        types = holder["types"]
        response = holder["client"].models.generate_content(  # type: ignore[union-attr]
            model=cfg.model,
            contents=user,
            config=types.GenerateContentConfig(  # type: ignore[union-attr]
                system_instruction=system,
                max_output_tokens=cfg.max_tokens,
            ),
        )

        # Gemini reports a blocked prompt and a blocked answer in two different
        # places, and both come back as a successful call with no text.
        feedback = getattr(response, "prompt_feedback", None)
        blocked = getattr(feedback, "block_reason", None)
        if blocked:
            raise NarrationUnavailable(f"gemini blocked the prompt ({blocked})")

        candidates = getattr(response, "candidates", None) or []
        if candidates:
            finish = str(getattr(candidates[0], "finish_reason", "") or "")
            if "SAFETY" in finish.upper() or "BLOCK" in finish.upper():
                raise NarrationUnavailable(f"gemini stopped on {finish}")
            if "MAX_TOKENS" in finish.upper():
                raise NarrationUnavailable(
                    "the draft was truncated at max_output_tokens"
                )

        return _nonempty(response.text or "", "gemini")

    return draft
