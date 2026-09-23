"""Stage 4 — the reverse pass: code that no ticket claims.

Stages 1–3 walk from a ticket to the code. This walks the other way, and it
is the half that finds *shadow scope*: work that exists in the repository
and appears nowhere in the plan. A backlog audit that only checks tickets
can only ever find over-claiming; this finds under-recording.

**Coverage is graded, not binary.** A file being proposed by the keyword
retriever means very little — the matcher is noisy by construction. A file
being *cited as evidence* in a verdict means a model looked at it and said
it implements something. So:

    cited      a verdict pointed at this file            — genuinely tracked
    retrieved  proposed for some ticket, never cited     — weak, probably noise
    unclaimed  never proposed for any ticket             — shadow scope

Only `unclaimed` is reported as shadow scope, and `retrieved` is kept
separate rather than folded into either side, because collapsing it would
silently decide a question the evidence does not answer.
"""

from __future__ import annotations

import collections
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tracelink.adjudicate import StructuredCaller, Usage
from tracelink.artifacts import (
    Corpus, SourceFile, TicketCandidates, Verdict,
    ROLE_CODE, ROLE_DECLARATIVE,
)

logger = logging.getLogger(__name__)

# Roles that can be shadow scope. Tests and data are excluded: untracked
# test code is normal and untracked generated data is not scope.
SHADOW_ROLES = (ROLE_CODE, ROLE_DECLARATIVE)

COVERAGE_CITED = "cited"
COVERAGE_RETRIEVED = "retrieved"
COVERAGE_UNCLAIMED = "unclaimed"


@dataclass
class ShadowFile:
    path: str
    coverage: str
    n_symbols: int
    size: int
    role: str
    symbols: list[str] = field(default_factory=list)


@dataclass
class ShadowGroup:
    """Neighbouring unclaimed files, grouped by directory.

    A capability is rarely one file, and a list of 60 paths is not something
    a person reads. Grouping by directory is a weak proxy for a module, but
    it is the one grouping that needs no LLM and cannot hallucinate.
    """

    directory: str
    files: list[ShadowFile]

    @property
    def n_symbols(self) -> int:
        return sum(f.n_symbols for f in self.files)

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)


class ShadowOut(BaseModel):
    """What the model is allowed to say about unclaimed code."""

    capability: str = Field(
        description="What this code does, in one sentence, in a product person's words.")
    user_facing: bool = Field(
        description="True if a user or operator would notice this existing. "
                    "Plumbing, adapters and internal helpers are False.")
    suggested_ticket: str = Field(
        description="The ticket title that should have existed, or '' if this is "
                    "not scope anybody would track.")
    confidence: Literal["high", "medium", "low"]


SHADOW_SYSTEM = """You are auditing a codebase for work that was built but never \
recorded in the tracker.

You are shown source files that no ticket in the backlog appears to describe. Say what \
they do and whether that is something a team would normally have tracked as scope.

Rules:
- Judge only from the code shown.
- Most untracked code is not scope. Internal helpers, adapters, wiring, base classes \
and utility modules should come back user_facing=false with an empty suggested_ticket. \
Say so plainly rather than inventing a ticket to fill the field.
- user_facing=true means a user or operator would notice this capability existing — a \
screen, a command, an integration, an export, a policy that changes behaviour.
- Do not speculate about why it is untracked. You cannot see the backlog."""


def classify_coverage(
    corpus: Corpus,
    candidates: list[TicketCandidates],
    verdicts: list[Verdict] | None = None,
) -> list[ShadowFile]:
    """Grade every eligible file by how strongly the backlog accounts for it."""
    retrieved = {c.path for tc in candidates for c in tc.candidates if c.strong}
    cited: set[str] = set()
    for v in verdicts or []:
        # A verdict that settled nothing is not evidence the file is tracked.
        if v.verdict == "unverified":
            continue
        for e in v.evidence:
            cited.add(e.file.replace("\\", "/"))

    symbols_by_path: dict[str, list[str]] = collections.defaultdict(list)
    for s in corpus.symbols:
        symbols_by_path[s.path].append(s.name)

    out: list[ShadowFile] = []
    for f in corpus.files:
        if f.role not in SHADOW_ROLES:
            continue
        names = symbols_by_path.get(f.path, [])
        if f.path in cited:
            cov = COVERAGE_CITED
        elif f.path in retrieved:
            cov = COVERAGE_RETRIEVED
        else:
            cov = COVERAGE_UNCLAIMED
        out.append(ShadowFile(path=f.path, coverage=cov, n_symbols=len(names),
                              size=f.size, role=f.role, symbols=sorted(names)[:20]))
    return out


