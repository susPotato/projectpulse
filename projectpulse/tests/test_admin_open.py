"""The admin gate, and the switch that turns it off.

Written because the switch is an opt-out from the security model. A flag that
silently stops working - or silently starts - is worse than no flag, so both
directions are pinned here rather than left to a deployment to discover.
"""

from __future__ import annotations

import pytest

from app import admin


class _Request:
    """The two attributes `admin` reads. Real enough for these rules."""

    def __init__(self, host: str = "203.0.113.7", headers: dict | None = None):
        self.client = type("C", (), {"host": host})()
        self.headers = headers or {}


def test_the_gate_is_shut_by_default(monkeypatch):
    """No flag, no token: a remote write is refused, as it always was."""
    monkeypatch.delenv(admin.OPEN_ENV, raising=False)
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    assert admin.is_open() is False
    assert admin.actor(_Request()) == ""
    assert admin.may_write(_Request()) is False


def test_the_flag_opens_it_and_says_which_actor(monkeypatch):
    """Open is its own actor.

    Not `"admin-token"`: a log line claiming a token was presented when none
    was is a worse problem than the open door it would be describing.
    """
    monkeypatch.setenv(admin.OPEN_ENV, "1")
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    assert admin.actor(_Request()) == "open"
    assert admin.may_write(_Request()) is True
    assert admin.require(_Request()) == "open"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_truthy_spellings_open_it(monkeypatch, value):
    monkeypatch.setenv(admin.OPEN_ENV, value)
    assert admin.is_open() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "   "])
def test_a_present_but_disabled_flag_stays_shut(monkeypatch, value):
    """The commonest way a flag like this gets turned on is by accident."""
    monkeypatch.setenv(admin.OPEN_ENV, value)
    assert admin.is_open() is False


def test_closing_it_restores_the_token_gate(monkeypatch):
    """Turning the flag off is the whole of the undo.

    `PULSE_ADMIN_TOKEN` is untouched by opening the gate, so removing the flag
    has to be enough to get the old behaviour back - otherwise "reversible in
    one command" is not true.
    """
    monkeypatch.setenv(admin.TOKEN_ENV, "s3cret")
    monkeypatch.setenv(admin.OPEN_ENV, "1")
    assert admin.actor(_Request()) == "open"

    monkeypatch.delenv(admin.OPEN_ENV)
    assert admin.actor(_Request()) == ""
    assert admin.actor(_Request(headers={admin.HEADER: "s3cret"})) == "admin-token"
    assert admin.actor(_Request(headers={admin.HEADER: "wrong"})) == ""


def test_the_page_is_told_the_gate_is_off(monkeypatch):
    """An open instance that looks like a locked one stays open for months."""
    monkeypatch.setenv(admin.OPEN_ENV, "1")
    state = admin.status()
    assert state["open"] is True
    assert state["open_env"] == admin.OPEN_ENV
    # Still never the token itself.
    assert "s3cret" not in repr(state)


def test_loopback_never_needed_the_flag(monkeypatch):
    monkeypatch.delenv(admin.OPEN_ENV, raising=False)
    monkeypatch.delenv(admin.TOKEN_ENV, raising=False)
    assert admin.actor(_Request(host="127.0.0.1")) == "loopback"
