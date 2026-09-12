"""The one path a collector takes to attach a project to its program.

Both convertors need a `programs` row to exist before they can write a
`projects` row that points at it, and both used to satisfy that need the same
wrong way:

    program_id = domain_id(SOURCE, "Program", connection_id, "DEFAULT")

That is the duplicate-program bug in one line. `SOURCE` differs between the two
collectors, so one program became `excel:Program:1:DEFAULT` and
`jira:Program:1:DEFAULT` - two rows with the same name, and the two source
projects of a single delivery (invariant 7) hanging off different ones. The
Programs list then suppressed the orphan by matching on *name*, and the rollup
route did not suppress it at all.

The fix is not a better id format at each call site. It is that a collector does
not get to decide what program something belongs to: it asks `app.scope`, which
is already the module that owns "which ids are one thing", and writes whatever
it is told. This module is that ask, in one function, so the two convertors
cannot drift apart again.

Nothing here invents a program for a project `scope` does not place. A project
whose program nobody has stated belongs to no program, `Project.program_id`
stays `NULL`, and every screen reports it as unassigned - which is true, and is
recoverable by saying so, unlike a project quietly filed under a program that
was made up to satisfy a foreign key.
"""

from __future__ import annotations

import logging

from app import scope
from app.models.domain import Program

log = logging.getLogger(__name__)


def ensure_program(session, project_id: str) -> str | None:
    """The program `project_id` belongs to, with its row created if absent.

    Returns the program id to stamp on the `projects` row, or `None` when the
    project is not placed in a program. Callers must pass that `None` straight
    through to `Project.program_id` rather than substituting a default.

    `project_id` may be any of a project's source ids: `scope.program_for`
    resolves the pairing first, so the Jira-side and Excel-side rows of one
    delivery project reach the same program. That resolution *is* the fix.
    """
    program_id = scope.program_for(project_id)
    if program_id is None:
        log.debug("project %s is not assigned to a program", project_id)
        return None

    if session.get(Program, program_id) is not None:
        return program_id

    declared = scope.find_program(program_id)
    if declared is None:
        # `scope` placed the project in a program it does not describe - a
        # registered project naming a program that was never set up. Create the
        # row so the foreign key holds and the project is reachable, named from
        # the id rather than from a guess.
        log.info("creating undeclared program %s", program_id)
        session.add(Program(id=program_id, name=program_id, status="Active"))
        return program_id

    session.add(
        Program(
            id=declared.program_id,
            name=declared.name,
            owner=declared.owner,
            status=declared.status,
        )
    )
    return program_id
