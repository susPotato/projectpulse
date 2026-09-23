"""The third axis: process work, whose evidence is usually somewhere else.

The 173 feature rows are one population and the 24 roadmap rows another
(`cohorts`). Above both sits a third: the keyed PM tickets the feature rows
hang under. `tickets` drops them on purpose, because they map to no code —
and dropping them lost the only rows in the export carrying real prose.
They hold 7,987 characters of it, against 0 on the feature rows.

They describe *governance*: hold a kickoff, develop the CM Plan, define the
ISMS keyword, request CI/CD. Work that is real, tracked, and produces
deliverables — almost none of which are files in this repository.

**Two rules, in this order, and the order is the whole point.**

1. **An open task owes nothing.** A deliverable missing from a task nobody
   claims to have finished is the plan, not a gap. This is checked first,
   so no amount of clever locating can turn an in-progress task into a
   finding. On the CoWorkLocal export that alone settles every row: 16 of
   17 are `In Progress`, one is `To Do`, none carries a resolved date.
2. **Unlocatable is not missing.** "Complete the information required on
   tab Project plan on FI2.0" names a system this pipeline cannot read.
   The deliverable may well exist; we are not entitled to an opinion. Nine
   of the sixteen descriptions name such a system.

Together they mean this stage will usually report that it *cannot* report.
That is the finding. The alternative — grepping the docs tree for "CM Plan",
finding nothing, and calling sixteen governance tasks incomplete — is the
`absence proves nothing` trap with a governance coat on, and it would be
wrong on every row here.

`EXTERNAL_SYSTEMS` is explanatory, never load-bearing. A deliverable is
verifiable when it resolves to a file we can see, and not otherwise; the
list only supplies *why* for the ones that do not, so the report can say
"FI2.0 holds this" instead of an unhelpful shrug.

**The deliverable names are extracted from prose and are approximate.**
They come out of running procedures written for people — numbered steps,
mid-sentence capitals, a noun that may or may not be the thing produced.
Expect a fragment like "Perform the baseline" alongside a clean "CM Plan".
That noise is tolerable only because it cannot manufacture a finding: rule
1 settles a task before extraction is consulted, and rule 2 turns a failed
lookup into "cannot see it" rather than "it is missing". A better extractor
would produce a longer report, not a different verdict.
"""

from __future__ import annotations

import collections
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tracelink.adapters.tickets_tabular import repair as _repair

#: Systems this organisation's process names, used only to explain why a
#: deliverable could not be located. Extend per tracker; nothing downstream
#: depends on it being complete.
EXTERNAL_SYSTEMS: dict[str, str] = {
    "FI2.0": r"FI\s?2\.0",
    "Akawork": r"Akawork",
    "ISMS keyword tool": r"ISMS keyword management",
    "a meeting": r"\bmeeting\b",
}

#: Words that end the name of a produced artifact.
ARTIFACT_NOUNS = ("plan", "charter", "schedule", "report", "document",
                  "baseline", "list", "procedure", "policy", "specification")

#: Articles, prepositions and UI words that can lead a phrase without being
#: part of the deliverable's name.
_LEADING = ("the", "a", "an", "on", "in", "of", "for", "tab", "this", "and")

#: Roles this process assigns work to. Ordered longest-first so "Operation
#: Manager" is not read as the two-letter "OM" inside it.
ROLES = ("Senior Manager", "Operation Manager", "HO Representative",
         "FSU OM", "BU OM", "Customer", "Admin", "PTL", "ISM", "PM", "QA",
         "CC", "PO", "BA", "AM", "SM", "IT")

#: How far into a step a role may sit and still be its subject. "PM assigns
#: a member..." and "CC completes CM Plan..." own their steps; the roles in
#: "Conduct kick-off meeting. Required participants are: Senior Manager,
#: PM, PTL, QA" are a guest list, and reading the first of them as the
#: owner handed that meeting to the Senior Manager instead of the PM.
SUBJECT_WINDOW = 40

