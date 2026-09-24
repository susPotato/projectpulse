"""Fetching a delivery project's repository, and checking its documents.

**Nothing here clones.** `tracelink.source.resolve` already does: shallow,
cached by URL and ref, re-fetched rather than re-downloaded on a refresh,
credential passed as an `Authorization` header rather than written into
`.git/config`, and - the part that matters most - it returns a `Revision`
naming the commit it read. A second implementation here would be a second
answer to "which code is this run about", and `tracelink.source.moved()`
would go blind against the one it did not write. That module opens by
describing exactly that failure, from a zip drop with no `.git`.

So this module is the ProjectPulse half: resolve the credential from the
row rather than from the environment, enforce the documentation tree, and
turn every failure into something a person reading a web page can act on.

**The documentation tree is a lock, not a default.** A repository that has
no `docs_path` is refused, and the refusal names the directories that *are*
at the top level. The alternative - accept it, find no documents, and let
the Traceability page report an empty documentation set - is the failure
mode the workbook upload already learned to avoid: `§0a` in `CLAUDE.md` is
four defects deep in "accepted and silently empty", and the fix each time
was to refuse with the thing it looked for and the thing it found.

Why refusing is right rather than harsh: `features.py` classifies every
document as grounded or ungrounded by resolving its claims against the
corpus, and that classification is what stops a target-architecture
document being handed to an adjudicator as evidence. A project with no
documents does not get a weaker version of that. It gets nothing, and the
whole delivery half of the pipeline - progress, gates, reconciliation,
governance - has no input at all. That is worth a 400 at registration
instead of an empty page a week later.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.config import settings

log = logging.getLogger(__name__)

#: Markdown is what the documentation adapters read
#: (`tracelink/adapters/featuremap_markdown.py`), so it is what counts
#: towards a tree being present. A `docs/` holding only PNGs is an asset
#: directory, and calling it a documentation tree would register a project
#: whose every documentation stage then reports nothing.
DOC_SUFFIXES = (".md", ".markdown")

#: Directories never worth walking when counting documents. Costs nothing
#: on a small tree and avoids reading a vendored `node_modules` on a large
#: one, which is minutes rather than seconds.
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             "site-packages", ".tox", "dist", "build"}


class RepoUnavailable(RuntimeError):
    """The repository could not be read. Always says what to do next.

    Distinct from `DocsMissing` below because the two have different
    owners: this one is usually a URL, a branch name or a credential, and
    that one is a decision about where the team keeps its documents.
    """


class DocsMissing(ValueError):
    """The repository is fine; the documentation tree is not there."""


class PipelineMissing(RuntimeError):
    """`tracelink` is not importable on this host, so nothing can clone.

    Reported rather than raised at import time, the same rule the model
    providers follow: a deployment without the pipeline serves every other
    page normally and this one feature says why it is off.
    """


def _tracelink_source():
    """`tracelink.source`, or a `PipelineMissing` naming the fix.

    Imported on first use rather than at module import, so the absence of
    the pipeline costs a reason on one screen instead of the app's boot.
    """
    home = (settings.tracelink_home or "").strip()
    if home and home not in sys.path:
        # Prepended, not appended: a stale copy installed into the
        # environment should not win over the checkout somebody pointed at
        # deliberately.
        sys.path.insert(0, home)
    try:
        from tracelink import source  # noqa: PLC0415
    except ImportError as exc:
        raise PipelineMissing(
            "the traceability pipeline is not installed on this server, so "
            "it cannot read a repository. Set PULSE_TRACELINK_HOME to the "
            "directory holding the `tracelink` package, or deploy an image "
            f"that copies it in ({exc})."
        ) from None
    return source


def available() -> tuple[bool, str]:
    """Whether this server can fetch a repository at all, and why not.

    Both halves are checked - the pipeline and git itself - because they
    fail for unrelated reasons and a page that reports only the first sends
    somebody to install the wrong thing.
    """
    try:
        _tracelink_source()
    except PipelineMissing as exc:
        return False, str(exc)
    if shutil.which("git") is None:
        return False, (
            "git is not on this server's PATH, so a repository cannot be "
            "cloned. The container image installs it; a local run needs it "
            "on PATH."
        )
    return True, ""


def looks_like_url(spec: str) -> bool:
    """Whether this is something to clone. Defers to the pipeline's rule."""
    return _tracelink_source().looks_like_url(spec)


