"""Stage 3b — check every citation against the source. Free, no model.

A verdict is an opinion. The citations under it are checkable facts, and
checking them is the cheapest grounding available: if a verdict points at
`core/chat_agent.py::SAVE_FILE_SPEC`, either that name is in that file or
the verdict is built on something that does not exist.

**Check the file, not the index.** The first version of this compared
citations against the symbol index and reported 45 of 483 as unresolved —
`i18n.py::LANGUAGES`, `theme.py::ACCENT`, `providers/factory.py::_REGISTRY`.
Every one of them was real. The index carries classes, functions and
methods; those are module-level constants, which it never held. A validator
that calls real code a hallucination is worse than no validator, so this
reads the file.

Three outcomes per citation, because they mean different things:

``defined``   the name appears as a definition — the strong case.
``present``   the name is in the file but not as a definition. It may be a
              call site or an attribute; real, but weaker than claimed.
``absent``    the name is not in the file. This is the one that matters.

A verdict is `grounded` only when every citation is `defined` or `present`,
and `ungrounded` the moment one is `absent`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tracelink.artifacts import Verdict

#: Definition syntax across the languages the corpus stage can read. Matched
#: against the literal name, so a false "defined" needs the name to appear
#: after one of these keywords — which is what being defined means.
# NOTE: indentation is matched with an explicit space/tab class. The
# generic whitespace metacharacter also spans newlines, so a pattern
# using it anchors at a preceding blank line and the line number
# reported as evidence points a reader above the actual definition.
DEFINITION = (
    r"^[ \t]*(?:async\s+)?def\s+{n}\b",                      # python
    r"^[ \t]*class\s+{n}\b",                                 # python, many others
    r"^[ \t]*{n}\s*[:=][^=]",                                # module constant / field
    r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+{n}\b",   # js / ts
    r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+{n}\b",       # js / ts
    r"^[ \t]*(?:public|private|protected|internal|static|final|[ \t])*"
    r"(?:class|interface|record|enum|struct)\s+{n}\b",    # java / c# / kotlin
    r"^[ \t]*(?:func|fn|sub|procedure)\s+{n}\b",             # go / rust / vb
    r"^[ \t]*{n}\s*\(",                                      # yaml-ish / call-like decl
)

SPLIT_CITED = re.compile(r"\s*(?:/|,|\band\b)\s*")


@dataclass
class CitationCheck:
    file: str
    symbol: str
    status: str            # defined | present | absent | no-symbol | no-file
    line: int | None = None


@dataclass
class Grounding:
    uid: str
    verdict: str
    status: str            # grounded | partly-grounded | ungrounded | uncited
    checks: list[CitationCheck] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"uid": self.uid, "verdict": self.verdict, "status": self.status,
                "checks": [c.__dict__ for c in self.checks]}


def find_definition(text: str, name: str) -> tuple[str, int | None]:
    """Locate `name` in `text`. Returns (status, 1-based line or None).

    A citation is usually qualified — `MainWindow._open_settings`,
    `OpenAICompatProvider.chat` — and the definition in the file is the bare
    member: `def _open_settings(self)`. Searching for the qualified string
    finds nothing, which reported 134 real methods as hallucinated on the
    first run. So each dotted part is tried, longest first.
    """
    if not name:
        return "no-symbol", None

    # `A.b.c` -> try "A.b.c", then "c", then "b". Longest first so an exact
    # match on the written form still wins.
    candidates = [name]
    if "." in name:
        parts = [p for p in name.split(".") if p]
        candidates += [parts[-1]] + parts[:-1][::-1]

    # A prose document is cited by heading: "Section 22. Development Activity
    # Blocking" against a file whose line reads "# 22. Development Activity
    # Blocking". The leading word is the citer's, not the document's.
    stripped = re.sub(r"^(?:section|chapter|part|§)\s+", "", name,
                      flags=re.I).strip()
    if stripped and stripped != name:
        candidates.insert(1, stripped)
    for c in list(candidates):
        m = re.match(r"^\d+(?:\.\d+)*\.?\s+(.*)$", c)
        if m and m.group(1):
            candidates.append(m.group(1))

    for candidate in candidates:
        esc = re.escape(candidate)
        for pattern in DEFINITION:
            m = re.search(pattern.format(n=esc), text, re.M)
            if m:
                return "defined", text[:m.start()].count("\n") + 1

    for candidate in candidates:
        m = re.search(rf"\b{re.escape(candidate)}\b", text)
        if m:
            return "present", text[:m.start()].count("\n") + 1
    return "absent", None


def _read_cited(rel: str, roots: list[Path]) -> str | None:
    """The cited file's text from the first root that has it, or None."""
    for base in roots:
        try:
            return (base / rel).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
    return None


