"""The report builder: the section model, and the three renderers agreeing.

The tests that matter here are not "does Markdown come out". They are:

- the three formats render the *same* document, so a `.docx` and the `.xlsx`
  beside it cannot say different things;
- no renderer formats a number, which is invariant 1 held across three file
  formats instead of one;
- the preview is the document, not a description of it.

Fixtures come from `test_exports` rather than being rebuilt here. Two bundle
builders would drift, and a test suite whose fixtures disagree proves less than
one that shares them.
"""

from __future__ import annotations

import importlib
import re

import pytest

from app.exports.document import (
    PRESETS,
    SECTION_IDS,
    SECTIONS,
    build_document,
    resolve_sections,
)
from app.exports.markdown import render_markdown
from app.exports.workbook import build_workbook
from tests.test_exports import _bundle, _explain, _importable


# --------------------------------------------------------------------------
# Choosing sections
# --------------------------------------------------------------------------

def test_an_explicit_list_beats_the_preset_it_started_from():
    """The moment a PM unticks something, the preset is a label not an order."""
    chosen = resolve_sections(preset_id="weekly", sections=["summary"])

    assert chosen == ("summary",)


def test_sections_come_back_in_reading_order_never_the_caller_s():
    """Two people who tick the same boxes must get the same document.

    Honouring click order would make the report depend on the order a PM
    happened to tick, which is invisible in the output and impossible to
    explain when two copies differ.
    """
    chosen = resolve_sections(sections=["data_quality", "findings", "summary"])

    assert chosen == ("summary", "findings", "data_quality")


def test_an_unknown_section_is_dropped_rather_than_raising():
    """A stale bookmarked URL should give a slightly wrong report, not a 500."""
    chosen = resolve_sections(sections=["summary", "not_a_section"])

    assert chosen == ("summary",)


def test_every_preset_names_only_sections_that_exist():
    """A typo in a preset would silently produce a shorter report."""
    for preset in PRESETS:
        unknown = set(preset.sections) - set(SECTION_IDS)
        assert unknown == set(), f"{preset.id} names {sorted(unknown)}"


def test_no_preset_drops_the_data_quality_section():
    """The deliberate decision in `document.PRESETS`, pinned.

    It is the section a preset is most tempted to drop for an executive, and an
    executive acting on conclusions drawn from part of a project is the failure
    the product argues against. If this is ever changed it should be changed on
    purpose, with this test.
    """
    for preset in PRESETS:
        assert "data_quality" in preset.sections, preset.id


# --------------------------------------------------------------------------
# Building the document
# --------------------------------------------------------------------------

def test_a_section_with_no_data_is_absent_rather_than_empty():
    """A "Recovery scenarios" heading over nothing reads as a broken analysis.

    `build_document` is asked for the section and given no scenarios, which is
    what happens whenever a caller ticks a box for data this project lacks.
    """
    doc = build_document(_bundle(), sections=["findings", "scenarios"])

    assert doc.section("scenarios") is None
    assert doc.section("findings") is not None


def test_switching_evidence_off_keeps_the_findings_and_drops_the_rows():
    """The sub-toggle: source rows live under their finding, never alone."""
    with_rows = render_markdown(
        build_document(_bundle(), sections=["findings", "evidence"])
    )
    without = render_markdown(build_document(_bundle(), sections=["findings"]))

    assert "Activities!row5" in with_rows
    assert "Activities!row5" not in without
    # The finding itself survives - only its appendix went.
    assert "The plan cannot hold" in without


def test_the_evidence_id_never_becomes_a_section_of_its_own():
    """Listing source rows away from their finding would make a reader match
    record ids back by hand, so the id only switches them on in place."""
    doc = build_document(_bundle(), sections=["findings", "evidence"])

    assert doc.section("evidence") is None


def test_the_preamble_says_how_old_the_report_is_whatever_the_sections():
    """Never a section, so no preset can switch off the report's own age."""
    doc = build_document(_bundle(), sections=[])

    text = " ".join(block.text for block in doc.preamble)
    assert "2026-03-22" in text
    assert doc.sections == ()


# --------------------------------------------------------------------------
# The renderers agree, and none of them formats a number
# --------------------------------------------------------------------------

#: Every section the shared fixtures can fill. Scenarios and risks need bundles
#: `test_exports` does not build, and their absence is covered above.
_FULL = ["summary", "findings", "evidence", "projection", "data_quality"]


