"""Which artifacts in a run were left behind by a change to an earlier stage.

Stages are independent and that is the point: re-running `retrieve` after a
config change should not force `adjudicate`. The cost is that nothing
notices when a stage *should* have been re-run and was not, and a run can
hold two artifacts that disagree about the same number while both look
finished.

This is not hypothetical. On 2026-09-22 a corpus fix rebuilt `corpus`,
`index`, `candidates` and `verdicts`; `grounding` and `links` were re-run
with them, `explain` and `cost_report` were not. The run then reported 73
corroborated verdicts in its ledger, 76 in `verdicts.json`, and an
`explain` page that restated the older set as prose. Nothing failed. The
integration invariants did not catch it either, because they cover the free
stages and this drift is between a paid stage and its summaries.

**Content, with timestamps only as a fallback.** Each artifact records a
fingerprint of every input it was built from, stamped by `artifacts.save`
because that is the one function every stage writes through. Staleness is
then a fingerprint that no longer matches — exact, and indifferent to when
anything was written.

Modification time was the first design and it was wrong, in the way that
matters most. A stage re-run to *identical* output still bumps its mtime,
so rebuilding a free stage reported everything below it as stale, up to and
including `adjudicate` at $9 a run. A check that cries wolf on the
expensive decision is one people learn to ignore.

What remains, stated rather than hidden:

* **An artifact written before stages stamped their inputs** has nothing to
  compare, so those fall back to mtime and are labelled as doing so. They
  over-report; one re-run of the free stages replaces them with content,
  and `--accept` settles a paid one whose inputs were checked by hand.
* **A fingerprint covers the payload, not the file.** Meta changes — a
  model name, a stamp, an accept — are not changes to the data and must not
  invalidate anything downstream.
* **Only what `DERIVES_FROM` lists is checked.** It is asserted against
  `cli.py` in the tests, because a map that drifts from the code is worse
  than no map at all.

It cannot report an artifact that was never written. A missing artifact is
a gap the pipeline already names, not drift.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

#: What each artifact is built from, as the CLI commands actually read them.
#: `tests/test_freshness.py` asserts this against `cli.py` rather than
#: trusting it, because a map that drifts from the code is worse than none.
DERIVES_FROM: dict[str, tuple[str, ...]] = {
    "translations": ("tickets",),
    "features": ("corpus",),
    "progress": ("corpus",),
    "index": ("corpus", "features", "tickets", "translations"),
    "candidates": ("corpus", "features", "tickets", "translations"),
    "config": ("corpus", "features", "tickets", "translations"),
    "reconciliation": ("candidates", "features", "progress", "tickets"),
    "delivery": ("progress",),
    "gates": ("corpus", "progress"),
    "verdicts": ("candidates", "corpus", "features", "tickets", "translations"),
    "shadow": ("candidates", "corpus", "verdicts"),
    "explain": ("tickets", "verdicts"),
    "links": ("corpus", "tickets", "verdicts"),
    "grounding": ("corpus", "verdicts"),
    "cost_report": ("translations", "verdicts", "explain", "grounding"),
    "cohorts": ("tickets", "translations", "verdicts"),
}

#: The command that rebuilds each artifact, and whether running it spends money.
REBUILD: dict[str, tuple[str, bool]] = {
    "translations": ("translate", True),
    "features": ("features", False),
    "progress": ("progress", False),
    "index": ("retrieve", False),
    "candidates": ("retrieve", False),
    "config": ("retrieve", False),
    "reconciliation": ("reconcile", False),
    "delivery": ("effort", False),
    "gates": ("gates", False),
    "verdicts": ("adjudicate", True),
    "shadow": ("shadow", False),
    "explain": ("explain", True),
    "links": ("couple", False),
    "grounding": ("verify", False),
    "cost_report": ("cost", False),
    "cohorts": ("cohorts", False),
}

# `governance` is deliberately in neither map, like `tickets`, `corpus` and
# `diagnosis`: it reads the export and the docs tree, which are external
# inputs this module does not track, so it has no artifact to go stale
# against.

FILENAME = {"cost_report": "cost_report.json"}


def _path(run: Path, name: str) -> Path:
    return run / FILENAME.get(name, f"{name}.json")


def fingerprint(path: str | Path) -> str:
    """A short content hash of an artifact's payload, or "" if absent.

    **The payload, not the file.** Hashing the whole file also hashes its
    meta, and meta changes for reasons that are not changes to the data:
    stamping inputs, accepting an artifact, recording a model name. Those
    would each invalidate everything downstream for no reason — accepting
    `verdicts` invalidated all five artifacts built on it, which is how
    this was found.

    Nothing is lost by narrowing it, because `DERIVES_FROM` lists every
    input a stage actually reads. A change does not need to propagate
    through an intermediate: whatever reads `corpus` is checked against
    `corpus` itself.
    """
    p = Path(path)
    if not p.exists():
        return ""
    try:
        payload = json.loads(p.read_text(encoding="utf-8")).get("payload")
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # Unreadable is not unchanged: fall back to the bytes so a corrupt
        # file still differs from a good one rather than silently matching.
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def stamp(path: str | Path) -> dict[str, str]:
    """What to record in an artifact's meta so staleness is decided by content.

    Called by `artifacts.save`, which is the one place every stage writes
    through — so no stage has to remember to do this, and a stage added
    later gets it by being named in `DERIVES_FROM`.
    """
    p = Path(path)
    inputs = DERIVES_FROM.get(p.stem)
    if not inputs:
        return {}
    got = {i: fingerprint(_path(p.parent, i)) for i in inputs}
    return {i: h for i, h in got.items() if h}


def accept(run: Path, name: str, when: str) -> list[str]:
    """Record an artifact's current inputs without rebuilding it.

    For the case the timestamp fallback cannot settle: an artifact written
    before stages stamped their inputs, whose inputs have since been
    rebuilt to identical content. There is nothing to fix and no way for
    the check to know that, so it keeps reporting — and a check that cries
    wolf on the one artifact that costs $9 to rebuild is a check people
    learn to ignore.

    This is an assertion by a person, not a measurement, so it is recorded
    as one: `inputs_accepted` sits beside the fingerprints and says when.
    It is the only way to silence a row, and it silences that row only
    until an input really changes.
    """
    import json as _json

    path = _path(run, name)
    body = _json.loads(path.read_text(encoding="utf-8"))
    meta = body.get("meta") or {}
    got = stamp(path)
    meta["inputs"] = got
    meta["inputs_accepted"] = when
    body["meta"] = meta
    path.write_text(_json.dumps(body, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return sorted(got)


def _recorded(path: Path) -> dict[str, str] | None:
    """The input fingerprints an artifact was written with, if it has any."""
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta") or {}
    except (json.JSONDecodeError, OSError):
        return None
    got = meta.get("inputs")
    return got if isinstance(got, dict) else None


@dataclass
class Stale:
    """One artifact built from an input that has since changed."""

    artifact: str
    #: The inputs that no longer match, newest first.
    newer: list[str]
    command: str
    paid: bool
    #: "content" when fingerprints disagree, "mtime" when the artifact
    #: predates fingerprinting and only its timestamp could be compared.
    basis: str = "content"

    @property
    def reason(self) -> str:
        verb = "was built from an older" if self.basis == "content" else "is older than"
        return f"{self.artifact} {verb} " + ", ".join(self.newer)


def check(run: Path) -> list[Stale]:
    """Artifacts present in `run` that predate an input they were built from."""
    run = Path(run)
    mtime: dict[str, float] = {}
    for name in set(DERIVES_FROM) | {i for v in DERIVES_FROM.values() for i in v}:
        p = _path(run, name)
        if p.exists():
            mtime[name] = p.stat().st_mtime

    out: list[Stale] = []
    for name, inputs in DERIVES_FROM.items():
        if name not in mtime:
            continue
        path = _path(run, name)
        recorded = _recorded(path)
        if recorded is not None:
            # Content, which is exact: an input rebuilt to the same bytes is
            # not a change, and copying the run does not hide one.
            changed = [i for i in inputs
                       if i in recorded and recorded[i] != fingerprint(_path(run, i))]
            basis = "content"
        else:
            # Written before stages stamped their inputs. Timestamps are all
            # that is left, and they over-report; say which was used.
            changed = [i for i in inputs if i in mtime and mtime[i] > mtime[name]]
            basis = "mtime"
        if changed:
            changed.sort(key=lambda i: -mtime.get(i, 0))
            cmd, paid = REBUILD[name]
            out.append(Stale(name, changed, cmd, paid, basis))
    out = [s for s in out if s.newer]
    # Deepest first: rebuilding an input restates the artifacts below it, so
    # reporting those first is the order somebody would actually work in.
    order = list(DERIVES_FROM)
    out.sort(key=lambda s: order.index(s.artifact))
    return out


def source_moved(run: Path) -> str:
    """Why this run is out of date against the code itself, or "".

    The rest of this module compares artifacts to each other, which cannot
    see the failure a repeated run actually has: every artifact agreeing,
    about a repository that moved last week. `corpus` records the revision
    it read; this asks that checkout what it says now.

    Silent when the run predates revision recording, or when the source was
    never a git repository - a zip drop cannot be stale in this sense, and
    printing a line about it on every check is how the line that matters
    gets ignored.
    """
    from tracelink import source as SRC

    path = _path(Path(run), "corpus")
    if not path.exists():
        return ""
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    recorded = (body.get("meta") or {}).get("revision")
    root = (body.get("payload") or {}).get("root")
    if not recorded or not root:
        return ""
    return SRC.moved(recorded, Path(root))


def render(stale: list[Stale], run: Path) -> str:
    lines = ["=" * 68, "ARTIFACTS LEFT BEHIND BY A LATER CHANGE", "=" * 68, ""]
    if not stale:
        lines.append(f"  none — every artifact in {run} matches the inputs it was "
                     f"built from.")
        lines += ["", "  Compared by payload content where an artifact recorded its",
                  "  inputs, by timestamp where it predates that."]
        return "\n".join(lines)

    free = [s for s in stale if not s.paid]
    paid = [s for s in stale if s.paid]
    width = max(len(s.artifact) for s in stale)
    for s in stale:
        tag = "  COSTS MONEY" if s.paid else ""
        how = "" if s.basis == "content" else "  (by timestamp only)"
        lines.append(f"  {s.artifact:<{width}}  built from an older "
                     f"{', '.join(s.newer)}{tag}{how}")
    lines.append("")
    if any(s.basis == "mtime" for s in stale):
        lines += ["  Some rows were written before stages recorded their inputs,",
                  "  so only timestamps could be compared. Those over-report: a",
                  "  stage re-run to identical output still looks like a change.",
                  "  Re-running the free stages once replaces this with content.",
                  ""]
    if free:
        lines.append("  free to rebuild, in this order:")
        for s in free:
            lines.append(f"      tracelink --run {run} {s.command}")
    if paid:
        lines += ["", "  these spend money — rebuild deliberately:"]
        for s in paid:
            lines.append(f"      tracelink --run {run} {s.command}")
    if any(s.basis == "mtime" for s in stale):
        lines += ["", "  If you have checked one of these and its inputs really are",
                  "  unchanged, record that instead of rebuilding it:",
                  f"      tracelink --run {run} stale --accept <artifact>"]
    lines += ["", "  Until then the run holds artifacts that disagree with each",
              "  other, and nothing downstream can tell which one is current."]
    return "\n".join(lines)
