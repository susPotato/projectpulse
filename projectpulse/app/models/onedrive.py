"""Where the Microsoft Graph sign-in is kept between runs.

The sign-in flow itself is in `app/ingest/sources/excel/graph_auth.py`; this is
only the row it serialises MSAL's token cache into.

In the database rather than on disk or in the OS credential store, for the same
reason the narration cache is: a Fly machine's filesystem is ephemeral and a
deployment runs more than one machine, so a cache on disk would mean signing in
again after every cold start and would be invisible to whichever machine happens
to be warm when a scheduled sync fires.

**This row is a credential.** MSAL's serialised cache contains a refresh token
for the signed-in account. It is stored as MSAL hands it over, which means the
database is the security boundary - the same one the connection string already
is. Do not log this column, do not return it from an API route, and do not add
it to a diagnostic dump; `graph_auth.signed_in_identity` exists so a screen can
show *who* is signed in without going near the token.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class OneDriveTokenCache(Timestamped, Base):
    """MSAL's serialised token cache, as one row.

    Single-row by design today - `graph_auth` writes it under the fixed id
    `default`, because one deployment watches one PM's OneDrive. The id column
    exists rather than a hardcoded singleton so that per-connection sign-in is a
    new row instead of a migration.
    """

    __tablename__ = "onedrive_token_cache"

    #: `default` today. See the class docstring for why it is a column.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: `msal.SerializableTokenCache.serialize()` - a JSON blob whose shape is
    #: MSAL's business, not ours. Text because it grows with the number of
    #: cached scopes and accounts, and a truncated token cache fails at refresh
    #: time rather than at write time.
    #:
    #: Read back through `cache.deserialize()`, which `graph_auth` wraps in a
    #: `try` - a corrupt value means "nobody is signed in", not a crash.
    serialized: Mapped[str] = mapped_column(Text)
