"""The report, once, in a form no file format has an opinion about.

A PM wants the same analysis as a Word attachment, as a spreadsheet they can
filter, and as text they can paste into an email. Writing that three times is
how three documents start disagreeing: one exporter rounds a ratio its own way,
another is updated when a section is added and the third is not, and six weeks
later two of them are quoted in the same meeting.

So the document is built **once**, here, as a list of blocks - headings,
paragraphs, notes, bullets, tables - and each renderer only walks it. Adding
`.pptx` later is a new walker and no new content. The rule is the same one
`report.py` already applied to the bundles: render what has been computed
rather than recomputing it, and the copies cannot drift.

**This module is the only place a report formats a number.** Every figure is
either already-substituted prose from `Finding.headline` or passed through
`assembler.format_fact`, and the renderers below it receive strings. That is
invariant 1 held across three file formats instead of one: a renderer that
cannot see a float cannot round one.

It is pure - no session, no clock, no vendor library - so the whole report is
testable without openpyxl, without python-docx and without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Sequence

from app.api.schemas.explain import ExplainBundle
from app.api.schemas.insight import Finding, InsightBundle
from app.intelligence.assembler import format_fact
from app.narration.fallback import QUESTION_HEADINGS, display_heading
from app.tracelabels import finding_title

#: How many source rows to list per finding. The evidence panel on screen caps
#: at the same number, and for the same reason: enough to show the claim is
#: real, few enough that the page stays readable.
MAX_EVIDENCE = 5

#: How many tasks to list in the projection table. The driving path is what a
#: steering conversation is about; a fifty-row table is an appendix.
MAX_PROJECTION_ROWS = 12

BlockKind = Literal["heading", "paragraph", "note", "bullets", "table"]


@dataclass(frozen=True)
class Block:
    """One piece of a report, in the smallest vocabulary all three formats share.

    Deliberately not rich. A block model that can express a page layout ends up
    being rendered faithfully by one exporter and approximated by the others,
    which is the drift this module exists to stop. Everything here has an
    obvious rendering in Word, in a spreadsheet cell and in Markdown.
    """

    kind: BlockKind
    #: `heading` and `paragraph` text; the caption of a `table`.
    text: str = ""
    #: Heading depth, 1 or 2. Ignored by the other kinds.
    level: int = 1
    #: `bullets` items.
    items: tuple[str, ...] = ()
    #: `table` header row.
    columns: tuple[str, ...] = ()
    #: `table` body. Every cell is already a string - see the module docstring.
    rows: tuple[tuple[str, ...], ...] = ()
    #: Set on a paragraph that leads with a bolded label, so Word can bold it
    #: and Markdown can emit `**label**` without parsing the sentence back.
    label: str = ""


@dataclass(frozen=True)
class Section:
    """A part of the report a PM can switch off."""

    id: str
    title: str
    blocks: tuple[Block, ...] = ()


@dataclass(frozen=True)
class ReportDoc:
    """A whole report, ready for any renderer."""

    title: str
    #: The provenance lines - as-of, generated-at, who wrote the summary. Always
    #: rendered, never a section, because a report whose age is optional is a
    #: report that gets quoted a month late.
    preamble: tuple[Block, ...] = ()
    sections: tuple[Section, ...] = ()

    def section(self, section_id: str) -> Section | None:
        return next((s for s in self.sections if s.id == section_id), None)


@dataclass(frozen=True)
class SectionSpec:
    """One switchable section, and what it needs to say anything.

    `requires` is what a caller must pass to `build_document` for the section to
    have content. It is published through the API so the builder screen can grey
    out a section it cannot fill, rather than offering a tick box that silently
    produces nothing.
    """

    id: str
    title: str
    description: str
    requires: str = ""


#: The catalogue, in the order a report reads. The builder screen renders this
#: rather than its own list, so a section added here appears in the UI without a
#: front-end change - and cannot be offered by the UI without existing here.
SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "summary",
        "Summary",
        "The four questions, in prose: what is at risk, why, what it impacts, "
        "what to do next.",
    ),
    SectionSpec(
        "findings",
        "Findings",
        "Every finding with its recommendation, the rule that fired and the "
        "causal chain behind it.",
    ),
    SectionSpec(
        "evidence",
        "Source rows",
        "The spreadsheet and Jira records each finding rests on. Switch off for "
        "an audience that will not check them.",
        requires="findings",
    ),
    SectionSpec(
        "projection",
        "Schedule projection",
        "The slip the dependency chain implies but the sheet does not yet show, "
        "driving path first.",
        requires="explain",
    ),
    SectionSpec(
        "scenarios",
        "Recovery scenarios",
        "What shortening a task or overlapping a dependency would recover, "
        "measured against the original commitment.",
        requires="scenarios",
    ),
    SectionSpec(
        "forecast",
        "Delivery forecast",
        "A range of finish dates, resampled from how far this plan has already "
        "drifted from its own baselines.",
        requires="forecast",
    ),
    SectionSpec(
        "risks",
        "Risk register",
        "The risks a PM has recorded, with their pre- and post-mitigation "
        "ratings.",
        requires="risks",
    ),
    SectionSpec(
        "model_read",
        "Proposed from task text by a model",
        "Risks a model read out of the issue text, each with the tasks it cites. "
        "Suggestions only - nothing here has been accepted into the register.",
        requires="drafts",
    ),
    SectionSpec(
        "traceability",
        "Delivery Verification",
        "What the repository says about tickets the tracker calls done, and "
        "what the team's own documents claim was built. Every row names where "
        "to look and how strong its evidence is.",
        requires="traceability",
    ),
    SectionSpec(
        "data_quality",
        "Data Limitations",
        "Rejected rows, uncertain identities and inferred dependencies. Kept in "
        "every preset on purpose.",
    ),
)

SECTION_IDS: tuple[str, ...] = tuple(spec.id for spec in SECTIONS)


@dataclass(frozen=True)
class Preset:
    """A named audience, and the sections it wants.

    Presets exist because the section list is the wrong question to ask a PM in
    a hurry. "Who is this for" they can answer; "should this contain the
    evidence appendix" they answer by leaving the default.
    """

    id: str
    label: str
    description: str
    sections: tuple[str, ...]


#: Three audiences, each a real conversation this document goes into.
#:
#: `data_quality` is in all three and that is deliberate: it is the section a
#: preset would most tempt you to drop for an executive, and an executive acting
#: on conclusions drawn from part of a project is the failure the whole product
#: argues against.
PRESETS: tuple[Preset, ...] = (
    Preset(
        "weekly",
        "Weekly status",
        "For the delivery team. Everything, including the source rows.",
        ("summary", "findings", "evidence", "projection", "model_read", "data_quality"),
    ),
    Preset(
        "steering",
        "Steering committee",
        "For the people who decide. Findings and the options, without the "
        "record-level appendix.",
        (
            "summary",
            "findings",
            "projection",
            "forecast",
            "scenarios",
            "risks",
            "model_read",
            "data_quality",
        ),
    ),
    Preset(
        "exec",
        "Executive brief",
        "One page. The outlook and what is driving it, nothing to work through.",
        ("summary", "projection", "forecast", "data_quality"),
    ),
)

DEFAULT_PRESET = "weekly"


def preset(preset_id: str) -> Preset | None:
    return next((p for p in PRESETS if p.id == preset_id), None)


def resolve_sections(
    *,
    preset_id: str | None = None,
    sections: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Which sections to build, from either a preset name or an explicit list.

    An explicit list wins, because the builder screen sends one the moment a PM
    unticks anything - at which point the preset it started from is a label, not
    an instruction. Unknown ids are dropped rather than raising: a stale
    bookmarked URL should produce a slightly wrong report, not a 500.

    The result keeps `SECTIONS` order, never the caller's. A report whose
    sections appear in tick order would come out differently for two people who
    chose the same set.
    """
    if sections is not None:
        chosen = {s for s in sections if s in SECTION_IDS}
    else:
        found = preset(preset_id or DEFAULT_PRESET)
        chosen = set(found.sections if found else ())
    return tuple(s for s in SECTION_IDS if s in chosen)


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


