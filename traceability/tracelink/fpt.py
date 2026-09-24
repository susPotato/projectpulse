"""The open models on FPT's AI gateway, as a second backend for every model stage.

Addressed as `--model fpt:<name>` (or `TRACELINK_MODEL=fpt:<name>`), so the
model string alone says where a call goes and the cache key, which already
includes the model, keeps the two vendors' answers apart for free.

**Why this is not just "call it and parse the JSON".** The Anthropic path gets
a schema the decoder is constrained to; an open model behind an OpenAI-shaped
gateway does not, and the gateway rate-limits per model. The client makes up
for both. The *prompt* is Claude's SYSTEM, unchanged - see `_call_fpt` in
`adjudicate.py` for the measurement behind leaving it alone.

- the schema is written into the system prompt, the reply is validated with
  the same pydantic model, and a reply that fails is sent back **once** with
  the validation error - the commonest failure is a stray fence or a missing
  field, which one repair fixes;
- a 429 from the gateway's per-model tokens-per-minute limit (500k) is
  waited out and retried, because six adjudicate workers on a small model
  reach it and would otherwise fail tickets that cost nothing to redo;
- `<think>` blocks some reasoning models emit inline are stripped before
  parsing rather than mistaken for the answer.

Standard library only, like the app's own FPT adapter: nothing to install,
and the gateway's `{"code", "message", "data"}` envelope is unwrapped
tolerantly because it proxies several upstreams.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

PREFIX = "fpt:"
BASE_URL = "https://token-api.fpt.ai/v1"
KEY_ENV = "FPT_API_KEY"

#: Reasoning models on the gateway bill their thinking inside max_tokens, and a
#: budget sized to the answer returns "truncated" before a word of it - the
#: app's probe found this at 120 tokens and it holds here at 4000.
MIN_TOKENS = 8000
TIMEOUT = 300.0
#: The gateway sits behind Cloudflare, which answers a default urllib UA with
#: 403 "error code: 1010" (found by the app's adapter on its first call).
USER_AGENT = "tracelink/1 (+projectpulse)"
RATE_LIMIT_RETRIES = 3


class GatewayError(RuntimeError):
    pass


def is_fpt(model: str) -> bool:
    return model.startswith(PREFIX)


def name(model: str) -> str:
    return model[len(PREFIX):] if is_fpt(model) else model


def key_present() -> bool:
    return bool(os.environ.get(KEY_ENV))


def contract(output_format) -> str:
    """The schema, stated in the prompt because nothing enforces it in decoding."""
    schema = json.dumps(output_format.model_json_schema(), ensure_ascii=False)
    return ("\n\n## Output\nReply with exactly ONE JSON object and nothing else - no prose "
            "before or after it, no code fence. Fill every required field. It must "
            "validate against this JSON schema:\n" + schema)


def chat(model: str, messages: list[dict], max_tokens: int) -> tuple[str, dict]:
    """One completion. Returns (text, usage). Raises GatewayError on any failure."""
    key = os.environ.get(KEY_ENV, "")
    if not key:
        raise GatewayError(f"no FPT credential; set {KEY_ENV}")
    base = (os.environ.get("FPT_BASE_URL") or BASE_URL).rstrip("/")
    body = json.dumps({
        "model": name(model),
        "messages": messages,
        "max_tokens": max(max_tokens, MIN_TOKENS),
        # Low, not zero: a verdict should not change between two runs of the
        # same prompt, but some gateway models loop at exactly 0.
        "temperature": 0.1,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
        "User-Agent": USER_AGENT,
    })
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read()[:300].decode("utf-8", "replace")
            if exc.code == 429 and attempt < RATE_LIMIT_RETRIES:
                # "Please try again in 60s." - honour it when it says so.
                m = re.search(r"try again in (\d+)\s*s", detail)
                time.sleep(min(int(m.group(1)) if m else 20, 90))
                continue
            raise GatewayError(f"fpt returned HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise GatewayError(f"fpt was unreachable: {exc.reason}") from exc
        except (json.JSONDecodeError, TimeoutError) as exc:
            raise GatewayError(f"fpt call failed: {exc}") from exc

    env = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    if "choices" not in env and raw.get("code") not in (None, 0, 200, "0", "200"):
        raise GatewayError(f"fpt refused ({raw.get('code')}): {raw.get('message')}")
    choices = env.get("choices") or []
    if not choices:
        raise GatewayError("fpt returned no choices")
    choice = choices[0]
    text = str((choice.get("message") or {}).get("content") or "")
    if choice.get("finish_reason") == "length" and "{" not in text:
        raise GatewayError("fpt stopped at max_tokens before answering")
    return text, env.get("usage") or {}


def extract(text: str) -> str:
    """The JSON object in a reply, without thinking blocks or fences around it."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("the reply contains no JSON object")
    return text[start:end + 1]


def structured(model: str, system: str, prompt: str, output_format,
               max_tokens: int) -> tuple[dict, dict]:
    """A validated object from an open model, with one repair round.

    Returns (payload, usage) where usage sums both rounds, because a repaired
    answer cost both calls.
    """
    messages = [
        {"role": "system", "content": system + contract(output_format)},
        {"role": "user", "content": prompt},
    ]
    used = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    last_error = ""
    for attempt in range(2):
        text, usage = chat(model, messages, max_tokens)
        used["input_tokens"] += int(usage.get("prompt_tokens") or 0)
        used["output_tokens"] += int(usage.get("completion_tokens") or 0)
        used["calls"] += 1
        try:
            return output_format.model_validate_json(extract(text)).model_dump(), used
        except ValueError as exc:  # pydantic's ValidationError is a ValueError
            last_error = str(exc)[:600]
            messages += [
                {"role": "assistant", "content": text[-4000:]},
                {"role": "user", "content": (
                    "That reply could not be used: " + last_error +
                    "\nReply again with only the corrected JSON object.")},
            ]
            time.sleep(0.5 * (attempt + 1))
    raise GatewayError(f"{model} gave no valid object after a repair: {last_error[:200]}")
