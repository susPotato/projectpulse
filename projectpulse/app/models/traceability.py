"""The backlog export a traceability run is built from.

The pipeline's `tickets` and `governance` stages read a tracker export as a
workbook - not the domain tables. That is deliberate upstream: `governance`
exists because the keyed PM tickets carry 7,987 characters of prose that the
feature rows do not, and that prose is in the export. Handing those stages
rows we had already normalised would lose the thing they are for.

**Why this is a table rather than a file on disk.** Exactly the reason
`UploadedSheet` gives: a Fly machine's filesystem does not survive a deploy,
so an export written to the container would have to be re-uploaded after every
release, and the run that depended on it would quietly have nothing behind it.

**Why it is not an `UploadedSheet`.** That row means "a workbook we read as a
schedule or a worklog", and its `kind` resolves to a `SheetContract` the excel
sync parses on every scan. A Jira export is neither of those shapes - it is the
*input* `app/ingest/sources/jira/export_sheet.py` converts into them, and the
conversion throws the original away. Storing it under a `kind` the sync does
not know would work only for as long as `all_watched()` keeps skipping unknown
kinds, which is an accident of that function rather than a promise.

One row per delivery project, and re-uploading replaces: a project has one
backlog, and two exports claiming to be it would give `tickets` and
`governance` two different answers about the same run.
"""

from __future__ import annotations

from sqlalchemy import LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Timestamped


class TicketExport(Timestamped, Base):
    """One delivery project's backlog export, as uploaded."""

    __tablename__ = "ticket_exports"

    #: The delivery project this is the backlog for, as `app.scope` knows it.
    project_id: Mapped[str] = mapped_column(String(200), primary_key=True)

    #: What the person called the file. Shown back to them so a run can say
    #: which export it was built from, rather than only when.
    original_filename: Mapped[str] = mapped_column(Text, default="")

    #: The workbook, byte for byte as uploaded. Never the converted sheets -
    #: see the module docstring.
    content: Mapped[bytes] = mapped_column(LargeBinary)

    #: The project name *inside* the export, when it holds more than one.
    #: Passed to the pipeline as `--project`; empty means take every row.
    #: Stored rather than asked for per run, because it is a property of the
    #: file and getting it wrong produces a run about somebody else's backlog.
    project_filter: Mapped[str] = mapped_column(String(200), default="")

    #: Which tracker status(es) mean finished, comma separated. The pipeline
    #: refuses to guess this - `reconcile` is skipped without it, and says so.
    done_status: Mapped[str] = mapped_column(String(200), default="")

    #: "loopback" or "admin-token", matching `ProjectRepo.updated_by`.
    updated_by: Mapped[str] = mapped_column(String(32), default="")
