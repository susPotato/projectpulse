"""The FPT gateway backend, offline: the HTTP call is faked at `urlopen`."""
from __future__ import annotations

import io
import json

import pytest

from tracelink import adjudicate as AD, fpt


def _reply(content: str, *, envelope: bool = True, finish: str = "stop") -> bytes:
    body = {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    if envelope:
        body = {"code": 200, "message": "ok", "data": body}
    return json.dumps(body).encode()


GOOD = json.dumps({"verdict": "corroborated", "confidence": "high",
                   "reasoning": "core/x.py does it.", "evidence": [
                       {"file": "core/x.py", "symbol": "f", "why": "implements it"}],
                   "status_conflict": False})


@pytest.fixture
def gateway(monkeypatch):
    """Queue replies; record what was sent."""
    monkeypatch.setenv("FPT_API_KEY", "k")
    sent, queue = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout=None):
        sent.append(json.loads(req.data))
        return Resp(queue.pop(0))

    monkeypatch.setattr(fpt.urllib.request, "urlopen", urlopen)
    return sent, queue


def test_model_prefix_selects_the_gateway():
    assert fpt.is_fpt("fpt:GLM-5.2") and fpt.name("fpt:GLM-5.2") == "GLM-5.2"
    assert not fpt.is_fpt("claude-opus-5")


def test_envelope_and_bare_bodies_both_parse(gateway):
    sent, queue = gateway
    queue += [_reply(GOOD), _reply(GOOD, envelope=False)]
    for _ in range(2):
        payload, used = fpt.structured("fpt:m", "SYS", "P", AD.VerdictOut, 4000)
        assert payload["verdict"] == "corroborated" and used["calls"] == 1
    # The prefix is stripped, the schema is in the system prompt, and the
    # budget is floored for reasoning models.
    assert sent[0]["model"] == "m"
    assert '"verdict"' in sent[0]["messages"][0]["content"]
    assert sent[0]["max_tokens"] >= fpt.MIN_TOKENS


def test_think_blocks_and_fences_are_stripped(gateway):
    _, queue = gateway
    queue.append(_reply("<think>maybe {not this}</think>\n```json\n" + GOOD + "\n```"))
    payload, _ = fpt.structured("fpt:m", "S", "P", AD.VerdictOut, 4000)
    assert payload["evidence"][0]["file"] == "core/x.py"


def test_an_invalid_reply_is_repaired_once(gateway):
    sent, queue = gateway
    queue += [_reply('{"verdict": "maybe"}'), _reply(GOOD)]
    payload, used = fpt.structured("fpt:m", "S", "P", AD.VerdictOut, 4000)
    assert payload["verdict"] == "corroborated"
    assert used["calls"] == 2 and used["input_tokens"] == 200
    # The second request carries the bad answer and the validation error.
    assert sent[1]["messages"][-1]["role"] == "user"
    assert "could not be used" in sent[1]["messages"][-1]["content"]


def test_two_invalid_replies_raise(gateway):
    _, queue = gateway
    queue += [_reply("no json here"), _reply("still none")]
    with pytest.raises(fpt.GatewayError):
        fpt.structured("fpt:m", "S", "P", AD.VerdictOut, 4000)


def test_structured_caller_routes_and_caches(gateway, tmp_path):
    sent, queue = gateway
    queue.append(_reply(GOOD))
    adj = AD.Adjudicator(model="fpt:m", cache_dir=tmp_path)
    first, u1 = adj.call(AD.SYSTEM, "P", AD.VerdictOut)
    again, u2 = adj.call(AD.SYSTEM, "P", AD.VerdictOut)
    assert first == again and u1.api_calls == 1 and u2.cached_calls == 1
    assert len(sent) == 1
    # Claude's SYSTEM, unchanged, then the schema contract.
    assert sent[0]["messages"][0]["content"].startswith(AD.SYSTEM)


def test_env_picks_the_default_model_and_its_credential(monkeypatch):
    monkeypatch.setenv(AD.MODEL_ENV, "fpt:GLM-5.2")
    monkeypatch.delenv("FPT_API_KEY", raising=False)
    assert AD.default_model() == "fpt:GLM-5.2"
    assert AD.Adjudicator().model == "fpt:GLM-5.2"
    assert not AD.api_key_present()
    assert "FPT_API_KEY" in AD.credential_hint()
    monkeypatch.setenv("FPT_API_KEY", "k")
    assert AD.api_key_present()


def test_a_rate_limit_is_waited_out(gateway, monkeypatch):
    sent, queue = gateway
    slept = []
    monkeypatch.setattr(fpt.time, "sleep", slept.append)
    real = fpt.urllib.request.urlopen
    calls = {"n": 0}

    def flaky(req, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise fpt.urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {},
                io.BytesIO(b'{"description":"Please try again in 7s."}'))
        return real(req, timeout)

    monkeypatch.setattr(fpt.urllib.request, "urlopen", flaky)
    queue.append(_reply(GOOD))
    payload, _ = fpt.structured("fpt:m", "S", "P", AD.VerdictOut, 4000)
    assert payload["verdict"] == "corroborated" and slept == [7]
