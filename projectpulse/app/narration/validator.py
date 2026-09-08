"""The gate every model draft passes before a reader sees it.

The product's thesis is that findings are computed deterministically and the model
only phrases them. That thesis is worth nothing unless something enforces it, and
this is that something. It assumes the model is *careless*, not malicious: the
failures it catches are the ones language models actually produce - a number
rewritten as prose, a task id that looks plausible and does not exist, a
"because" attached to two facts that were never causally linked.

Eight stages, all of them run even after one fails, because a prompt is easier to
fix when you can see every objection at once.

============================  ==========================================
Stage                         Rejects
============================  ==========================================
``shape``                     empty, truncated, or runaway drafts
``no_literal_digits``         **the important one** - any digit outside a token
``known_tokens``              a token naming a fact that does not exist
``required_tokens``           quietly dropping a number the finding must state
``known_entities``            task ids the bundle never mentioned
``no_unsupported_causation``  "because" with no causal link behind it
``no_overclaimed_certainty``  turning a projection into a promise
``no_model_artifacts``        meta-commentary, fences, leaked instructions
============================  ==========================================

`no_literal_digits` is the load-bearing one. Substitution happens *after*
validation, so a model that invented a figure would have had to write it as a
literal - and a literal digit is exactly what this refuses. A model can therefore
never change a number, only fail to produce one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN = re.compile(r"\{\{(\w+)\}\}")

#: Task-shaped identifiers a PM would recognise: WBS-108, QA-001, EPIC-12.
ENTITY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")

MIN_LENGTH = 40
MAX_LENGTH = 4000

#: Connectives that assert causation. Allowed only when a causal chain was
#: actually established - otherwise the model is inventing the one claim the
#: whole system exists to make carefully.
CAUSAL_WORDS = (
    "because",
    "caused by",
    "due to",
    "as a result of",
    "resulted in",
    "led to",
    "owing to",
    "triggered by",
)

#: Certainty a projection cannot support. The schedule engine says what the plan
#: implies, never what will happen.
CERTAINTY_WORDS = (
    "will definitely",
    "guaranteed",
    "certainly will",
    "is certain to",
    "without doubt",
    "no chance",
    "impossible to miss",
    "always results",
)

#: Model artefacts and prompt leakage.
ARTEFACT_PATTERNS = (
    re.compile(r"\bas an ai\b", re.I),
    re.compile(r"\bi (?:cannot|can't|am unable)\b", re.I),
    re.compile(r"\bas a language model\b", re.I),
    re.compile(r"```"),
    re.compile(r"\bsystem prompt\b", re.I),
    re.compile(r"\byou are a\b", re.I),
    re.compile(r"\{\%"),  # a template directive that should never survive
)


@dataclass(frozen=True)
class ValidationIssue:
    stage: str
    message: str
    detail: str = ""

    def __str__(self) -> str:
        return f"[{self.stage}] {self.message}{f': {self.detail}' if self.detail else ''}"


@dataclass
class ValidationResult:
    ok: bool = True
    issues: list[ValidationIssue] = field(default_factory=list)
    stages_run: list[str] = field(default_factory=list)

    def add(self, stage: str, message: str, detail: str = "") -> None:
        self.ok = False
        self.issues.append(ValidationIssue(stage, message, detail))

    @property
    def summary(self) -> str:
        """One line, suitable for `narration_fallback_reason`."""
        if self.ok:
            return "passed"
        return "; ".join(str(issue) for issue in self.issues)


def _strip_tokens(text: str) -> str:
    return TOKEN.sub(" ", text)


def _scannable(text: str) -> str:
    """Text with tokens and task ids removed, ready for the digit scan.

    Task identifiers legitimately contain digits - ``WBS-114`` is a name, not a
    quantity - so they are removed before looking for numbers. They are not
    thereby unchecked: the `known_entities` stage verifies every one of them
    against the bundle, so a model cannot smuggle a figure in as a fake id.

    Dates are deliberately *not* exempt. A date is a value the server computes and
    substitutes, so a model writing one as a literal is exactly the failure this
    catches.
    """
    return ENTITY.sub(" ", _strip_tokens(text))


def contains_quantity(text: str) -> bool:
    """Whether `text` states a figure that must have come from the server.

    The public form of the `no_literal_digits` rule, exported so that
    `client.py` can hold *itself* to it: a brief that leaks a value into the
    prompt would let a model copy that value rather than emit the token for it,
    and the copy would then read as though the server had produced it.

    Sharing one definition is the point. Two implementations of "contains a
    number" would eventually disagree, and the disagreement would be a prompt
    that looks safe and is not.
    """
    return bool(re.search(r"\d", _scannable(text)))


def validate_draft(
    draft: str,
    *,
    facts: dict[str, str],
    allowed_entities: set[str] | None = None,
    required_tokens: set[str] | None = None,
    has_causal_link: bool = False,
) -> ValidationResult:
    """Run all eight stages against one model draft.

    Args:
        draft: tokenised prose from the model. Must still contain `{{tokens}}` -
            it is validated *before* substitution, which is what makes the digit
            rule enforceable.
        facts: the token names the model was permitted to use.
        allowed_entities: task ids present in the bundle. None disables the check,
            which should only happen in tests.
        required_tokens: numbers the finding must state. Omitting one is a silent
            downgrade of the finding, so it is a failure rather than a warning.
        has_causal_link: whether a chain was actually established.
    """
    result = ValidationResult()
    naked = _strip_tokens(draft)
    # Task ids are names, not quantities - see `_scannable`.
    countable = _scannable(draft)

    # 1. shape
    result.stages_run.append("shape")
    stripped = draft.strip()
    if not stripped:
        result.add("shape", "draft is empty")
    elif len(stripped) < MIN_LENGTH:
        result.add("shape", "draft is too short to be a narrative", f"{len(stripped)} chars")
    elif len(stripped) > MAX_LENGTH:
        result.add("shape", "draft is too long", f"{len(stripped)} chars")

    # 2. no literal digits - the rule the whole design rests on
    result.stages_run.append("no_literal_digits")
    digits = re.findall(r"\d+(?:[.,]\d+)?", countable)
    if digits:
        result.add(
            "no_literal_digits",
            "draft contains a number outside a token; every figure must be a "
            "{{token}} so the server can substitute it",
            ", ".join(sorted(set(digits))[:5]),
        )

    # 3. known tokens
    result.stages_run.append("known_tokens")
    used = set(TOKEN.findall(draft))
    unknown = used - set(facts)
    if unknown:
        result.add(
            "known_tokens",
            "draft uses tokens with no matching fact",
            ", ".join(sorted(unknown)),
        )

    # 4. required tokens
    result.stages_run.append("required_tokens")
    if required_tokens:
        missing = set(required_tokens) - used
        if missing:
            result.add(
                "required_tokens",
                "draft omits a number the finding must state",
                ", ".join(sorted(missing)),
            )

    # 5. known entities
    result.stages_run.append("known_entities")
    if allowed_entities is not None:
        mentioned = set(ENTITY.findall(naked))
        invented = mentioned - allowed_entities
        if invented:
            result.add(
                "known_entities",
                "draft names tasks that are not in this bundle",
                ", ".join(sorted(invented)),
            )

    # 6. no unsupported causation
    result.stages_run.append("no_unsupported_causation")
    if not has_causal_link:
        lowered = naked.casefold()
        found = [word for word in CAUSAL_WORDS if word in lowered]
        if found:
            result.add(
                "no_unsupported_causation",
                "draft asserts a cause, but no causal chain was established",
                ", ".join(found),
            )

    # 7. no overclaimed certainty
    result.stages_run.append("no_overclaimed_certainty")
    lowered = naked.casefold()
    overclaimed = [word for word in CERTAINTY_WORDS if word in lowered]
    if overclaimed:
        result.add(
            "no_overclaimed_certainty",
            "draft states a projection as a certainty",
            ", ".join(overclaimed),
        )

    # 8. no model artefacts
    result.stages_run.append("no_model_artifacts")
    artefacts = [p.pattern for p in ARTEFACT_PATTERNS if p.search(draft)]
    if artefacts:
        result.add(
            "no_model_artifacts",
            "draft contains meta-commentary or leaked instructions",
            ", ".join(artefacts[:3]),
        )

    return result
