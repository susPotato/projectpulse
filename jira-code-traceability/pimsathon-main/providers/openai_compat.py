"""OpenAI-compatible provider (internal gateways, Azure OpenAI, LiteLLM, vLLM...).

Targets the ``POST {base_url}/chat/completions`` streaming endpoint with the
standard function-calling schema. Works with any server that speaks the OpenAI
Chat Completions API.
"""
from __future__ import annotations

import json
import threading
from typing import Any, Dict, List, Optional

import requests

from .base import (
    CancelFn, CancelWatchdog, MODEL_NOT_FOUND_HINT, Provider, ProviderError,
    TextCallback, ThinkStreamSplitter, ToolSpec,
)

_TIMEOUT = (5, 30)  # (connect, read) seconds — lower for faster Stop response
_MAX_RETRIES = 6      # auto-retry on rate-limit (429) up to this many times


class OpenAICompatProvider(Provider):
    name = "openai_compat"
    supports_vision = True

    def _url(self) -> str:
        base = str(self.conf.get("base_url", "")).rstrip("/")
        if not base:
            raise ProviderError("base_url is not configured for the OpenAI-compatible provider.")
        return f"{base}/chat/completions"

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.conf.get("api_key")
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    @staticmethod
    def _to_api_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "assistant" and m.get("tool_calls"):
                out.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": [{
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {}), ensure_ascii=False),
                        },
                    } for tc in m["tool_calls"]],
                })
            elif role == "tool":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                })
            else:
                content = m.get("content", "")
                if isinstance(content, list):
                    # Preview tab's region-selection → AI fix flow: a list of
                    # canonical content blocks (see providers/base.py docstring).
                    blocks = []
                    for block in content:
                        if block.get("type") == "image":
                            mime = block.get("mime", "image/png")
                            blocks.append({"type": "image_url", "image_url": {
                                "url": f"data:{mime};base64,{block.get('data', '')}",
                            }})
                        else:
                            blocks.append({"type": "text", "text": block.get("text", "")})
                    out.append({"role": role, "content": blocks})
                else:
                    out.append({"role": role, "content": content})
        return out

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        on_text: Optional[TextCallback] = None,
        cancel: Optional[CancelFn] = None,
        on_reasoning: Optional[TextCallback] = None,
    ) -> Dict[str, Any]:
        work = list(messages)   # local copy we can trim on context overflow
        payload: Dict[str, Any] = {"model": self.model, "stream": True}
        if tools:
            payload["tools"] = [t.to_openai() for t in tools]
            payload["tool_choice"] = "auto"

        text_parts: List[str] = []
        # Accumulate tool-call fragments keyed by streamed index.
        tool_acc: Dict[int, Dict[str, Any]] = {}
        usage_seen: Dict[str, Any] = {}   # final "usage" block, if the server sends one

        # Some gateways inline reasoning as <think>…</think> in the content stream
        # (rather than a separate reasoning_content field). Route that to
        # on_reasoning (→ "Thinking" indicator) and keep the answer bubble clean.
        def _emit_answer(t: str) -> None:
            text_parts.append(t)
            if on_text:
                on_text(t)

        splitter = ThinkStreamSplitter(on_text=_emit_answer, on_reasoning=on_reasoning)

        for attempt in range(1, _MAX_RETRIES + 2):
            payload["messages"] = self._to_api_messages(work)
            try:
                resp = self._request(
                    "POST", self._url(), headers=self._headers(), json=payload,
                    stream=True, timeout=_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise ProviderError(f"Could not reach the gateway: {exc}") from exc
            # requests/urllib3 falls back to Latin-1 for text/* responses whose
            # Content-Type omits an explicit charset (common for SSE streams) —
            # every non-ASCII UTF-8 byte pair then gets misread as two Latin-1
            # characters ("ô" → "Ã´"), corrupting every non-English reply. The
            # body is always UTF-8 JSON/SSE in practice, so force it explicitly
            # rather than trust the guess.
            resp.encoding = "utf-8"

            if resp.status_code >= 400:
                code = resp.status_code
                wait = self._retry_after(resp)
                err = self._error_text(resp)
                resp.close()
                # Rate limited (TPM/RPM) — wait the suggested time and retry.
                if code == 429 and attempt <= _MAX_RETRIES:
                    if self._wait_or_cancel(wait, cancel, on_text, attempt):
                        return _assemble_assistant(text_parts, tool_acc)  # cancelled
                    continue
                # Prompt too long — auto-compress and retry. First try dropping the
                # oldest turn; if there's nothing left to drop (e.g. the very first
                # message of a new conversation is itself oversized, typically from
                # a large attachment), shrink that message's own content instead of
                # giving up immediately.
                if code == 400 and attempt <= _MAX_RETRIES and self._is_context_overflow(err):
                    work, changed = self._drop_oldest_turn(work)
                    note = "\n✂ Lịch sử quá dài — tự nén bớt rồi thử lại…\n"
                    if not changed:
                        work, changed = self._shrink_last_message(work)
                        note = "\n✂ Tin nhắn/đính kèm quá dài cho model này — tự cắt bớt nội dung rồi thử lại…\n"
                    if changed:
                        if on_text:
                            on_text(note)
                        continue
                if self._is_context_overflow(err):
                    raise ProviderError(self._friendly_context_error(err))
                raise ProviderError(err)
            break  # 200 OK → stream the response below

        # Stream the body. A gateway/proxy can drop the connection mid-stream
        # ("Response ended prematurely" / connection reset): if nothing was
        # received yet, silently re-send the request a couple of times; if a
        # partial answer already streamed, keep it and just note the cut —
        # never surface the raw transport error over usable content.
        stream_retries = 0
        # If cancel is a threading.Event (new worker._stop_event), we can wait
        # on it with a timeout in parallel with the streaming read — this makes
        # Stop interrupt immediately even during LLM "thinking" silence.
        cancel_event: Optional[threading.Event] = None
        if isinstance(cancel, threading.Event):
            cancel_event = cancel
        elif hasattr(cancel, "is_set") and callable(getattr(cancel, "wait")):
            # Duck-type: anything with is_set() and wait() counts as Event-like
            cancel_event = cancel

        def _wait_cancel(ev: threading.Event, resp: requests.Response) -> None:
            """Block until cancel is set, then close the response to unblock iter_lines."""
            ev.wait()
            try:
                resp.close()
            except Exception:
                pass

        cancel_thread: Optional[threading.Thread] = None
        if cancel_event is not None:
            cancel_thread = threading.Thread(
                target=_wait_cancel, args=(cancel_event, resp), daemon=True)
            cancel_thread.start()

        while True:
            try:
                with CancelWatchdog(resp, cancel):
                    for raw in resp.iter_lines(decode_unicode=True):
                        if self._is_cancelled(cancel):
                            break
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[len("data:"):].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = chunk.get("choices") or []
                        if chunk.get("usage"):
                            usage_seen = chunk["usage"]
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        # Reasoning models (Qwen3, DeepSeek-R1, …) stream their private
                        # thinking in a separate field — surface it as "thinking" activity
                        # only, never as part of the answer.
                        rc = delta.get("reasoning_content") or delta.get("reasoning")
                        if rc and on_reasoning:
                            on_reasoning(rc)
                        piece = delta.get("content")
                        if piece:
                            splitter.feed(piece)   # splits inline <think>…</think> out of the answer
                        for tc in delta.get("tool_calls", []) or []:
                            idx = tc.get("index", 0)
                            slot = tool_acc.setdefault(idx, {"id": "", "name": "", "args": ""})
                            if tc.get("id"):
                                slot["id"] = tc["id"]
                            fn = tc.get("function", {})
                            if fn.get("name"):
                                slot["name"] = fn["name"]
                            if fn.get("arguments"):
                                slot["args"] += fn["arguments"]
                resp.close()
                break   # stream finished normally (or cancelled)
            except requests.RequestException as exc:
                resp.close()
                # If cancel was requested, close cleanly without retry
                if cancel_event is not None and cancel_event.is_set():
                    break
                if self._is_cancelled(cancel):
                    break
                if text_parts or tool_acc:
                    # Partial answer already on screen — keep it, note the cut.
                    if on_text:
                        on_text("\n⚠ Kết nối bị ngắt giữa chừng — hiển thị phần đã nhận được.\n")
                    break
                stream_retries += 1
                if stream_retries > 2:
                    raise ProviderError(
                        f"Kết nối tới gateway bị ngắt giữa chừng (đã thử lại {stream_retries - 1} lần): {exc}"
                    ) from exc
                if on_text:
                    on_text("\n⚠ Kết nối bị ngắt — đang thử lại…\n")
                try:
                    resp = self._request(
                        "POST", self._url(), headers=self._headers(), json=payload,
                        stream=True, timeout=_TIMEOUT,
                    )
                except requests.RequestException as exc2:
                    raise ProviderError(f"Could not reach the gateway: {exc2}") from exc2
                resp.encoding = "utf-8"   # same Latin-1-fallback fix as the initial request
                if resp.status_code >= 400:
                    err = self._error_text(resp)
                    resp.close()
                    raise ProviderError(err)

        splitter.flush()   # emit any held-back tail (partial tag / trailing text)
        self._record_usage(work, text_parts, tool_acc, usage_seen)
        return _assemble_assistant(text_parts, tool_acc)

    def _record_usage(self, messages, text_parts, tool_acc, usage_seen) -> None:
        """One Dashboard usage event per turn: real counts when the server's
        final chunk carried a "usage" block, a ~4 chars/token estimate
        otherwise. Never breaks the turn."""
        try:
            from ..core import usage_tracker as ut

            if usage_seen:
                ut.record(self.name, self.model,
                          usage_seen.get("prompt_tokens", 0),
                          usage_seen.get("completion_tokens", 0),
                          (usage_seen.get("prompt_tokens_details") or {}).get("cached_tokens", 0))
            else:
                sent = json.dumps(self._to_api_messages(messages), ensure_ascii=False)
                got = "".join(text_parts) + "".join(s["args"] for s in tool_acc.values())
                ut.record(self.name, self.model, ut.estimate_tokens(sent),
                          ut.estimate_tokens(got), 0, estimated=True)
        except Exception:  # noqa: BLE001
            pass

    def list_models(self):
        self.last_error = ""
        base = str(self.conf.get("base_url", "")).rstrip("/")
        if not base:
            self.last_error = "Base URL is not configured (Settings → OpenAI-compatible)."
            return []
        try:
            resp = self._request("GET", f"{base}/models", headers=self._headers(),
                                 timeout=(10, 30))
            if resp.status_code >= 400:
                self.last_error = self._error_text(resp)
                return []
            data = resp.json().get("data", [])
            ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
            if not ids:
                self.last_error = "Gateway responded but returned no models."
            return ids
        except requests.RequestException as exc:
            self.last_error = f"Could not reach the gateway: {exc}"
            return []
        except ValueError as exc:
            self.last_error = f"Gateway returned an invalid (non-JSON) response: {exc}"
            return []

    @staticmethod
    def _error_text(resp: requests.Response) -> str:
        try:
            body = resp.json()
            # Prefer the OpenAI-style {"error": {"message": ...}} shape; some
            # gateways instead return a FLAT body like {"message": "Not found",
            # "description": "...", "code": 404} — "description" is usually the
            # human-readable one there, so try it before falling back to the
            # generic top-level "message" (often just "Not found") or a raw dump.
            err_obj = body.get("error")
            msg = (
                (err_obj.get("message") if isinstance(err_obj, dict) else None)
                or body.get("description")
                or body.get("message")
                or json.dumps(body)
            )
        except ValueError:
            msg = resp.text[:300]
        text = f"Gateway error {resp.status_code}: {msg}"
        if resp.status_code == 404 and "model" in msg.lower():
            # A model-not-found/unavailable response — this is recoverable by
            # just picking a different model, not a real outage. Say so
            # explicitly so the user doesn't read it as the app being broken.
            text += MODEL_NOT_FOUND_HINT
        return text


def _assemble_assistant(text_parts: List[str], tool_acc: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    tool_calls: List[Dict[str, Any]] = []
    for idx in sorted(tool_acc):
        slot = tool_acc[idx]
        if not slot["name"]:
            continue
        try:
            args = json.loads(slot["args"]) if slot["args"].strip() else {}
        except json.JSONDecodeError:
            args = {"_raw": slot["args"]}
        tool_calls.append({
            "id": slot["id"] or f"call_{idx}",
            "name": slot["name"],
            "arguments": args,
        })
    return {
        "role": "assistant",
        "content": Provider.strip_think("".join(text_parts)),
        "tool_calls": tool_calls,
    }
