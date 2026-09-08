"""The language model, fenced in on every side.

This is the last piece of the narration layer and deliberately the least
powerful. `fallback.py` already writes a complete narrative; `validator.py`
already refuses a bad draft. What is left for a model is phrasing, and this
module's whole job is to make sure that is *all* it gets.

Three fences, and the order they are built in is the argument.

**The model is never shown a digit.** `build_brief` assembles the prompt out of
`Finding.headline_template` - the prose as its rule author wrote it, tokens
intact - never `Finding.headline`, which has the numbers substituted in. Values
are disclosed only where they are not quantities at all: a category, a severity,
a field name, a task id. The brief then holds itself to the rule it imposes:
`_assert_no_quantity` runs `validator.contains_quantity` over the finished
prompt and raises rather than send a prompt that leaks a figure. A model that
cannot see a number cannot change one - it can only fail to ask for it.

**The draft is validated before substitution, never after.** By then the text
still contains `{{tokens}}`, so any figure the model invented is necessarily a
literal digit, and `no_literal_digits` is exactly what refuses it. Reversing
these two steps would quietly make the entire gate decorative.

**Failure is a downgrade, never an outage.** Every path out of `narrate` returns
a narrative: a rejected draft, a missing API key, an SDK that was never
installed, a socket timeout and an outright refusal all end at
`fallback.render_narrative`, with `narration_fallback_reason` saying which. That
is what makes the model optional enough to be worth having - the demo does not
depend on a network call, so the network call is allowed to fail.

**No vendor is named in this file.** A model is a `Drafter` -
`(system, user) -> str` - and the adapters live in `providers.py`. Everything
that makes a model safe to use happens on either side of that signature, so
switching from Claude to GPT to Gemini changes which adapter is constructed and
nothing else. Each SDK is an optional extra imported on first call, so a judge
running `python -m scripts.demo` needs no package and no key.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.api.schemas.insight import Finding, InsightBundle
from app.intelligence.assembler import format_fact, substitute
from app.narration.fallback import QUESTION_HEADINGS, render_narrative
from app.narration.validator import (
    ENTITY,
    TOKEN,
    contains_quantity,
    validate_draft,
)

log = logging.getLogger(__name__)

#: Signature of anything that can produce a draft: `(system, user) -> text`.
#: Injected rather than imported so the whole of `narrate` is testable with a
#: two-line stub and no network - the same reason `assembler.build_bundle` takes
#: its evidence resolver as an argument.
Drafter = Callable[[str, str], str]

#: How many findings to describe. The narrative summarises; a brief listing
#: every finding invites the model to summarise nothing and enumerate instead.
#: Bounded by `_LABELS`, since each finding needs its own token namespace.
MAX_BRIEF_FINDINGS = 8

#: Labels for the findings in the brief - letters, not numbers. See the comment
#: in `build_brief`.
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: How many of the top finding's figures the draft must actually state. A
#: narrative that mentions no number is a real downgrade from the template, and
#: `required_tokens` is the validator stage that catches it - but requiring
#: every figure would reject good prose for being concise.
MAX_REQUIRED_TOKENS = 3

#: The headings the page splits on. The fifth is conditional: the template only
#: emits it when there is a caveat to report, so the model is not held to it.
REQUIRED_HEADINGS = QUESTION_HEADINGS[:4]


class BriefLeak(RuntimeError):
    """A prompt was built that contains a figure the model must not see.

    A programming error, not a runtime condition, and raised rather than logged:
    continuing would hand a model the very numbers this layer exists to keep
    away from it. `narrate` catches it and serves the template, so the bug
    surfaces as a visible fallback reason instead of a silent leak.
    """


class NarrationUnavailable(RuntimeError):
    """The model could not be reached, or answered with something unusable."""


@dataclass(frozen=True)
class NarrationBrief:
    """Everything a model is given, and everything needed to check what it says.

    `facts` is the substitution table and the only place a digit appears. It is
    keyed by the *namespaced* token names the prompt advertises, because two
    findings routinely carry the same field - `max_propagated_days` belongs to
    whichever one asked for it, and a flat merge would silently give both the
    same value.
    """

    system: str
    user: str
    facts: dict[str, str] = field(default_factory=dict)
    allowed_entities: set[str] = field(default_factory=set)
    required_tokens: set[str] = field(default_factory=set)
    has_causal_link: bool = False


@dataclass(frozen=True)
class NarrationOutcome:
    """What the caller writes onto the bundle.

    `source` is `"model"` only when a draft passed every stage and substituted
    cleanly. Anything else is `"template"` with a `fallback_reason`, so a
    downgrade is always visible on the page rather than inferred from prose
    style.
    """

    narrative: str
    source: str = "template"
    fallback_reason: str | None = None
    attempts: int = 0


# --------------------------------------------------------------------------
# Building the brief.
# --------------------------------------------------------------------------

_SYSTEM = """You write the delivery summary a project manager reads first.

