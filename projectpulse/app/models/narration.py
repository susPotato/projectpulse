"""Where a model's phrasing is remembered, so the same facts are paid for once.

The reasoning for caching at all is in `app/narration/cache.py`; this is only
the table it writes to.

Persisted rather than held in process memory because a Fly deployment stops
machines when idle and runs more than one: an in-memory cache would miss on
every cold start and disagree between machines answering consecutive requests.

**The key is a fingerprint of the facts, not of the project.** `cache_key` is a
sha256 of the project id plus the deterministic template narrative - which is
itself built from every finding the model would have been shown - so the entry
is invalidated by the data changing rather than by a clock. There is deliberately
no expiry column: an entry whose facts still hold is still correct, and one whose
facts have changed is never looked up again because the fingerprint moved.
"""

from __future__ import annotations

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class NarrationCacheEntry(Timestamped, Base):
    """One model-written narrative, keyed by the facts that produced it.

    Only a *successful* model draft is ever written here - see the cache
    module's docstring. Caching a fallback would make a transient network blip
    or a credential about to be fixed stick for the life of the entry.
    """

    __tablename__ = "narration_cache"

    #: sha256 hex of `project_id` + the template narrative. 64 characters,
    #: fixed - sized exactly rather than left as unbounded text because it is
    #: the primary key and every lookup is an equality test on it.
    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: Kept alongside the key even though it is folded into it. The key is a
    #: hash and cannot be read back, so without this column there is no way to
    #: answer "what is cached for this project" - which is what the Settings
    #: screen and any future eviction would both need.
    project_id: Mapped[str] = mapped_column(String(255), index=True)

    #: The prose as the model wrote it, after the eight-stage gate accepted it
    #: and the server substituted the figures. Text, not String: a narrative is
    #: four paragraphs and a column limit here would truncate one silently.
    narrative: Mapped[str] = mapped_column(Text)
