"""A small local console for watching the retriever work.

Not the product UI. This exists so a change to a spreadsheet can be made in Excel
and its consequences seen immediately: which rows were read, which were refused,
what state changes came out, how precisely they are dated, and how many of them
can actually be ordered against each other.

    python -m scripts.demo          # then open http://127.0.0.1:8000

The workflow it is built around is the real one: edit `data/demo/*.xlsx` in Excel,
or `data/demo/jira/*.json` in any editor, press Sync, and look at what changed.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.db import create_all, drop_all, session_scope
from app.ingest.runner import run_sync
from app.ingest.sources.excel.reader import sha256_file
from app.ingest.sources.excel.source import WATCHED
from app.intelligence.temporal.ordering import OrderingBasis, ordering_basis
from app.models.domain import StateChange
from app.models.sync import RawReject, SheetScan, SyncRun

# Importing the source modules registers them with the runner.
import app.ingest.sources.excel.source  # noqa: F401
import app.ingest.sources.jira.source  # noqa: F401

app = FastAPI(title="ProjectPulse retriever console")
STATIC = Path(__file__).parent / "static"


class SyncRequest(BaseModel):
    source: str = "excel"
    #: Simulated scan time. Blank means now. It matters: scan times are what bound
    #: every spreadsheet change, so replaying a timeline needs control of them.
    now: str | None = None


class StepRequest(BaseModel):
    step: int = 0


def _parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _short(entity_id: str) -> str:
    return entity_id.split(":", 3)[-1]


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/state")
def state() -> dict:
    """Everything the console renders, in one round trip."""
    with session_scope() as session:
        changes = session.scalars(
            select(StateChange).order_by(
                StateChange.occurred_at.desc(), StateChange.entity_id
            )
        ).all()
        rejects = session.scalars(
            select(RawReject).order_by(RawReject.id.desc())
        ).all()
        runs = session.scalars(
            select(SyncRun).order_by(SyncRun.id.desc()).limit(12)
        ).all()
        scans = session.scalars(select(SheetScan)).all()

        last_scan: dict[str, SheetScan] = {}
        for scan in scans:
            prior = last_scan.get(scan.scope)
            if prior is None or scan.scanned_at > prior.scanned_at:
                last_scan[scan.scope] = scan

        # Ordering, computed live so the effect of each sync is visible.
        pairs = []
        for a in changes:
            for b in changes:
                if a is b:
                    continue
                basis = ordering_basis(a, b)
                if basis is not OrderingBasis.UNPROVABLE:
                    pairs.append((a, b, basis))

        cross = [
            {
                "earlier": f"{_short(a.entity_id)}.{a.field}",
                "later": f"{_short(b.entity_id)}.{b.field}",
                "basis": str(basis),
            }
            for a, b, basis in pairs
            if a.precision == "exact" and b.precision == "bounded"
        ]

        total_pairs = len(changes) * (len(changes) - 1)

        files = []
        for watched in WATCHED:
            path = Path(settings.data_root) / watched.file_name
            scope = f"{watched.file_name}#{watched.sheet_name}"
            scan = last_scan.get(scope)
            exists = path.exists()
            current_hash = sha256_file(path) if exists else None
            files.append(
                {
                    "kind": "excel",
                    "scope": scope,
                    "path": str(path),
                    "exists": exists,
                    "last_scan": scan.scanned_at.isoformat() if scan else None,
                    "rows": scan.row_count if scan else 0,
                    # The console's most useful single fact: is there anything
                    # to ingest, or would a sync be a no-op?
                    "dirty": bool(exists and scan and scan.sha256 != current_hash),
                    "never_scanned": exists and scan is None,
                }
            )

        jira_dir = Path(settings.data_root) / "jira"
        jira_files = sorted(jira_dir.glob("*.json")) if jira_dir.exists() else []
        jira_issues = 0
        jira_items = 0
        for path in jira_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            issues = payload.get("issues", [])
            jira_issues += len(issues)
            jira_items += sum(
                len(h.get("items", []))
                for issue in issues
                for h in (issue.get("changelog") or {}).get("histories", [])
            )

        return {
            "data_root": str(settings.data_root),
            "files": files,
            "jira": {
                "dir": str(jira_dir),
                "files": [p.name for p in jira_files],
                "issues": jira_issues,
                "field_changes": jira_items,
            },
            "counts": {
                "changes": len(changes),
                "exact": sum(1 for c in changes if c.precision == "exact"),
                "bounded": sum(1 for c in changes if c.precision == "bounded"),
                "low_confidence": sum(
                    1 for c in changes if c.identity_confidence == "low"
                ),
                "orderable": len(pairs),
                "total_pairs": total_pairs,
                "rejects": len(rejects),
            },
            "changes": [
                {
                    "entity": _short(c.entity_id),
                    "source": c.entity_id.split(":", 1)[0],
                    "field": c.field,
                    "old": c.old_value,
                    "new": c.new_value,
                    "precision": c.precision,
                    "confidence": c.identity_confidence,
                    "lower": c.occurred_at_lower.isoformat(),
                    "upper": c.occurred_at.isoformat(),
                }
                for c in changes[:200]
            ],
            "cross_pairs": cross[:40],
            "rejects": [
                {
                    "where": f"{r.sheet_name}!row{r.row_index}",
                    "reason": r.reason,
                }
                for r in rejects[:40]
            ],
            "runs": [
                {
                    "id": r.id,
                    "source": r.source,
                    "trigger": r.trigger,
                    "status": r.status,
                    "at": r.started_at.isoformat() if r.started_at else None,
                    "rows_ok": r.rows_ok,
                    "rejected": r.rows_rejected,
                    "changes": r.changes_emitted,
                }
                for r in runs
            ],
        }


@app.post("/api/sync")
def sync(request: SyncRequest) -> dict:
    """The same call the scheduler and the PM's button make."""
    now = _parse_now(request.now)
    try:
        with session_scope() as session:
            outcome = run_sync(session, request.source, "manual", now=now)
    except Exception as exc:  # noqa: BLE001 - surfaced in the console, not swallowed
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {
        "ok": True,
        "run_id": outcome.run_id,
        "status": outcome.status,
        "rows_ok": outcome.rows_ok,
        "rows_rejected": outcome.rows_rejected,
        "changes_emitted": outcome.changes_emitted,
        "notes": outcome.notes,
        "at": now.isoformat(),
    }


@app.post("/api/write-step")
def write_step(request: StepRequest) -> dict:
    """Overwrite the demo workbooks at a given point in their story.

    A shortcut for the guided tour. Editing the files in Excel by hand does
    exactly the same thing and is the more convincing demonstration.
    """
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gen_demo_data", "--step", str(request.step)],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    return {
        "ok": result.returncode == 0,
        "output": (result.stdout or result.stderr).strip(),
    }


@app.post("/api/write-jira")
def write_jira() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "scripts.gen_jira_data"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[2],
    )
    return {
        "ok": result.returncode == 0,
        "output": (result.stdout or result.stderr).strip(),
    }


@app.post("/api/reset")
def reset() -> dict:
    """Drop and recreate the schema, so a timeline can be replayed from scratch."""
    drop_all()
    create_all()
    return {"ok": True}