@contextmanager
def _token_in_env(token: str):
    """Lend a credential to `tracelink.source` for the length of one call.

    That module reads a token from the environment and never from an
    argument, deliberately, so it cannot land in a shell history file. Here
    the credential comes from a database row instead, so the environment is
    how it gets there - set around the call and restored after, rather than
    exported for the process, so one project's token is never in scope
    while another project's fetch runs.
    """
    name = _tracelink_source().TOKEN_ENV[0]
    previous = os.environ.get(name)
    if token:
        os.environ[name] = token
    elif previous is not None:
        # An empty token means *this* repository is public. Leaving an
        # unrelated ambient token set would send it to a host that has no
        # business receiving it.
        del os.environ[name]
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def docs_dir(tree: Path, docs_path: str) -> Path:
    """Where the documents are, refusing a path that leaves the repository.

    `docs_path` arrives from a form. `../../etc` would resolve to somewhere
    real and be read, so containment is checked rather than assumed - the
    one validation that cannot be left to the thing being validated.
    """
    cleaned = (docs_path or "docs").strip().strip("/\\") or "docs"
    candidate = (tree / cleaned).resolve()
    root = tree.resolve()
    if candidate != root and root not in candidate.parents:
        raise DocsMissing(
            f"{docs_path!r} points outside the repository. Give a path "
            f"relative to its root, such as 'docs' or 'backend/docs'."
        )
    return candidate


def count_docs(directory: Path) -> int:
    """How many markdown documents are under here, at any depth."""
    total = 0
    for current, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        total += sum(1 for f in filenames
                     if f.lower().endswith(DOC_SUFFIXES))
    return total


def _top_level(tree: Path) -> list[str]:
    """The directories a person would see, for a refusal that helps."""
    try:
        return sorted(
            p.name for p in tree.iterdir()
            if p.is_dir() and p.name not in SKIP_DIRS
        )[:20]
    except OSError:
        return []


def check_docs(tree: Path, docs_path: str) -> tuple[Path, int]:
    """Enforce the lock: the tree exists, and it holds documents.

    Returns the directory and its document count. Raises `DocsMissing`
    naming what was looked for and what is actually there - the manners
    `POST /api/sources/upload` settled on for a workbook with no readable
    sheet.
    """
    directory = docs_dir(tree, docs_path)
    wanted = (docs_path or "docs").strip().strip("/\\") or "docs"
    if not directory.is_dir():
        found = _top_level(tree)
        have = ", ".join(found) if found else "no directories at all"
        raise DocsMissing(
            f"this repository has no {wanted!r} directory. Its top level "
            f"holds: {have}. ProjectPulse reads a project's documents from "
            f"one tree in the repository - create {wanted}/, or name the "
            f"directory the documents are already in."
        )
    count = count_docs(directory)
    if count == 0:
        raise DocsMissing(
            f"{wanted!r} is there but holds no markdown. The documentation "
            f"stages read .md files, so a tree without them produces no "
            f"progress, gates or reconciliation. Add the documents, or "
            f"point at the directory that has them."
        )
    return directory, count


def fetch(*, repo_url: str, ref: str = "", docs_path: str = "docs",
          token: str = "", cache: Path | None = None,
          generate_missing_docs: bool = False) -> dict[str, Any]:
    """Clone or refresh a repository and validate its documentation tree.

    Returns what the registration needs to record: the working tree, the
    revision that was read, and the document count. Raises rather than
    returning a half answer - a row written from a failed fetch is a
    registration pointing at a commit nobody read.

    `generate_missing_docs` turns a missing documentation tree from a refusal
    into `docs_dir: None` with the refusal's text in `docs_missing` - the
    caller has checked `app.codewiki_docs.unavailable()` and will generate the
    tree instead. A repository that *has* documents is read as it always was.
    """
    src = _tracelink_source()
    if shutil.which("git") is None and src.looks_like_url(repo_url):
        raise RepoUnavailable(available()[1])

    cache = Path(cache or settings.repo_cache)
    try:
        cache.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RepoUnavailable(
            f"cannot write the clone cache at {cache}: {exc}. Set "
            f"PULSE_REPO_CACHE to a writable directory."
        ) from None

    with _token_in_env(token):
        try:
            tree, revision = src.resolve(repo_url, ref=ref, cache=cache)
        except src.SourceError as exc:
            # Its messages already name the next step, including the
            # private-versus-absent ambiguity GitHub creates. Passing them
            # through beats paraphrasing them into something vaguer.
            raise RepoUnavailable(str(exc)) from None
        except (OSError, ValueError) as exc:
            raise RepoUnavailable(
                f"could not read {repo_url!r}: {exc}"
            ) from None

    # The lock is applied after the fetch and before anything is written,
    # so a repository with no documents leaves no registration behind -
    # the fifth defect in `CLAUDE.md` §0a, which is that a refused import
    # still left a project on the portfolio.
    # Containment first and outside the `try`: a path that leaves the
    # repository is refused whatever the caller allows, or generating the
    # "missing" documents would write them wherever it pointed.
    docs_dir(tree, docs_path)
    try:
        directory, count = check_docs(tree, docs_path)
        missing = ""
    except DocsMissing as exc:
        if not generate_missing_docs:
            raise
        directory, count, missing = None, 0, str(exc)

    log.info("fetched %s at %s, %d documents under %s",
             repo_url, revision.short or "unknown", count, docs_path)
    return {
        "tree": tree,
        "docs_dir": directory,
        "doc_count": count,
        "docs_missing": missing,
        "commit": revision.commit,
        "ref": revision.ref or ref,
        "dirty": revision.dirty,
        "revision": revision,
    }