Every finding below was computed by a deterministic engine - rules, dependency
graph traversal, and interval arithmetic over dates a human typed. You choose
the words. You do not choose the facts.

Four rules, in the order they matter.

1. NEVER WRITE A DIGIT. Every figure - a count, a number of days, a percentage,
   a date - reaches you as a {{token}}. Copy the token exactly, both pairs of
   braces included, and place it where that figure belongs in your sentence.
   The server swaps in the real value after your draft is checked, so a token is
   the only way a number can reach the reader. A digit you type yourself did not
   come from the data, so a draft containing one is discarded whole.
2. Name only the tasks listed under "Tasks in this analysis". A task id such as
   WBS-108 is a name rather than a quantity, so write those out normally.
3. Claim a cause only where the brief records a causal chain, and only between
   the two items that chain names. Where the brief records none, say plainly
   that no cause can be established from this data - and then avoid "because",
   "due to", "led to" and "resulted in" entirely, in every section.
4. Do not promise. The engine reports what the plan implies, never what will
   happen, so no "guaranteed", "will definitely" or "certain to".

Write these sections, each as its heading alone on one line followed by one
short paragraph. Use the headings verbatim - the page splits your text on them:

<<HEADINGS>>

Write the last section only if the brief lists something under "What the
analysis could not use".

Keep each paragraph to a few sentences and lead with the worst problem. Plain
prose: no bullet lists, no markdown, no headings of your own, and no remarks
about these instructions.
"""


def _disclosable(text: str) -> bool:
    """Whether a value can be written into the prompt as itself.

    True for the words - a category, a severity, a field name, a task id. False
    the moment a quantity is in there, and then the value has to travel as a
    token instead.
    """
    return bool(text) and not contains_quantity(text)


def _namespace(text: str, prefix: str) -> str:
    """`{{lag_min}}` -> `{{f3_lag_min}}`, matching this finding's fact keys."""
    return TOKEN.sub(lambda m: "{{" + prefix + m.group(1) + "}}", text)


def _template_of(finding: Finding) -> tuple[str, str]:
    """The tokenised prose for one finding, or the safest available substitute.

    A bundle built before `headline_template` existed - or by a rule whose prose
    carried no token at all - still has to narrate. Falling back to the
    substituted `headline` is safe *only* when it holds no quantity, which
    `_disclosable` decides; otherwise the finding is described by its structure
    alone rather than by prose the model could copy numbers out of.
    """
    headline = finding.headline_template or (
        finding.headline if _disclosable(finding.headline) else ""
    )
    recommendation = finding.recommendation_template or (
        finding.recommendation if _disclosable(finding.recommendation) else ""
    )
    return headline, recommendation


def _entities_in(bundle: InsightBundle) -> set[str]:
    """Every task id the bundle actually mentions.

    The allow-list for the validator's `known_entities` stage. Built from the
    findings themselves rather than from the task table, because a model citing
    a real task that this analysis said nothing about is still inventing the
    connection.
    """
    found: set[str] = set()
    for finding in bundle.findings:
        for text in (finding.headline, finding.recommendation):
            found.update(ENTITY.findall(text))
        if finding.causal_link is not None:
            for step in (finding.causal_link.cause, finding.causal_link.effect):
                found.update(ENTITY.findall(step.entity_label))
    return found


def _chain_line(finding: Finding) -> str:
    """The causal chain as structure, with no value in it.

    Deliberately not the lag or the dates - those are in the tokenised headline
    already. What the model needs here is what it is *allowed to say*: which two
    items, and how well evidenced the connection between them is.
    """
    link = finding.causal_link
    if link is None:
        return ""
    return (
        f"    causal chain: {link.cause.entity_label} ({link.cause.field}) "
        f"explains {link.effect.entity_label} ({link.effect.field}); "
        f"link {link.evidence_basis}, ordering {link.ordering_basis}, "
        f"hypothesis \"{link.template_name}\""
    )


