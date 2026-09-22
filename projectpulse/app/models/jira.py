"""Which Jira a delivery project is collected from.

One row per (delivery project, Jira project). A portfolio has many of both
and they do not line up one to one: two delivery projects can be tracked in
one Jira under different keys, and one delivery project can draw on two
Jiras after a merger. So the link is a row rather than a setting, and the
credential travels with it - a token that works for one site is not a token
for another.

**The token is sealed the way `LlmCredential` seals an API key**, with the
same `PULSE_SECRET_KEY` and the same masked hint stored in the clear so a
page can list connections without decrypting anything. Two credential
stores with two rotation stories would be one too many.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class JiraConnection(Timestamped, Base):
    """One delivery project, one Jira project, one credential."""

    __tablename__ = "jira_connections"
    __table_args__ = (
        # The same Jira project must not be linked to one delivery project
        # twice: the collector's watermark is keyed by (connection, project
        # key), and two rows would race each other's raw writes.
        UniqueConstraint("project_id", "project_key",
                         name="uq_jira_connection_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    #: The delivery project this feeds, as `app.scope` knows it.
    project_id: Mapped[str] = mapped_column(String(200), index=True)

    #: Base URL including any context path - `https://host/jiradc`. Stored
    #: normalised, so the collector never re-derives it from a pasted page.
    site: Mapped[str] = mapped_column(String(500))

    #: Present for Jira Cloud, empty for a Data Center bearer token. Which
    #: of the two it is follows from this, so it is not stored separately.
    email: Mapped[str] = mapped_column(String(200), default="")

    #: The Jira project key, e.g. `COWORKLOCAL`.
    project_key: Mapped[str] = mapped_column(String(64))

    #: Fernet token. Opaque without `PULSE_SECRET_KEY`; see `app/llm/keys.py`.
    ciphertext: Mapped[str] = mapped_column(Text)

    #: A masked hint, in the clear on purpose so a list can tell two
    #: credentials apart without the server decrypting either.
    hint: Mapped[str] = mapped_column(String(64), default="")

    #: Which `PULSE_SECRET_KEY` sealed this row, for the same rotation
    #: story `LlmCredential` documents.
    key_version: Mapped[int] = mapped_column(Integer, default=1)

    #: "loopback" or "admin-token" - provenance for an audit read, not
    #: identity.
    updated_by: Mapped[str] = mapped_column(String(32), default="")
