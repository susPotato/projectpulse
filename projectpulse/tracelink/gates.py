"""Stage 4d — check the repository against the gates its own documents set.

Every other stage in this pipeline *reads*. This one evaluates: a quality
gate in a progress document carries a criterion and, usually, the command
that proves it, and those criteria are checkable arithmetic over a corpus
we have already built.

Why not just run the command. Because the command is somebody else's:
`python scripts/audit_security.py` assumes a script that, on this
repository, does not exist — and even where it does, running a shell
command out of a document is executing untrusted input from a file nobody
in this process wrote. So the *criteria* are reimplemented here as checks
over `corpus.json`, and the document's command is quoted beside the result
so a person can run the real thing if they want to.

There are three checks and they are deliberately generic. Each is a
property of any codebase, each maps onto a threshold the document states
in its own words, and none of them names this project:

* `file_size`    — no file over N lines.
* `layer_purity` — no import of X inside directory Y.
* `secrets`      — no literal credential in a configuration file.

A gate whose criterion matches none of them comes back `unchecked`, which
is honest and common. The point is not to automate every gate; it is to
stop an unsigned checkbox being the only thing anybody knows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tracelink.artifacts import Corpus, Gate, ROLE_CODE, ROLE_DECLARATIVE, ROLE_TEST

PASS, FAIL, UNCHECKED = "pass", "fail", "unchecked"

# A criterion states a bound in the document's own prose. The number and the
# unit are language-independent; the words around them are not, so only the
# number is read.
LOC_LIMIT = re.compile(r"(\d{2,5})\s*(?:lines?|dòng|行|líneas|Zeilen|LOC)", re.I)
# `--max-lines 400`, `--max 400`: the same bound, stated to a tool.
FLAG_LIMIT = re.compile(r"--[\w-]*(?:max|limit|lines)[\w-]*[= ](\d{2,5})", re.I)

# "0 import PySide6 trong domain/" — a module that must not appear inside a
# directory. Both operands are quoted or path-shaped, so both are findable
# without knowing which language the sentence is in.
IMPORTABLE = re.compile(r"\b([A-Z][A-Za-z0-9_]{2,})\b")
DIRISH = re.compile(r"\b([a-z][a-z0-9_]{2,})/")

# `pass` on its own is not a credential word. Matching it caught "test
# pass" in a criterion and answered two unrelated gates with a secrets
# audit; it also caught the word in every comment it appeared in.
SECRET_KEYS = re.compile(
    r"(pass(?:word|wd)|pwd|secret|token|api[_-]?key|credential|unlock)", re.I)

# The key and the literal have to be one assignment, not two things that
# happen to share a line. Scanning the line loosely and then grabbing any
# quoted run from it reported 73 credentials, most of them words inside
# comments — `# master switch — ON by default ("chọn…")`.
SECRET_PAIR = re.compile(
    r"""["']?(\w*(?:pass(?:word|wd)|pwd|_pw|secret|token|api[_-]?key"""
    r"""|credential|unlock)\w*)["']?\s*[:=]\s*["']([^"'\n]{3,})["']""", re.I)
# A credential literal has no whitespace in it. Without this, an icon named
# `unlock` whose value is an SVG path — `unlock = '<rect x='` — is reported
# as a leaked secret. Rules out a passphrase with spaces, which is both
# rarer and the cheaper thing to miss.
HAS_SPACE = re.compile(r"\s")
PLACEHOLDER = re.compile(
    r"^(|none|null|changeme|xxx+|todo|example|dummy|test|<[^>]*>|\$\{[^}]*\}|\*+)$",
    re.I)
# Everything after an unquoted `#` is a comment; a word in prose is not a key.
COMMENT = re.compile(r"""(?<!["'])#.*$""")


@dataclass
class Result:
    """What one gate's criterion turned out to be, when measured."""

    name: str
    criterion: str
    command: str = ""
    check: str = ""                   # which implemented check ran
    status: str = UNCHECKED
    detail: str = ""
    offenders: list[str] = field(default_factory=list)
    signed_off: bool = False
    doc: str = ""
    line: int = 0

    @property
    def contradicts_signoff(self) -> bool:
        """Signed off in the document, failing when measured."""
        return self.signed_off and self.status == FAIL


# --------------------------------------------------------------------------
# The checks
# --------------------------------------------------------------------------

def check_file_size(corpus: Corpus, limit: int) -> tuple[str, str, list[str]]:
    """Files over a line bound, production and test counted separately.

    Every phrasing of this rule seen so far says *production*, and a long
    test file is conventionally fine — but "production" is a word in
    somebody's sentence, and reading it would mean reading the language.
    So both numbers are reported and the reader applies whichever the
    criterion meant. The verdict follows the production count, because
    that is what the rule is for.
    """
    root = Path(corpus.root)
    over: list[tuple[int, str]] = []
    over_test: list[tuple[int, str]] = []
    counted = tests = 0
    for f in corpus.files:
        if f.role not in (ROLE_CODE, ROLE_TEST):
            continue
        try:
            n = len((root / f.path).read_text(encoding="utf-8",
                                              errors="ignore").splitlines())
        except OSError:
            continue
        if f.role == ROLE_TEST:
            tests += 1
            if n > limit:
                over_test.append((n, f.path))
            continue
        counted += 1
        if n > limit:
            over.append((n, f.path))
    over.sort(reverse=True)
    status = FAIL if over else PASS
    detail = (f"{len(over)} of {counted} production files exceed {limit} lines"
              f"; {len(over_test)} of {tests} test files also do")
    return status, detail, [f"{n} lines  {p}" for n, p in over[:20]]