def significant(files: list[ShadowFile], min_symbols: int = 2) -> list[ShadowFile]:
    """Unclaimed files substantial enough to be worth a person's attention.

    `__init__.py` and one-line shims are untracked in every repository ever
    written and reporting them is noise, not a finding.
    """
    return sorted(
        (f for f in files
         if f.coverage == COVERAGE_UNCLAIMED and f.n_symbols >= min_symbols),
        key=lambda f: (-f.n_symbols, -f.size, f.path),
    )


def group_by_directory(files: list[ShadowFile]) -> list[ShadowGroup]:
    buckets: dict[str, list[ShadowFile]] = collections.defaultdict(list)
    for f in files:
        parent = str(Path(f.path).parent).replace("\\", "/")
        buckets["." if parent == "." else parent].append(f)
    groups = [ShadowGroup(directory=d, files=sorted(fs, key=lambda f: -f.n_symbols))
              for d, fs in buckets.items()]
    return sorted(groups, key=lambda g: (-g.n_symbols, g.directory))


def summarise(files: list[ShadowFile]) -> dict[str, int]:
    c = collections.Counter(f.coverage for f in files)
    return {
        "eligible_files": len(files),
        "cited": c[COVERAGE_CITED],
        "retrieved_only": c[COVERAGE_RETRIEVED],
        "unclaimed": c[COVERAGE_UNCLAIMED],
    }


def build_prompt(group: ShadowGroup, corpus: Corpus, max_files: int, window: int) -> str:
    root = Path(corpus.root)
    parts = [f"## Untracked code in `{group.directory}/`", ""]
    for f in group.files[:max_files]:
        try:
            lines = (root / f.path).read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        body = lines[:window]
        tail = "" if len(lines) <= window else \
            f"\n# ... [{len(lines) - window} lines omitted] ..."
        parts.append(f"### {f.path} ({len(lines)} lines, {f.n_symbols} symbols: "
                     f"{', '.join(f.symbols[:8])})")
        parts.append("```\n" + "\n".join(body) + tail + "\n```")
    parts.append("")
    parts.append("What capability is this, and is it scope a team would have tracked?")
    return "\n".join(parts)


def describe_groups(
    groups: list[ShadowGroup],
    corpus: Corpus,
    caller: StructuredCaller,
    max_files: int = 3,
    window: int = 120,
    on_result=None,
) -> tuple[list[dict], Usage]:
    total = Usage()
    out: list[dict] = []
    for g in groups:
        prompt = build_prompt(g, corpus, max_files, window)
        try:
            payload, usage = caller.call(SHADOW_SYSTEM, prompt, ShadowOut,
                                         label=g.directory)
        except Exception as exc:                       # noqa: BLE001
            logger.error("%s: %s", g.directory, exc)
            continue
        total.add(usage)
        row = {
            "directory": g.directory,
            "files": [f.path for f in g.files],
            "n_symbols": g.n_symbols,
            "cost_usd": round(usage.cost(caller.model), 6),
            **payload,
        }
        out.append(row)
        if on_result:
            on_result(g, row, usage)
    return out, total


def render(counts: dict[str, int], groups: list[ShadowGroup],
           described: list[dict] | None = None, top: int = 15) -> str:
    lines = ["=" * 72, "SHADOW SCOPE — code the backlog does not account for",
             "=" * 72, ""]
    n = counts["eligible_files"] or 1
    for k, label in (("cited", "cited by a verdict      "),
                     ("retrieved_only", "retrieved but never cited"),
                     ("unclaimed", "never retrieved at all  ")):
        v = counts[k]
        lines.append(f"  {label} {v:>4}  ({100 * v / n:4.1f}%)")
    lines.append("")
    lines.append("  'retrieved but never cited' is deliberately neither side: the")
    lines.append("  matcher proposed the file and no verdict relied on it.")
    lines.append("")

    if described:
        scope = [d for d in described if d.get("user_facing")]
        lines += ["-" * 72,
                  f"{len(scope)} of {len(described)} untracked areas look like real scope",
                  ""]
        for d in sorted(described, key=lambda d: not d.get("user_facing")):
            mark = "!" if d.get("user_facing") else " "
            lines.append(f"{mark} {d['directory']}/  ({d['n_symbols']} symbols, "
                         f"{len(d['files'])} files)")
            lines.append(f"    {d['capability']}")
            if d.get("suggested_ticket"):
                lines.append(f"    suggested ticket: {d['suggested_ticket']}")
            lines.append(f"    confidence={d['confidence']}")
            lines.append("")
    else:
        lines += ["-" * 72, f"top {min(top, len(groups))} unclaimed areas by symbol count",
                  "  (run with --describe to have the model name the capability)", ""]
        for g in groups[:top]:
            lines.append(f"  {g.directory}/  {g.n_symbols} symbols in {len(g.files)} files")
            for f in g.files[:4]:
                lines.append(f"      {f.path}  ({f.n_symbols} symbols)")
    return "\n".join(lines)
