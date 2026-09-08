"""The status report a project manager circulates.

The same findings as the Insight screen, in the format the conversation around
them actually happens in - a document attached to an email, read by people who
will never open the app.

**It formats nothing.** Every figure is either already-substituted prose from
`Finding.headline` or passed through `assembler.format_fact`, the one formatter
in the product. That matters more here than anywhere else: a report is the
artefact that outlives the session, so a number rendered its own way here would
be the copy that gets quoted in a steering meeting six weeks later, disagreeing
with a screen nobody has open.

**It states its own provenance.** Every finding carries its rule trace, its
causal basis and its source rows, and the report ends with what the analysis
could not use. A status report that presents conclusions without saying which
rest on inferred dependencies is the exact document this product exists to
replace.

`python-docx` is an optional extra. Nothing else imports this module, so the
demo, the tests and the API all run without it - `report_bytes` raises a plain
message naming the extra if it is missing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

from app.api.schemas.explain import ExplainBundle
from app.api.schemas.insight import Finding, InsightBundle
from app.intelligence.assembler import format_fact
from app.narration.fallback import QUESTION_HEADINGS

#: How many source rows to list per finding. The evidence panel on screen caps
#: at the same number, and for the same reason: enough to show the claim is
#: real, few enough that the page stays readable.
MAX_EVIDENCE = 5

#: How many tasks to list in the projection table. The driving path is what a
#: steering conversation is about; a fifty-row table is an appendix.
MAX_PROJECTION_ROWS = 12

_SEVERITY_ORDER = ("critical", "high", "medium", "low", "info")


class ReportUnavailable(RuntimeError):
    """`python-docx` is not installed."""


def _docx():
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ReportUnavailable(
            'python-docx is not installed; pip install -e ".[report]"'
        ) from exc
    return docx


def _basis_in_words(basis: str) -> str:
    """How a claim was established, for a reader who will not know the term.

    The words matter: `same_project` sounds like a connection and is barely one,
    and a report that renders it as "linked" would overstate the weakest
    evidence the system produces.
    """
    return {
        "dependency_edge": "a dependency stated in the schedule sheet",
        "dependency_path": "a chain of dependencies, not a direct link",
        "same_entity": "both changes are on the same task",
        "same_project": "only that both belong to this project - the weakest "
                        "link the system reports",
        "none": "no causal link was established",
    }.get(basis, basis)


def _add_finding(document, finding: Finding, index: int) -> None:
    """One finding, with everything needed to challenge it."""
    document.add_heading(
        f"{index}. [{finding.severity.upper()}] {finding.category}", level=2
    )

    # Already substituted by the assembler. Rendered verbatim.
    document.add_paragraph(finding.headline)

    if finding.recommendation:
        paragraph = document.add_paragraph()
        paragraph.add_run("Recommended action: ").bold = True
        paragraph.add_run(finding.recommendation)

    if finding.rule_trace is not None:
        paragraph = document.add_paragraph()
        paragraph.add_run(f"Rule {finding.rule_trace.rule_id} fired because:").bold = True
        for condition in finding.rule_trace.conditions:
            document.add_paragraph(condition, style="List Bullet")
        if finding.rule_trace.rationale:
            note = document.add_paragraph()
            note.add_run("Why this threshold: ").italic = True
            note.add_run(finding.rule_trace.rationale).italic = True

    link = finding.causal_link
    if link is not None:
        paragraph = document.add_paragraph()
        paragraph.add_run("Cause: ").bold = True
        paragraph.add_run(
            f"{link.cause.entity_label} ({link.cause.field}) explains "
            f"{link.effect.entity_label} ({link.effect.field}), "
            f"{format_fact('lag_min', link.lag_days_min)} to "
            f"{format_fact('lag_max', link.lag_days_max)} days later. "
            f"Established on {_basis_in_words(link.evidence_basis)}."
        )
        if link.ordering_basis != "exact":
            timing = document.add_paragraph()
            timing.add_run(
                "The later change was seen by comparing two spreadsheet "
                "snapshots, so its timing is a range rather than a point. The "
                "order still holds because the two ranges do not overlap."
            ).italic = True

    if finding.evidence:
        paragraph = document.add_paragraph()
        paragraph.add_run("Source rows: ").bold = True
        for ref in finding.evidence[:MAX_EVIDENCE]:
            where = ref.remark or ref.url or ref.raw_table or "source record"
            document.add_paragraph(
                f"{where}  (record {ref.raw_data_id})", style="List Bullet"
            )


def _add_projection(document, explain: ExplainBundle) -> None:
    """The slip the dependency chain implies but the sheet does not yet show.

    The driving path first, because that is the only part of the table anyone
    acts on - the rest is context for why those dates move.
    """
    document.add_heading("Schedule projection", level=1)

    if explain.project_slip_days is not None:
        paragraph = document.add_paragraph()
        paragraph.add_run("Projected slip against the plan: ").bold = True
        paragraph.add_run(
            f"{format_fact('project_slip_days', explain.project_slip_days)} days"
        )

    if explain.depends_on_inferred_edges:
        caveat = document.add_paragraph()
        caveat.add_run(
            "This projection depends on dependency edges inferred from the "
            "schedule's own dates rather than stated by a person. Confirm them "
            "before quoting this figure outside the team."
        ).italic = True

    driving = set(explain.driving_path)
    # Driving path first, then the rest - each group keeping the forward-pass
    # order, so the table still reads as a chain rather than a ranking.
    steps = [s for s in explain.steps if s.entity_id in driving]
    steps += [s for s in explain.steps if s.entity_id not in driving]
    steps = steps[:MAX_PROJECTION_ROWS]
    if not steps:
        return

    table = document.add_table(rows=1, cols=5)
    table.style = "Light Grid Accent 1"
    for cell, label in zip(
        table.rows[0].cells,
        ("Task", "Planned finish", "Projected finish", "Implied slip", "On driving path"),
    ):
        cell.text = label
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True

    for step in steps:
        cells = table.add_row().cells
        cells[0].text = step.label
        cells[1].text = format_fact("planned_end", step.planned_end) if step.planned_end else "-"
        cells[2].text = (
            format_fact("projected_end", step.projected_end) if step.projected_end else "-"
        )
        cells[3].text = (
            format_fact("propagated_days", step.propagated_days)
            if step.propagated_days
            else "-"
        )
        cells[4].text = "yes" if step.entity_id in driving else "no"


def _add_data_quality(document, bundle: InsightBundle) -> None:
    """What the analysis could not use.

    Last section rather than a footnote, and never omitted. A report of
    conclusions drawn on part of a project, which does not say which part, is
    the document this product is arguing against.
    """
    document.add_heading(QUESTION_HEADINGS[4], level=1)
    quality = bundle.data_quality

    rows = (
        ("Source rows that could not be parsed", "rows_rejected", quality.rows_rejected),
        (
            "Changes whose row identity is uncertain",
            "changes_low_confidence",
            quality.changes_low_confidence,
        ),
        ("State changes observed", "changes_total", quality.changes_total),
        (
            "Tasks with a baseline to compare against",
            "baseline_coverage",
            quality.baseline_coverage,
        ),
        ("Dependency edges stated by a person", "edges_stated", quality.edges_stated),
        (
            "Dependency edges inferred from dates",
            "edges_inferred",
            quality.edges_inferred,
        ),
        ("Dependency edges dropped as unusable", "edges_dropped", quality.edges_dropped),
    )
    for label, name, value in rows:
        paragraph = document.add_paragraph(style="List Bullet")
        paragraph.add_run(f"{label}: ")
        paragraph.add_run(format_fact(name, value)).bold = True

    if quality.depends_on_inferred_edges:
        note = document.add_paragraph()
        note.add_run(
            "The schedule conclusion changes if the inferred edges are removed. "
            "It is a derived claim, not a stated one."
        ).italic = True


def build_report(
    bundle: InsightBundle,
    *,
    explain: ExplainBundle | None = None,
    project_name: str = "",
    generated_at: datetime | None = None,
):
    """The whole report as a `docx.Document`, ready to save."""
    docx = _docx()
    document = docx.Document()

    stamp = generated_at or datetime.now(timezone.utc)
    document.add_heading(f"Delivery status - {project_name or bundle.project_id}", 0)

    header = document.add_paragraph()
    header.add_run(
        f"Reflects the project as at {format_fact('as_of', bundle.as_of)}. "
        f"Report generated {format_fact('generated_at', stamp)}."
    ).italic = True

    # Where the prose came from, before the prose. A reader who cannot tell
    # which they are looking at cannot judge either.
    source = document.add_paragraph()
    source.add_run(
        "Summary written by "
        + (
            "a language model, from findings it was not permitted to change."
            if bundle.narration_source == "model"
            else "the deterministic template."
        )
    ).italic = True

    # The narrative arrives with its own headings, which are the four questions.
    # Split so each becomes a real heading rather than a wall of text.
    document.add_heading("Summary", level=1)
    for block in bundle.narrative.split("\n\n"):
        lines = block.strip().split("\n", 1)
        if len(lines) == 2 and lines[0] in QUESTION_HEADINGS:
            document.add_heading(lines[0], level=2)
            document.add_paragraph(lines[1])
        elif block.strip():
            document.add_paragraph(block.strip())

    document.add_heading("Findings", level=1)
    if not bundle.findings:
        document.add_paragraph(
            "Nothing in this project breaches a delivery threshold."
        )
    for index, finding in enumerate(bundle.by_severity(), start=1):
        _add_finding(document, finding, index)

    if explain is not None:
        _add_projection(document, explain)

    _add_data_quality(document, bundle)
    return document


def report_bytes(
    bundle: InsightBundle,
    *,
    explain: ExplainBundle | None = None,
    project_name: str = "",
    generated_at: datetime | None = None,
) -> bytes:
    """The report as bytes, for an HTTP response or a file write."""
    buffer = BytesIO()
    build_report(
        bundle,
        explain=explain,
        project_name=project_name,
        generated_at=generated_at,
    ).save(buffer)
    return buffer.getvalue()
