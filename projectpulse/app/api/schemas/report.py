"""The report builder's contract: what can be chosen, and what it will produce.

Two bundles, and the split is the point.

`ReportOptions` is the catalogue - the presets and the sections, straight from
`app/exports/document.py`. The builder screen renders *that* rather than its own
list, so a section added to the exporter appears in the UI with no front-end
change, and the UI cannot offer one the exporter does not have.

`ReportPreview` is the document itself, as blocks. It is the same `ReportDoc`
the `.docx`, `.xlsx` and Markdown renderers walk, so what a PM reads on the
screen before pressing download is not an approximation of the file - it is the
file, rendered by a fourth walker. A preview built from a second description of
the report is a preview that eventually lies.
"""

from __future__ import annotations

from pydantic import Field

from app.api.schemas.base import Response


class ReportBlock(Response):
    """One block. Mirrors `exports.document.Block` exactly.

    Every value is already a string: the document module formatted them through
    `assembler.format_fact` before they reached this schema, so no number
    crosses the wire as a float for a browser's locale to re-render its own way.
    """

    kind: str
    text: str = ""
    level: int = 1
    items: list[str] = Field(default_factory=list)
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    label: str = ""


class ReportSection(Response):
    """A part of the report that was actually built.

    A section the PM ticked but which had no data to fill it is absent here
    rather than present and empty - the same rule `build_document` follows.
    """

    id: str
    title: str
    blocks: list[ReportBlock] = Field(default_factory=list)


class ReportPreview(Response):
    """The whole document, exactly as every downloadable format will render it."""

    title: str
    preamble: list[ReportBlock] = Field(default_factory=list)
    sections: list[ReportSection] = Field(default_factory=list)
    #: Echoed back so the screen can show what it actually got, rather than what
    #: it asked for. They differ whenever a section had no data.
    resolved_sections: list[str] = Field(default_factory=list)


class ReportSectionOption(Response):
    """One section a PM can switch on or off."""

    id: str
    title: str
    description: str
    #: What the section needs before it can say anything - `explain`,
    #: `scenarios`, `risks`, or `findings` for the evidence sub-toggle. Empty
    #: when the section is always available.
    requires: str = ""
    #: Whether this project has that data today. The screen disables a tick box
    #: rather than offering one that silently produces nothing.
    available: bool = True


class ReportPresetOption(Response):
    """A named audience and the sections it selects."""

    id: str
    label: str
    description: str
    sections: list[str] = Field(default_factory=list)


class ReportFormatOption(Response):
    """One downloadable format.

    `available` is not decoration: `.docx` needs the optional `python-docx`
    extra, and a download button that 500s at a demo is worse than a button
    that says why it is greyed out.
    """

    id: str
    label: str
    extension: str
    description: str
    available: bool = True
    unavailable_reason: str = ""


class ReportOptions(Response):
    """Everything the builder screen needs to draw itself."""

    project_id: str
    presets: list[ReportPresetOption] = Field(default_factory=list)
    sections: list[ReportSectionOption] = Field(default_factory=list)
    formats: list[ReportFormatOption] = Field(default_factory=list)
    default_preset: str = ""
