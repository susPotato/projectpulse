"""Multi-turn chat, via whichever vendor narration is already configured with.

Used to be Gemini only - the one vendor actually installed when this feature
was first built. That stopped being true the moment the project switched to
Anthropic (`PULSE_NARRATION_PROVIDER=anthropic`) and the Dockerfile followed
suit: every reply then failed with "the 'google-genai' package is not
installed", correctly reported (`ChatUnavailable`, never a 500) but wrong -
the configured vendor was never even tried. `chat()` now dispatches on the
same provider name `narration/providers.py` already reads, so "which vendor
answers /insight" and "which vendor answers the Agent tab" stop being able
to disagree.

Still deliberately narrower than `narration/providers.py`'s `Drafter`
abstraction: that shape is one system prompt plus one user turn, built for
narrating a fixed brief. A chat is a growing list of turns, which is a
materially different call shape per SDK, so this is its own small adapter
per vendor rather than a shoehorned reuse.

No tool use, no file access, no command execution - see the Agent tab's own
banner. This is a conversation, nothing more.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "anthropic": "claude-opus-5",
    "gemini": "gemini-3.8-flash",
    "openai": "gpt-5.6-terra",
    "fpt": "gemma-4-31B-it",
}
MAX_OUTPUT_TOKENS = 4000

SYSTEM_PROMPT = """You are the assistant on ProjectPulseAI's Agent tab.

ProjectPulseAI's other pages (Insight, Schedule, Risk) show only what a
deterministic engine computed from real data - rules, dependency graphs and
arithmetic over dates a person typed.

When a PROJECT BRIEF block appears below, it is that same computed snapshot,
for the project the user is looking at. Answer from it directly: name the
tasks, quote the dates and counts it gives you. Two rules:

- Quote, never recompute. Every figure in the brief was produced by the same
  engine that drew the pages beside you, so repeating one keeps you consistent
  with them and working one out yourself does not.
- The brief's "WHAT THIS PROJECT'S DATA CANNOT SUPPORT" section is binding. If
  it says there are no dependency edges, then this project has no critical
  path and no blockers to describe, however reasonable a chain would sound.
  Say the data does not carry it.

If a question needs something the brief does not contain, say so plainly
instead of estimating. Never invent a figure, date or status.

With no PROJECT BRIEF below, you have not been given any project data - say so
if asked something only the Insight or Risk page could answer.

Be concise and direct. You may discuss project management, general
questions, or anything else the user asks.

When a message includes a block marked "[Content fetched from <url>]", that
text was fetched from the link automatically - use it to answer, and if the
block instead says the fetch failed or is unsupported, say so plainly rather
than guessing at what the page might contain."""


class ChatUnavailable(RuntimeError):
    """The model could not be reached, or answered with nothing usable."""


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "user" or "assistant"
    content: str


def chat(
    turns: list[ChatTurn],
    *,
    provider: str,
    model: str = "",
    api_key: str | None = None,
    base_url: str | None = None,
    context: str | None = None,
) -> str:
    """One reply, given the whole conversation so far - dispatched to
    whichever vendor `provider` names, the same set `narration/providers.py`
    supports. Stateless on purpose, same reasoning as before: the client
    resends the transcript each turn, correct across Fly's multiple/
    ephemeral machines the way a stateful in-memory session would not be.
    """
    name = (provider or "").strip().lower()
    resolved_model = model or DEFAULT_MODELS.get(name, "")

    #: The brief goes in the *system* prompt, not into a user turn. A user turn
    #: is something the person said, and a transcript that quietly puts words in
    #: their mouth makes "why did it say that?" unanswerable - and on the next
    #: turn the client would resend it as though they had typed it.
    system = SYSTEM_PROMPT
    if context:
        system = "\n".join(
            [SYSTEM_PROMPT, "", "--- PROJECT BRIEF ---", context, "--- end of brief ---"]
        )

    if name == "anthropic":
        return _anthropic_chat(
            turns, model=resolved_model, api_key=api_key, base_url=base_url, system=system
        )
    if name == "gemini":
        return _gemini_chat(turns, model=resolved_model, api_key=api_key, system=system)
    raise ChatUnavailable(
        f"chat is not wired up for {provider!r} yet - only anthropic and "
        "gemini support the Agent tab's free-form chat today. Switch "
        "PULSE_NARRATION_PROVIDER, or ask on Insight/Risk instead."
    )


def _anthropic_chat(
    turns: list[ChatTurn],
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    system: str = SYSTEM_PROMPT,
) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise ChatUnavailable(
            'the \'anthropic\' package is not installed; pip install -e ".[llm]"'
        ) from exc

    try:
        client = anthropic.Anthropic(
            **({"api_key": api_key} if api_key else {}),
            **({"base_url": base_url} if base_url else {}),
        )
        response = client.messages.create(
            model=model or DEFAULT_MODELS["anthropic"],
            max_tokens=MAX_OUTPUT_TOKENS,
            system=system,
            messages=[
                {"role": "assistant" if t.role == "assistant" else "user", "content": t.content}
                for t in turns
            ],
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a downgrade
        raise ChatUnavailable(f"{type(exc).__name__}: {exc}") from exc

    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "category", None)
        raise ChatUnavailable(f"claude declined ({detail})")

    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise ChatUnavailable("claude returned an empty reply")
    return text


def _gemini_chat(
    turns: list[ChatTurn],
    *,
    model: str,
    api_key: str | None,
    system: str = SYSTEM_PROMPT,
) -> str:
    """One reply, given the whole conversation so far.

    Stateless on purpose - the caller (the API route) holds no server-side
    session, and the client resends the transcript each turn. Simple, and
    correct across Fly's multiple/ephemeral machines the same way a stateful
    in-memory session would not be (the lesson already applied to the
    narration cache).
    """
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ChatUnavailable(
            "the 'google-genai' package is not installed; "
            'pip install -e ".[llm-gemini]"'
        ) from exc

    try:
        # Client construction itself can fail here - e.g. no API key found in
        # the argument or the environment - so it has to be inside the same
        # fence as the call, not before it.
        client = genai.Client(**({"api_key": api_key} if api_key else {}))
        contents = [
            types.Content(
                role="model" if turn.role == "assistant" else "user",
                parts=[types.Part(text=turn.content)],
            )
            for turn in turns
        ]
        response = client.models.generate_content(
            model=model or DEFAULT_MODELS["gemini"],
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_OUTPUT_TOKENS,
            ),
        )
    except Exception as exc:  # noqa: BLE001 - any failure is a downgrade
        raise ChatUnavailable(f"{type(exc).__name__}: {exc}") from exc

    feedback = getattr(response, "prompt_feedback", None)
    blocked = getattr(feedback, "block_reason", None)
    if blocked:
        raise ChatUnavailable(f"gemini blocked the prompt ({blocked})")

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise ChatUnavailable("gemini returned an empty reply")
    return text