_STEP = re.compile(r"(?m)^\s*(\d+)[.)]\s+")
#: Case-sensitive on purpose. These are acronyms, and a case-insensitive
#: "IT" matches the word "it" in every other sentence.
_ROLE = re.compile(r"\b(" + "|".join(ROLES) + r")\b")
#: "3 working days", "25 wds", "10 WDs" - the commitments a procedure makes.
_SLA = re.compile(r"\b(\d+)\s*(working days|business days|wds)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")

#: Each qualifier must begin with a letter, so the numbering of a procedure
#: ("1. Perform the baseline", "... 3. Schedule") cannot become part of a
#: deliverable's name.
_PHRASE = re.compile(
    r"\b((?:[A-Za-z][\w./-]*\s+){0,3}(?:" + "|".join(ARTIFACT_NOUNS) + r"))\b",
    re.IGNORECASE)
_URL = re.compile(r"https?://[^\s,;)]+")


#: A line of an acceptance form: "4. KPI Input : yes/no/na".
_ACCEPT = re.compile(r"(?m)^\s*(\d+)[.)]\s*(.+?)\s*:\s*(.+?)\s*$")


@dataclass
class AcceptanceItem:
    """One question an acceptance form asks, and whether anyone answered it.

    The form arrives with its answer *menu* in the answer slot — "yes/no",
    "Yes_Compliant/ Yes_Non-Compliant". Somebody filling it in replaces the
    menu with one of its options. So a value that still contains a slash is
    a question nobody has answered, and that is the whole test: no wording
    list, no per-project configuration.
    """

    n: int
    label: str
    #: The choices the form offers, when it is still offering them.
    options: list[str] = field(default_factory=list)
    #: The choice somebody made, if they made one.
    answer: str = ""

    @property
    def answered(self) -> bool:
        return bool(self.answer)

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "label": self.label, "options": self.options,
                "answer": self.answer, "answered": self.answered}


@dataclass
class Step:
    """One numbered step of a procedure, as something a person can tick off.

    This is the part of the export a PM can actually act on. The verdict
    machinery below decides what may be *claimed*; a step is simply work
    the process says to do, with whoever the text puts in front of it.
    """

    n: int
    text: str
    #: The first role named in the step. The one the sentence is about —
    #: "PM assigns a member to play the role of CC" is the PM's step, and
    #: taking the last role would hand it to CC.
    owner: str = ""
    #: Every role the step names, owner included: who has to be in the room.
    involves: list[str] = field(default_factory=list)
    #: "3 working days" — a commitment with a clock, when the text sets one.
    sla: str = ""
    contacts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "text": self.text, "owner": self.owner,
                "involves": self.involves, "sla": self.sla,
                "contacts": self.contacts}


@dataclass
class GovTask:
    """One process ticket, and what can honestly be said about it."""

    key: str
    summary: str
    status: str
    description: str
    #: Who the tracker says owns this. Not who the procedure says: those are
    #: roles, in the step text, and the two disagreeing is a finding.
    assignee: str = ""
    steps: list[Step] = field(default_factory=list)
    #: The acceptance form attached to this ticket, from `QA ACC_Note`.
    acceptance: list[AcceptanceItem] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)
    #: "not-claimed-done" | "delivered" | "unlocatable" | "no-deliverable-named"
    verdict: str = ""
    #: Files in the docs tree that a deliverable resolved to.
    resolved: list[str] = field(default_factory=list)
    why: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "summary": self.summary, "status": self.status,
                "assignee": self.assignee,
                "steps": [s.as_dict() for s in self.steps],
                "acceptance": [a.as_dict() for a in self.acceptance],
                "deliverables": self.deliverables, "systems": self.systems,
                "urls": self.urls, "verdict": self.verdict,
                "resolved": self.resolved, "why": self.why}