def _summary_blocks(bundle: InsightBundle) -> tuple[Block, ...]:
    """The narrative, split back into the four questions it was written as.

    The narrator emits its headings inline; a report that pasted the whole
    string as one paragraph would lose the structure that makes it skimmable.
    """
    blocks: list[Block] = []
    for chunk in bundle.narrative.split("\n\n"):
        lines = chunk.strip().split("\n", 1)
        if len(lines) == 2 and lines[0] in QUESTION_HEADINGS:
            # Split on the stored heading, print the display label. The two
            # are separate for the reason `DISPLAY_HEADINGS` documents: a
            # narrative cached before the relabelling still splits here.
            blocks.append(Block("heading", display_heading(lines[0]), level=2))
            blocks.append(Block("paragraph", lines[1]))
        elif chunk.strip():
            blocks.append(Block("paragraph", chunk.strip()))
    return tuple(blocks)


def _finding_blocks(
    finding: Finding, index: int, *, with_evidence: bool
) -> tuple[Block, ...]:
    """One finding, with everything needed to challenge it."""
    blocks: list[Block] = [
        Block(
            "heading",
            f"{index}. [{finding.severity.upper()}] {finding.category}",
            level=2,
        ),
        # Already substituted by the assembler. Rendered verbatim.
        Block("paragraph", finding.headline),
    ]

    if finding.recommendation:
        blocks.append(
            Block("paragraph", finding.recommendation, label="Recommended action: ")
        )

    if finding.rule_trace is not None:
        blocks.append(
            Block(
                "paragraph",
                "",
                label=f"Rule {finding.rule_trace.rule_id} fired because:",
            )
        )
        blocks.append(Block("bullets", items=tuple(finding.rule_trace.conditions)))
        if finding.rule_trace.rationale:
            blocks.append(
                Block(
                    "note",
                    f"Why this threshold: {finding.rule_trace.rationale}",
                )
            )

    link = finding.causal_link
    if link is not None:
        blocks.append(
            Block(
                "paragraph",
                f"{link.cause.entity_label} ({link.cause.field}) explains "
                f"{link.effect.entity_label} ({link.effect.field}), "
                f"{format_fact('lag_min', link.lag_days_min)} to "
                f"{format_fact('lag_max', link.lag_days_max)} days later. "
                f"Established on {_basis_in_words(link.evidence_basis)}.",
                label="Cause: ",
            )
        )
        if link.ordering_basis != "exact":
            blocks.append(
                Block(
                    "note",
                    "The later change was seen by comparing two spreadsheet "
                    "snapshots, so its timing is a range rather than a point. "
                    "The order still holds because the two ranges do not "
                    "overlap.",
                )
            )

    if with_evidence and finding.evidence:
        blocks.append(Block("paragraph", "", label="Source rows: "))
        blocks.append(
            Block(
                "bullets",
                items=tuple(
                    # The same fallback chain as the evidence panel on screen
                    # (`Evidence` in `web/src/pages/Insight.tsx`). It used to
                    # end at `raw_table` while the screen ended at `label`, so
                    # one row could be named two different ways in two places
                    # that are supposed to be the same claim.
                    f"{ref.remark or ref.url or ref.label or 'source record'}"
                    f"  (record {ref.raw_data_id})"
                    for ref in finding.evidence[:MAX_EVIDENCE]
                ),
            )
        )

    return tuple(blocks)


