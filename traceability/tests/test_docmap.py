"""Contract tests for the doc-section <-> code-region map.

The map is pure arithmetic over `features.json` and `corpus.json`, so
these tests are about the two things arithmetic can still get wrong:
pointing at a region that is not really one, and reporting coverage that
is not really coverage.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink import docmap as DM
from tracelink.artifacts import Corpus, SourceFile, Symbol, ROLE_CODE
from tracelink.config import DocsConfig
from tracelink.features import build


DOC = """\
# Billing Service

## Token Usage

| # | Function | Notes |
|---|----------|-------|
| 1.1 | `charge_card()` | Take payment |
| 1.2 | `refund()` | Give it back |

## Reporting

- `build_report()` renders the figures

## Everything

- every class has an `__init__()`
"""


@pytest.fixture
def repo(tmp_path: Path) -> Corpus:
    return Corpus(
        root=str(tmp_path),
        files=[SourceFile(path="billing.py", role=ROLE_CODE, size=4000,
                          language="python"),
               SourceFile(path="reporting.py", role=ROLE_CODE, size=2000,
                          language="python"),
               SourceFile(path="quiet.py", role=ROLE_CODE, size=900,
                          language="python")],
        symbols=[
            Symbol(sid="billing.py::Billing", path="billing.py", name="Billing",
                   kind="class", line=1, end_line=120),
            Symbol(sid="billing.py::Billing.charge_card", path="billing.py",
                   name="Billing.charge_card", kind="method", line=10, end_line=40),
            # Deliberately 6 lines after charge_card ends: close enough that
            # a reader sees one area of the file, not two.
            Symbol(sid="billing.py::Billing.refund", path="billing.py",
                   name="Billing.refund", kind="method", line=46, end_line=70),
            Symbol(sid="billing.py::Billing.settle", path="billing.py",
                   name="Billing.settle", kind="method", line=300, end_line=340),
            Symbol(sid="billing.py::Billing.__init__", path="billing.py",
                   name="Billing.__init__", kind="method", line=3, end_line=8),
            Symbol(sid="reporting.py::build_report", path="reporting.py",
                   name="build_report", kind="function", line=12, end_line=60),
            Symbol(sid="reporting.py::Report.__init__", path="reporting.py",
                   name="Report.__init__", kind="method", line=70, end_line=74),
            Symbol(sid="quiet.py::nobody_mentions_me", path="quiet.py",
                   name="nobody_mentions_me", kind="function", line=1, end_line=30),
            Symbol(sid="quiet.py::Quiet.__init__", path="quiet.py",
                   name="Quiet.__init__", kind="method", line=32, end_line=36),
            Symbol(sid="quiet.py::Other.__init__", path="quiet.py",
                   name="Other.__init__", kind="method", line=40, end_line=44),
            Symbol(sid="quiet.py::Third.__init__", path="quiet.py",
                   name="Third.__init__", kind="method", line=50, end_line=54),
            Symbol(sid="quiet.py::Fourth.__init__", path="quiet.py",
                   name="Fourth.__init__", kind="method", line=60, end_line=64),
        ],
    )


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "guide.md").write_text(DOC, encoding="utf-8")
    return d


@pytest.fixture
def dm(repo, docs) -> DM.DocMap:
    return DM.build(build(docs, repo), repo)


# --------------------------------------------------------------------------
# A section points at lines, not at a file
# --------------------------------------------------------------------------

def test_section_pins_a_line_range(dm):
    sec = next(s for s in dm.sections if "Token Usage" in s.key)
    region = next(r for r in sec.regions if r.path == "billing.py")
    assert region.start == 10, "the range must start at the first documented symbol"
    assert region.end == 70, "and end at the last"
    assert "Billing.charge_card" in region.symbols
    assert "Billing.refund" in region.symbols


def test_nearby_symbols_are_one_region_and_distant_ones_are_not(repo, docs):
    """A gap of six lines is one area to a reader; a gap of 230 is not."""
    (docs / "guide.md").write_text(
        DOC + "\n- and `settle()` closes the batch\n", encoding="utf-8")
    dm = DM.build(build(docs, repo), repo)
    billing = [r for s in dm.sections for r in s.regions if r.path == "billing.py"]
    assert any(r.start == 10 and r.end == 70 for r in billing)
    assert any(r.start == 300 for r in billing), "a distant symbol is its own region"


def test_a_region_is_locatable_by_a_human(dm):
    sec = next(s for s in dm.sections if "Token Usage" in s.key)
    assert sec.line > 0, "the section must say where it is in the document"
    assert str(sec.regions[0]) == "billing.py:10-70"


# --------------------------------------------------------------------------
# Ambiguity: the failure that makes a map look complete and mean nothing
# --------------------------------------------------------------------------

def test_a_name_matching_everything_pins_nothing(dm):
    """`__init__` resolves in every class; it is a convention, not a place.

    Left in, the "Everything" section documents every file in the corpus
    and the coverage report reads as near-total.
    """
    sec = next(s for s in dm.sections if "Everything" in s.key)
    assert sec.resolved >= 1, "the name really is in the code"
    assert sec.ambiguous >= 1, "but it is counted as unpinnable"
    assert sec.regions == [], "and it points at no line"


def test_the_ambiguity_bar_is_configurable(repo, docs):
    loose = DM.build(build(docs, repo), repo, DocsConfig(max_pin_targets=99))
    sec = next(s for s in loose.sections if "Everything" in s.key)
    assert sec.regions, "raising the bar lets the convention through again"


def test_quiet_file_is_undocumented_despite_having_an_init(dm):
    """The regression the ambiguity bar exists to prevent."""
    assert "quiet.py" in {f.path for f in dm.undocumented}


# --------------------------------------------------------------------------
# Coverage means two different things and both are reported
# --------------------------------------------------------------------------

def test_class_level_mention_covers_lines_without_naming_symbols(repo, docs):
    (docs / "coarse.md").write_text(
        "# Coarse\n\n## All of it\n\n- see `Billing`\n", encoding="utf-8")
    dm = DM.build(build(docs, repo), repo)
    billing = next(f for f in dm.files if f.path == "billing.py")
    assert billing.fraction > billing.precision, (
        "naming a class covers its whole line span while locating one symbol; "
        "reporting only the line figure would call that documented")


def test_symbol_precision_counts_individually_named_symbols(dm):
    billing = next(f for f in dm.files if f.path == "billing.py")
    assert billing.symbols_named == 2      # charge_card, refund
    assert billing.symbols == 5
    assert 0 < billing.precision < 1


def test_undocumented_files_are_listed(dm):
    assert "quiet.py" in {f.path for f in dm.undocumented}
    assert "billing.py" not in {f.path for f in dm.undocumented}


# --------------------------------------------------------------------------
# Both directions agree
# --------------------------------------------------------------------------

def test_lookups_are_consistent_in_both_directions(dm):
    sec = next(s for s in dm.sections if "Token Usage" in s.key)
    back = DM.sections_for(dm, "billing.py")
    assert sec.key in {s.key for s, _ in back}
    regions = next(rs for s, rs in back if s.key == sec.key)
    assert {str(r) for r in regions} == {str(r) for r in sec.regions
                                         if r.path == "billing.py"}


def test_summary_reports_both_measures(dm):
    s = DM.summary(dm)
    assert "median_symbol_precision_of_documented_files" in s
    assert "median_line_coverage_of_documented_files" in s
    assert s["files_undocumented"] >= 1


# --------------------------------------------------------------------------
# Degradation, not failure, when the analyser gives no lines
# --------------------------------------------------------------------------

def test_missing_line_numbers_degrade_to_file_level(repo, docs):
    """An older corpus.json has no spans. It must still map, coarsely."""
    blind = Corpus(root=repo.root, files=repo.files,
                   symbols=[Symbol(sid=s.sid, path=s.path, name=s.name, kind=s.kind)
                            for s in repo.symbols])
    dm = DM.build(build(docs, blind), blind)
    sec = next(s for s in dm.sections if "Token Usage" in s.key)
    assert sec.files == ["billing.py"], "the file is still the honest answer"
    assert all(r.start == 0 for r in sec.regions) or not sec.regions