def deliverables(text: str) -> list[str]:
    """Artifact names the description says to produce or update.

    **Two words at least, one of them capitalised.** A single noun is not a
    deliverable name, and treating it as one is how "Hold Project KickOff
    Meeting" came back *delivered* — its description says "schedule", which
    matched four unrelated files in the docs tree. Requiring a qualifier
    also recovers the opposite miss: the noun is often lowercase in this
    prose ("tab Project plan on FI2.0"), so matching is case-insensitive
    and the capital is required somewhere in the phrase instead.
    """
    out: set[str] = set()
    for m in _PHRASE.finditer(text or ""):
        words = m.group(1).split()
        while words and words[0].lower() in _LEADING:
            words.pop(0)
        # The name begins at the *last* capitalised word that still leaves
        # two words, which drops the verb and article a procedure leads
        # with: "Update the CM Plan" -> "CM Plan", "Complete tab Project
        # plan" -> "Project plan". Anything earlier is the sentence.
        start = next((i for i in range(len(words) - 2, -1, -1)
                      if words[i][:1].isupper()), None)
        if start is None:
            continue
        name = words[start:]
        if len(name) < 2:
            continue
        out.add(" ".join(name))
    return sorted(out)


#: Headers that carry the acceptance form. `QA ACC_Note` is this tracker's
#: custom field; the rest are what the same form is called when it arrives
#: from a different export of the same board. Matched loosely - punctuation
#: and case vary between a Jira CSV export and a hand-saved spreadsheet -
#: because a form that is present but unread reports zero open questions,
#: which is indistinguishable from a form everybody answered.
ACCEPTANCE_COLUMNS = ("qa acc note", "qa acceptance", "acceptance note",
                      "acceptance criteria", "qa note")

_HEADER_PUNCT = re.compile(r"[^a-z0-9]+")


def acceptance_text(row: dict[str, Any]) -> str:
    """The acceptance form on this row, under whichever header carries it."""
    for header, value in row.items():
        canon = _HEADER_PUNCT.sub(" ", str(header or "").lower()).strip()
        if canon in ACCEPTANCE_COLUMNS and str(value or "").strip():
            return str(value)
    return ""


def acceptance(text: str) -> list[AcceptanceItem]:
    """The questions an acceptance form asks, answered or not.

    `QA ACC_Note` carries eight of them on every process ticket, with their
    answer domains written in: yes/no, yes/no/na, Yes_Compliant/
    Yes_Non-Compliant. On this export not one is answered on any ticket —
    136 open acceptance questions — which is a far more certain finding
    than anything the code half produces, because it needs no comparison
    to anything. The form says what it wants and is empty.

    Labels are left in whatever language they were written in. Translating
    "Cắt quyền server dự án" here would put a guess in front of the only
    person who can act on it.
    """
    out: list[AcceptanceItem] = []
    for m in _ACCEPT.finditer(repair(text or "")):
        label = " ".join(m.group(2).split())
        value = m.group(3).strip()
        # A slash still in the answer slot means the menu is intact, so
        # nobody has chosen. Anything else is somebody's answer.
        if "/" in value:
            out.append(AcceptanceItem(
                n=int(m.group(1)), label=label,
                options=[o.strip() for o in value.split("/") if o.strip()]))
        else:
            out.append(AcceptanceItem(n=int(m.group(1)), label=label,
                                      answer=value))
    return out


#: Re-exported from the adapter, where the repair now happens on the way in.
#: Kept under this name because `governance.repair` is what the tests and the
#: CLI call, and because a second copy is how two spellings of the same fix
#: drift apart.
repair = _repair