def _findings_blocks(
    bundle: InsightBundle, *, with_evidence: bool
) -> tuple[Block, ...]:
    if not bundle.findings:
        return (
            Block(
                "paragraph",
                "Nothing in this project breaches a delivery threshold.",
            ),
        )
    blocks: list[Block] = []
    for index, finding in enumerate(bundle.by_severity(), start=1):
        blocks.extend(_finding_blocks(finding, index, with_evidence=with_evidence))
    return tuple(blocks)


def _projection_blocks(explain: ExplainBundle) -> tuple[Block, ...]:
    """The slip the dependency chain implies but the sheet does not yet show.

    The driving path first, because that is the only part of the table anyone
    acts on - the rest is context for why those dates move.
    """
    blocks: list[Block] = []

    if explain.project_slip_days is not None:
        blocks.append(
            Block(
                "paragraph",
                f"{format_fact('project_slip_days', explain.project_slip_days)} days",
                label="Projected slip against the plan: ",
            )
        )

    if explain.depends_on_inferred_edges:
        blocks.append(
            Block(
                "note",
                "This projection depends on dependency edges inferred from the "
                "schedule's own dates rather than stated by a person. Confirm "
                "them before quoting this figure outside the team.",
            )
        )

    driving = set(explain.driving_path)
    # Driving path first, then the rest - each group keeping the forward-pass
    # order, so the table still reads as a chain rather than a ranking.
    steps = [s for s in explain.steps if s.entity_id in driving]
    steps += [s for s in explain.steps if s.entity_id not in driving]
    steps = steps[:MAX_PROJECTION_ROWS]
    if not steps:
        return tuple(blocks)

    blocks.append(
        Block(
            "table",
            columns=(
                "Task",
                "Planned finish",
                "Projected finish",
                "Implied slip",
                "On driving path",
            ),
            rows=tuple(
                (
                    step.label,
                    format_fact("planned_end", step.planned_end)
                    if step.planned_end
                    else "-",
                    format_fact("projected_end", step.projected_end)
                    if step.projected_end
                    else "-",
                    format_fact("propagated_days", step.propagated_days)
                    if step.propagated_days
                    else "-",
                    "yes" if step.entity_id in driving else "no",
                )
                for step in steps
            ),
        )
    )
    return tuple(blocks)