def check_layer_purity(corpus: Corpus, module: str,
                       directories: list[str]) -> tuple[str, str, list[str]]:
    """No import of `module` inside any of `directories`."""
    present = [d for d in directories
               if any(f.path.startswith(f"{d}/") for f in corpus.files)]
    if not present:
        # Passing because the directory does not exist is not passing.
        return (UNCHECKED,
                f"no such directory in this repository: "
                f"{', '.join(d + '/' for d in directories)}", [])

    root = Path(corpus.root)
    hits: list[str] = []
    for f in corpus.files:
        if not any(f.path.startswith(f"{d}/") for d in present):
            continue
        try:
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if re.match(rf"\s*(?:from|import)\s+{re.escape(module)}\b", line):
                hits.append(f"{f.path}:{n}  {line.strip()[:60]}")
    status = FAIL if hits else PASS
    return status, (f"{len(hits)} import(s) of {module} under "
                    f"{', '.join(d + '/' for d in present)}"), hits[:20]


def check_secrets(corpus: Corpus) -> tuple[str, str, list[str]]:
    """A literal credential in a file the application reads as configuration.

    Both assignment forms, because the first pass only checked `x = "..."`
    and this repository writes its credential as a dict entry:
    `"sandbox_pw": "quandh14"` — the exact string its own documents name as
    the reason two tests fail.
    """
    root = Path(corpus.root)
    hits: list[str] = []
    for f in corpus.files:
        if f.role not in (ROLE_CODE, ROLE_DECLARATIVE):
            continue
        try:
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            code = COMMENT.sub("", line)
            for key, value in SECRET_PAIR.findall(code):
                if PLACEHOLDER.match(value.strip()) or HAS_SPACE.search(value):
                    continue
                hits.append(f"{f.path}:{n}  {key} = {value[:32]!r}")
    status = FAIL if hits else PASS
    return status, f"{len(hits)} literal credential(s) in tracked files", hits[:20]


# --------------------------------------------------------------------------
# Matching a written criterion to a check
# --------------------------------------------------------------------------

def imported_modules(corpus: Corpus) -> set[str]:
    """Every top-level module name this corpus imports anywhere.

    The discriminator that stops the matcher inventing rules. A criterion
    is scanned for capitalised words as candidate module names, and a gate
    called "Checkpoint 1: Contracts & Fakes" offers up `Checkpoint` — which
    produced a confident PASS for "0 imports of Checkpoint under tests/".
    Requiring the name to be something the repository actually imports
    costs one pass and removes the whole class of nonsense.
    """
    root = Path(corpus.root)
    found: set[str] = set()
    pattern = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", re.M)
    for f in corpus.files:
        if f.role not in (ROLE_CODE, ROLE_TEST):
            continue
        try:
            text = (root / f.path).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in pattern.finditer(text):
            found.add(m.group(1).split(".")[0])
    return found


def evaluate(gate: Gate, corpus: Corpus,
             importable: set[str] | None = None) -> Result:
    """Pick the check a written criterion is asking for, or none.

    The criterion is what states the rule; the gate's title and command are
    not. Reading all three together let `tests/` from `pytest tests/` and
    `scripts/` from `python scripts/audit_security.py` stand in as the
    directory a rule was about, and three gates were answered with a check
    they had never asked for. Only the flag form of a size limit is read
    from the command, because that is the one place a command states a
    threshold the prose leaves out.
    """
    criterion = " / ".join(v for v in gate.fields.values() if v)
    r = Result(name=gate.name or gate.command, criterion=criterion[:160],
               command=gate.command, signed_off=gate.signed_off,
               doc=gate.doc, line=gate.line)
    importable = imported_modules(corpus) if importable is None else importable

    limit = LOC_LIMIT.search(criterion) or FLAG_LIMIT.search(gate.command)
    if limit:
        r.check = "file_size"
        r.status, r.detail, r.offenders = check_file_size(corpus, int(limit.group(1)))
        return r

    # Before layer purity, because "no plaintext secret in config" also
    # mentions a directory and would otherwise be answered as an import rule.
    if SECRET_KEYS.search(criterion):
        r.check = "secrets"
        r.status, r.detail, r.offenders = check_secrets(corpus)
        return r

    modules = [m for m in IMPORTABLE.findall(criterion) if m in importable]
    if modules:
        dirs = set(DIRISH.findall(criterion)) | {
            w for w in re.findall(r"\b([a-z][a-z0-9_]{3,})\b", criterion)
            if any(f.path.startswith(f"{w}/") for f in corpus.files)}
        if dirs:
            r.check = "layer_purity"
            r.status, r.detail, r.offenders = check_layer_purity(
                corpus, modules[0], sorted(dirs))
            return r
        # The rule names a real module and directories that do not exist.
        # Saying "pass" would be the worst available answer.
        r.check = "layer_purity"
        r.status = UNCHECKED
        r.detail = (f"names {modules[0]} but no directory in the criterion "
                    f"exists in this repository")
        return r

    r.detail = "no implemented check matches this criterion"
    return r


def run(gates: list[Gate], corpus: Corpus) -> list[Result]:
    # One scan of the corpus for the whole batch, not one per gate.
    importable = imported_modules(corpus)
    return [evaluate(g, corpus, importable) for g in gates]


def summary(results: list[Result]) -> dict[str, int]:
    out = {PASS: 0, FAIL: 0, UNCHECKED: 0, "contradicts_signoff": 0}
    for r in results:
        out[r.status] += 1
        if r.contradicts_signoff:
            out["contradicts_signoff"] += 1
    return out
