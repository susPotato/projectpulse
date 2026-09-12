"""State a deployed app has to keep, and a container's disk cannot.

Two tables, both here for the same reason the narration cache and the OneDrive
sign-in are in the database: **a Fly machine's filesystem does not survive a
deploy**, and a deployment can run more than one machine. Before this, a
document imported through the browser wrote its bytes to `settings.data_root`
and its registration to two JSON files under `PULSE_STATE_DIR`, all three on
container-local disk - so importing a project worked until the next
`fly deploy`, at which point the project vanished from the picker while its
ingested rows sat on in Postgres, orphaned.

That was the one thing standing between "the website is a demo of the local
app" and "the website is the app".

⚠️ **`UploadedSheet.file_name` is the logical name, and half the scope key.**
It is the primary key here for exactly that reason: the scope
(`"<file_name>#<sheet_name>"`) is how a scan finds the previous scan, the
baseline rows and every `occurred_at_lower` bound, so it has to be stable
across transports and across deploys. See `excel/transport.py`, which exists
to keep a temp download from ever reaching it.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class UploadedSheet(Timestamped, Base):
    """One manually imported workbook: its bytes, and what we read it as.

    Deliberately one row rather than two - a blob table plus a registration
    table would let the two disagree, and a registration pointing at bytes
    that are not there is precisely the broken state this replaces.

    Re-importing the same document for the same project overwrites this row,
    which is what makes the differ work: the file name is derived from the
    project and the kind, so the scope is unchanged and scan N is compared
    against scan N-1 rather than looking like a sheet never seen before.
    """

    __tablename__ = "uploaded_sheets"

    #: The logical name (`upload_<project slug>_<kind>.xlsx`). Half the scope
    #: key - see the module docstring. Never a path.
    file_name: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: Which delivery project this sheet belongs to, as `app/scope.py` knows it.
    project_id: Mapped[str] = mapped_column(String(255), index=True)

    #: `schedule` or `worklog` - the key into `excel.source.SHEET_KINDS`, which
    #: is what resolves the contract. Stored rather than the contract itself,
    #: so a contract gaining a column is not a data migration.
    kind: Mapped[str] = mapped_column(String(32))

    #: The tab the table is actually on, resolved from the workbook's own
    #: contents by `reader.find_sheet` when it was imported. The other half of
    #: the scope key, and stored for the same reason: re-resolving it per scan
    #: would let a tab rename lose the baseline.
    sheet_name: Mapped[str] = mapped_column(String(255))

    #: What the person called the file when they uploaded it. Shown to a human
    #: in the evidence panel - `file_name` above is our identifier and would be
    #: meaningless to them.
    original_filename: Mapped[str | None] = mapped_column(Text, default=None)

    #: The workbook. `LargeBinary` maps to BYTEA on Postgres and BLOB on
    #: SQLite; a delivery schedule is tens of kilobytes, so the row is small
    #: and keeping it beside its registration is worth more than the elegance
    #: of object storage this deployment does not have.
    content: Mapped[bytes] = mapped_column(LargeBinary)


class AppSetting(Timestamped, Base):
    """A small JSON document someone changed while the app was running.

    One row per setting group, keyed by name (`narration` is the only one
    today). A table rather than a column per field, because these are settings
    a *screen* owns: adding one should not be a schema change, and nothing
    queries them by anything but their key.

    ⚠️ **The narration row can contain an API key.** `app/narration/store.py`
    already says the key is stored in plaintext and never returned to a
    browser; moving it here changes where that plaintext lives, not whether it
    is plaintext - the database is the security boundary, the same one the
    connection string already is. Do not log this column, do not return it
    from a route, and do not add it to a diagnostic dump. A deployment that
    injects `ANTHROPIC_API_KEY` as a platform secret never needs this row at
    all, which remains the recommended path.
    """

    __tablename__ = "app_settings"

    #: The setting group - `narration` today.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)

    #: The group's fields as a JSON object. Read through a `try` by its owning
    #: module: an unreadable value means "nobody has configured this", which
    #: is the ordinary state, not a crash.
    value: Mapped[str] = mapped_column(Text, default="{}")


class RegisteredProject(Timestamped, Base):
    """A delivery project somebody added, as opposed to the built-in demo seed.

    `app/scope.py` owns the meaning of these rows and merges them over its own
    `_SEED`; this is only where they live. A project registered here but never
    ingested is correct and shows as `no_data` on the portfolio - it is a
    project somebody has told us about, not one we have seen a sheet for.
    """

    __tablename__ = "registered_projects"

    #: The id every other source id for this project is re-pointed at.
    canonical_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: What a person calls it.
    name: Mapped[str] = mapped_column(Text)

    #: The same delivery project's ids in other source systems, JSON-encoded
    #: (invariant 7). A list in one column rather than a child table: nothing
    #: queries by it, and a join would only make `all_projects()` slower on a
    #: path that runs on every request.
    also: Mapped[str] = mapped_column(Text, default="[]")

    #: Which program this project belongs to, recorded at the moment somebody
    #: uploaded a document for it. Nullable and not a foreign key, for the same
    #: reason `Risk.project_id` is not one: a project may be registered before
    #: the program row exists, and refusing the registration for a row that has
    #: not been created yet loses the document. `app/scope.py` owns the meaning.
    program_id: Mapped[str | None] = mapped_column(String(255), default=None)


class RegisteredProgram(Timestamped, Base):
    """A program somebody created, as opposed to the built-in demo seed.

    The counterpart of `RegisteredProject`, and here for the same reason: a
    program created in the browser has to survive a deploy, and a Fly machine's
    filesystem does not.

    Kept apart from the `programs` domain table on purpose. That table is
    *materialized* - a row appears there when a collector ingests something into
    the program (`app/ingest/programs.py`) - while this one is *declared*: it is
    somebody saying "this program exists", which is true before any document
    has been seen for it. `app/scope.py` merges the two and owns the meaning, so
    a program with no projects yet is a real, listable program rather than a
    row nothing can create.
    """

    __tablename__ = "registered_programs"

    #: `program:Program:0:<KEY>` - source-neutral, see `app/scope.py`.
    program_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    #: What a person calls it.
    name: Mapped[str] = mapped_column(Text)

    owner: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(50), default="Active")