def _move_in_words(move) -> str:
    """One scenario move, phrased the way the Recovery panel phrases it.

    Word-for-word the same as `ScenarioRow` in `web/src/pages/Insight.tsx`. A
    bare task label - which is all `move.label` is - tells a reader which task
    without saying what is being done to it, and the two moves the engine
    supports are opposites.
    """
    if move.kind == "compress":
        return f"{move.label} {format_fact('move_days', move.days)}d shorter"
    return (
        f"{move.label} starts {format_fact('move_days', move.days)}d "
        f"before {move.against_label}"
    )


def _outcome_in_words(days_late: int) -> str:
    """Whether the scenario lands, measured against the *original* commitment.

    Never a bare number. `days_late` is negative when a scenario finishes early,
    and "-3" under a column headed "still late by" is a figure a reader has to
    stop and decode - one of them will decode it as three days late.
    """
    if days_late <= 0:
        return "meets the commitment"
    return f"still {format_fact('days_late', days_late)}d past it"


def _scenario_blocks(scenarios) -> tuple[Block, ...]:
    """Recovery options, each measured against the original commitment.

    Two figures and never one: `days_earlier` is against doing nothing,
    `days_late` against what was committed. Compressing a task moves the plan
    too, so a scenario compared against its own plan reports "on time" for
    having moved the goalposts.
    """
    if not scenarios.scenarios:
        return (
            Block(
                "paragraph",
                "No recovery scenario changes the outcome. Either the plan is "
                "not late, or no single change to it recovers a day.",
            ),
        )

    blocks: list[Block] = [
        Block(
            "table",
            columns=(
                "Scenario",
                "Change",
                "Projected finish",
                "Days recovered",
                "Against the commitment",
            ),
            rows=tuple(
                (
                    scenario.summary,
                    "; ".join(_move_in_words(move) for move in scenario.moves) or "-",
                    format_fact("projected_end", scenario.projected_end)
                    if scenario.projected_end
                    else "-",
                    format_fact("days_earlier", scenario.days_earlier),
                    _outcome_in_words(scenario.days_late),
                )
                for scenario in scenarios.scenarios
            ),
        ),
        # Feasibility is not modelled, and a table of options invites a reader
        # to assume it is. `resources` is empty by decision - the sheets carry
        # no allocation data - so the report says so where the options are.
        Block(
            "note",
            "These are arithmetic, not plans. The schedule carries no resource "
            "or capacity data, so nothing here says a change is achievable - "
            "only what it would recover if it were.",
        ),
    ]
    return tuple(blocks)


