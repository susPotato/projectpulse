"""Provider base classes and the canonical message/tool model.

Canonical message shapes (provider-agnostic)::

    {"role": "system",    "content": "..."}
    {"role": "user",      "content": "..."}
    {"role": "assistant", "content": "...", "tool_calls": [ToolCall, ...]}
    {"role": "tool",      "tool_call_id": "...", "name": "...", "content": "..."}

A ToolCall is ``{"id": str, "name": str, "arguments": dict}``.

A user/assistant message's ``content`` is USUALLY a plain string, but MAY
instead be a list of content blocks when an image is attached (Preview tab's
region-selection → AI fix flow is the only caller today)::

    {"role": "user", "content": [
        {"type": "text", "text": "..."},
        {"type": "image", "data": "<base64>", "mime": "image/png"},
    ]}

Build the image block with :func:`image_content_block`. Each provider's
``chat()`` translates a list ``content`` into its own wire format (Anthropic's
``source.base64`` blocks / OpenAI's ``image_url`` data-URI blocks) — see
``_split``/``_to_api_messages`` in ``anthropic.py``/``openai_compat.py``.
Only providers with ``supports_vision = True`` should be sent one.
"""
from __future__ import annotations

import base64
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


def image_content_block(image_bytes: bytes, mime: str = "image/png") -> Dict[str, Any]:
    """The canonical image content block (see module docstring) for
    ``image_bytes`` — base64-encodes once here so every call site/provider
    shares the exact same encoding."""
    return {"type": "image", "data": base64.b64encode(image_bytes).decode("ascii"), "mime": mime}


_MAX_RETRIES = 6  # auto-retry on rate-limit (429) up to this many times

# Appended to a gateway's "model not found/unavailable" error (see
# openai_compat.py::_error_text) — recoverable by picking a different model,
# not a real outage. ui/chat_panel.py checks for this exact marker to decide
# whether to restore the user's typed message into the composer so they can
# just switch model and resend instead of retyping the whole prompt.
MODEL_NOT_FOUND_HINT = "\n→ Hãy chọn model khác trong ⚙ Settings rồi gửi lại tin nhắn."


def is_model_not_found_error(err: str) -> bool:
    return MODEL_NOT_FOUND_HINT in (err or "")


# Callback invoked with each streamed text fragment.
TextCallback = Callable[[str], None]
# Returns True when the caller wants to abort the in-flight request.
CancelFn = Callable[[], bool]


class CancelWatchdog:
    """Closes a streaming response as soon as ``cancel()`` reports True.

    ``resp.iter_lines()`` only gets a chance to check ``cancel()`` between
    chunks actually received from the socket — if the server goes quiet
    (e.g. "thinking" with no bytes sent yet), that blocking read can't be
    pre-empted from outside and Stop has no visible effect until the next
    byte arrives or the read timeout elapses (up to 600s). This runs a
    lightweight poller (same 0.2s-poll style as ``deps.run_cancellable``'s
    subprocess cancellation) alongside the blocking read and force-closes
    the response the moment cancellation is requested, which unblocks
    ``iter_lines()`` with a ``requests.RequestException`` the caller already
    treats as a cancelled stream."""

    def __init__(self, resp, cancel: Optional[CancelFn], poll_secs: float = 0.15):
        self._resp = resp
        self._cancel = cancel
        self._poll_secs = poll_secs
        self._done = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def __enter__(self) -> "CancelWatchdog":
        if self._cancel is not None:
            self._thread = threading.Thread(target=self._watch, daemon=True)
            self._thread.start()
        return self

    def _watch(self) -> None:
        while not self._done.is_set():
            # Support both Callable and threading.Event
            if hasattr(self._cancel, "is_set"):
                cancelled = self._cancel.is_set()
            else:
                cancelled = bool(self._cancel())
            if cancelled:
                try:
                    self._resp.close()
                except Exception:  # noqa: BLE001 — best-effort, never crash the watchdog
                    pass
                return
            self._done.wait(self._poll_secs)

    def __exit__(self, *exc_info) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


# Reasoning models (Qwen3, DeepSeek-R1, …) stream their private "thinking" apart
# from the answer. This matches an inline <think>…</think> block so we can drop it
# from the visible answer when a server inlines it into the content stream.
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL | re.IGNORECASE)


