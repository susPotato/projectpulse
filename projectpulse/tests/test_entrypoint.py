"""What the container entrypoint has to do, and what it quietly stopped doing.

The image used to end with `USER pulse`. That is not compatible with a Fly
volume, which mounts root:root - a process that has already dropped
privileges cannot make its own state directory writable - so the entrypoint
starts as root, chowns `/data`, and drops to uid 10001 itself.

Replacing a Docker `USER` directive with `setpriv` loses everything Docker was
doing for free, and the expensive one is `HOME`. `setpriv` changes the ids and
nothing else, so `HOME` stays `/root`; uid 10001 cannot read it; psycopg looks
there for an optional client certificate and a TLS Postgres turns the
unreadable directory into `Permission denied` rather than "no such file". The
app then exits 1 before serving a request and the machine reboots in a loop.
It took a real deploy to find, so it gets a test.

These read the script rather than run it - it is `sh`, and the suite runs on
Windows. They are cheap and they pin the three things whose absence is silent.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO / "docker-entrypoint.sh"
DOCKERFILE = REPO / "Dockerfile"


@pytest.fixture(scope="module")
def script() -> str:
    assert ENTRYPOINT.exists(), "the container entrypoint is missing"
    return ENTRYPOINT.read_text(encoding="utf-8")


def test_the_entrypoint_sets_home_before_dropping_privileges(script):
    """The defect that took the deployed app down, in one assertion."""
    assert re.search(r"export\s+HOME=/home/pulse", script), (
        "HOME is not set. `setpriv` does not change it, so it stays /root - "
        "unreadable to uid 10001 - and psycopg reports `Permission denied` "
        "looking for $HOME/.postgresql/postgresql.crt against a TLS Postgres. "
        "The app exits 1 at boot and the machine reboot-loops."
    )
    home = script.index("export HOME=")
    drop = script.index("exec setpriv")
    assert home < drop, "HOME must be set before the exec that drops privileges"


def test_the_entrypoint_drops_privileges(script):
    """Running the app as root would be a real regression, not a detail."""
    assert "setpriv" in script
    assert "--reuid=\"$APP_UID\"" in script or "--reuid=10001" in script
    assert 'APP_UID=10001' in script


def test_the_entrypoint_chowns_the_state_directory(script):
    """The reason the `USER` directive had to go at all."""
    assert "chown" in script
    assert "STATE_DIR" in script


def test_the_entrypoint_has_unix_line_endings():
    """A CRLF here makes the kernel look for an interpreter named `sh\\r`.

    `core.autocrlf=true` is the Windows default and checks text files out
    with CRLF, so this file is correct in the working copy it was written in
    and broken in the next clone - the container exits before a line of the
    app runs, and the image that built fine locally is the one that fails.
    `.gitattributes` pins `*.sh` to LF; this is what notices if that entry is
    ever removed.
    """
    raw = ENTRYPOINT.read_bytes()
    assert b"\r\n" not in raw, (
        "docker-entrypoint.sh has CRLF line endings. Check `.gitattributes` "
        "still carries `*.sh text eol=lf`, then re-checkout the file: "
        "`git rm --cached <path> && git checkout <path>`."
    )
    assert raw.startswith(b"#!/bin/sh\n")


def test_the_dockerfile_uses_the_entrypoint_and_not_a_user_directive():
    """Both together, or neither works.

    A `USER pulse` line reintroduced here would make the chown fail - the
    process could no longer do it - and `/data` would go back to being
    unwritable.
    """
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "docker-entrypoint.sh" in text
    assert "ENTRYPOINT" in text
    active = [
        line for line in text.splitlines()
        if line.strip().startswith("USER ")
    ]
    assert not active, (
        f"Dockerfile carries {active!r}. The entrypoint has to start as root "
        f"to chown the volume mount; it drops to uid 10001 itself."
    )
