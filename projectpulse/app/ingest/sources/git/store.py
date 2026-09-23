"""Saving, listing and using a project's repository registration.

The credential half defers entirely to `app/llm/keys.py`, exactly as
`jira/store.py` does: same `PULSE_SECRET_KEY`, same Fernet, same masked
hint. Nothing here implements encryption.

**A registration is only written after a successful fetch.** The route
fetches first and saves second, so the row's `commit` always names a
commit this server actually read and its `doc_count` a tree it actually
walked. The alternative - save, then fetch, then patch the row - leaves a
project registered against a repository nobody could reach the moment the
fetch fails, which is the state `§0a` spent a defect removing for uploads.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.models.repo import ProjectRepo


def _cipher():
    from app.llm import keys

    return keys._fernet()


def normalise_docs_path(raw: str) -> str:
    """The documents path as it will be stored.

    Slashes trimmed and backslashes folded, so a Windows-style path pasted
    from Explorer resolves to the same tree as the same path typed by hand
    - the row is a key into a POSIX checkout either way.
    """
    cleaned = (raw or "").strip().replace("\\", "/").strip("/")
    return cleaned or "docs"


def save(session, *, project_id: str, repo_url: str, ref: str = "",
         docs_path: str = "docs", token: str | None = None,
         actor: str = "", fetched: dict[str, Any] | None = None
         ) -> ProjectRepo:
    """Create or replace the repository registration for one project.

    `token` of `None` keeps whatever is stored, so somebody changing the
    branch does not have to paste their credential again and cannot blank
    it by leaving the field alone. An empty string is the explicit "this
    repository is public" - it clears the credential, which is the only
    way to stop sending a token to a host that no longer needs one.
    """
    from app.llm import keys

    project_id = (project_id or "").strip()
    repo_url = (repo_url or "").strip()
    if not project_id:
        raise ValueError("Choose which delivery project this repository is for.")
    if not repo_url:
        raise ValueError(
            "Give a clone URL, such as https://github.com/org/repo.git"
        )

    row = session.scalar(
        select(ProjectRepo).where(ProjectRepo.project_id == project_id)
    )
    if row is None:
        row = ProjectRepo(project_id=project_id, repo_url=repo_url)
        session.add(row)

    row.repo_url = repo_url
    row.ref = (ref or "").strip()
    row.docs_path = normalise_docs_path(docs_path)
    row.updated_by = actor or ""
    row.last_error = ""

    if token is not None:
        if token.strip():
            row.ciphertext = _cipher().encrypt(
                token.strip().encode("utf-8")
            ).decode("ascii")
            row.hint = keys.mask(token.strip())
        else:
            row.ciphertext = ""
            row.hint = ""

    if fetched:
        from app.models.base import utcnow

        row.commit = fetched.get("commit", "")
        row.dirty = bool(fetched.get("dirty"))
        row.doc_count = int(fetched.get("doc_count", 0))
        # The ref the clone landed on, which is not always the one asked
        # for: an empty request resolves to the remote's default branch,
        # and storing what was read beats storing what was typed.
        row.ref = fetched.get("ref") or row.ref
        row.fetched_at = utcnow()
    return row


class NoCredential(ValueError):
    """This registration holds no token."""


def token_for(row: ProjectRepo) -> str:
    """The credential, decrypted, or "" for a public repository.

    Absence is an answer here rather than an error, which is the opposite
    of `jira/store.token_for`. A Jira link with no token can fetch
    nothing; a public repository clones perfectly well without one, and
    the common case should not have to catch an exception.
    """
    if not (row.ciphertext or "").strip():
        return ""
    return _cipher().decrypt(row.ciphertext.encode("ascii")).decode("utf-8")


def for_project(session, project_id: str) -> ProjectRepo | None:
    return session.scalar(
        select(ProjectRepo).where(ProjectRepo.project_id == project_id)
    )


def record_failure(session, project_id: str, reason: str) -> None:
    """Remember why a refresh failed, for the person who was not watching.

    Only ever written against a row that already exists: a *first*
    registration that fails leaves nothing behind, by design.
    """
    row = for_project(session, project_id)
    if row is not None:
        row.last_error = (reason or "")[:2000]


def listing(session, project_id: str | None = None) -> list[dict[str, Any]]:
    """Every registration, or one project's, without decrypting anything."""
    query = select(ProjectRepo).order_by(ProjectRepo.project_id)
    if project_id:
        query = query.where(ProjectRepo.project_id == project_id)
    return [
        {
            "project_id": row.project_id,
            "repo_url": row.repo_url,
            "ref": row.ref,
            "docs_path": row.docs_path,
            "commit": row.commit,
            "short_commit": row.commit[:12] if row.commit else "",
            "dirty": row.dirty,
            "doc_count": row.doc_count,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
            "last_error": row.last_error,
            # The hint, never the token. `private` is what a list actually
            # wants to show, and it follows from the hint being there.
            "hint": row.hint,
            "private": bool(row.ciphertext),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in session.scalars(query).all()
    ]


def delete(session, project_id: str) -> bool:
    """Forget one registration.

    Anything already derived from that repository stays, the same rule
    `delete_import` and `delete_jira_connection` keep: removing a source
    does not empty the pages built from it.
    """
    row = for_project(session, project_id)
    if row is None:
        return False
    session.delete(row)
    return True
