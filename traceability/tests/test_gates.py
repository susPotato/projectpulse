"""Contract tests for Stage 4d — the gates a document sets, measured.

Half of these exist because the first version answered gates that had not
been asked. A criterion is read; a title and a command are not, and the
difference produced three confident results about rules nobody wrote.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import gates as G
from tracelink.artifacts import (
    Corpus, Gate, SourceFile, Symbol, ROLE_CODE, ROLE_DECLARATIVE,
)

BIG = "\n".join(f"x = {i}" for i in range(500))
SMALL = "\n".join(f"y = {i}" for i in range(50))

CONFIG = '''\
DEFAULTS = {
    "sandbox_pw": "hunter2",          # the real one
    "api_key": "",                    # empty, so not a secret
    "token": "changeme",              # a placeholder
    "note": "the password is stored elsewhere",
}
# password = "not-a-real-assignment-just-a-comment"
unlock = '<rect x='
'''

LAYERED = "from PySide6.QtWidgets import QWidget\nimport os\n"


@pytest.fixture
def corpus(tmp_path: Path) -> Corpus:
    (tmp_path / "ui").mkdir()
    (tmp_path / "big.py").write_text(BIG, encoding="utf-8")
    (tmp_path / "small.py").write_text(SMALL, encoding="utf-8")
    (tmp_path / "config.py").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "ui" / "widget.py").write_text(LAYERED, encoding="utf-8")
    files = [
        SourceFile(path="big.py", role=ROLE_CODE, size=len(BIG), language="python"),
        SourceFile(path="small.py", role=ROLE_CODE, size=len(SMALL), language="python"),
        SourceFile(path="config.py", role=ROLE_CODE, size=len(CONFIG),
                   language="python"),
        SourceFile(path="ui/widget.py", role=ROLE_CODE, size=len(LAYERED),
                   language="python"),
    ]
    return Corpus(root=str(tmp_path), files=files, symbols=[
        Symbol(sid="big.py::x", path="big.py", name="x", kind="function")])


def _gate(criterion: str, command: str = "", signed: bool = False) -> Gate:
    return Gate(name="G", command=command, signed_off=signed,
                fields={"Criterion": criterion}, doc="d.md", line=1)


# --------------------------------------------------------------------------
# The checks themselves
# --------------------------------------------------------------------------

def test_file_size_counts_and_names_the_offenders(corpus):
    status, detail, offenders = G.check_file_size(corpus, 400)
    assert status == "fail"
    assert "1 of 4" in detail
    assert any("big.py" in o for o in offenders)


def test_file_size_passes_under_the_limit(corpus):
    assert G.check_file_size(corpus, 1000)[0] == "pass"


def test_a_secret_is_a_key_and_a_literal_in_one_assignment(corpus):
    status, detail, hits = G.check_secrets(corpus)
    assert status == "fail"
    assert any("hunter2" in h for h in hits)


@pytest.mark.parametrize("fragment", [
    "changeme",                      # a placeholder
    "not-a-real-assignment",         # inside a comment
    "<rect",                         # an SVG path on a variable called unlock
    "stored elsewhere",              # the word in prose, no assignment
])
def test_what_is_not_a_secret(corpus, fragment):
    """Each of these was reported as a credential by an earlier version.

    Together they took the count on the real repository from 3 to 73.
    """
    _, _, hits = G.check_secrets(corpus)
    assert not any(fragment in h for h in hits), hits


def test_layer_purity_finds_a_banned_import(corpus):
    status, detail, hits = G.check_layer_purity(corpus, "PySide6", ["ui"])
    assert status == "fail"
    assert any("ui/widget.py" in h for h in hits)


def test_a_rule_about_a_directory_that_does_not_exist_is_not_a_pass(corpus):
    """The failure mode that makes a compliance report worthless.

    `0 PySide6 imports under domain/` is satisfied trivially when there is
    no `domain/`. Reporting that as a pass tells a reader the architecture
    holds, when what happened is that it was never built.
    """
    status, detail, _ = G.check_layer_purity(corpus, "PySide6", ["domain"])
    assert status == "unchecked"
    assert "no such directory" in detail


# --------------------------------------------------------------------------
# Matching a criterion to a check
# --------------------------------------------------------------------------

def test_a_limit_in_prose_selects_the_size_check(corpus):
    r = G.evaluate(_gate("no production file over 400 lines"), corpus)
    assert r.check == "file_size" and r.status == "fail"


def test_a_limit_stated_only_to_the_tool_is_still_read(corpus):
    r = G.evaluate(_gate("files must be small",
                         command="python check_loc.py --max-lines 400"), corpus)
    assert r.check == "file_size"


def test_the_title_and_command_do_not_supply_operands(corpus):
    """Reading all three together invented rules out of nothing.

    A gate called "Checkpoint 1: Contracts & Fakes" run with
    `pytest tests/contracts` was answered "0 imports of Checkpoint under
    tests/" — a confident PASS for a rule that does not exist.
    """
    g = Gate(name="Checkpoint 1: Contracts & Fakes",
             command="pytest tests/contracts tests/fakes",
             fields={"Criterion": "DTO and fakes exist, tests pass"},
             doc="d.md", line=1)
    r = G.evaluate(g, corpus)
    assert r.status == "unchecked"
    assert r.check == ""


def test_the_word_pass_does_not_mean_password(corpus):
    r = G.evaluate(_gate("five scenarios pass end to end"), corpus)
    assert r.check != "secrets"


def test_a_module_the_repository_never_imports_is_not_a_module(corpus):
    r = G.evaluate(_gate("no Checkpoint inside ui/"), corpus)
    assert r.check == "" and r.status == "unchecked"


def test_a_real_module_with_a_missing_directory_is_unchecked(corpus):
    r = G.evaluate(_gate("0 import PySide6 in domain and application"), corpus)
    assert r.status == "unchecked"
    assert "PySide6" in r.detail


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def test_a_gate_signed_off_and_failing_is_flagged(corpus):
    r = G.evaluate(_gate("no file over 400 lines", signed=True), corpus)
    assert r.status == "fail" and r.contradicts_signoff


def test_an_unsigned_failing_gate_is_not_a_contradiction(corpus):
    r = G.evaluate(_gate("no file over 400 lines"), corpus)
    assert r.status == "fail" and not r.contradicts_signoff


def test_summary_counts_every_status(corpus):
    results = G.run([_gate("no file over 400 lines"),
                     _gate("nothing checkable here")], corpus)
    s = G.summary(results)
    assert s["fail"] == 1 and s["unchecked"] == 1 and s["pass"] == 0