#: The caveat section: each `DataQuality` field with what it means in words.
#: Every one of them is a count or a proportion, so every one travels as a
#: token - which is why the section is built from a table rather than written.
_QUALITY_FIELDS = (
    ("rows_rejected", "source rows that could not be parsed"),
    ("changes_low_confidence", "changes whose row identity is uncertain"),
    ("changes_total", "state changes observed in total"),
    ("baseline_coverage", "of tasks that have a baseline to compare against"),
    ("edges_stated", "dependency edges a person stated"),
    ("edges_inferred", "dependency edges inferred from the sheet's own dates"),
    ("edges_dropped", "dependency edges dropped as unusable"),
)


def _quality_lines(bundle: InsightBundle, facts: dict[str, str]) -> list[str]:
    """What the analysis could not use, as tokens.

    Values are formatted by `assembler.format_fact` rather than here. Invariant
    1 is that numbers are formatted in exactly one place, and a percentage
    rendered its own way in this module is precisely how a caveat panel starts
    disagreeing with the finding above it.
    """
    quality = bundle.data_quality
    lines = []
    for name, meaning in _QUALITY_FIELDS:
        token = f"dq_{name}"
        facts[token] = format_fact(name, getattr(quality, name))
        lines.append(f"    {{{{{token}}}}} {meaning}")

    if quality.depends_on_inferred_edges:
        lines.append(
            "    the schedule conclusion changes if the inferred edges are "
            "removed, so it is a derived claim rather than a stated one - "
            "say so"
        )
    return lines


def build_brief(
    bundle: InsightBundle, *, max_findings: int = MAX_BRIEF_FINDINGS
) -> NarrationBrief:
    """Turn a bundle into a prompt that contains no figure at all.

    Raises `BriefLeak` if it fails at that, which is the one check in this
    module worth more than the prompt engineering around it.
    """
    facts: dict[str, str] = {"as_of": bundle.as_of.isoformat()[:10]}
    entities = _entities_in(bundle)
    ranked = bundle.by_severity()[: min(max_findings, len(_LABELS))]

    blocks: list[str] = []
    required: set[str] = set()

    for position, finding in enumerate(ranked):
        # Findings are labelled A, B, C rather than 1, 2, 3. A numbered list is
        # the obvious way to write this and `_assert_no_quantity` rejects it:
        # every digit in the brief is a digit the model can copy, and it has no
        # way to know that this particular one was only ever a bullet.
        label = _LABELS[position]
        prefix = f"f{label.lower()}_"
        facts.update({prefix + k: v for k, v in finding.facts.items()})

        headline, recommendation = _template_of(finding)
        lines = [f"[{label}] {finding.category}, severity {finding.severity}"]
        if headline:
            lines.append(f"    says: {_namespace(headline, prefix)}")
        if recommendation:
            lines.append(f"    action: {_namespace(recommendation, prefix)}")
        if finding.rule_trace is not None and _disclosable(
            finding.rule_trace.rationale
        ):
            lines.append(f"    why this threshold: {finding.rule_trace.rationale}")
        chain = _chain_line(finding)
        if chain:
            lines.append(chain)
        blocks.append("\n".join(lines))

        if position == 0 and headline:
            # The worst finding's figures are the ones the narrative must not
            # quietly drop. Sorted so the requirement is the same every run.
            required = {
                prefix + name
                for name in sorted(set(TOKEN.findall(headline)))[:MAX_REQUIRED_TOKENS]
            }

    quality = _quality_lines(bundle, facts)

    parts = [
        f"Tasks in this analysis: {', '.join(sorted(entities)) or 'none'}",
        "",
        "The analysis reflects the project as at {{as_of}}.",
        "",
    ]
    if blocks:
        parts += [
            "FINDINGS, worst first. Rephrase these. Add none of your own.",
            "",
            "\n\n".join(blocks),
        ]
    else:
        parts.append(
            "FINDINGS: none. Nothing in this project breaches a delivery "
            "threshold, and no causal chain was established. Say that plainly "
            "rather than reaching for something to report."
        )
    parts += ["", "WHAT THE ANALYSIS COULD NOT USE", *quality]

    if required:
        parts += [
            "",
            "Figures the summary must state: "
            + ", ".join("{{" + name + "}}" for name in sorted(required)),
        ]

    user = "\n".join(parts)
    # `.replace`, not `.format`: the system prompt is full of `{{token}}`
    # examples, and `.format` would collapse every one of them to `{token}` -
    # teaching the model the one syntax the validator refuses.
    system = _SYSTEM.replace(
        "<<HEADINGS>>", "\n".join([*REQUIRED_HEADINGS, QUESTION_HEADINGS[4]])
    )

    # Only the brief is checked, and deliberately so. It is generated from the
    # findings, so it is the half that could carry a value out of the bundle;
    # the system prompt is a constant with no project data in it, and its own
    # numbered rules are prose rather than anything a model could mistake for
    # a measurement.
    _assert_no_quantity(user, "brief")

    return NarrationBrief(
        system=system,
        user=user,
        facts=facts,
        allowed_entities=entities,
        required_tokens=required,
        has_causal_link=any(f.causal_link is not None for f in bundle.findings),
    )


