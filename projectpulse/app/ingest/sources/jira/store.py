"""Saving, listing and using a Jira connection.

The credential half defers entirely to `app/llm/keys.py`: same
`PULSE_SECRET_KEY`, same Fernet, same masked hint, so there is one
rotation story rather than two. Nothing here implements encryption.

**A saved connection is normalised.** `connect.normalise_site` runs before
the row is written, so what is stored is a base URL the collector can use
directly - not the ticket page somebody pasted. Validating on the way in
means every later read is already correct, rather than every later reader
re-deriving it and one of them getting it wrong.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.ingest.sources.jira import connect
from app.models.jira import JiraConnection


def _cipher():
    from app.llm import keys

    return keys._fernet()


def save(session, *, project_id: str, site: str, email: str,
         project_key: str, token: str, actor: str = "") -> JiraConnection:
    """Create or update the link between one delivery project and one Jira.

    An empty `token` on an existing row keeps the stored one, so somebody
    editing the project key does not have to paste their credential again -
    and cannot accidentally blank it by leaving the field alone.
    """
    from app.llm import keys

    project_id = (project_id or "").strip()
    project_key = (project_key or "").strip().upper()
    if not project_id:
        raise ValueError("Choose which delivery project this Jira feeds.")
    if not project_key:
        raise ValueError("Name the Jira project key, e.g. COWORKLOCAL.")

    # Raises `ConnectionRefused` for anything unfetchable, so a row is
    # never written pointing at localhost or plain http.
    base = connect.normalise_site(site)

    row = session.scalar(
        select(JiraConnection).where(
            JiraConnection.project_id == project_id,
            JiraConnection.project_key == project_key,
        )
    )
    if row is None:
        # A connection with no credential is a *link*: it binds a Jira
        # project key to a delivery project and nothing more. That is all
        # `/api/jira/ingest` needs, and it is what a server that cannot
        # reach Jira should hold - there is no reason for a token to sit
        # on a host that could never use it. `Collect` refuses such a row
        # with a reason rather than failing at the request.
        row = JiraConnection(project_id=project_id, project_key=project_key,
                             ciphertext="", hint="")
        session.add(row)

    row.site = base
    row.email = (email or "").strip()
    row.updated_by = actor or ""
    if (token or "").strip():
        row.ciphertext = _cipher().encrypt(token.encode("utf-8")).decode("ascii")
        row.hint = keys.mask(token)
    return row


class NoCredential(ValueError):
    """This link holds no token, so nothing can be fetched with it."""


def token_for(row: JiraConnection) -> str:
    """The credential, decrypted. Raises if there is none, or it is sealed
    with a key this instance cannot read."""
    if not (row.ciphertext or "").strip():
        raise NoCredential(
            f"the link to {row.project_key} holds no API token, so this "
            f"server cannot fetch with it. That is the normal state for a "
            f"host that Jira refuses anyway - push issues to it with "
            f"`python -m scripts.jira_push` instead."
        )
    return _cipher().decrypt(row.ciphertext.encode("ascii")).decode("utf-8")


def listing(session, project_id: str | None = None) -> list[dict[str, Any]]:
    """Every connection, or one project's, without decrypting anything."""
    query = select(JiraConnection).order_by(JiraConnection.project_id,
                                            JiraConnection.project_key)
    if project_id:
        query = query.where(JiraConnection.project_id == project_id)
    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "site": row.site,
            "email": row.email,
            "project_key": row.project_key,
            # The hint, never the token. A list is a read-only screen and
            # has no business holding a credential it does not need.
            "hint": row.hint,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
        for row in session.scalars(query).all()
    ]


def delete(session, connection_id: int) -> bool:
    """Forget one link. The rows it collected stay, as `delete_import` does."""
    row = session.get(JiraConnection, connection_id)
    if row is None:
        return False
    session.delete(row)
    return True
