"""Feature rows from the *structured* documents in a docs tree.

Teams generate as much documentation as they write. A UI audit emits a
control inventory; a screenshot run emits a manifest; a refactor plan emits
a symbol table. Those files are already the thing the markdown adapter is
trying to reconstruct from prose — records with a name, a path and a line —
and reading only `*.md` leaves them on the floor. Here that was 300 KB of
pre-anchored claims sitting unread beside the documents that were parsed.

Nothing here knows any of those formats. Three structural rules:

**J1 — find the records.** A document is either a list of objects, or an
object with one list of objects inside it; that list is the records. A
document shaped like neither yields nothing, which is correct.

**J2 — an object's values are its claims.** Every string value is offered
to the same `normalise_claim` the markdown adapter uses, so a path is a
path claim and an identifier is a symbol claim by exactly the same test.
Values that are neither become the label and the fields.

**J3 — a nested list of objects inherits its parent's claims.** A control
at line 108 belongs to the file named once on the record above it. Without
inheritance every control is an orphan; with it, 343 of them are anchored.

Line numbers in the record are deliberately *not* read. The corpus already
knows where a resolved symbol is, and trusting the document's own line
would mean trusting a number nobody re-checked — `docmap` would then pin a
section to a line that moved three commits ago. A document is believed
about *what* exists, never about where.
"""

from __future__ import annotations

import json
from pathlib import Path

from tracelink.adapters._markdown import clean_label
from tracelink.adapters.featuremap_markdown import normalise_claim
from tracelink.artifacts import Claim, Feature

# A value long enough to be prose rather than a code name.
MAX_LABEL = 200
# Objects smaller than this are usually wrappers, not records.
MIN_RECORD_KEYS = 2


def _records(data) -> tuple[list[dict], str]:
    """The list of objects this document is really about, and its key."""
    if isinstance(data, list):
        rows = [r for r in data if isinstance(r, dict)]
        return (rows, "") if rows else ([], "")
    if isinstance(data, dict):
        best: tuple[list[dict], str] = ([], "")
        for key, value in data.items():
            if not isinstance(value, list):
                continue
            rows = [r for r in value if isinstance(r, dict)]
            if len(rows) > len(best[0]):
                best = (rows, key)
        return best
    return [], ""


def _claims_and_text(record: dict) -> tuple[list[Claim], str, dict[str, str]]:
    claims: list[Claim] = []
    seen: set[str] = set()
    label = ""
    fields: dict[str, str] = {}

    for key, value in record.items():
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        got = normalise_claim(text)
        if got and got[0] not in seen:
            seen.add(got[0])
            claims.append(Claim(text=text, name=got[0], kind=got[1]))
            continue
        cleaned = clean_label(text)[:MAX_LABEL]
        if cleaned:
            fields[str(key)] = cleaned
            if len(cleaned) > len(label):
                label = cleaned
    return claims, label, fields


def parse_document(data, doc: str, start_index: int = 0) -> list[Feature]:
    rows, array_key = _records(data)
    if not rows:
        return []

    heading = [doc] + ([array_key] if array_key else [])
    features: list[Feature] = []

    for n, record in enumerate(rows, start=1):
        if len(record) < MIN_RECORD_KEYS:
            continue
        claims, label, fields = _claims_and_text(record)

        nested: list[tuple[str, list[dict]]] = [
            (str(k), [r for r in v if isinstance(r, dict)])
            for k, v in record.items()
            if isinstance(v, list) and any(isinstance(r, dict) for r in v)
        ]

        if not nested:
            if claims or label:
                features.append(Feature(
                    fid="", doc=doc, heading=list(heading),
                    label=label or f"record {n}",
                    description=" — ".join(f"{k}: {v}" for k, v in fields.items())[:600],
                    line=n, claims=claims,
                ))
            continue

        # J3: the parent names the file; the children name what is in it.
        for key, children in nested:
            for child in children:
                cclaims, clabel, cfields = _claims_and_text(child)
                merged = list(cclaims)
                have = {c.name for c in merged}
                for c in claims:
                    if c.name not in have:
                        merged.append(Claim(text=c.text, name=c.name, kind=c.kind))
                if not merged and not clabel:
                    continue
                features.append(Feature(
                    fid="", doc=doc, heading=heading + [key],
                    label=clabel or f"record {n}",
                    description=" — ".join(f"{k}: {v}"
                                           for k, v in cfields.items())[:600],
                    line=n, claims=merged,
                ))

    for i, f in enumerate(features):
        f.fid = f"J{start_index + i:04d}"
    return features


def read_json_docs(docs_dir: str | Path, start_index: int = 0) -> list[Feature]:
    root = Path(docs_dir)
    if not root.is_dir():
        return []
    out: list[Feature] = []
    for p in sorted(root.rglob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
        except (OSError, ValueError):
            # A malformed or huge JSON file is skipped, not fatal. The docs
            # tree is somebody else's output directory.
            continue
        out.extend(parse_document(data, p.relative_to(root).as_posix(),
                                  start_index=start_index + len(out)))
    return out
