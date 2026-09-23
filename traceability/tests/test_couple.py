"""Inferred ticket relationships.

The tests that matter here are the *refusals*. An inference engine that
finds links is easy; one that declines to link two tickets because the only
thing joining them is a logger is the whole point.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracelink.artifacts import (
    Corpus, FileEdge, SourceFile, Ticket, Verdict, VerdictEvidence, ROLE_CODE,
)
from tracelink.couple import (
    KIND_DEPENDS, KIND_SHARES, CoupleConfig, by_ticket, cited_files, infer,
)


def _corpus(paths, edges=()):
    return Corpus(
        files=[SourceFile(p, ROLE_CODE, 500, "python") for p in paths],
        symbols=[], root=".", analyzer="test",
        edges=[FileEdge(src=a, dst=b, weight=w) for a, b, w in edges],
    )


def _v(uid, *files, verdict="corroborated"):
    return Verdict(uid=uid, verdict=verdict, confidence="high", reasoning="r",
                   evidence=[VerdictEvidence(f, "why") for f in files])


def _t(uid):
    return Ticket(uid=uid, summary=f"ticket {uid}")


# --------------------------------------------------------------------------
# What it finds
# --------------------------------------------------------------------------

def test_import_edge_becomes_a_directional_dependency():
    links, _ = infer(
        [_t("a"), _t("b")],
        [_v("a", "ui/panel.py"), _v("b", "core/model.py")],
        _corpus(["ui/panel.py", "core/model.py"],
                [("ui/panel.py", "core/model.py", 3)]),
    )
    dep = [l for l in links if l.kind == KIND_DEPENDS]
    assert len(dep) == 1
    assert (dep[0].src, dep[0].dst) == ("a", "b")
    assert dep[0].via == ["ui/panel.py -> core/model.py"]


def test_same_file_becomes_a_symmetric_link():
    links, _ = infer(
        [_t("a"), _t("b")],
        [_v("a", "core/shared.py"), _v("b", "core/shared.py")],
        _corpus(["core/shared.py"]),
    )
    assert [l.kind for l in links] == [KIND_SHARES]
    assert {links[0].src, links[0].dst} == {"a", "b"}
    assert links[0].via == ["core/shared.py"]


def test_two_independent_edges_raise_confidence():
    links, _ = infer(
        [_t("a"), _t("b")],
        [_v("a", "ui/one.py", "ui/two.py"), _v("b", "core/model.py")],
        _corpus(["ui/one.py", "ui/two.py", "core/model.py"],
                [("ui/one.py", "core/model.py", 1),
                 ("ui/two.py", "core/model.py", 1)]),
    )
    dep = [l for l in links if l.kind == KIND_DEPENDS][0]
    assert len(dep.via) == 2
    assert dep.confidence == "high"


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------

def test_a_utility_everything_imports_links_nothing():
    """The i18n trap: a module 90% of the code imports joins no two tickets."""
    files = [f"m{i}.py" for i in range(20)] + ["util/log.py"]
    edges = [(f"m{i}.py", "util/log.py", 1) for i in range(20)]
    verdicts = [_v(f"t{i}", f"m{i}.py", "util/log.py") for i in range(20)]
    links, stats = infer([_t(f"t{i}") for i in range(20)], verdicts,
                         _corpus(files, edges))
    assert "util/log.py" in stats["hub_reasons"]
    assert not any("util/log.py" in v for l in links for v in l.via)


def test_a_file_most_of_the_backlog_touches_is_excluded():
    verdicts = [_v(f"t{i}", "app.py") for i in range(20)]
    _links, stats = infer([_t(f"t{i}") for i in range(20)], verdicts,
                          _corpus(["app.py"]))
    assert "app.py" in stats["hub_reasons"]
    assert "implicated in 20 tickets" in stats["hub_reasons"]["app.py"]


def test_unverified_verdicts_contribute_no_links():
    """A verdict that settled nothing is not evidence of a relationship."""
    links, stats = infer(
        [_t("a"), _t("b")],
        [_v("a", "core/x.py", verdict="unverified"),
         _v("b", "core/x.py", verdict="unverified")],
        _corpus(["core/x.py"]),
    )
    assert links == []
    assert stats["tickets_with_cited_code"] == 0


def test_candidates_are_not_used_only_cited_evidence():
    """Two tickets sharing a bad keyword guess must not become related."""
    links, _ = infer(
        [_t("a"), _t("b")],
        [Verdict(uid="a", verdict="corroborated", confidence="high",
                 reasoning="r", evidence=[]),
         Verdict(uid="b", verdict="corroborated", confidence="high",
                 reasoning="r", evidence=[])],
        _corpus(["core/x.py"]),
    )
    assert links == []


def test_a_diffuse_edge_is_reported_with_low_confidence():
    """Six tickets each side: the import is real, the pairing is not."""
    srcs = [f"s{i}" for i in range(4)]
    dsts = [f"d{i}" for i in range(4)]
    verdicts = ([_v(u, "ui/panel.py") for u in srcs] +
                [_v(u, "core/model.py") for u in dsts])
    links, _ = infer([_t(u) for u in srcs + dsts], verdicts,
                     _corpus(["ui/panel.py", "core/model.py"],
                             [("ui/panel.py", "core/model.py", 9)]))
    dep = [l for l in links if l.kind == KIND_DEPENDS]
    assert dep and all(l.confidence == "low" for l in dep)


def test_no_self_links():
    links, _ = infer(
        [_t("a")], [_v("a", "ui/panel.py", "core/model.py")],
        _corpus(["ui/panel.py", "core/model.py"],
                [("ui/panel.py", "core/model.py", 2)]),
    )
    assert all(l.src != l.dst for l in links)


def test_missing_edges_still_give_shared_file_links():
    """The AST fallback supplies no edges; the tool must degrade, not fail."""
    links, stats = infer(
        [_t("a"), _t("b")],
        [_v("a", "core/shared.py"), _v("b", "core/shared.py")],
        _corpus(["core/shared.py"], edges=()),
    )
    assert stats["depends_on"] == 0
    assert stats["shares_code"] == 1


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def test_links_are_capped_per_ticket():
    cfg = CoupleConfig(max_per_ticket=2, hub_file_fraction=9.0,
                       hub_fanin_fraction=9.0, hub_floor=999)
    verdicts = [_v(f"t{i}", "core/shared.py") for i in range(8)]
    links, _ = infer([_t(f"t{i}") for i in range(8)], verdicts,
                     _corpus(["core/shared.py"]), cfg)
    seen = {}
    for l in links:
        seen[l.src] = seen.get(l.src, 0) + 1
        seen[l.dst] = seen.get(l.dst, 0) + 1
    assert max(seen.values()) <= 2


def test_by_ticket_states_direction_from_each_side():
    links, _ = infer(
        [_t("a"), _t("b")],
        [_v("a", "ui/panel.py"), _v("b", "core/model.py")],
        _corpus(["ui/panel.py", "core/model.py"],
                [("ui/panel.py", "core/model.py", 1)]),
    )
    view = by_ticket(links)
    assert view["a"][0]["direction"] == "to"
    assert view["b"][0]["direction"] == "from"
    assert view["a"][0]["other"] == "b"


def test_cited_files_normalises_windows_paths():
    got = cited_files([_v("a", "core\\model.py")])
    assert got["a"] == {"core/model.py"}