def check_verdict(v: Verdict, root: Path, cache: dict[str, str | None],
                  docs_roots: tuple[Path, ...] = ()) -> Grounding:
    if not v.evidence:
        # Not a failure. `unverified` is supposed to cite nothing, and saying
        # "ungrounded" about it would turn an honest refusal into a fault.
        return Grounding(uid=v.uid, verdict=v.verdict, status="uncited")

    checks: list[CitationCheck] = []
    for e in v.evidence:
        rel = e.file.replace("\\", "/")
        if rel not in cache:
            # The repository first, then the documentation tree: `adjudicate
            # --docs` shows the model documents by their path *inside* that
            # tree, so `refactor/Checklist.md` is `docs/refactor/...` here.
            # Checking only the repository root reported every such citation
            # as "file does not exist" and failed the run over files that
            # were there.
            cache[rel] = _read_cited(rel, [root, *docs_roots])
        text = cache[rel]
        if text is None:
            checks.append(CitationCheck(rel, e.symbol, "no-file"))
            continue
        if not e.symbol:
            checks.append(CitationCheck(rel, "", "no-symbol"))
            continue
        # CodeWiki names an artifact node `path/file.yaml::file.yaml`, so a
        # citation echoing that id is pointing at the whole file, not at a
        # symbol inside it. 29 of the first run's 30 "absent" citations were
        # this — our own id format, read back as a hallucination.
        if e.symbol.strip() == Path(rel).name:
            checks.append(CitationCheck(rel, e.symbol, "no-symbol"))
            continue
        # A citation sometimes names two things: "make_judge / parse_score".
        for name in SPLIT_CITED.split(e.symbol):
            name = name.split("::")[-1].split("(")[0].strip()
            if not name:
                continue
            status, line = find_definition(text, name)
            checks.append(CitationCheck(rel, name, status, line))

    bad = [c for c in checks if c.status in ("absent", "no-file")]
    good = [c for c in checks if c.status in ("defined", "present")]
    if bad and good:
        status = "partly-grounded"
    elif bad:
        status = "ungrounded"
    elif good:
        status = "grounded"
    else:
        status = "uncited"
    return Grounding(uid=v.uid, verdict=v.verdict, status=status, checks=checks)


def verify(verdicts: list[Verdict], root: str | Path,
           docs_roots: tuple[str | Path, ...] = ()) -> tuple[list[Grounding], dict]:
    root = Path(root)
    extra = tuple(Path(d) for d in docs_roots if d)
    cache: dict[str, str | None] = {}
    out = [check_verdict(v, root, cache, extra) for v in verdicts]

    citations = [c for g in out for c in g.checks]
    stats = {
        "verdicts": len(out),
        "citations": len(citations),
        "defined": sum(1 for c in citations if c.status == "defined"),
        "present": sum(1 for c in citations if c.status == "present"),
        "absent": sum(1 for c in citations if c.status == "absent"),
        "no_file": sum(1 for c in citations if c.status == "no-file"),
        "grounded": sum(1 for g in out if g.status == "grounded"),
        "partly_grounded": sum(1 for g in out if g.status == "partly-grounded"),
        "ungrounded": sum(1 for g in out if g.status == "ungrounded"),
        "uncited": sum(1 for g in out if g.status == "uncited"),
    }
    return out, stats


def render(groundings: list[Grounding], stats: dict) -> str:
    n = stats["citations"] or 1
    lines = ["=" * 72, "CITATION CHECK — every anchor, against the source",
             "=" * 72, ""]
    lines.append(f"  {stats['citations']} citations across "
                 f"{stats['verdicts']} verdicts")
    for key, label in (("defined", "defined in the file"),
                       ("present", "present, but not as a definition"),
                       ("absent", "NOT IN THE FILE"),
                       ("no_file", "file does not exist")):
        v = stats[key]
        lines.append(f"    {v:>5}  ({100 * v / n:4.1f}%)  {label}")
    lines += ["", "  verdicts:"]
    for key in ("grounded", "partly_grounded", "ungrounded", "uncited"):
        lines.append(f"    {stats[key]:>5}  {key.replace('_', ' ')}")

    bad = [g for g in groundings if g.status in ("ungrounded", "partly-grounded")]
    if bad:
        lines += ["", "-" * 72,
                  "VERDICTS RESTING ON SOMETHING THAT IS NOT THERE", ""]
        for g in bad[:20]:
            for c in g.checks:
                if c.status in ("absent", "no-file"):
                    lines.append(f"  {g.uid}  [{g.verdict}]  "
                                 f"{c.file}::{c.symbol}  — {c.status}")
    else:
        lines += ["", "  Every cited name was found. No verdict rests on an "
                  "anchor that does not exist."]
    lines += ["", "  'present' is not a fault: a citation can name a call site "
              "rather than", "  a definition. 'absent' is the one that "
              "invalidates a verdict."]
    return "\n".join(lines)