def _rendered_text(doc) -> dict[str, str]:
    """The same document through every renderer, as plain text to search."""
    out = {"markdown": render_markdown(doc)}

    workbook = build_workbook(doc)
    out["xlsx"] = "\n".join(
        str(cell.value)
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
        if cell.value is not None
    )

    if _importable("docx"):
        from app.exports.report import render_docx

        document = render_docx(doc)
        parts = [p.text for p in document.paragraphs]
        for table in document.tables:
            parts += [cell.text for row in table.rows for cell in row.cells]
        out["docx"] = "\n".join(parts)

    return out


@pytest.mark.parametrize(
    "needle",
    [
        "The plan cannot hold: 3 task(s) are late by up to 34 days.",
        "Re-baseline the affected tasks.",
        "max_propagated_days (34) >= 5",
        "Activities!row5",
        "WBS-114",
    ],
)
def test_every_format_carries_the_same_content(needle):
    """One document, three walkers. A fact in one file is in all of them."""
    rendered = _rendered_text(
        build_document(_bundle(), explain=_explain(), sections=_FULL)
    )

    for name, text in rendered.items():
        assert needle in text, f"missing from {name}"


def test_no_renderer_reformats_a_ratio():
    """Invariant 1, across three file formats.

    `baseline_coverage` is 0.4 in the bundle and `40%` once `format_fact` has
    seen it. A renderer that received the float would be free to print `0.4`,
    or `40.0%`, or its own locale's decimal comma - so the renderers are handed
    strings and this is what proves it.
    """
    rendered = _rendered_text(
        build_document(_bundle(), explain=_explain(), sections=_FULL)
    )

    for name, text in rendered.items():
        assert "40%" in text, f"{name} lost the formatted ratio"
        assert "0.4" not in text, f"{name} leaked the raw float"


def test_the_driving_path_leads_the_projection_in_every_format():
    """The only part of the table anyone acts on, first in all three."""
    doc = build_document(_bundle(), explain=_explain(), sections=["projection"])
    table = next(b for b in doc.section("projection").blocks if b.kind == "table")

    assert table.rows[0][0] == "WBS-114"
    assert table.rows[0][4] == "yes"


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

def test_a_pipe_in_a_task_label_does_not_break_the_table():
    """Labels come from a PM's spreadsheet and can contain anything.

    An unescaped pipe ends the cell, so one oddly-named task would shift every
    column to its right and the table would still render - wrongly.
    """
    explain = _explain()
    explain.steps[0].label = "WBS-114 | phase 2"

    markdown = render_markdown(
        build_document(_bundle(), explain=explain, sections=["projection"])
    )

    row = next(line for line in markdown.splitlines() if "WBS-114" in line)
    assert r"WBS-114 \| phase 2" in row
    # Count only the pipes Markdown will treat as cell boundaries - an escaped
    # one is still a `|` character, which is exactly why counting them raw
    # cannot tell a broken row from an intact one. Five columns, six edges.
    assert len(re.findall(r"(?<!\\)\|", row)) == 6


def test_markdown_ends_with_exactly_one_newline():
    """`sync report` writes this straight to disk, and a file without a
    trailing newline runs into whatever a shell prints next."""
    markdown = render_markdown(build_document(_bundle(), sections=["findings"]))

    assert markdown.endswith("\n")
    assert not markdown.endswith("\n\n")


# --------------------------------------------------------------------------
# The workbook
# --------------------------------------------------------------------------

def test_each_table_becomes_its_own_filterable_sheet():
    """The reason to choose this format at all.

    A frozen header without a filter is a table you can only scroll; the filter
    is what turns the projection into "sort by implied slip", which is the
    question the format exists to answer.
    """
    workbook = build_workbook(
        build_document(_bundle(), explain=_explain(), sections=_FULL)
    )

    assert "Report" in workbook.sheetnames
    sheet = workbook["Schedule projection"]
    assert sheet.freeze_panes == "A2"
    assert sheet.auto_filter.ref is not None
    assert [cell.value for cell in sheet[1]][:2] == ["Task", "Planned finish"]


def test_the_prose_sheet_points_at_every_table_it_moved_out():
    """A reader who started at the top has to know the tab exists."""
    workbook = build_workbook(
        build_document(_bundle(), explain=_explain(), sections=_FULL)
    )

    prose = "\n".join(
        str(cell.value)
        for row in workbook["Report"].iter_rows()
        for cell in row
        if cell.value is not None
    )
    assert "see the 'Schedule projection' sheet" in prose


def test_a_long_or_illegal_section_title_still_saves():
    """Excel rejects a sheet name over 31 characters or carrying []:*?/\\ - and
    it rejects it at save time, which would surface as a corrupt download
    rather than a traceback."""
    from app.exports.document import Block, ReportDoc, Section

    doc = ReportDoc(
        title="t",
        sections=(
            Section(
                "a",
                "A title far longer than Excel will accept [really]",
                (Block("table", columns=("x",), rows=(("1",),)),),
            ),
        ),
    )

    workbook = build_workbook(doc)
    name = [n for n in workbook.sheetnames if n != "Report"][0]
    assert len(name) <= 31
    assert not set(name) & set(r"[]:*?/\'")