def steps(text: str) -> list[Step]:
    """The numbered steps of a procedure, in order.

    These descriptions are running instructions, not summaries: "1. PM
    assigns a member to play the role of CC... 2. CC completes CM Plan and
    sends for review... Resolved time follows the IT/ISM SLA: 3 working
    days". Split on the numbering and each fragment is a piece of work with
    an owner, and sometimes a clock and an address.

    Text before the first number is preamble, not a step. The kickoff
    ticket opens with nine bullets of agenda before "The tasks are: 1." —
    turning that into step zero would put an agenda item on somebody's
    checklist.
    """
    text = text or ""
    marks = list(_STEP.finditer(text))
    out: list[Step] = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if not body:
            continue
        body = " ".join(repair(body).split())
        sla = _SLA.search(body)
        # Dedupe while keeping first-seen order. The owner is the first role
        # in subject position; a role further in is a participant, not the
        # one doing the work. No owner is better than the wrong one.
        involves: list[str] = []
        owner = ""
        for m2 in _ROLE.finditer(body):
            r = m2.group(1)
            if r not in involves:
                involves.append(r)
            if not owner and m2.start() < SUBJECT_WINDOW:
                owner = r
        out.append(Step(
            n=int(m.group(1)),
            text=body,
            owner=owner,
            involves=involves,
            sla=f"{sla.group(1)} {sla.group(2).lower()}" if sla else "",
            contacts=sorted(set(_EMAIL.findall(body))),
        ))
    return out


def systems(text: str) -> list[str]:
    return sorted(name for name, pat in EXTERNAL_SYSTEMS.items()
                  if re.search(pat, text or "", re.I))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def locate(name: str, docs: list[str]) -> list[str]:
    """Files in the visible tree whose path carries this deliverable's name."""
    n = _norm(name)
    if len(n) < 4:
        return []
    return sorted(d for d in docs if n in _norm(d))


def classify(rows: list[dict[str, Any]], docs: list[str],
             done_status: set[str] | None) -> list[GovTask]:
    """One verdict per process ticket, in the order the two rules demand."""
    out: list[GovTask] = []
    for r in rows:
        desc = str(r.get("Description") or "")
        t = GovTask(
            key=str(r.get("Key") or ""),
            summary=str(r.get("Summary") or ""),
            status=str(r.get("Status") or ""),
            assignee=str(r.get("Assignee") or ""),
            description=desc,
            steps=steps(desc),
            acceptance=acceptance(acceptance_text(r)),
            deliverables=deliverables(desc),
            systems=systems(desc),
            urls=sorted(set(_URL.findall(desc))),
        )

        # Rule 1, first and unconditionally. An open task owes nothing, so
        # nothing below it can manufacture a finding from a missing file.
        if done_status is None:
            t.verdict = "not-claimed-done"
            t.why = ("no done-status given, and which status means finished is "
                     "not a thing to guess")
        elif t.status not in done_status:
            t.verdict = "not-claimed-done"
            t.why = f"status {t.status!r} is not a finished state"
        elif not t.deliverables:
            t.verdict = "no-deliverable-named"
            t.why = "marked done, but the description names nothing to look for"
        else:
            for d in t.deliverables:
                t.resolved.extend(locate(d, docs))
            if t.resolved:
                t.verdict = "delivered"
                t.why = f"found {len(t.resolved)} matching file(s)"
            else:
                # Rule 2. Not found is not missing.
                t.verdict = "unlocatable"
                where = ", ".join(t.systems) or "somewhere not visible here"
                t.why = f"deliverable is not a file we can see; {where} holds it"
        out.append(t)
    return out


