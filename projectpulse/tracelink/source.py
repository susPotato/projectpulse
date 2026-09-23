"""Where the code came from, and which version of it a run actually read.

The first run of this pipeline analysed a zip drop with no `.git`, so the
question "is this run still current?" had no answer. Nothing recorded what
revision the verdicts were about, and a repository that moved the next
morning looked identical to one that had not. For a one-off audit that is
tolerable. For a page somebody checks every week it is the whole problem:
a stale verdict and a fresh one are indistinguishable.

So a code source is resolved once, here, and it reports two things: the
directory to read, and a `Revision` naming what is in it.

**A git URL is cloned; a local path is used in place.** Cloning is shallow
and to a cache keyed by URL and ref, because a refresh that re-downloads a
history nobody reads is a refresh people stop running. A local checkout is
never modified - no fetch, no pull, no checkout - because the most annoying
possible behaviour for this tool is moving somebody's working tree while
they are in it.

**A dirty tree is reported, not refused.** Analysing uncommitted work is a
perfectly ordinary thing to do while a refactor is in flight. What is not
ordinary is *forgetting* you did it, so the revision carries `dirty` and
every artifact built from it says so.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Prefixes and suffixes that make a string a repository to clone rather
#: than a directory to read. A local path wins whenever it exists, so a
#: directory literally named `git@...` is still read from disk.
_URL_HINTS = ("git@", "http://", "https://", "ssh://", "git://")

#: How long a git call may take before we stop waiting. A clone of a large
#: repository is slow; a hung credential prompt is forever, and the second
#: is what this guards against.
CLONE_TIMEOUT = 600
QUERY_TIMEOUT = 30

#: Where a token for a private repository is read from, in order.
#:
#: Read from the environment and never from an argument, so it does not
#: land in a shell history file. `GITHUB_TOKEN` is last because it is the
#: one most likely to be set for something else.
TOKEN_ENV = ("TRACELINK_GIT_TOKEN", "GIT_TOKEN", "GITHUB_TOKEN")


def _token() -> str:
    for name in TOKEN_ENV:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _auth_args(url: str) -> list[str]:
    """`git -c` arguments that authenticate this clone, if a token is set.

    Sent as an `Authorization` header rather than embedded in the URL.
    A token in the URL is written into `.git/config` by the clone and
    stays there - it then shows up in `git remote -v`, in any error
    message quoting the remote, and in whatever backs that directory up.
    The header is used for the request and not persisted.

    **It is visible in this process's command line while git runs.** On a
    shared host that is a real exposure; on a developer's machine it is
    the same trade every `git -c` credential does. Stated rather than
    hidden, because the alternative helpers each have their own leak and
    pretending otherwise is how a token ends up somewhere worse.
    """
    token = _token()
    if not token or not url.lower().startswith("http"):
        return []
    pair = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
    return ["-c", f"http.extraHeader=Authorization: Basic {pair}"]


class SourceError(RuntimeError):
    """The code source could not be resolved. Always says what to do next."""


@dataclass(frozen=True)
class Revision:
    """What a run read, in terms somebody can check out again."""

    #: Full commit sha, or "" when the source is not a git repository.
    commit: str = ""
    #: Branch or tag, when the checkout is on one.
    ref: str = ""
    #: Uncommitted changes were present in the tree that was read.
    dirty: bool = False
    #: Where it came from: a URL, or the path as given.
    origin: str = ""

    @property
    def short(self) -> str:
        return self.commit[:12] if self.commit else ""

    @property
    def known(self) -> bool:
        return bool(self.commit)

    def describe(self) -> str:
        if not self.known:
            return f"{self.origin or 'source'} (not a git repository)"
        bits = [self.short]
        if self.ref:
            bits.append(f"on {self.ref}")
        if self.dirty:
            bits.append("with uncommitted changes")
        return " ".join(bits)

    def as_dict(self) -> dict[str, Any]:
        return {"commit": self.commit, "ref": self.ref, "dirty": self.dirty,
                "origin": self.origin}


def looks_like_url(spec: str) -> bool:
    """Whether this names a repository to clone rather than a path to read."""
    if Path(spec).exists():
        return False
    return spec.startswith(_URL_HINTS) or spec.endswith(".git")


def _git(*args: str, cwd: Path | None = None, timeout: int = QUERY_TIMEOUT):
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, timeout=timeout,
        capture_output=True, text=True,
        # A clone that stops to ask for a password never finishes and never
        # says why. Failing with git's own error is the readable outcome.
        # PATH is passed through because git needs to find its own helpers.
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0",
             "GCM_INTERACTIVE": "never"},
    )


def revision_of(path: Path, origin: str = "") -> Revision:
    """What `path` is, as a revision. Never raises: not-git is an answer."""
    path = Path(path)
    origin = origin or str(path)
    if shutil.which("git") is None:
        return Revision(origin=origin)
    try:
        head = _git("rev-parse", "HEAD", cwd=path)
        if head.returncode != 0:
            return Revision(origin=origin)
        commit = head.stdout.strip()

        # git answers from the nearest enclosing repository, which is not
        # the same question. A directory dropped inside a checkout and
        # never added is not versioned by it, and taking that repository's
        # commit would stamp the run with a revision describing somebody
        # else's code. So when the repository root is an ancestor rather
        # than this directory, it has to actually track something here.
        top = _git("rev-parse", "--show-toplevel", cwd=path)
        root = Path(top.stdout.strip()) if top.returncode == 0 else None
        if root and root.resolve() != path.resolve():
            tracked = _git("ls-files", "--", ".", cwd=path)
            if tracked.returncode != 0 or not tracked.stdout.strip():
                return Revision(origin=origin)

        branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=path)
        ref = branch.stdout.strip() if branch.returncode == 0 else ""
        # Scoped to this directory: in a monorepo, an edit somewhere else
        # is not a reason to call the code we read uncommitted.
        status = _git("status", "--porcelain", "--", ".", cwd=path)
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else False
    except (OSError, subprocess.SubprocessError):
        return Revision(origin=origin)
    return Revision(commit=commit, ref="" if ref == "HEAD" else ref,
                    dirty=dirty, origin=origin)


def _cache_dir(root: Path, url: str, ref: str) -> Path:
    key = hashlib.sha256(f"{url}@{ref}".encode()).hexdigest()[:16]
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "repo"
    return root / f"{name}-{key}"


def resolve(spec: str, *, ref: str = "", cache: Path | None = None
            ) -> tuple[Path, Revision]:
    """A code source as (directory to read, what is in it).

    `spec` is a local path or a git URL. A URL is cloned shallowly into
    `cache` and reused on later runs - fetched to move it to `ref`, never
    re-cloned - so a weekly refresh costs one fetch rather than one
    download.
    """
    if not looks_like_url(spec):
        path = Path(spec)
        if not path.is_dir():
            raise SourceError(
                f"{spec!r} is not a directory and does not look like a git "
                f"URL. Pass a checkout to read, or a URL to clone."
            )
        return path, revision_of(path, origin=spec)

    if shutil.which("git") is None:
        raise SourceError(
            f"{spec!r} looks like a git URL but git is not on PATH. Install "
            f"git, or clone it yourself and pass the directory."
        )

    cache = Path(cache or (Path.home() / ".tracelink" / "sources"))
    cache.mkdir(parents=True, exist_ok=True)
    target = _cache_dir(cache, spec, ref)

    if (target / ".git").is_dir():
        # Already have it: move the existing clone rather than re-download.
        fetch = _git(*_auth_args(spec), "fetch", "--depth", "1", "origin",
                     ref or "HEAD", cwd=target, timeout=CLONE_TIMEOUT)
        if fetch.returncode != 0:
            raise SourceError(
                f"could not fetch {spec!r} into {target}: "
                f"{fetch.stderr.strip() or 'git fetch failed'}"
            )
        _git("checkout", "--force", "FETCH_HEAD", cwd=target,
             timeout=CLONE_TIMEOUT)
    else:
        args = [*_auth_args(spec), "clone", "--depth", "1"]
        if ref:
            args += ["--branch", ref]
        args += [spec, str(target)]
        out = _git(*args, timeout=CLONE_TIMEOUT)
        if out.returncode != 0:
            hint = ""
            if not _token() and "not found" in (out.stderr or "").lower():
                hint = (" GitHub answers the same way for a private "
                        "repository and one that does not exist. If it is "
                        f"private, set {TOKEN_ENV[0]} to a token that can "
                        "read it, or pass a local checkout instead.")
            raise SourceError(
                f"could not clone {spec!r}: "
                f"{out.stderr.strip() or 'git clone failed'}.{hint}"
            )

    rev = revision_of(target, origin=spec)
    if ref and not rev.ref:
        rev = Revision(commit=rev.commit, ref=ref, dirty=rev.dirty,
                       origin=spec)
    return target, rev


def moved(recorded: dict[str, Any] | None, path: Path) -> str:
    """Why a run is out of date against its source, or "" when it is not.

    Compares what an artifact recorded against what the checkout says now.
    Silent when either side has no revision: a run of a zip drop cannot be
    stale in this sense, and saying so every time would train people to
    ignore the line that matters.
    """
    if not recorded or not recorded.get("commit"):
        return ""
    now = revision_of(path)
    if not now.known:
        return ""
    if now.commit != recorded["commit"]:
        return (f"the repository has moved: read at "
                f"{recorded['commit'][:12]}, now at {now.short}")
    if now.dirty and not recorded.get("dirty"):
        return "the checkout has uncommitted changes that this run did not read"
    return ""