class ThinkStreamSplitter:
    """Splits a *streamed* content string into answer text and reasoning text.

    Many OpenAI-compatible gateways inline a reasoning model's thoughts as a
    ``<think>…</think>`` block right inside the ``content`` stream (instead of a
    separate ``reasoning_content`` field). Feeding every chunk through this
    splitter routes the text inside ``<think>…</think>`` to ``on_reasoning`` (so the
    UI shows a "Thinking" indicator) and everything else to ``on_text`` (the visible
    answer). Tags that straddle chunk boundaries are handled by holding back a small
    tail until the next chunk arrives; call :meth:`flush` when the stream ends."""

    _OPEN = "<think>"
    _CLOSE = "</think>"

    def __init__(self, on_text=None, on_reasoning=None):
        self._on_text = on_text
        self._on_reasoning = on_reasoning
        self._buf = ""
        self._in_think = False

    def feed(self, piece: str) -> None:
        if not piece:
            return
        self._buf += piece
        self._drain()

    def flush(self) -> None:
        if self._buf:
            self._emit(self._buf)
            self._buf = ""

    # -- internals -----------------------------------------------------
    def _emit(self, text: str) -> None:
        if not text:
            return
        cb = self._on_reasoning if self._in_think else self._on_text
        if cb:
            cb(text)

    def _partial_tail(self, tag: str) -> int:
        """How many trailing chars of the buffer could be the start of ``tag``
        (so we hold them back rather than emit a half-written tag)."""
        for k in range(min(len(tag) - 1, len(self._buf)), 0, -1):
            if self._buf[-k:].lower() == tag[:k].lower():
                return k
        return 0

    def _drain(self) -> None:
        while self._buf:
            tag = self._CLOSE if self._in_think else self._OPEN
            idx = self._buf.lower().find(tag)
            if idx == -1:
                keep = self._partial_tail(tag)
                cut = len(self._buf) - keep
                if cut > 0:
                    self._emit(self._buf[:cut])
                    self._buf = self._buf[cut:]
                return
            self._emit(self._buf[:idx])
            self._buf = self._buf[idx + len(tag):]
            self._in_think = not self._in_think


class ProviderError(RuntimeError):
    """Raised for any provider/transport failure (network, auth, bad status)."""