@dataclass
class Summary:
    tasks: list[GovTask]
    docs_seen: int

    @property
    def counts(self) -> dict[str, int]:
        return dict(collections.Counter(t.verdict for t in self.tasks))

    @property
    def checkable(self) -> int:
        """Tasks this export could ever support a finding about."""
        return sum(1 for t in self.tasks
                   if t.verdict in ("delivered", "unlocatable"))

    @property
    def delegation(self) -> dict[str, Any]:
        """Who the tracker loads against who the procedure names.

        The procedure is written in roles - PM assigns, CC completes, ISM
        reviews. The tracker carries one name per ticket. When every ticket
        carries the *same* name, the checklist has been created but never
        handed out, and that is a thing a PM can act on this morning.
        """
        names = collections.Counter(t.assignee for t in self.tasks if t.assignee)
        roles = sorted({r for t in self.tasks for st in t.steps
                        for r in st.involves})
        return {"assignees": dict(names), "roles_named": roles,
                "single_assignee": len(names) == 1 and len(self.tasks) > 1}

    @property
    def acceptance_open(self) -> dict[str, Any]:
        """Acceptance questions asked, and how many nobody has answered.

        Counted per ticket, not per distinct question: the same form on
        seventeen tickets is seventeen things to answer, not eight.
        """
        items = [a for t in self.tasks for a in t.acceptance]
        open_ = [a for a in items if not a.answered]
        labels = collections.Counter(a.label for a in open_)
        return {"asked": len(items), "unanswered": len(open_),
                "tickets": sum(1 for t in self.tasks if t.acceptance),
                "questions": [k for k, _ in labels.most_common()]}

    @property
    def n_steps(self) -> int:
        return sum(len(t.steps) for t in self.tasks)

    @property
    def by_owner(self) -> dict[str, int]:
        """Steps per role — who this process actually loads up."""
        c = collections.Counter(s.owner for t in self.tasks for s in t.steps
                                if s.owner)
        return dict(c.most_common())

    @property
    def commitments(self) -> list[dict[str, Any]]:
        """Steps carrying a deadline. The only rows with a clock on them."""
        return [{"key": t.key, "n": s.n, "owner": s.owner, "sla": s.sla,
                 "text": s.text, "contacts": s.contacts}
                for t in self.tasks for s in t.steps if s.sla]

    def as_dict(self) -> dict[str, Any]:
        return {"tasks": [t.as_dict() for t in self.tasks],
                "counts": self.counts, "checkable": self.checkable,
                "n_steps": self.n_steps, "by_owner": self.by_owner,
                "delegation": self.delegation,
                "acceptance_open": self.acceptance_open,
                "commitments": self.commitments,
                "docs_seen": self.docs_seen}


#: Issue types that name management work rather than delivery. Matched
#: word by word against the tracker's own type, so "PM Task" and "Planning
#: Task" qualify while "Task", "Story" and "Bug" do not.
#:
#: Deliberately absent: "Product". In this export the single `Product` row
#: is the container everything else hangs under, but "Product Backlog Item"
#: is an ordinary *delivery* type in Scrum trackers, and matching the word
#: would sweep a whole backlog in here. A project that wants it says so
#: with `--process-type`.
MANAGEMENT_WORDS = frozenset({
    "pm", "planning", "process", "governance", "admin", "administration",
    "management", "managerial",
})

_WORDS = re.compile(r"[a-z]+")


def is_management_type(value: Any) -> bool:
    """Whether a tracker's issue type names management rather than delivery."""
    return bool(MANAGEMENT_WORDS.intersection(
        _WORDS.findall(str(value or "").lower())))


def select(rows: list[dict[str, Any]], *, type_column: str | None = None,
           types: set[str] | None = None,
           ) -> tuple[list[dict[str, Any]] | None, str]:
    """The process tickets among `rows`, and the rule that picked them.

    **This used to be "the row has a key", and that was a proxy for the
    shape of one spreadsheet rather than a fact about process work.** In
    that export the 17 management rows were the only keyed ones and the 173
    delivery rows were nested beneath them unkeyed, so keyed-ness and
    management happened to coincide. Read the same backlog out of Jira
    directly and every row has a key, because every row is a real issue —
    and the proxy selected all 190.

    The tracker was recording the distinction all along, in a column both
    exports carry: `PM Task` and `Product` on the management rows, `Task`
    or `Story` on the delivery ones. So this reads the type.

    Returns `(None, reason)` when the export names no issue type at all,
    which is not a failure - it means this rule cannot decide and the
    caller should fall back rather than silently select nothing.
    """
    if types:
        wanted = {t.strip().lower() for t in types if t.strip()}
        picked = [r for r in rows
                  if str(r.get(type_column) or "").strip().lower() in wanted]
        named = ", ".join(sorted(types))
        if type_column is None:
            return [], "no issue-type column, so --process-type matched nothing"
        return picked, f"issue type is one of: {named} (given on the command line)"

    if type_column is None:
        return None, "this export names no issue type"

    picked = [r for r in rows if is_management_type(r.get(type_column))]
    return picked, f"{type_column!r} names management work"


