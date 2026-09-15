"""Anthropic Claude provider (Messages API, streaming)."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import requests

from .base import CancelFn, CancelWatchdog, Provider, ProviderError, TextCallback, ToolSpec

_TIMEOUT = (15, 600)
_ANTHROPIC_VERSION = "2023-06-01"
_MAX_TOKENS = 4096
_MAX_RETRIES = 6  # auto-retry on rate-limit (429) / overloaded


class AnthropicProvider(Provider):
    name = "anthropic"
    supports_vision = True

    def _url(self) -> str:
        base = str(self.conf.get("base_url") or "https://api.anthropic.com").rstrip("/")
        return f"{base}/v1/messages"

    def _headers(self) -> Dict[str, str]:
        key = self.conf.get("api_key")
        if not key:
            raise ProviderError("Anthropic API key is not configured.")
        return {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }

    _FALLBACK_MODELS = ["claude-opus-4-8", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]

    def list_models(self):
        self.last_error = ""
        base = str(self.conf.get("base_url") or "https://api.anthropic.com").rstrip("/")
        try:
            resp = self._request("GET", f"{base}/v1/models", headers=self._headers(),
                                 timeout=(10, 30))
            if resp.status_code < 400:
                data = resp.json().get("data", [])
                ids = [m.get("id") for m in data if isinstance(m, dict) and m.get("id")]
                if ids:
                    return ids
                self.last_error = "Anthropic API responded but returned no models — using the built-in fallback list."
            else:
                self.last_error = f"Anthropic API error {resp.status_code}: {resp.text[:200]}"
        except ProviderError as exc:
            self.last_error = str(exc)
        except requests.RequestException as exc:
            self.last_error = f"Could not reach the Anthropic API: {exc}"
        except ValueError as exc:
            self.last_error = f"Anthropic API returned an invalid (non-JSON) response: {exc}"
        return list(self._FALLBACK_MODELS)

    @staticmethod
    def _split(messages: List[Dict[str, Any]]):
        system_parts: List[str] = []
        api: List[Dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                if m.get("content"):
                    system_parts.append(m["content"])
            elif role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id", ""),
                    "content": m.get("content", ""),
                }
                if api and api[-1]["role"] == "user" and api[-1].get("_tool"):
                    api[-1]["content"].append(block)
                else:
                    api.append({"role": "user", "content": [block], "_tool": True})
            elif role == "assistant":
                blocks: List[Dict[str, Any]] = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []) or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": tc["name"],
                        "input": tc.get("arguments", {}),
                    })
                api.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            else:  # user
                content = m.get("content", "")
                if isinstance(content, list):
                    # Preview tab's region-selection → AI fix flow: a list of
                    # canonical content blocks (see providers/base.py docstring).
                    blocks = []
                    for block in content:
                        if block.get("type") == "image":
                            blocks.append({"type": "image", "source": {
                                "type": "base64",
                                "media_type": block.get("mime", "image/png"),
                                "data": block.get("data", ""),
                            }})
                        else:
                            blocks.append({"type": "text", "text": block.get("text", "")})
                    api.append({"role": "user", "content": blocks})
                else:
                    api.append({"role": "user", "content": [{"type": "text", "text": content}]})
        for msg in api:
            msg.pop("_tool", None)
        return "\n\n".join(system_parts), api

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[ToolSpec]] = None,
        on_text: Optional[TextCallback] = None,
        cancel: Optional[CancelFn] = None,
        on_reasoning: Optional[TextCallback] = None,
    ) -> Dict[str, Any]:
        work = list(messages)   # local copy we can trim on context overflow
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": _MAX_TOKENS,
            "stream": True,
        }
        if tools:
            tool_defs = [t.to_anthropic() for t in tools]
            # Prompt caching: mark the end of the (large, stable) tool list so
            # Anthropic caches the whole tools+system prefix and reuses it across
            # the many turns of one agent loop. Only the growing message tail
            # changes each turn, so this turns most of the per-turn input into a
            # cache read (~10% the cost + far lower latency). Unsupported prefixes
            # simply aren't cached — no error — so this is safe on any gateway.
            tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}
            payload["tools"] = tool_defs

        text_parts: List[str] = []
        # Per content-block scratch for tool_use assembly.
        blocks: Dict[int, Dict[str, Any]] = {}
        usage_seen: Dict[str, Any] = {}   # real token counts from stream events

        for attempt in range(1, _MAX_RETRIES + 2):
            system, api_messages = self._split(work)
            payload["messages"] = api_messages
            if system:
                # Structured system block + cache_control so the (large, stable)
                # system prompt — tool guide, skills, security rules — is cached
                # and reused across the agent loop instead of re-sent every turn.
                payload["system"] = [{
                    "type": "text", "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                payload.pop("system", None)
            try:
                resp = self._request(
                    "POST", self._url(), headers=self._headers(), json=payload,
                    stream=True, timeout=_TIMEOUT,
                )
            except requests.RequestException as exc:
                raise ProviderError(f"Could not reach the Anthropic API: {exc}") from exc
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
                # Rate limited / overloaded — wait and retry instead of failing.
                if code in (429, 529) and attempt <= _MAX_RETRIES:
                    if self._wait_or_cancel(wait, cancel, on_text, attempt):
                        return {"role": "assistant", "content": "", "tool_calls": []}
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
            break  # 200 OK → stream below

        # Stream the body — same mid-stream drop handling as the OpenAI
        # provider: retry silently when nothing arrived yet, keep a partial
        # answer with a note instead of surfacing the raw transport error.
        stream_retries = 0
        while True:
            try:
                with CancelWatchdog(resp, cancel):
                    for raw in resp.iter_lines(decode_unicode=True):
                        if self._is_cancelled(cancel):
                            break
                        if not raw or not raw.startswith("data:"):
                            continue
                        data = raw[len("data:"):].strip()
                        if not data:
                            continue
                        try:
                            evt = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        etype = evt.get("type")
                        if etype == "message_start":
                            u = (evt.get("message") or {}).get("usage") or {}
                            usage_seen["in"] = u.get("input_tokens", 0)
                            usage_seen["cache"] = u.get("cache_read_input_tokens", 0)
                        elif etype == "message_delta":
                            u = evt.get("usage") or {}
                            if u.get("output_tokens"):
                                usage_seen["out"] = u["output_tokens"]
                        if etype == "content_block_start":
                            idx = evt.get("index", 0)
                            cb = evt.get("content_block", {})
                            if cb.get("type") == "tool_use":
                                blocks[idx] = {"id": cb.get("id", ""), "name": cb.get("name", ""), "json": ""}
                        elif etype == "content_block_delta":
                            idx = evt.get("index", 0)
                            delta = evt.get("delta", {})
                            if delta.get("type") == "text_delta":
                                piece = delta.get("text", "")
                                if piece:
                                    text_parts.append(piece)
                                    if on_text:
                                        on_text(piece)
                            elif delta.get("type") == "thinking_delta":
                                # Extended-thinking reasoning — activity only, not the answer.
                                if on_reasoning and delta.get("thinking"):
                                    on_reasoning(delta["thinking"])
                            elif delta.get("type") == "input_json_delta" and idx in blocks:
                                blocks[idx]["json"] += delta.get("partial_json", "")
                        elif etype == "message_stop":
                            break
                        elif etype == "error":
                            raise ProviderError(f"Anthropic: {evt.get('error', {}).get('message', 'error')}")
                resp.close()
                break   # stream finished normally (or cancelled)
            except requests.RequestException as exc:
                resp.close()
                if self._is_cancelled(cancel):
                    break
                if text_parts or blocks:
                    if on_text:
                        on_text("\n⚠ Kết nối bị ngắt giữa chừng — hiển thị phần đã nhận được.\n")
                    break
                stream_retries += 1
                if stream_retries > 2:
                    raise ProviderError(
                        f"Kết nối tới Anthropic bị ngắt giữa chừng (đã thử lại {stream_retries - 1} lần): {exc}"
                    ) from exc
                if on_text:
                    on_text("\n⚠ Kết nối bị ngắt — đang thử lại…\n")
                system, api_messages = self._split(work)
                payload["messages"] = api_messages
                if system:
                    payload["system"] = system
                try:
                    resp = self._request(
                        "POST", self._url(), headers=self._headers(), json=payload,
                        stream=True, timeout=_TIMEOUT,
                    )
                except requests.RequestException as exc2:
                    raise ProviderError(f"Could not reach the Anthropic API: {exc2}") from exc2
                resp.encoding = "utf-8"   # same Latin-1-fallback fix as the initial request
                if resp.status_code >= 400:
                    err = self._error_text(resp)
                    resp.close()
                    raise ProviderError(err)

        tool_calls: List[Dict[str, Any]] = []
        for idx in sorted(blocks):
            b = blocks[idx]
            try:
                args = json.loads(b["json"]) if b["json"].strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": b["json"]}
            tool_calls.append({"id": b["id"], "name": b["name"], "arguments": args})

        # Dashboard usage event — real counts from the stream's usage events,
        # else a ~4 chars/token estimate. Never breaks the turn.
        try:
            from ..core import usage_tracker as ut

            if usage_seen:
                ut.record(self.name, self.model, usage_seen.get("in", 0),
                          usage_seen.get("out", 0), usage_seen.get("cache", 0))
            else:
                sent = json.dumps(payload.get("messages", []), ensure_ascii=False)
                got = "".join(text_parts) + "".join(b["json"] for b in blocks.values())
                ut.record(self.name, self.model, ut.estimate_tokens(sent),
                          ut.estimate_tokens(got), 0, estimated=True)
        except Exception:  # noqa: BLE001
            pass

        return {"role": "assistant", "content": "".join(text_parts), "tool_calls": tool_calls}

    @staticmethod
    def _error_text(resp: requests.Response) -> str:
        try:
            body = resp.json()
            msg = body.get("error", {}).get("message") or json.dumps(body)
        except ValueError:
            msg = resp.text[:300]
        return f"Anthropic error {resp.status_code}: {msg}"