def _assert_no_quantity(text: str, what: str) -> None:
    """Refuse to send a prompt with a figure in it, and say which line.

    Naming the offending lines matters more than it looks. This fires while a
    rule table or a template is being edited, and "something leaked" sends the
    author back through the whole brief; the line itself sends them to the one
    string that needs a token.
    """
    leaks = [line.strip() for line in text.splitlines() if contains_quantity(line)]
    if leaks:
        raise BriefLeak(
            f"the {what} states a figure outside a token, so a model could copy "
            "it - and a copied number is indistinguishable on the page from a "
            f"computed one. Offending line(s): {' | '.join(leaks[:3])}"
        )


# --------------------------------------------------------------------------
# Running one draft past the gate.
# --------------------------------------------------------------------------


def _missing_headings(draft: str) -> list[str]:
    """Headings the page needs and the draft does not have.

    Checked here rather than as a ninth validator stage: the validator's subject
    is whether a model can be trusted with a claim, and this is only whether the
    text fits the panel it renders into. A draft missing a heading would leave
    that section of the insight screen blank.
    """
    return [heading for heading in REQUIRED_HEADINGS if heading not in draft]


def narrate(
    bundle: InsightBundle,
    *,
    drafter: Drafter | None = None,
    attempts: int = 2,
) -> NarrationOutcome:
    """The narrative for one bundle, and an honest account of where it came from.

    Always returns usable prose. With no `drafter`, or on any failure at all,
    that is the deterministic template - `fallback_reason` then says why, which
    is the difference between a downgrade a reader can see and one they cannot.

    `attempts` is 2 because the validator reports every objection at once, so a
    rejected draft can be sent back with the objections attached. That fixes the
    ordinary failure - a model that wrote one figure as a word - without turning
    into a retry loop that spends real money on a prompt that is simply wrong.
    """
    template = render_narrative(bundle)
    if drafter is None:
        # No reason, because nothing was refused. `fallback_reason` means a
        # model was asked and its answer was not used; setting it here would
        # make the default configuration look like a failed one on every page.
        return NarrationOutcome(template, "template", None)

    try:
        brief = build_brief(bundle)
    except BriefLeak as exc:
        log.error("narration brief rejected: %s", exc)
        return NarrationOutcome(template, "template", f"brief rejected: {exc}")

    user = brief.user
    reason = "no attempt made"
    attempt = 0

    for attempt in range(1, max(attempts, 1) + 1):
        try:
            draft = drafter(brief.system, user)
        except Exception as exc:  # noqa: BLE001 - any failure is a downgrade
            log.warning("narration draft %d failed: %s", attempt, exc)
            return NarrationOutcome(
                template, "template", f"{type(exc).__name__}: {exc}", attempt
            )

        result = validate_draft(
            draft,
            facts=brief.facts,
            allowed_entities=brief.allowed_entities,
            required_tokens=brief.required_tokens,
            has_causal_link=brief.has_causal_link,
        )
        missing = _missing_headings(draft)
        if result.ok and not missing:
            narrative, unresolved = substitute(draft, brief.facts)
            if unresolved:
                # Unreachable while `known_tokens` passes, and checked anyway:
                # a literal `{{token}}` on the page discredits every number on
                # it, not just that one.
                reason = f"unresolved tokens: {', '.join(sorted(set(unresolved)))}"
                log.error("narration %s", reason)
                break
            return NarrationOutcome(narrative, "model", None, attempt)

        objections = [] if result.ok else [result.summary]
        if missing:
            objections.append(f"[headings] missing: {', '.join(missing)}")
        reason = "; ".join(objections)
        log.info("narration draft %d rejected: %s", attempt, reason)
        user = (
            f"{brief.user}\n\n"
            "Your previous draft was rejected for these reasons. Fix every one "
            "of them and write the summary again:\n"
            f"{reason}"
        )

    # `attempt` is what actually happened rather than what was budgeted. The
    # count is read when diagnosing a prompt, and an inflated one sends the
    # reader looking for a request that was never made.
    return NarrationOutcome(template, "template", reason, attempt)