def _forecast_blocks(forecast) -> tuple[Block, ...]:
    """The range, or the reason there is not one.

    A refusal is rendered rather than skipped. `build_document` drops a section
    with no blocks, so returning nothing here would make an unsupported forecast
    look like a section nobody asked for - and the reason is the most useful
    sentence in it.
    """
    if not forecast.available:
        return (
            Block("paragraph", forecast.reason),
            Block(
                "note",
                "No range is shown because the data does not support one. That "
                "is the honest answer, not a missing section.",
            ),
        )

    return (
        Block(
            "table",
            columns=("Confidence", "Finish", "Against the commitment"),
            rows=tuple(
                (
                    f"P{point.percentile}",
                    format_fact("finish", point.finish),
                    f"+{format_fact('days_late', point.days_late)} days"
                    if point.days_late > 0
                    else "meets the commitment",
                )
                for point in forecast.points
            ),
        ),
        Block(
            "paragraph",
            f"{format_fact('trials', forecast.trials)} trials, resampling "
            f"{format_fact('observations', forecast.observations)} observed "
            f"drift(s) onto {format_fact('open_tasks', forecast.open_tasks)} "
            "open task(s). The forward pass alone, with nothing else moving, "
            f"says {format_fact('projected_end', forecast.projected_end)}.",
            label="Basis: ",
        ),
        # The sample itself. Six observations is thin, and printing them is what
        # lets a reader see that it is thin rather than take the percentiles on
        # faith - the same argument as the evidence rows under a finding.
        Block(
            "bullets",
            items=tuple(
                f"{observation.label}: committed "
                f"{format_fact('committed', observation.committed)}, now "
                f"{format_fact('planned', observation.planned)} "
                f"({format_fact('drift', observation.days)} days)"
                for observation in forecast.sample
            ),
        ),
        Block("note", f"{forecast.method} {forecast.assumption}"),
    )


def _risk_blocks(risks) -> tuple[Block, ...]:
    """The risks a PM recorded, as against the ones the engine detected.

    Kept visibly separate from Findings for that reason. A register entry is a
    human judgement with a rating somebody chose; a finding is a rule firing on
    observed data. Merging them into one list would let the report present an
    opinion and a measurement in the same voice.
    """
    if not risks.risks:
        return (
            Block("paragraph", "No risks have been recorded for this project."),
        )

    return (
        Block(
            "paragraph",
            "Recorded by a person, as against the findings above, which are "
            "rules firing on observed data.",
        ),
        Block(
            "table",
            columns=(
                "No.",
                "Risk",
                "Status",
                "Category",
                "Pre-mitigation",
                "Post-mitigation",
                "Owner",
            ),
            rows=tuple(
                (
                    risk.risk_no or "-",
                    ("KEY  " if risk.key_risk else "") + risk.title,
                    risk.status,
                    risk.category or "-",
                    risk.pre_rating or "-",
                    risk.post_rating or "-",
                    risk.responsible or "-",
                )
                for risk in risks.risks
            ),
        ),
    )


#: How a traceability row was established, worst-founded last. The report
#: prints this beside every row rather than sorting it away, for the same
#: reason the register and the findings are separate sections: a reader who
#: cannot tell a measurement from a model's reading cannot act on either.
_EVIDENCE_WORDS = {
    "measured": "checked against the repository",
    "cited": "a model's reading, its citation verified",
    "partly-grounded": "a model's reading, some citations unverified",
    "ungrounded": "a model's reading, citations did not verify",
    "uncited": "a model's reading, nothing cited",
    "direct": "the task's own code anchor",
    "area": "the area, not this task",
    "failed check": "cited something that is not there",
}



