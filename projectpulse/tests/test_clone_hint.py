"""What a refused clone tells the person who asked for it.

Written from a real report: a private repository registered from the browser
with the token box empty, and the page answered

    fatal: could not read Username for 'https://github.com':
    terminal prompts disabled.

which is about terminals, and the problem is not about terminals. The hint
that would have explained it existed and keyed off the other spelling of the
same refusal - the one you only get on a host that has a credential helper,
which a container never does.
"""

from __future__ import annotations

import pytest

from tracelink import source

#: What git actually printed on the deployment, and what it prints on a
#: laptop, for the same private repository.
IN_A_CONTAINER = (
    "Cloning into '/app/.pulse/repos/Coworklocal-e3cf67f9'...\n"
    "fatal: could not read Username for 'https://github.com': "
    "terminal prompts disabled."
)
ON_A_LAPTOP = "remote: Repository not found.\nfatal: Authentication failed for 'https://github.com/o/r/'"


@pytest.fixture(autouse=True)
def _no_ambient_token(monkeypatch):
    for name in source.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("stderr", [IN_A_CONTAINER, ON_A_LAPTOP])
def test_both_spellings_of_a_refusal_earn_the_hint(stderr):
    """The two messages mean one thing, so they get one answer."""
    hint = source._credential_hint(stderr)
    assert "private" in hint
    assert source.TOKEN_ENV[0] in hint
    # It has to name the thing a browser user can actually do, not only the
    # environment variable a CLI user would set.
    assert "registration form" in hint


def test_an_unrelated_failure_stays_quiet():
    """A DNS error is not a credential problem, and saying so would mislead."""
    assert source._credential_hint(
        "fatal: unable to access 'https://github.com/o/r/': "
        "Could not resolve host: github.com"
    ) == ""
    assert source._credential_hint("") == ""
    assert source._credential_hint(None) == ""


def test_a_supplied_token_gets_different_advice(monkeypatch):
    """"Try a token" is wrong for somebody whose token is merely expired.

    Worse, it reads as though the token they gave was ignored - which sends
    them looking for a bug in the form rather than at the token's scope.
    """
    monkeypatch.setenv(source.TOKEN_ENV[0], "ghp_whatever")
    hint = source._credential_hint(ON_A_LAPTOP)
    assert "A token was supplied" in hint
    assert "expired" in hint and "scope" in hint
    assert "registration form" not in hint
