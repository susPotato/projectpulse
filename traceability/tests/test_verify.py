"""Citation checking.

A validator that never fires is worse than none: it converts "unchecked"
into "checked and clean". So the tests that matter most here are the ones
that plant a fabricated citation and require it to be caught — and, equally,
the ones that plant a *real* one in an awkward shape and require it to be
left alone.

Every false-positive class below is one this checker actually produced
against the demo run before it was fixed.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.artifacts import Verdict, VerdictEvidence
from tracelink.verify import find_definition, render, verify

PY_SOURCE = '''\
LANGUAGES = {"en": "English"}
ACCENT = "#123456"


class MainWindow:
    """Shell."""

    def _open_settings(self):
        return 1

    def _build_topbar(self):
        return 2


def make_judge(spec):
    return spec
'''


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(PY_SOURCE, encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "rules.yaml").write_text(
        "sandbox:\n  enabled: true\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text(
        "intro\n\n# 22. Development Activity Blocking\n\nbody\n", encoding="utf-8")
    return tmp_path


def _v(uid, *pairs, verdict="corroborated"):
    return Verdict(uid=uid, verdict=verdict, confidence="high", reasoning="r",
                   evidence=[VerdictEvidence(f, "why", s) for f, s in pairs])


# --------------------------------------------------------------------------
# It must catch a fabrication
# --------------------------------------------------------------------------

def test_an_invented_symbol_is_caught(tmp_path):
    root = _repo(tmp_path)
    got, stats = verify([_v("t1", ("app.py", "TotallyMadeUpClass"))], root)
    assert stats["absent"] == 1
    assert got[0].status == "ungrounded"


def test_an_invented_file_is_caught(tmp_path):
    root = _repo(tmp_path)
    got, stats = verify([_v("t1", ("nowhere/ghost.py", "Thing"))], root)
    assert stats["no_file"] == 1
    assert got[0].status == "ungrounded"


def test_one_bad_citation_among_good_ones_is_partly_grounded(tmp_path):
    root = _repo(tmp_path)
    got, _ = verify([_v("t1", ("app.py", "MainWindow"),
                        ("app.py", "NotHere"))], root)
    assert got[0].status == "partly-grounded"


def test_a_fabrication_makes_the_command_fail(tmp_path):
    """`verify` exits non-zero on ungrounded verdicts, so CI can gate on it."""
    root = _repo(tmp_path)
    _got, stats = verify([_v("t1", ("app.py", "Invented"))], root)
    assert stats["ungrounded"] == 1


# --------------------------------------------------------------------------
# It must not cry wolf — each of these was a real false positive
# --------------------------------------------------------------------------

def test_a_module_level_constant_counts_as_defined(tmp_path):
    """45 of the first run's citations were constants the symbol index never
    held: LANGUAGES, ACCENT, _REGISTRY. All real."""
    root = _repo(tmp_path)
    _got, stats = verify([_v("t1", ("app.py", "LANGUAGES"),
                             ("app.py", "ACCENT"))], root)
    assert stats["defined"] == 2 and stats["absent"] == 0


def test_a_qualified_method_counts_as_defined(tmp_path):
    """`MainWindow._open_settings` is defined as `def _open_settings`.
    Searching the qualified string reported 134 real methods as invented."""
    root = _repo(tmp_path)
    _got, stats = verify([_v("t1", ("app.py", "MainWindow._open_settings"),
                             ("app.py", "MainWindow._build_topbar"))], root)
    assert stats["defined"] == 2 and stats["absent"] == 0


def test_an_artifact_node_id_is_a_whole_file_citation(tmp_path):
    """CodeWiki names artifact nodes `path/f.yaml::f.yaml`. A citation
    echoing that points at the file, not at a symbol in it."""
    root = _repo(tmp_path)
    _got, stats = verify([_v("t1", ("config/rules.yaml", "rules.yaml"))], root)
    assert stats["absent"] == 0
    assert stats["defined"] + stats["present"] == 0  # counted as no-symbol


def test_a_prose_heading_is_found_despite_the_citers_prefix(tmp_path):
    root = _repo(tmp_path)
    _got, stats = verify(
        [_v("t1", ("notes.md", "Section 22. Development Activity Blocking"))],
        root)
    assert stats["absent"] == 0


def test_two_names_in_one_citation_are_both_checked(tmp_path):
    root = _repo(tmp_path)
    _got, stats = verify([_v("t1", ("app.py", "make_judge / MainWindow"))], root)
    assert stats["defined"] == 2


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def test_a_verdict_citing_nothing_is_uncited_not_ungrounded(tmp_path):
    """`unverified` is supposed to cite nothing. Calling that a fault would
    punish the one honest answer the model can give."""
    root = _repo(tmp_path)
    got, stats = verify([_v("t1", verdict="unverified")], root)
    assert got[0].status == "uncited"
    assert stats["ungrounded"] == 0


def test_definition_beats_mere_presence():
    status, line = find_definition(PY_SOURCE, "MainWindow")
    assert status == "defined" and line == 5


def test_a_name_that_only_appears_in_a_string_is_present_not_defined():
    status, _ = find_definition('x = "referenced_only"\n', "referenced_only")
    assert status == "present"


def test_render_says_so_when_everything_checks_out(tmp_path):
    root = _repo(tmp_path)
    got, stats = verify([_v("t1", ("app.py", "MainWindow"))], root)
    assert "No verdict rests on an anchor that does not exist" in render(got, stats)


def test_a_document_cited_by_its_path_inside_the_docs_tree_is_found(tmp_path):
    """`adjudicate --docs` shows documents by their path inside the docs tree.
    Checked only against the repository root, `refactor/Checklist.md` read as
    "file does not exist" and failed the run over a file that was there."""
    root = _repo(tmp_path)
    docs = root / "docs"
    (docs / "refactor").mkdir(parents=True)
    (docs / "refactor" / "Checklist.md").write_text("# Checklist\n", encoding="utf-8")
    cited = [_v("t1", ("refactor/Checklist.md", ""))]

    _, before = verify(cited, root)
    assert before["no_file"] == 1

    got, stats = verify(cited, root, [docs])
    assert stats["no_file"] == 0
    assert got[0].status != "ungrounded"


def test_the_docs_tree_does_not_excuse_an_invented_file(tmp_path):
    root = _repo(tmp_path)
    (root / "docs").mkdir(exist_ok=True)
    got, stats = verify([_v("t1", ("nowhere/ghost.md", ""))], root, [root / "docs"])
    assert stats["no_file"] == 1
    assert got[0].status == "ungrounded"
