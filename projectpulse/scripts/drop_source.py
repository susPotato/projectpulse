"""Remove one *source* of a paired project, keeping the project itself.

    python -m scripts.drop_source --source excel:Project:upload:cowork-local --dry-run

`app.projects.remove` deletes a delivery project and every source paired
onto it - which is right when the project is going away, and exactly wrong
when one source has been superseded by another. CoWorkLocal reached that
state: the spreadsheet and the Jira collection describe the same 190 work
items, so pairing them summed to 380 tasks instead of merging, and calling
`remove` on the delivery id would have taken the Jira rows and their 351
state changes with it.

So this deletes one source id and nothing else. **The `projects` row and
its registration survive on purpose**: the delivery project is identified
by that id everywhere - dashboards, the traceability run manifest, any
saved board - and deleting it to re-register a different one would break
every reference to buy nothing. What is left is an empty source row that
the surviving pairing hangs off.

Order matters, the same way it does in `projects.remove`: state changes
first, because finding them needs the task ids that are about to go.

The upload behind the source goes too when `--forget-upload` is passed,
because a watched sheet that stays registered is re-imported on the next
sync and the rows come straight back.
"""

from __future__ import annotations

# Must run before any `app.*` import - see scripts/_bootstrap.py.
from scripts._bootstrap import bootstrap

bootstrap()

import argparse  # noqa: E402

from sqlalchemy import delete, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.models.domain import (  # noqa: E402
    Dependency,
    Milestone,
    Project,
    QaItem,
    Resource,
    Risk,
    StateChange,
    Task,
)
from app.models.uploads import UploadedSheet  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", required=True,
                        help="the source project id to empty, e.g. "
                             "excel:Project:upload:cowork-local")
    parser.add_argument("--forget-upload", action="store_true",
                        help="also delete the uploaded sheet, so a later sync "
                             "does not re-import the rows just removed")
    parser.add_argument("--keep-milestones", action="store_true",
                        help="leave the milestones behind. Use this when the "
                             "source is being rebuilt rather than retired: a "
                             "re-key drops the old rows and writes new ones, "
                             "and the feature grouping is not reproducible "
                             "from the tracker, so deleting it loses it for "
                             "good. This has already cost one restore.")
    parser.add_argument("--dry-run", action="store_true",
                        help="count what would go and write nothing")
    args = parser.parse_args(argv)

    source = args.source.strip()
    counts: dict[str, int] = {}

    with session_scope() as session:
        if session.get(Project, source) is None:
            raise SystemExit(f"no project row with id {source!r}")

        task_ids = list(session.scalars(
            select(Task.id).where(Task.project_id == source)).all())
        qa_ids = list(session.scalars(
            select(QaItem.id).where(QaItem.project_id == source)).all())
        entity_ids = task_ids + qa_ids

        # State changes first: they are found *through* the rows below.
        if entity_ids:
            counts["state_changes"] = len(session.scalars(
                select(StateChange.id)
                .where(StateChange.entity_id.in_(entity_ids))).all())
        else:
            counts["state_changes"] = 0

        models = [(Dependency, "dependencies"), (Resource, "resources"),
                  (Risk, "risks"), (QaItem, "qa_items"), (Task, "tasks")]
        if not args.keep_milestones:
            models.append((Milestone, "milestones"))
        for model, name in models:
            column = getattr(model, "project_id", None)
            if column is None:
                continue
            counts[name] = len(session.scalars(
                select(model.id).where(column == source)).all())

        sheets = list(session.scalars(
            select(UploadedSheet).where(UploadedSheet.project_id == source)).all())
        counts["uploaded_sheets"] = len(sheets) if args.forget_upload else 0

        print(f"source: {source}")
        for name, n in counts.items():
            print(f"  {name:<16}{n:>6}")

        if args.dry_run:
            print("\ndry run - nothing was written")
            return 0

        if entity_ids:
            session.execute(
                delete(StateChange).where(StateChange.entity_id.in_(entity_ids)))
        for model, _ in models:
            column = getattr(model, "project_id", None)
            if column is not None:
                session.execute(delete(model).where(column == source))
        if args.forget_upload:
            for sheet in sheets:
                session.delete(sheet)

    print("\nremoved. The project row and its pairing are untouched, so the "
          "delivery project keeps its id and its other source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