def _traceability_blocks(trace) -> tuple[Block, ...]:
    """What the repository says, as against what the tracker and the docs say.

    A fourth kind of claim, and it gets its own section for exactly the
    reason the register and the model proposals do. A finding is a rule
    firing on the schedule. A register entry is a judgement somebody signed.
    A model proposal is neither. This is a fourth thing again: a statement
    checked against source code, where "checked" ranges from arithmetic over
    the corpus to a model's reading whose citation was verified afterwards.

    Flattening those into the findings list would put a line count and a
    language model's opinion in the same voice, so the strength of each row
    is printed as a column rather than sorted away.
    """
    rows = getattr(trace, "findings", None) or []
    if not rows:
        return (
            Block("paragraph",
                  "A traceability run exists for this project but found nothing "
                  "that needs a person."),
        )

    totals = getattr(trace, "totals", {}) or {}
    lead = (
        f"{len(rows)} thing(s) want a human, from a run over "
        f"{totals.get('tickets', 0)} tracker item(s) against the repository."
    )
    if totals.get("contradicted"):
        lead += (
            f" {totals['contradicted']} ticket(s) are contradicted outright: the "
            f"code positively does something else."
        )

    blocks: list[Block] = [Block("paragraph", lead)]

    # Grouped by kind, in the order the digest ranks them, so the two rows
    # that matter are not buried under thirty-five that merely repeat.
    seen: list[str] = []
    for row in rows:
        kind = row.get("kind", "")
        if kind not in seen:
            seen.append(kind)

    for kind in seen:
        group = [r for r in rows if r.get("kind") == kind]
        blocks.append(Block("heading", finding_title(kind), level=2))
        blocks.append(Block(
            "table",
            columns=("What", "Where", "How it was established"),
            rows=tuple(
                (
                    str(r.get("title", ""))[:120],
                    str(r.get("where") or "-")[:80],
                    _EVIDENCE_WORDS.get(r.get("evidence", ""), r.get("evidence", "")),
                )
                for r in group[:20]
            ),
        ))
        if len(group) > 20:
            blocks.append(Block("note", f"{len(group) - 20} more of this kind."))

    blocks.append(Block(
        "note",
        "“The area, not this task” means the row reached the tracker "
        "through the section of the document it sits in rather than through its "
        "own code anchor - it is about this part of the system, not this one "
        "task. Every row whose code is missing is joined that way, necessarily, "
        "because a task claiming a file that does not exist leaves nothing for a "
        "ticket to touch.",
    ))
    return tuple(blocks)


def _model_read_blocks(drafts) -> tuple[Block, ...]:
    """Risks a model proposed from task text, with the rows it read them from.

    Its own section, below the register and below the findings, and the ordering
    is the argument. A finding is a rule firing on a computed number. A register
    entry is a judgement a person made and signed. This is neither: nobody
    computed it and nobody has agreed to it yet, and a reader who cannot tell
    the three apart cannot act on any of them.

    So the claims are never merged into either list, the heading says where they
    came from, and every one is printed with the tasks it cites. The citation is
    the whole point - it is what turns "a model said this" into something a
    reader can check in a minute against their own tracker. A proposal that
    cannot point at a row never reaches the register and never reaches this
    section either; `app/risks/drafts.py` discards it at source.

    Accepted proposals are not here. Accepting one moves it into the register
    above, which is exactly what accepting means.
    """
    if not drafts.drafts:
        reason = (drafts.reason or "").strip()
        return (
            Block(
                "paragraph",
                reason or "Nothing has been proposed from this project's task text.",
            ),
        )

    label = {t.task_id: (t.label or t.task_id) for t in drafts.cited_tasks}
    text = {t.task_id: t for t in drafts.cited_tasks}

    blocks: list[Block] = [
        Block(
            "paragraph",
            "Read by a model from what people wrote in the tracker. Not computed, "
            "not evidence, and not in the risk register: each is a suggestion "
            "shown with the tasks it was drawn from so both can be judged. "
            "Nothing here has been accepted by anyone.",
        ),
        Block(
            "table",
            columns=("Proposed risk", "Category", "Suggested rating", "Read from"),
            rows=tuple(
                (
                    draft.title,
                    draft.category or "-",
                    draft.pre_rating or "not rated",
                    ", ".join(
                        label.get(i.strip(), i.strip())
                        for i in (draft.cited_task_ids or "").split(",")
                        if i.strip()
                    )
                    or "-",
                )
                for draft in drafts.drafts
            ),
        ),
    ]

    #: The task text itself, once per task rather than once per citation - the
    #: same task is routinely cited by three proposals, and repeating its body
    #: under each would treble the appendix and bury the claims it supports.
    cited_ids: list[str] = []
    for draft in drafts.drafts:
        for raw in (draft.cited_task_ids or "").split(","):
            task_id = raw.strip()
            if task_id and task_id not in cited_ids:
                cited_ids.append(task_id)

    if cited_ids:
        blocks.append(
            Block(
                "paragraph",
                "The task text each proposal was read from, as the model saw it "
                "- excerpted to the same length, so what is checked is what was "
                "read.",
            )
        )
        blocks.append(
            Block(
                "table",
                columns=("Task", "Title", "Status", "Text the model was given"),
                rows=tuple(
                    (
                        label.get(task_id, task_id),
                        (text[task_id].title or "-") if task_id in text else "-",
                        (text[task_id].status or "-") if task_id in text else "-",
                        (text[task_id].text or "-") if task_id in text else "(task not found)",
                    )
                    for task_id in cited_ids
                ),
            )
        )
    return tuple(blocks)


