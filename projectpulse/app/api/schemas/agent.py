"""The Agent chat contract - free-form, unlike every other bundle here.

No evidence, no facts, no `{{token}}` substitution: this is the one surface
in the API with none of `insight.py`'s guarantees, and its shape says so by
being this much smaller than every other schema in this package.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.api.schemas.base import Response

Role = Literal["user", "assistant"]


class ChatMessage(Response):
    role: Role
    content: str


class ChatRequest(Response):
    #: The whole conversation so far, oldest first. Stateless server-side -
    #: see `app/agent/chat.py`.
    messages: list[ChatMessage] = Field(default_factory=list)
    #: Which project the question is about. Empty means none, and the chat then
    #: has no project data at all - which it says rather than guessing.
    #:
    #: Sent on every turn rather than captured at the start of the
    #: conversation, for the same reason the transcript is: nothing is held
    #: server-side, and a person who switches project mid-chat means the next
    #: question is about the new one.
    project: str = ""


class ChatResponse(Response):
    reply: str = ""
    #: The project whose brief was in scope for this reply, as its name - so the
    #: page can show what the answer was grounded in. Empty when none was, which
    #: is a materially different conversation and must not look the same.
    grounded_in: str = ""
    #: False when the model could not be reached - `error` then says why, the
    #: same "downgrade must be visible" rule `narration_fallback_reason` follows.
    ok: bool = True
    error: str | None = None
