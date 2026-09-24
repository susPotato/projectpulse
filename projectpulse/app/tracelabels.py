"""What each traceability finding is called, and what a PM should do about it.

One list, because there were three. `app/api/static/traceability.html` had a
title and a paragraph per kind, `app/exports/document.py` had a shorter title
per kind, and the two had already drifted: the Word export called
`unsupported-citation` "A verdict resting on something that is not there"
while the page called it "The reason given points at nothing". A reader who
saw the page and then the report had no way to know those were the same ten
categories.

So the titles live here, the export imports them, and the API serves them to
the page alongside the findings themselves (`labels` on `/api/traceability`).
The page keeps a copy only as a fallback for a server that predates this.

**The vocabulary is deliberately not the pipeline's.** Internal keys
(`status-conflict`) never change - they are how the run's JSON is written and
how every stage refers to itself. What changed is only what a person reads,
and the rule the reviewer set for it: say what the finding means for delivery
and what to do next, in one sentence, with no method in it. "A model read the
ticket and the code it points at" is how the answer was obtained; the PM
needs to know that the code and the ticket disagree and who to ask.
"""

from __future__ import annotations

#: kind -> (title, what it means and what to do)
#:
#: Titles are short noun phrases in Title Case, 2-5 words, because they are
#: headings in a scannable list. The sentence under each is one sentence: a
#: consequence, then an action.
FINDING_LABELS: dict[str, tuple[str, str]] = {
    "contradicted": (
        "Code Contradicts Ticket",
        "The code does something different from what the ticket claims; "
        "check with the developer and correct whichever one is wrong.",
    ),
    "status-conflict": (
        "Built But Still Open",
        "The code for this looks finished while the ticket is still open; "
        "confirm with the team and update the tracker.",
    ),
    "built-cancelled": (
        "Built Despite Cancellation",
        "The ticket was cancelled but its code is in the repository; decide "
        "whether it ships or should come out.",
    ),
    "gate-failing": (
        "Team Quality Checks Failing",
        "A rule this team wrote for itself is broken in the code; assign a "
        "fix before the next release.",
    ),
    "acceptance-unanswered": (
        "Acceptance Not Signed Off",
        "The sign-off form on these tickets is still blank; chase the "
        "reviewer before anyone calls them done.",
    ),
    "process-undelegated": (
        "Process Tasks Not Assigned",
        "The written procedure names roles nobody has been given; hand the "
        "steps out or this work has no owner.",
    ),
    "roles-concentrated": (
        "One Person Holds Every Role",
        "The same person requests, specifies and builds nearly every item; "
        "add a second reviewer before this becomes a single point of failure.",
    ),
    "missing-test": (
        "Promised Tests Missing",
        "A task marked finished says it wrote a test that is not in the "
        "code; ask for the test or reopen the task.",
    ),
    "documented-not-built": (
        "Marked Done, No Code Found",
        "The documents call this done and no matching code exists; check "
        "whether the code you have is older than the documents before "
        "raising it with the team.",
    ),
    "unsupported-citation": (
        "Unsupported AI Verdicts",
        "The reason given for this verdict points at a file or function that "
        "is not there; treat the item as unjudged and review it by hand.",
    ),
    "unclaimed-code": (
        "Code Without a Ticket",
        "Code in the repository that no backlog item accounts for; raise a "
        "ticket for it or confirm it is out of scope.",
    ),
}


def finding_title(kind: str) -> str:
    """The heading for a finding kind, or the raw key if it is a new one.

    Falling through rather than raising, for the same reason
    `narration.display_heading` does: a run produced by a newer pipeline may
    carry a kind this build has not heard of, and printing the key is more
    use than dropping the section.
    """
    pair = FINDING_LABELS.get(kind)
    return pair[0] if pair else kind


def finding_guidance(kind: str) -> str:
    """The one-sentence consequence-and-action line, or empty for a new kind."""
    pair = FINDING_LABELS.get(kind)
    return pair[1] if pair else ""


def as_payload() -> dict[str, dict[str, str]]:
    """The shape the Traceability page reads off `/api/traceability`."""
    return {
        kind: {"title": title, "why": why}
        for kind, (title, why) in FINDING_LABELS.items()
    }