def _data_quality_blocks(bundle: InsightBundle) -> tuple[Block, ...]:
    """What the analysis could not use.

    A section rather than a footnote, and never omitted by a preset. A report of
    conclusions drawn on part of a project, which does not say which part, is
    the document this product exists to replace.
    """
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

    blocks: list[Block] = [
        Block(
            "bullets",
            items=tuple(
                f"{label}: {format_fact(name, value)}" for label, name, value in rows
            ),
        )
    ]
    if quality.depends_on_inferred_edges:
        blocks.append(
            Block(
                "note",
                "The schedule conclusion changes if the inferred edges are "
                "removed. It is a derived claim, not a stated one.",
            )
        )
    return tuple(blocks)


def build_document(
    bundle: InsightBundle,
    *,
    explain: ExplainBundle | None = None,
    scenarios=None,
    forecast=None,
    risks=None,
    drafts=None,
    traceability=None,
    sections: Sequence[str] | None = None,
    project_name: str = "",
    generated_at: datetime | None = None,
) -> ReportDoc:
    """The whole report as blocks, for any renderer to walk.

    `sections` is the resolved list from `resolve_sections`; omitting it builds
    the default preset. A section whose data was not supplied is skipped rather
    than rendered empty - a "Recovery scenarios" heading over nothing reads as a
    bug in the analysis rather than a caller who did not ask for it.
    """
    chosen = tuple(sections) if sections is not None else resolve_sections()
    stamp = generated_at or datetime.now(timezone.utc)

    preamble = (
        Block(
            "note",
            f"Reflects the project as at {format_fact('as_of', bundle.as_of)}. "
            f"Report generated {format_fact('generated_at', stamp)}.",
        ),
        # Where the prose came from, before the prose. A reader who cannot tell
        # which they are looking at cannot judge either.
        Block(
            "note",
            "Summary written by "
            + (
                "a language model, from findings it was not permitted to change."
                if bundle.narration_source == "model"
                else "the deterministic template."
            ),
        ),
    )

    built: list[Section] = []
    for spec in SECTIONS:
        if spec.id not in chosen:
            continue
        if spec.id == "summary":
            blocks = _summary_blocks(bundle)
        elif spec.id == "findings":
            blocks = _findings_blocks(bundle, with_evidence="evidence" in chosen)
        elif spec.id == "evidence":
            # Not a section of its own: source rows belong under the finding
            # they support, so this id only switches them on above. Listing them
            # separately would make a reader match record ids back by hand.
            continue
        elif spec.id == "projection":
            blocks = _projection_blocks(explain) if explain is not None else ()
        elif spec.id == "scenarios":
            blocks = _scenario_blocks(scenarios) if scenarios is not None else ()
        elif spec.id == "forecast":
            blocks = _forecast_blocks(forecast) if forecast is not None else ()
        elif spec.id == "risks":
            blocks = _risk_blocks(risks) if risks is not None else ()
        elif spec.id == "model_read":
            blocks = _model_read_blocks(drafts) if drafts is not None else ()
        elif spec.id == "traceability":
            blocks = (_traceability_blocks(traceability)
                      if traceability is not None else ())
        elif spec.id == "data_quality":
            blocks = _data_quality_blocks(bundle)
        else:  # pragma: no cover - SECTIONS and this branch move together
            blocks = ()

        if blocks:
            built.append(Section(spec.id, spec.title, blocks))

    return ReportDoc(
        title=f"Delivery status - {project_name or bundle.project_id}",
        preamble=preamble,
        sections=tuple(built),
    )