@dataclass
class ToolSpec:
    """A tool the model may call. ``parameters`` is a JSON-Schema object."""

    name: str
    description: str
    parameters: Dict[str, Any]

    def to_openai(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


class Provider:
    """Abstract provider. Subclasses implement :meth:`chat`."""

    name = "base"
    # Can this provider's chat() accept a list-of-blocks `content` (image
    # attached)? False by default — a provider must opt in once it actually
    # translates the block shape in its own request-building code.
    supports_vision = False

    def __init__(self, conf: Dict[str, Any]):
        self.conf = conf
        self.model = conf.get("model", "")
        # Set by list_models() on failure (network/auth/bad-response) instead of
        # silently swallowing the error — Settings' "Test connection" / "Load
        # models" surfaces this so "model won't load" has a concrete reason.
        self.last_error = ""

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        on_text: Optional[TextCallback] = None,
        cancel: Optional[CancelFn] = None,
        on_reasoning: Optional[TextCallback] = None,
    ) -> Dict[str, Any]:
        """Run one turn.

        Streams answer fragments via ``on_text`` and (for reasoning models) private
        "thinking" fragments via ``on_reasoning`` — the caller uses the latter only
        to show a live "thinking" indicator, never as part of the answer. Returns the
        canonical assistant message ``{"role": "assistant", "content": str,
        "tool_calls": [...]}``.
        """
        raise NotImplementedError

    @staticmethod
    def strip_think(text: str) -> str:
        """Remove any inline ``<think>…</think>`` block from a final answer — a
        safety net for servers that fold reasoning into the content stream instead
        of a separate reasoning field."""
        if not text or "<think>" not in text.lower():
            return text
        return _THINK_BLOCK.sub("", text).strip()

    def list_models(self) -> List[str]:
        """Return available model ids for this provider (empty if unsupported).
        On failure, subclasses set ``self.last_error`` with a human-readable
        reason instead of just returning ``[]``."""
        return []

    def test_connection(self) -> "tuple[bool, str]":
        """Best-effort connectivity check for Settings' 'Test connection'
        button — calls list_models() and turns the result into a message the
        user can actually act on (vs. a silent empty model list).

        Checked ``last_error`` FIRST, even when models is non-empty: some
        providers (Anthropic) return a built-in fallback list on failure, so a
        non-empty result alone doesn't prove the connection actually worked."""
        self.last_error = ""
        models = self.list_models()
        if self.last_error:
            return False, self.last_error
        if models:
            return True, f"OK — {len(models)} model(s) available."
        return False, "No models returned. Check base_url/API key and network access."

    # -- shared helpers ------------------------------------------------
    @staticmethod
    def _is_cancelled(cancel) -> bool:
        """Check cancel — supports both Callable and threading.Event (immediate)."""
        if cancel is None:
            return False
        # threading.Event or anything with is_set() — O(1), no function call overhead
        if hasattr(cancel, "is_set"):
            return cancel.is_set()
        # Legacy callable (worker.is_cancelled bound method)
        try:
            return bool(cancel())
        except Exception:
            return False

    @staticmethod
    def _retry_after(resp) -> int:
        """Seconds to wait before retrying a 429 — from the Retry-After header or
        a 'try again in Ns' hint in the body; capped to keep the UI responsive."""
        ra = getattr(resp, "headers", {}).get("Retry-After")
        if ra:
            try:
                return min(120, max(1, int(float(ra))))
            except ValueError:
                pass
        try:
            m = re.search(r"in\s+(\d+)\s*s", resp.text)
            if m:
                return min(120, max(1, int(m.group(1))))
        except Exception:  # noqa: BLE001
            pass
        return 20

    def _wait_or_cancel(self, seconds: int, cancel, on_text, attempt: int) -> bool:
        """Sleep ``seconds`` in small steps (so Stop works). Returns True if the
        user cancelled during the wait."""
        if on_text:
            on_text(f"\n⏳ Rate limit — waiting {seconds}s, then retrying (attempt {attempt})…\n")
        for _ in range(max(1, seconds * 2)):
            if self._is_cancelled(cancel):
                return True
            time.sleep(0.5)
        return False

    @staticmethod
    def _is_context_overflow(text: str) -> bool:
        """True when an error means the prompt exceeded the model context window."""
        t = (text or "").lower()
        return any(s in t for s in (
            "context length", "context window", "maximum context", "context_length_exceeded",
            "input tokens", "reduce the length", "too many tokens", "maximum_tokens",
            "max_tokens", "prompt is too long",
        ))

    @staticmethod
    def _drop_oldest_turn(messages: List[Dict[str, Any]]):
        """Drop the oldest complete user→(assistant/tool) turn, keeping leading
        system messages. Returns ``(new_messages, changed)``."""
        n = len(messages)
        i = 0
        while i < n and messages[i].get("role") == "system":
            i += 1
        if i >= n:
            return messages, False
        j = i + 1
        while j < n and messages[j].get("role") != "user":
            j += 1
        if j >= n:
            return messages, False  # only one turn left — can't trim further
        return messages[:i] + messages[j:], True

    # Below this, a message's own content is truncated rather than dropped —
    # so shrinking never removes a whole turn's worth of context, only shaves
    # the oversized one down.
    _MIN_SHRINKABLE_CHARS = 2000

    @classmethod
    def _shrink_last_message(cls, messages: List[Dict[str, Any]]):
        """Cut the last message's own text content in half.

        ``_drop_oldest_turn`` can't help when the overflow is inside a single
        turn — most commonly the very first message of a new conversation,
        oversized because a large file attachment's extracted text got
        embedded directly into that message's content. Without this, such a
        turn can NEVER be auto-compacted (there is nothing "older" to drop)
        and the raw gateway error would surface to the user every time.
        Returns ``(new_messages, changed)``."""
        if not messages:
            return messages, False
        last = messages[-1]
        content = last.get("content")
        if not isinstance(content, str) or len(content) < cls._MIN_SHRINKABLE_CHARS:
            return messages, False  # nothing left worth shrinking
        half = len(content) // 2
        trimmed = content[:half] + "\n\n…(nội dung đã bị cắt bớt tự động vì quá dài cho model này)…"
        new_last = dict(last)
        new_last["content"] = trimmed
        return messages[:-1] + [new_last], True

    @staticmethod
    def _friendly_context_error(err: str) -> str:
        return (
            "Nội dung quá dài cho model này ngay cả sau khi tự nén lịch sử/cắt bớt "
            "tin nhắn. Hãy xoá bớt file đính kèm, chia nhỏ yêu cầu, hoặc đổi sang một "
            f"model có context lớn hơn.\n\n{err}"
        )

    def describe(self) -> str:
        return f"{self.name}:{self.model}"

    # -- TLS: auto-recover from a self-signed/internal-CA gateway ------
    def _request(self, method: str, url: str, **kwargs):
        """Like ``requests.post``/``requests.get`` (dispatched by ``method``),
        with one difference: if the gateway presents a self-signed/internal
        certificate that fails normal verification, this transparently
        captures and pins that EXACT certificate (trust on first use) and
        retries once — instead of making the user hunt down a .pem file in
        Settings. Skipped when an explicit CA bundle is already configured,
        since that is a deliberate choice.

        Dispatches via ``requests.<method>`` (not ``requests.request``) so
        tests/callers that patch ``requests.post``/``requests.get`` directly
        keep working."""
        from ..core import tls_trust

        return tls_trust.request(method, url, ca_bundle=self.conf.get("ca_bundle"), **kwargs)