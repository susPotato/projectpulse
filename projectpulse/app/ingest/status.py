"""The one vocabulary every source normalises its status text into.

`TODO`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `DROPPED` - and `OTHER` for
anything this does not recognise, because a wrong mapping is worse than an
honest unknown.

**This lives here because two copies disagreed.** The spreadsheet reader
learned, one incident at a time, that `Resolved` and `Release it` mean
finished and that `Cancelled` is its own state; the Jira convertor kept a
five-entry map that had never heard of any of them. Reading the same board
through Jira instead of the spreadsheet therefore put 152 of 190 tasks into
`OTHER`, and `OTHER` counts as *open* everywhere downstream - so 129
released tasks reported as unfinished and overdue on a page whose whole job
is to say what is late.

`DROPPED` is deliberately not `DONE`: cancelled work was not delivered, so
counting it complete inflates a completion figure, and counting it open
reports it late forever.

Keys are lowercase; `normalise` folds case and whitespace, because one
tracker writes `To Do` and another writes `TO DO`.
"""

from __future__ import annotations

#: Status text, lowercased -> the normalized vocabulary the rules compare
#: against. Anything unmapped becomes OTHER rather than being guessed at.
STATUS_MAP = {
    "not started": "TODO",
    "todo": "TODO",
    "to do": "TODO",
    "open": "TODO",
    "in progress": "IN_PROGRESS",
    "wip": "IN_PROGRESS",
    "blocked": "BLOCKED",
    "on hold": "BLOCKED",
    "done": "DONE",
    "complete": "DONE",
    "completed": "DONE",
    "closed": "DONE",
    # `Resolved` is one of the commonest states in a real Jira workflow and was
    # falling through to OTHER, which every downstream count reads as *open* -
    # so a finished task was reported overdue, in progress and stale at once.
    "resolved": "DONE",
    "fixed": "DONE",
    "delivered": "DONE",
    # The terminal state in the FPT Jira workflow this app reads: the work is
    # shipped. Spelled as an instruction rather than a state, which is why it
    # read as ambiguous and fell through to OTHER - and OTHER is counted as
    # *open* everywhere downstream, so 142 released items on one board were
    # reporting as unfinished work and suppressing every completion figure.
    "release it": "DONE",
    "released": "DONE",
    # The same terminal state as `Release it`, spelled without the pronoun.
    # The spreadsheet export writes one and the Jira REST API writes the
    # other for the identical workflow step, so a board read through the API
    # put all 129 finished tickets into OTHER - counted as open, and reported
    # late - while the same board read from the sheet came out correct.
    "release": "DONE",
    # Work that was finished and has been sent back. It is live again, not
    # closed: treating it as DONE would hide a reopened ticket from every
    # page that asks what is still outstanding.
    "re-open": "IN_PROGRESS",
    "reopen": "IN_PROGRESS",
    "reopened": "IN_PROGRESS",
    # Work that will not happen. Its own state rather than DONE, because it was
    # not delivered - counting it as complete would inflate a completion figure,
    # and counting it as open would report a cancelled task as late forever.
    "cancelled": "DROPPED",
    "canceled": "DROPPED",
    "won't do": "DROPPED",
    "wont do": "DROPPED",
    "will not do": "DROPPED",
    "rejected": "DROPPED",
    "duplicate": "DROPPED",
    "abandoned": "DROPPED",
    "obsolete": "DROPPED",
    # Common board columns that are unambiguously one of the three live states.
    # Anything genuinely ambiguous is still left as OTHER rather than guessed:
    # a wrong mapping is worse than an honest unknown.
    "backlog": "TODO",
    "new": "TODO",
    "in review": "IN_PROGRESS",
    "review": "IN_PROGRESS",
    "in testing": "IN_PROGRESS",
    "testing": "IN_PROGRESS",
    "impeded": "BLOCKED",
    "waiting": "BLOCKED",
}


#: What a page means by "this is finished and will not move again".
CLOSED = frozenset({"DONE", "DROPPED"})


def normalise(value: str | None) -> str:
    """Tracker status text -> the shared vocabulary, or OTHER."""
    return STATUS_MAP.get((value or "").strip().lower(), "OTHER")