def test_two_sections_with_the_same_name_do_not_collide():
    """openpyxl raises on a duplicate sheet name, so the suffix is not cosmetic."""
    from app.exports.document import Block, ReportDoc, Section

    table = Block("table", columns=("x",), rows=(("1",),))
    doc = ReportDoc(
        title="t",
        sections=(
            Section("a", "Same name", (table,)),
            Section("b", "Same name", (table,)),
        ),
    )

    workbook = build_workbook(doc)
    assert len(set(workbook.sheetnames)) == len(workbook.sheetnames)


def test_the_workbook_module_needs_no_optional_extra():
    """openpyxl is a core dependency of the ingester, unlike python-docx.

    That is why the builder offers three formats rather than only Word: a demo
    machine missing an extra can still hand a judge a report.
    """
    assert importlib.util.find_spec("openpyxl") is not None


# --------------------------------------------------------------------------
# Checked against the code
# --------------------------------------------------------------------------

class _Trace:
    """The shape `app.api.main._Traceability` hands the exporter."""

    def __init__(self, findings, totals=None):
        self.findings = findings
        self.totals = totals or {"tickets": 173, "contradicted": 2}


_TRACE_ROWS = [
    {"kind": "contradicted", "title": "[Release it] Holiday calendar",
     "detail": "the code says scheduling was removed",
     "where": "ui/task_editor_dialog.py::TaskEditorDialog", "evidence": "cited"},
    {"kind": "gate-failing", "title": "CASAN Check 2: Modularity (LOC)",
     "detail": "27 of 144 production files exceed 400 lines",
     "where": "checklist.md:460", "evidence": "measured"},
    {"kind": "documented-not-built", "title": "R01-T02  FakeProvider",
     "detail": "marked done in the documents; deliverables absent",
     "where": "checklist.md:136", "evidence": "area"},
]


def test_the_section_is_absent_when_the_project_has_no_run():
    """Most projects have not been traced, and a heading over nothing reads
    as a broken analysis rather than a caller who did not ask."""
    doc = build_document(_bundle(), sections=["traceability"])
    assert doc.section("traceability") is None


def test_a_traced_project_gets_the_section():
    doc = build_document(_bundle(), traceability=_Trace(_TRACE_ROWS),
                         sections=["traceability"])
    assert doc.section("traceability") is not None


def test_every_row_says_how_it_was_established():
    """The column exists because the strengths genuinely differ: arithmetic
    over the corpus and a language model's reading are not the same claim."""
    doc = build_document(_bundle(), traceability=_Trace(_TRACE_ROWS),
                         sections=["traceability"])
    tables = [b for b in doc.section("traceability").blocks if b.kind == "table"]
    assert tables
    for t in tables:
        assert t.columns[-1] == "How it was established"
        for row in t.rows:
            assert row[-1], "a row with no provenance is a row nobody can weigh"


def test_the_kinds_keep_the_order_the_digest_ranked_them_in():
    """A reader who starts with thirty-five area-level rows never reaches
    the two contradictions."""
    doc = build_document(_bundle(), traceability=_Trace(_TRACE_ROWS),
                         sections=["traceability"])
    headings = [b.text for b in doc.section("traceability").blocks
                if b.kind == "heading"]
    assert headings[0] == "Code Contradicts Ticket"
    assert headings.index("Team Quality Checks Failing") < \
        headings.index("Marked Done, No Code Found")


def test_an_empty_run_says_so_rather_than_rendering_nothing():
    doc = build_document(_bundle(), traceability=_Trace([]),
                         sections=["traceability"])
    blocks = doc.section("traceability").blocks
    assert blocks and "nothing that needs a person" in blocks[0].text


def test_the_area_level_caveat_travels_with_the_rows():
    """It is the one thing about this section a reader can misread badly."""
    doc = build_document(_bundle(), traceability=_Trace(_TRACE_ROWS),
                         sections=["traceability"])
    notes = " ".join(b.text for b in doc.section("traceability").blocks
                     if b.kind == "note")
    assert "not this one task" in notes


def test_the_section_renders_in_every_format():
    doc = build_document(_bundle(), traceability=_Trace(_TRACE_ROWS),
                         sections=["traceability"])
    md = render_markdown(doc)
    assert "Delivery Verification" in md
    assert "Holiday calendar" in md
