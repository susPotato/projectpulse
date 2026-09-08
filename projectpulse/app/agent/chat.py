"""Multi-turn chat, via the same vendor already configured for narration.

Deliberately narrower than `narration/providers.py`: that module is built to
support three vendors interchangeably because narration is meant to be
provider-agnostic. Chat is a v0 - Gemini only, the one vendor actually
configured and verified this session (`GOOGLE_API_KEY`, `PULSE_NARRATION_
PROVIDER=gemini`). Extending to Anthropic/OpenAI later is the same shape of
change `providers.py` already demonstrates, not a redesign.

No tool use, no file access, no command execution. `pimsathon-main`'s agent
tooling exists to let an agent act on a machine safely, which needs a whole
sandbox/security layer (`security/`, `appcontainer_sandbox.py`) to be worth
building. This is a conversation, nothing more - the smallest version of
"more options for the user" that does not import that whole problem.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-flash"
MAX_OUTPUT_TOKENS = 4000

SYSTEM_PROMPT = """You are the assistant on ProjectPulseAI's Agent tab.

ProjectPulseAI's other pages (Insight, Schedule, Risk) show only what a
deterministic engine computed from real data - rules, dependency graphs and
arithmetic over dates a person typed. You are different and must say so when
it matters: you have not been given this project's live data, so you must
never invent specific figures, dates or task statuses as if you had looked
them up. If asked something only the Insight or Risk page could answer,
say that plainly and suggest checking there instead of guessing.

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


def gemini_chat(
    turns: list[ChatTurn],
    *,
    model: str = DEFAULT_MODEL,
    api_key: str | None = None,
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
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
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
