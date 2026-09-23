"""Which git repository a delivery project's code and documents come from.

One row per delivery project. Unlike a Jira link - where two delivery
projects can genuinely be tracked in one Jira, and one in two - a delivery
project has *one* codebase. Two repositories claiming to be the same
project's code would give the corpus two answers to "where is this
symbol?", and every verdict downstream cites a symbol. So `project_id` is
the primary key and re-registering replaces.

**The clone is not here, and must not be.** What is stored is the
registration: the URL, the ref, where the documents are, and the commit we
last read. The working tree lives in a cache on container-local disk, which
`tracelink.source.resolve` already treats as disposable - it re-fetches
rather than re-clones, and a Fly machine that loses it pays one shallow
clone. That is the right split for this deployment: `uploaded_sheets` holds
workbook bytes because an upload *cannot* be re-derived, and a clone always
can.

**`docs_path` is the lock.** It defaults to `docs` and is stored per
project rather than assumed globally, because a monorepo puts them
somewhere else and a registration that silently read the wrong tree would
produce a documentation report about another team's product. `source.py`
refuses a repository where that path is absent, so the column never points
at nothing.

The token is sealed exactly as `JiraConnection` seals its own, with the
same `PULSE_SECRET_KEY` and the same masked hint kept in the clear - one
rotation story, not three.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class ProjectRepo(Timestamped, Base):
    """One delivery project, one repository, one documentation tree."""

    __tablename__ = "project_repos"

    #: The delivery project this is the code for, as `app.scope` knows it.
    #: Primary key - see the module docstring for why this is one-to-one.
    project_id: Mapped[str] = mapped_column(String(200), primary_key=True)

    #: A clone URL, as somebody would paste it: `https://host/org/repo.git`,
    #: `git@host:org/repo.git`. Stored as given rather than normalised - a
    #: URL is the argument git takes, and rewriting it is how a working
    #: remote becomes a broken one. `source.py` validates it is a URL at
    #: all, which is the part a typo gets wrong.
    repo_url: Mapped[str] = mapped_column(String(500))

    #: Branch or tag to read. Empty means the remote's default HEAD, which
    #: is what a shallow clone with no `--branch` gets.
    ref: Mapped[str] = mapped_column(String(200), default="")

    #: Where the documentation tree is, relative to the repository root.
    #: **The lock.** See the module docstring.
    docs_path: Mapped[str] = mapped_column(String(300), default="docs")

    #: Full sha of the commit the last successful fetch read, or "" before
    #: the first one. This is what makes staleness answerable:
    #: `tracelink.source.moved()` compares it against the checkout, and a
    #: run built from another commit can say so rather than looking current.
    commit: Mapped[str] = mapped_column(String(64), default="")

    #: The tree read at `commit` had uncommitted changes. Only possible for
    #: a local path registration; recorded rather than refused, the same
    #: rule `tracelink.source` documents.
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)

    #: When that fetch succeeded. Distinct from `updated_at`, which moves
    #: when somebody edits the ref without fetching.
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None
    )

    #: How many markdown documents were found under `docs_path` at
    #: `commit`. Stored so the Sources list can say "42 documents" without
    #: a clone, and so a refresh that drops to zero is visible as a change
    #: rather than as an empty page somebody has to explain.
    doc_count: Mapped[int] = mapped_column(Integer, default=0)

    #: Why the last fetch failed, in the words a person can act on, or ""
    #: when it succeeded. Kept on the row rather than only returned from
    #: the request that failed, because the failure a person needs to see
    #: is usually the one from the scheduled refresh they were not watching.
    last_error: Mapped[str] = mapped_column(Text, default="")

    #: Fernet token for a private-repository credential, or "" for a public
    #: repository. Opaque without `PULSE_SECRET_KEY`; see `app/llm/keys.py`.
    ciphertext: Mapped[str] = mapped_column(Text, default="")

    #: A masked hint, in the clear on purpose so a list can tell two
    #: credentials apart without the server decrypting either.
    hint: Mapped[str] = mapped_column(String(64), default="")

    #: Which `PULSE_SECRET_KEY` sealed this row, for the rotation story
    #: `LlmCredential` documents.
    key_version: Mapped[int] = mapped_column(Integer, default=1)

    #: "loopback" or "admin-token" - provenance for an audit read, not
    #: identity.
    updated_by: Mapped[str] = mapped_column(String(32), default="")
