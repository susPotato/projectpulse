"""Who may change anything on a deployed instance.

`app/api/main.py` used to answer this with one rule: writes come from loopback
or they do not happen. That was the right call at the time - a settings form
that accepts an API key should not be reachable from the internet - but it also
meant the deployed app could not be configured at all, which is the thing this
module changes.

**Default-deny is preserved.** With no `PULSE_ADMIN_TOKEN` set, a remote write
is refused exactly as before; nothing about an existing deployment loosens by
upgrading. Setting the token is the deliberate act that opens the door, and the
refusal message says so rather than leaving someone guessing.

Loopback still passes without a token, so `python -m scripts.serve` on a laptop
behaves as it always has.

It sits at the top level rather than under `app/llm/`, where it started: the
same question is asked by the import controls, which have nothing to do with
models.

**The token is compared, never stored here.** It lives wherever platform
secrets live. There is no lockout and no attempt counter: a token from
`scripts.secret` has 256 bits of entropy, and a counter on an unguessable
secret buys nothing while adding a denial-of-service lever - anybody could lock
the real operator out by spraying wrong tokens.
"""

from __future__ import annotations

import hmac
import os

TOKEN_ENV = "PULSE_ADMIN_TOKEN"

#: Set this to turn the gate off entirely, so a deployed instance accepts
#: administrative writes from anyone who can reach it.
#:
#: **This is an opt-out from the security model, not a configuration
#: preference**, and it exists because the alternative people actually reach
#: for is worse: unsetting `PULSE_ADMIN_TOKEN` does not open the door, it
#: bolts it - `actor()` then refuses every remote write - so somebody who
#: wants to stop being asked for a token, and finds that unsetting it makes
#: the app *less* usable, ends up editing this module by hand and leaving no
#: trace of what they changed or how to undo it.
#:
#: One named flag is reversible in one command, greppable, and reported to
#: the browser by `status()` so the state is visible on the page rather than
#: only in an environment nobody re-reads.
#:
#: What it exposes, on a public URL, is worth stating plainly rather than
#: leaving to be discovered: every route listed in `require`'s docstring, and
#: in particular the six that call a language model on this deployment's own
#: API key. An open instance is one whose model spend is open too.
OPEN_ENV = "PULSE_OPEN_ADMIN"

#: Checked before `Authorization`, so a browser's own auth header cannot
#: collide with this.
HEADER = "X-Pulse-Admin-Token"


def configured() -> bool:
    """Whether a token has been set, so remote administration is possible."""
    return bool(os.environ.get(TOKEN_ENV, "").strip())


def is_open() -> bool:
    """Whether the gate has been deliberately switched off.

    Truthy strings only, and `"0"`/`"false"`/`"no"` read as off - an env var
    that is present but disabled is the commonest way a flag like this gets
    turned on by accident.
    """
    raw = os.environ.get(OPEN_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def is_local(request) -> bool:
    """Whether this request came from the machine the app runs on.

    Behind Fly's proxy `request.client.host` is the edge, never loopback - so
    this stays false on a deployment, which is the point.
    """
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"}


def presented_token(request) -> str:
    """The token this request carries, from either accepted place."""
    header = request.headers.get(HEADER, "")
    if header:
        return header.strip()

    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def actor(request) -> str:
    """`"loopback"`, `"admin-token"`, or `""` when this request may not write.

    Returned rather than raised so a read-only route can use the same rule to
    decide what to *show* - the settings page asks this to render itself as
    editable or not, which is why `writable` on the settings response was
    wrong to hardcode.
    """
    if is_local(request):
        return "loopback"

    # Checked before the token, and reported as its own actor rather than as
    # `"admin-token"`: a log line saying a token was presented when none was
    # is a worse problem than the open door itself.
    if is_open():
        return "open"

    expected = os.environ.get(TOKEN_ENV, "").strip()
    presented = presented_token(request)
    # Constant-time, and only after both are known non-empty: comparing
    # against an unset token would otherwise let an empty header through.
    if expected and presented and hmac.compare_digest(expected, presented):
        return "admin-token"
    return ""


def may_write(request) -> bool:
    return bool(actor(request))


def require(request) -> str:
    """The actor, or an `HTTPException` explaining what is missing.

    The two refusals are deliberately different. "No token is configured" is a
    deployment that has not opted in and needs a secret set; "the token is
    wrong" is a request that tried and failed. Collapsing them into one
    message sends the first case hunting for a typo in a value that does not
    exist.
    """
    from fastapi import HTTPException

    who = actor(request)
    if who:
        return who

    # Deliberately not "settings": this now guards the routes that call a
    # language model on the deployment's own key, and the one that deletes a
    # project. A message naming only settings would read as a wrong answer on
    # the Agent tab, which is one of the places it surfaces.
    if not configured():
        raise HTTPException(
            status_code=403,
            detail=(
                "This action either spends money on this deployment's API key "
                "or cannot be undone, so it is limited to the machine the app "
                f"runs on. Set {TOKEN_ENV} as a platform secret to allow it "
                "from a browser - `python -m scripts.secret` prints one."
            ),
        )

    raise HTTPException(
        status_code=403,
        detail=(
            "This action either spends money on this deployment's API key or "
            "cannot be undone. Paste your admin token on /llm to unlock this "
            "browser tab."
        ),
    )


def status() -> dict:
    """What the page may know about the auth setup. Never the token."""
    return {
        "token_configured": configured(),
        "header": HEADER,
        "env_var": TOKEN_ENV,
        # So a page can say the gate is off. An open instance that looks
        # exactly like a locked one is how an instance stays open for months
        # - the same failure `fly.toml` records for `PULSE_NARRATION`, where
        # the only readable configuration said the opposite of what
        # production was doing.
        "open": is_open(),
        "open_env": OPEN_ENV,
    }