def build(rows: list[dict[str, Any]], docs: list[str],
          done_status: set[str] | None) -> Summary:
    return Summary(classify(rows, docs, done_status), len(docs))


def render(s: Summary) -> str:
    lines = ["=" * 72, "GOVERNANCE — process tickets, and what can be checked",
             "=" * 72, ""]
    n = len(s.tasks)
    if not n:
        lines.append("  no process tickets in this export.")
        return "\n".join(lines)

    # The checklist first, because it is the part somebody can act on today.
    # The verdicts below say what may be *claimed*; these say what to do.
    lines += [f"  {s.n_steps} procedure steps across {n} process tickets", ""]
    for t in s.tasks:
        if not t.steps:
            continue
        lines.append(f"  {t.key} — {t.summary[:56]}")
        for st in t.steps:
            who = f"[{st.owner}]" if st.owner else "[unassigned]"
            clock = f"  (within {st.sla})" if st.sla else ""
            lines.append(f"      {st.n}. {who:<12} {st.text[:78]}{clock}")
            for c in st.contacts:
                lines.append(f"{'':>10}   send to: {c}")
        lines.append("")

    d = s.delegation
    if d["assignees"] or s.by_owner:
        lines += ["-" * 72, "WHO IS SUPPOSED TO DO THIS", ""]
        for role, c in s.by_owner.items():
            lines.append(f"  the text puts {c} step(s) on {role}")
        if s.by_owner:
            lines.append("")
        for name, c in d["assignees"].items():
            lines.append(f"  the tracker assigns {c} ticket(s) to {name}")
        if d["single_assignee"]:
            lines.append("")
            lines.append(f"  Every one of {len(s.tasks)} process tickets is "
                         f"assigned to the same person,")
            shown = d["roles_named"][:8]
            more = len(d["roles_named"]) - len(shown)
            lines.append(f"  while the procedures name {len(d['roles_named'])} "
                         f"different roles: {', '.join(shown)}"
                         f"{f' and {more} more' if more else ''}.")
            lines.append("  The checklist exists and has not been handed out.")
        lines.append("")

    a = s.acceptance_open
    if a["asked"]:
        lines += ["-" * 72, "ACCEPTANCE QUESTIONS NOBODY HAS ANSWERED", ""]
        lines.append(f"  {a['unanswered']} of {a['asked']} unanswered, across "
                     f"{a['tickets']} tickets.")
        lines.append("  The form still shows its own menu in every answer slot,")
        lines.append("  which is what an untouched form looks like.")
        lines.append("")
        for q in a["questions"]:
            lines.append(f"      {q}")
        lines.append("")

    if s.commitments:
        lines += ["-" * 72, "COMMITMENTS WITH A CLOCK", ""]
        for c in s.commitments:
            lines.append(f"  {c['key']} step {c['n']} — within {c['sla']} "
                         f"({c['owner'] or 'unassigned'})")
        lines.append("  No start event is recorded against these tickets, so")
        lines.append("  nothing here can say whether a deadline was met.")
        lines.append("")

    lines += ["-" * 72, "WHAT THIS EXPORT CAN SUPPORT", ""]
    for k, v in sorted(s.counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {k:<22} {v:>3}")
    lines.append("")
    if not s.checkable:
        lines.append(f"  0 of {n} process tickets can support a finding about a")
        lines.append("  missing deliverable. Every one is either still open — so")
        lines.append("  its deliverable is owed, not overdue — or names a system")
        lines.append("  this pipeline cannot read.")
        lines.append("")
        lines.append("  That is the finding. Grepping the docs tree for these names")
        lines.append("  and reporting what is absent would be wrong on every row.")
    else:
        lines.append(f"  {s.checkable} of {n} are claimed done and name something to "
                     f"look for.")
    held = collections.Counter(sys for t in s.tasks for sys in t.systems)
    if held:
        lines += ["", "  to verify these at all, you need access to:"]
        for name, c in held.most_common():
            lines.append(f"      {name:<22} named by {c} ticket(s)")
    return "\n".join(lines)
