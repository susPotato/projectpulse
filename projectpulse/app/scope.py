"""Which source ids are one delivery project, and which program owns it.

Invariant 7: Jira and Excel each create their own `projects` row for the same
piece of delivery, so analysing either alone throws away every cross-source
claim. `analyze_project(..., also=[...])` re-points them at one canonical id -
and until now the pairing was a literal `also = ["jira:Project:1:HRMS"]` sitting
in the API route, which meant the portfolio view had no way to know that two
rows were one project and would have listed HRMS twice.

**The same invariant applies one level up, and used not to.** Each convertor
built its program id as `domain_id(SOURCE, "Program", connection_id, "DEFAULT")`,
so one program became `excel:Program:1:DEFAULT` *and* `jira:Program:1:DEFAULT` -
two rows, the same name, and HRMS's two source projects hanging off different
ones. The Programs list papered over it by dropping an empty program that shared
a *name* with a non-empty one, which is name-equality entity resolution in the
display layer: exactly the fuzzy merge this module exists to avoid, and it left
`/api/programs/jira:Program:1:DEFAULT` answering 200 with zero projects,
indistinguishable from a program nobody has filed a project against yet.

A program is not an artifact of the system a document arrived from. It is the
thing the delivery is *for*, it is the tenant boundary (content addressing,
retention, and the unit factors in `app/units.py` are all scoped to it), and
its identity therefore cannot contain a source name. Programs are keyed
`program:Program:0:<KEY>` - source-neutral, connection-neutral - and membership
is declared here, in the one module that already owns "which ids are one
thing", rather than invented by whichever collector happened to run first.

This is that pairing, in one place. The demo project is a built-in seed;
anything registered since - typically by uploading a document for a project
nobody has seen before, via Settings > Sources - is layered on top of it.

**Registered projects live in the database**, not in a file. They used to be a
small JSON file under `PULSE_STATE_DIR`, which works on a laptop and quietly
fails on a host: a Fly machine's filesystem does not survive a deploy, so a
project imported through the browser disappeared from every picker on the next
release while its ingested rows stayed in Postgres, orphaned. Same reasoning as
the narration cache and the OneDrive sign-in, and the same conclusion.

Read fresh on every call rather than cached, because a second worker process
must see what the first one wrote - and an unreachable or not-yet-created table
returns "just the seed" rather than raising, exactly as a missing file did.
Nothing here needs a session from its caller; a read that cannot happen is not
an error at this layer.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


#: The source component every program id carries. Not a source system - that
#: is the defect this constant closes - but a namespace saying "this row is
#: about a program, and no collector owns it".
PROGRAM_SOURCE = "program"

#: Connection id for programs. Zero because a program does not belong to a
#: connection: that was the second half of the duplicate-program bug, since two
#: connections to the same program produced two ids for it.
PROGRAM_CONNECTION = 0


def program_domain_id(key: str) -> str:
    """The id of the program known by `key`.

    One function so the convertors, the seed, the migration and the tests
    cannot disagree about the format - the same reason `scripts/migrate_ids.py`
    calls `domain_id` rather than doing string surgery.
    """
    from app.ids import domain_id

    return domain_id(PROGRAM_SOURCE, "Program", PROGRAM_CONNECTION, key)


@dataclass(frozen=True)
class DeliveryProgram:
    """A program: the thing projects are grouped under, and the tenant boundary.

    Carries no source component (see the module docstring) and no
    configuration: the unit factors and working-day calendar a program works in
    live in `app/units.py`, keyed by `program_id`, because they are decisions
    about a client rather than facts about a grouping.
    """

    program_id: str
    name: str
    owner: str | None = None
    status: str = "Active"


@dataclass(frozen=True)
class DeliveryProject:
    """One project as a delivery manager thinks of it, whatever fed it."""

    #: The id every other source id is re-pointed at.
    canonical_id: str
    name: str
    #: The same delivery project as other source systems call it.
    also: tuple[str, ...] = field(default_factory=tuple)
    #: The program this project belongs to. Declared here rather than derived
    #: from whichever collector created the row, which is what let one program
    #: exist twice. `None` means nobody has said yet - a legitimate state for a
    #: project registered by upload before its program was chosen, and the
    #: reason the portfolio can still show it.
    program_id: str | None = None

    @property
    def source_ids(self) -> tuple[str, ...]:
        return (self.canonical_id, *self.also)


#: The demo portfolio. HRMS is one project, two sources - the case the
#: precision model exists for, so it is the right seed to ship. SAIN and
#: Example Project (`scripts/gen_portfolio_data.py`) are single-source and
#: exist so the portfolio, and the Program-level rollups built on it, have
#: more than one row - the names match the PM's own cross-project mockups
#: (`Layout_Program`'s Risk/Schedule screens list "Example Project, SAIN,
#: HRMS" together). A registered project of the same canonical_id overrides
#: its entry here rather than duplicating it, so re-uploading the demo's own
#: files is a no-op.
_SEED: tuple[DeliveryProject, ...] = (
    DeliveryProject(
        canonical_id="excel:Project:1:HRMS",
        name="HRMS Platform",
        also=("jira:Project:1:HRMS",),
        program_id=program_domain_id("DEFAULT"),
    ),
    DeliveryProject(
        canonical_id="excel:Project:1:SAIN",
        name="SAIN",
        program_id=program_domain_id("DEFAULT"),
    ),
    DeliveryProject(
        canonical_id="excel:Project:1:EXPROJ",
        name="Example Project",
        program_id=program_domain_id("DEFAULT"),
    ),
)

#: The programs the demo ships with. `CLOUD` deliberately has no projects - a
#: program somebody has set up and not yet filed anything against is a real
#: state, and the Programs list has to render it as `no_data` rather than hide
#: it. Note what is no longer needed to make that work: with programs keyed
#: source-neutrally there is no duplicate to suppress, so the list no longer
#: drops empty programs by name and an empty program is believed.
_SEED_PROGRAMS: tuple[DeliveryProgram, ...] = (
    DeliveryProgram(
        program_id=program_domain_id("DEFAULT"),
        name="Digital Transformation 2026",
        status="Active",
    ),
    DeliveryProgram(
        program_id=program_domain_id("CLOUD"),
        name="Cloud-First Initiative",
        status="Active",
    ),
)


def _rows() -> list[DeliveryProject]:
    """Registered projects, or nothing at all if they cannot be read.

    Every failure mode collapses to "there are none", which is the same answer
    a missing JSON file gave and the right one here: `all_projects()` is on the
    path of every request, and a database that is down or a schema that has not
    been created yet must degrade to the built-in seed rather than take down
    the portfolio, the project picker and the risk form with it.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models.uploads import RegisteredProject

    try:
        with session_scope() as session:
            rows = session.execute(
                select(
                    RegisteredProject.canonical_id,
                    RegisteredProject.name,
                    RegisteredProject.also,
                    RegisteredProject.program_id,
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("ignoring unreadable project registry: %s", exc)
        return []

    out: list[DeliveryProject] = []
    for canonical_id, name, also, program_id in rows:
        if not canonical_id or not name:
            continue
        try:
            paired = tuple(str(a) for a in json.loads(also or "[]"))
        except (TypeError, json.JSONDecodeError):
            # A pairing we cannot read is one project with one source id,
            # which is the conservative reading - never a dropped project.
            paired = ()
        out.append(
            DeliveryProject(
                canonical_id=canonical_id,
                name=name,
                also=paired,
                program_id=program_id or None,
            )
        )
    return out


def all_projects() -> tuple[DeliveryProject, ...]:
    """Every delivery project: the built-in demo, plus anything registered
    since. Read fresh each call - see the module docstring for why."""
    merged: dict[str, DeliveryProject] = {p.canonical_id: p for p in _SEED}
    for entry in _rows():
        merged[entry.canonical_id] = entry
    return tuple(merged.values())


def register(
    canonical_id: str,
    name: str,
    also: tuple[str, ...] = (),
    program_id: str | None = None,
) -> DeliveryProject:
    """Add a project, or update its pairing/name if the id already exists.

    Called when a document is uploaded for a project id nobody has ingested
    before - see `POST /api/sources/upload`. Committed immediately so the
    portfolio picks it up (as `no_data`, correctly, until something is
    actually ingested for it) without a restart, and so it is still there
    after the next deploy.

    `program_id` is accepted here because the upload is the moment the choice
    is available to make: a person adding a project knows which program it is
    for, and deriving it afterwards from whichever collector ran is how one
    program ended up existing twice. `None` is honest - an uploaded project
    whose program nobody stated belongs to no program until somebody says so,
    and the portfolio shows it either way.

    Unlike the read above, a failure here is **raised**: the caller is in the
    middle of accepting somebody's document and telling them it worked when
    the project was not recorded is the bug this whole change is about.
    """
    from app.db import session_scope
    from app.models.uploads import RegisteredProject

    entry = DeliveryProject(
        canonical_id=canonical_id,
        name=name,
        also=tuple(also),
        program_id=program_id,
    )
    with session_scope() as session:
        session.merge(
            RegisteredProject(
                canonical_id=canonical_id,
                name=name,
                also=json.dumps(list(also)),
                program_id=program_id,
            )
        )
    return entry


def find(canonical_id: str) -> DeliveryProject | None:
    return next((p for p in all_projects() if p.canonical_id == canonical_id), None)


def resolve(source_id: str) -> DeliveryProject | None:
    """The delivery project a source id belongs to, canonical or paired.

    `find` matches the canonical id only. A caller holding
    `jira:Project:1:HRMS` is naming the same delivery project as
    `excel:Project:1:HRMS` - invariant 7 - and telling it that project is
    unknown is how one project's data ends up filed in two places. Anything
    written *against* a project should be stored under the returned
    `canonical_id` rather than whichever id the caller happened to have.
    """
    for project in all_projects():
        if source_id == project.canonical_id or source_id in project.also:
            return project
    return None


def source_ids_for(source_id: str) -> list[str]:
    """Every id one delivery project may have been filed under.

    For reading back something stored per project by an earlier version, or by
    a caller that only had a paired id: a query over these finds the project's
    rows whichever of its ids they carry. An unknown id is returned as itself,
    for the same reason `also_for` returns an empty list rather than raising.
    """
    project = resolve(source_id)
    return list(project.source_ids) if project else [source_id]


def program_for(source_id: str) -> str | None:
    """The program a project belongs to, given *any* of its source ids.

    This is the fix for the duplicate program, and the `resolve` call is the
    whole of it. `jira:Project:1:HRMS` and `excel:Project:1:HRMS` are one
    delivery project (invariant 7), so they belong to one program; asking the
    `projects` table directly returns whichever program the collector that
    wrote that row invented, which is how the same program came to exist under
    two ids with the same name.

    Returns `None` for a project nobody has assigned, which callers must render
    as "no program" rather than substituting an arbitrary one.
    """
    project = resolve(source_id)
    return project.program_id if project is not None else None


def _program_rows() -> list[DeliveryProgram]:
    """Programs somebody created, or nothing at all if they cannot be read.

    Degrades to "there are none" on every failure, exactly as `_rows` does and
    for the same reason: this runs on the path of every request, and a database
    that is down or a table that has not been created yet must leave the
    built-in seed working rather than take the Programs list down with it.
    """
    from sqlalchemy import select

    from app.db import session_scope
    from app.models.uploads import RegisteredProgram

    try:
        with session_scope() as session:
            rows = session.execute(
                select(
                    RegisteredProgram.program_id,
                    RegisteredProgram.name,
                    RegisteredProgram.owner,
                    RegisteredProgram.status,
                )
            ).all()
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("ignoring unreadable program registry: %s", exc)
        return []

    return [
        DeliveryProgram(
            program_id=program_id,
            name=name,
            owner=owner,
            status=status or "Active",
        )
        for program_id, name, owner, status in rows
        if program_id and name
    ]


def all_programs() -> tuple[DeliveryProgram, ...]:
    """Every program: the demo seed, the ones somebody created, and any a
    registered project names.

    Three sources, in increasing order of authority. A program reachable only
    because a project points at it still has to appear, or that project is
    filed somewhere the UI cannot navigate to; its name then falls back to the
    key inside its id, which is better than a blank row and disappears as soon
    as the program is given a real name.
    """
    merged: dict[str, DeliveryProgram] = {p.program_id: p for p in _SEED_PROGRAMS}
    for entry in _program_rows():
        merged[entry.program_id] = entry
    for project in all_projects():
        pid = project.program_id
        if pid and pid not in merged:
            merged[pid] = DeliveryProgram(program_id=pid, name=_key_of(pid))
    return tuple(merged.values())


def slugify(name: str) -> str:
    """A name reduced to the key half of a domain id.

    One function because two call sites derive an id from a typed name - adding
    a project here and uploading the first document for a project nobody has
    seen - and they must agree. If they drift, uploading a sheet for a project
    somebody already added by name creates a *second* project rather than
    filling in the one they made, which is the duplicate this module exists to
    prevent, arriving through the front door.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if slug:
        return slug

    # A name with no ASCII letters or digits in it - "工数管理", "予算" - slugs to
    # nothing. Returning a constant here would map *every* such name onto one
    # id, so the second Japanese-named program a client creates would silently
    # become the first one. Fall back to a short stable digest of the name: ugly
    # in a URL, distinct per name, and identical across processes and restarts,
    # which a `hash()` would not be.
    import hashlib

    text = (name or "").strip()
    if not text:
        return "unnamed"
    return "x" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]


def register_program(
    name: str, owner: str | None = None, status: str = "Active"
) -> DeliveryProgram:
    """Create a program, or rename one whose key the name already maps to.

    The id is derived from the name rather than typed, so a program cannot be
    created with a source system's name inside its identity - the defect this
    whole namespace exists to close. Two programs whose names slug identically
    ("Cloud First" and "cloud-first") are one program, deliberately: that is
    almost always somebody creating the same thing twice, and the alternative
    is two rows a person cannot tell apart on the list.

    Raised on failure, never swallowed - unlike the read above. Somebody is
    waiting on a form, and telling them it worked when nothing was written is
    the class of bug this registry replaced.
    """
    program_id = program_domain_id(slugify(name).upper())

    from app.db import session_scope
    from app.models.uploads import RegisteredProgram

    entry = DeliveryProgram(
        program_id=program_id, name=name.strip(), owner=owner, status=status
    )
    with session_scope() as session:
        session.merge(
            RegisteredProgram(
                program_id=program_id,
                name=entry.name,
                owner=entry.owner,
                status=entry.status,
            )
        )
    return entry


def find_program(program_id: str) -> DeliveryProgram | None:
    return next((p for p in all_programs() if p.program_id == program_id), None)


def projects_in(program_id: str) -> tuple[DeliveryProject, ...]:
    """Every delivery project in one program.

    The Program rollup's project set, resolved from the one place that knows
    program membership rather than by filtering a `projects` table whose
    `program_id` column is per-source.
    """
    return tuple(p for p in all_projects() if p.program_id == program_id)


def _key_of(program_id: str) -> str:
    """The trailing key of a program id, for a program with no declared name."""
    from app.ids import parse_domain_id

    try:
        return parse_domain_id(program_id)[3][-1]
    except (ValueError, IndexError):
        return program_id


def also_for(source_id: str) -> list[str]:
    """The project's *other* source ids, for `analyze_project(also=...)`.

    Resolves rather than looks up, and that is the whole of a real defect this
    used to have. It called `find`, which matches canonical ids only, so
    `also_for("jira:Project:1:HRMS")` answered `[]` - not because that project
    is unpaired but because the caller happened to hold the paired half. The
    effect was silent and bad: `/api/insight?project=jira:Project:1:HRMS`
    analysed one source of a two-source project and presented the result as the
    whole project (measured on the demo: 1 finding instead of 10, 4 schedule
    rows instead of 10, 0 logged hours instead of 28).

    That is invariant 7 failing exactly the way `resolve` was written to
    prevent - "telling it that project is unknown is how one project's data
    ends up filed in two places" - and `risks/service.py` already resolved
    while these callers did not.

    An id genuinely nobody has paired still gets `[]`: that caller is asking a
    legitimate question whose answer is "just this one source".
    """
    project = resolve(source_id)
    if project is None:
        return []
    return [other for other in project.source_ids if other != source_id]


def canonical_pairing(source_id: str) -> tuple[str, list[str]]:
    """`(canonical_id, other_ids)` for whichever of a project's ids you hold.

    The pair a project-scoped API route needs, in one call. `also_for` alone
    fixes *which rows get analysed*; this also fixes *what the result is filed
    under*, so a bundle fetched by the Jira id reports the same `project_id` as
    one fetched by the Excel id instead of two bundles that describe one project
    under two names. Same rule `resolve` states: anything written against a
    project belongs under the returned `canonical_id`, not under whichever id
    the caller happened to have.

    An unknown id is returned as itself with no pairing - the caller is not
    wrong to ask, and 404ing here would take a legitimate "I have not ingested
    that yet" and turn it into "that is not a project".
    """
    project = resolve(source_id)
    if project is None:
        return source_id, []
    return project.canonical_id, [
        other for other in project.source_ids if other != project.canonical_id
    ]


def __getattr__(name: str):
    """`from app.scope import PORTFOLIO` still works, read fresh each time.

    A handful of call sites predate the registry and import the name directly
    rather than calling `all_projects()`; PEP 562 makes that import re-resolve
    on every access instead of freezing the seed at whatever moment this
    module first loaded.
    """
    if name == "PORTFOLIO":
        return all_projects()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
