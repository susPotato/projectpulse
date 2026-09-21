"""Who may change model settings and keys.

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

#: Checked before `Authorization`, so a browser's own auth header cannot
#: collide with this.
HEADER = "X-Pulse-Admin-Token"


def configured() -> bool:
    """Whether a token has been set, so remote administration is possible."""
    return bool(os.environ.get(TOKEN_ENV, "").strip())


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

    if not configured():
        raise HTTPException(
            status_code=403,
            detail=(
                "this deployment has no admin token, so settings can only be "
                f"changed from the machine the app runs on. Set {TOKEN_ENV} as "
                "a platform secret to administer it remotely, or set the "
                "provider and key as environment variables instead."
            ),
        )

    raise HTTPException(
        status_code=403,
        detail=(
            f"a valid {HEADER} header is required to change settings on a "
            "deployed instance."
        ),
    )


def status() -> dict:
    """What the page may know about the auth setup. Never the token."""
    return {
        "token_configured": configured(),
        "header": HEADER,
        "env_var": TOKEN_ENV,
    }
