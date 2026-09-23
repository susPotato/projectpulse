"""Stage 4 contracts — the reverse pass.

The thing worth pinning is the *grading*: what counts as the backlog
accounting for a file, and the refusal to collapse the ambiguous middle
tier into either answer.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.artifacts import (
    Candidate, Corpus, Evidence, SourceFile, Symbol, TicketCandidates,
    Verdict, VerdictEvidence, ROLE_CODE, ROLE_DATA, ROLE_DECLARATIVE, ROLE_TEST,
)
from tracelink.shadow import (
    COVERAGE_CITED, COVERAGE_RETRIEVED, COVERAGE_UNCLAIMED,
    classify_coverage, group_by_directory, render, significant, summarise,
)


def _corpus() -> Corpus:
    files = [
        SourceFile("ui/tracked.py", ROLE_CODE, 900, "python"),
        SourceFile("ui/proposed.py", ROLE_CODE, 900, "python"),
        SourceFile("core/hidden.py", ROLE_CODE, 900, "python"),
        SourceFile("core/tiny.py", ROLE_CODE, 40, "python"),
        SourceFile("tests/test_x.py", ROLE_TEST, 900, "python"),
        SourceFile("i18n.py", ROLE_DATA, 400_000, "python"),
        SourceFile("config/rules.yaml", ROLE_DECLARATIVE, 300, ""),
    ]
    symbols = []
    for path, names in {
        "ui/tracked.py": ["A", "B", "C"],
        "ui/proposed.py": ["D", "E"],
        "core/hidden.py": ["F", "G", "H", "I"],
        "core/tiny.py": ["J"],
        "tests/test_x.py": ["K", "L"],
    }.items():
        symbols += [Symbol(f"{path}::{n}", path, n, "function") for n in names]
    return Corpus(files=files, symbols=symbols, root=".", analyzer="test")


def _cands(*paths: str) -> list[TicketCandidates]:
    ev = [Evidence("stem", "x", 1, 2, True)]
    return [TicketCandidates("t1", [Candidate(p, list(ev)) for p in paths])]


def _verdict(verdict: str, *files: str) -> Verdict:
    return Verdict(uid="t1", verdict=verdict, confidence="high", reasoning="r",
                   evidence=[VerdictEvidence(f, "why") for f in files])


def test_three_coverage_tiers():
    files = classify_coverage(
        _corpus(),
        _cands("ui/tracked.py", "ui/proposed.py"),
        [_verdict("corroborated", "ui/tracked.py")],
    )
    cov = {f.path: f.coverage for f in files}
    assert cov["ui/tracked.py"] == COVERAGE_CITED
    assert cov["ui/proposed.py"] == COVERAGE_RETRIEVED
    assert cov["core/hidden.py"] == COVERAGE_UNCLAIMED


def test_unverified_verdict_does_not_count_as_tracked():
    """A verdict that settled nothing is not evidence the file is tracked."""
    files = classify_coverage(
        _corpus(), _cands("ui/tracked.py"),
        [_verdict("unverified", "ui/tracked.py")],
    )
    cov = {f.path: f.coverage for f in files}
    assert cov["ui/tracked.py"] == COVERAGE_RETRIEVED


def test_contradicted_verdict_still_counts_as_tracked():
    files = classify_coverage(
        _corpus(), _cands("ui/tracked.py"),
        [_verdict("contradicted", "ui/tracked.py")],
    )
    assert {f.path: f.coverage for f in files}["ui/tracked.py"] == COVERAGE_CITED


def test_windows_paths_in_evidence_are_normalised():
    files = classify_coverage(
        _corpus(), _cands("ui/tracked.py"),
        [_verdict("corroborated", "ui\\tracked.py")],
    )
    assert {f.path: f.coverage for f in files}["ui/tracked.py"] == COVERAGE_CITED


def test_tests_and_data_are_not_shadow_scope():
    paths = {f.path for f in classify_coverage(_corpus(), [], None)}
    assert "tests/test_x.py" not in paths
    assert "i18n.py" not in paths
    assert "config/rules.yaml" in paths, "config can be untracked scope"


def test_significant_drops_trivial_files():
    files = classify_coverage(_corpus(), [], None)
    keep = {f.path for f in significant(files, min_symbols=2)}
    assert "core/hidden.py" in keep
    assert "core/tiny.py" not in keep, "one-symbol shims are noise, not a finding"


def test_significant_ranks_by_symbol_count():
    files = classify_coverage(_corpus(), [], None)
    ranked = significant(files, min_symbols=1)
    assert ranked[0].path == "core/hidden.py"


def test_grouping_is_by_directory():
    files = classify_coverage(_corpus(), _cands("ui/tracked.py"), None)
    groups = {g.directory: g for g in group_by_directory(significant(files, 2))}
    assert "core" in groups and "ui" in groups
    assert groups["core"].n_symbols == 4


def test_summary_counts_add_up():
    files = classify_coverage(
        _corpus(), _cands("ui/proposed.py"), [_verdict("corroborated", "ui/tracked.py")])
    c = summarise(files)
    assert c["cited"] + c["retrieved_only"] + c["unclaimed"] == c["eligible_files"]


def test_render_without_verdicts_explains_the_middle_tier():
    files = classify_coverage(_corpus(), _cands("ui/proposed.py"), None)
    out = render(summarise(files), group_by_directory(significant(files, 2)))
    assert "retrieved but never cited" in out
    assert "--describe" in out, "must say how to get capability names"


def test_render_with_descriptions_separates_real_scope():
    files = classify_coverage(_corpus(), [], None)
    groups = group_by_directory(significant(files, 2))
    described = [
        {"directory": "core", "files": ["core/hidden.py"], "n_symbols": 4,
         "capability": "Exports audit logs to CSV", "user_facing": True,
         "suggested_ticket": "Audit log export", "confidence": "high"},
        {"directory": "ui", "files": ["ui/proposed.py"], "n_symbols": 2,
         "capability": "Base class for dialogs", "user_facing": False,
         "suggested_ticket": "", "confidence": "high"},
    ]
    out = render(summarise(files), groups, described)
    assert "1 of 2 untracked areas look like real scope" in out
    assert "Audit log export" in out
    assert out.index("Exports audit logs") < out.index("Base class for dialogs")
