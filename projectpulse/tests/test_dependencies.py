"""Guards for the dependency-edge resolver.

The DAG is the input to every impact number the product will quote. An edge that
should not exist does not merely add noise - it moves a milestone date on a slide
a PM takes to a steering committee. So the tests here are mostly about what the
resolver *refuses* to emit.
"""

from __future__ import annotations

from datetime import date

from app.ingest.sources.excel.dependencies import (
    SOURCE_IMPLICIT,
    SOURCE_STATED,
    resolve_edges,
)
from app.ingest.sources.excel.identity import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    ResolvedRow,
)


def row(
    key: str,
    *,
    predecessor: str | None = None,
    start: date | None = None,
    end: date | None = None,
    confidence: str = CONFIDENCE_HIGH,
    index: int = 0,
    milestone: str | None = None,
) -> ResolvedRow:
    return ResolvedRow(
        row_key=key,
        confidence=confidence,
        payload={
            "task_id": key,
            "title": f"task {key}",
            "predecessor": predecessor,
            "start_date": start,
            "baseline_end": end,
            "milestone": milestone,
        },
        row_index=index,
    )


def pairs(result) -> set[tuple[str, str]]:
    return {e.pair for e in result.edges}


# --------------------------------------------------------------------------
# Stated edges
# --------------------------------------------------------------------------


def test_a_plain_predecessor_becomes_one_edge():
    result = resolve_edges(
        [row("A", index=1), row("B", predecessor="A", index=2)], infer_wbs=False
    )

    assert pairs(result) == {("A", "B")}
    assert result.edges[0].source == SOURCE_STATED
    assert result.edges[0].dep_type == "FS"
    assert result.edges[0].lag_days == 0
    assert not result.rejects


def test_one_cell_can_state_several_predecessors():
    result = resolve_edges(
        [
            row("A", index=1),
            row("B", index=2),
            row("C", predecessor="A, B", index=3),
        ],
        infer_wbs=False,
    )

    assert pairs(result) == {("A", "C"), ("B", "C")}


def test_ms_project_lag_notation_is_parsed():
    result = resolve_edges(
        [row("A", index=1), row("B", predecessor="AFS+2d", index=2)], infer_wbs=False
    )

    edge = result.edges[0]
    assert edge.pair == ("A", "B")
    assert edge.dep_type == "FS"
    assert edge.lag_days == 2


def test_negative_lag_is_parsed():
    result = resolve_edges(
        [row("A", index=1), row("B", predecessor="A SS -3 days", index=2)],
        infer_wbs=False,
    )

    edge = result.edges[0]
    assert edge.dep_type == "SS"
    assert edge.lag_days == -3


def test_an_id_that_ends_in_a_relation_code_is_not_truncated():
    """``WBS-FS`` is a task id, not ``WBS-`` with an FS suffix.

    The whole token is matched against the sheet's keys before the MS-Project
    suffix grammar is tried, precisely so a legitimate id survives.
    """
    result = resolve_edges(
        [row("WBS-FS", index=1), row("B", predecessor="WBS-FS", index=2)],
        infer_wbs=False,
    )

    assert pairs(result) == {("WBS-FS", "B")}
    assert not result.rejects


def test_a_bad_token_does_not_cost_the_good_ones():
    result = resolve_edges(
        [
            row("A", index=1),
            row("B", index=2),
            row("C", predecessor="A, NOPE-1, B", index=3),
        ],
        infer_wbs=False,
    )

    assert pairs(result) == {("A", "C"), ("B", "C")}
    assert len(result.rejects) == 1
    assert "NOPE-1" in result.rejects[0].reason


# --------------------------------------------------------------------------
# What gets refused
# --------------------------------------------------------------------------


def test_a_dangling_predecessor_is_rejected_not_invented():
    """The graph must never assert a path to a node that is not in the sheet."""
    result = resolve_edges([row("B", predecessor="GHOST", index=1)], infer_wbs=False)

    assert result.edges == []
    assert len(result.rejects) == 1
    assert "does not match any row" in result.rejects[0].reason


def test_a_row_cannot_be_its_own_predecessor():
    result = resolve_edges([row("A", predecessor="A", index=1)], infer_wbs=False)

    assert result.edges == []
    assert "its own predecessor" in result.rejects[0].reason


def test_an_edge_touching_a_low_confidence_row_is_dropped():
    """An edge is only as trustworthy as the identity of the rows it joins.

    A title-matched row might not be the task we think it is, so an edge to it
    could attach the wrong predecessor to the wrong successor - which is exactly
    how a causal chain becomes fiction.
    """
    result = resolve_edges(
        [
            row("A", index=1),
            row("~anon-abc", predecessor="A", confidence=CONFIDENCE_LOW, index=2),
        ],
        infer_wbs=False,
    )

    assert result.edges == []
    assert "low confidence" in result.rejects[0].reason


def test_ids_differing_only_in_case_are_ambiguous_not_guessed():
    result = resolve_edges(
        [
            row("Task-1", index=1),
            row("TASK-1", index=2),
            row("B", predecessor="task-1", index=3),
        ],
        infer_wbs=False,
    )

    assert result.edges == []
    assert "capitalisation" in result.rejects[0].reason


def test_the_same_edge_stated_twice_is_deduplicated_silently():
    result = resolve_edges(
        [row("A", index=1), row("B", predecessor="A, A", index=2)], infer_wbs=False
    )

    assert pairs(result) == {("A", "B")}
    assert not result.rejects


# --------------------------------------------------------------------------
# Cycles - a cyclic schedule has no critical path at all
# --------------------------------------------------------------------------


def test_a_cycle_costs_exactly_one_edge():
    result = resolve_edges(
        [
            row("A", predecessor="C", index=1),
            row("B", predecessor="A", index=2),
            row("C", predecessor="B", index=3),
        ],
        infer_wbs=False,
    )

    assert len(result.edges) == 2
    assert len(result.rejects) == 1
    assert "cycle" in result.rejects[0].reason


def test_a_two_row_cycle_is_broken():
    result = resolve_edges(
        [row("A", predecessor="B", index=1), row("B", predecessor="A", index=2)],
        infer_wbs=False,
    )

    assert len(result.edges) == 1
    assert len(result.rejects) == 1


def test_a_diamond_is_not_a_cycle():
    """Two paths converging is normal scheduling, and must survive intact."""
    result = resolve_edges(
        [
            row("A", index=1),
            row("B", predecessor="A", index=2),
            row("C", predecessor="A", index=3),
            row("D", predecessor="B, C", index=4),
        ],
        infer_wbs=False,
    )

    assert len(result.edges) == 4
    assert not result.rejects


def test_a_stated_edge_outranks_an_inferred_one_when_a_cycle_must_break():
    """Order matters: the human's answer is the one that survives.

    B->A is stated. The dates would otherwise imply A->B, which would close a
    loop; the inferred edge is the one dropped.
    """
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 10), index=1),
            row(
                "B",
                predecessor="A",
                start=date(2026, 1, 12),
                end=date(2026, 1, 20),
                index=2,
            ),
        ]
    )

    assert all(e.source == SOURCE_STATED for e in result.edges)
    assert pairs(result) == {("A", "B")}


# --------------------------------------------------------------------------
# WBS-implicit inference
# --------------------------------------------------------------------------


def test_an_edge_is_inferred_when_the_dates_support_it():
    """Architecture §5.4: each activity starts when the previous one finishes."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 10), index=1),
            row("B", start=date(2026, 1, 12), end=date(2026, 1, 20), index=2),
        ]
    )

    assert pairs(result) == {("A", "B")}
    edge = result.edges[0]
    assert edge.source == SOURCE_IMPLICIT
    assert edge.lag_days == 2


def test_parallel_rows_produce_no_inferred_edge():
    """Overlapping work is not a dependency, and guessing one would be fiction."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 31), index=1),
            row("B", start=date(2026, 1, 5), end=date(2026, 1, 20), index=2),
        ]
    )

    assert result.edges == []


def test_inference_never_overrides_a_stated_predecessor():
    """A human wrote B follows A. We do not additionally attach B to C."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 5), index=1),
            row("C", start=date(2026, 1, 6), end=date(2026, 1, 10), index=2),
            row("B", predecessor="A", start=date(2026, 1, 12), end=date(2026, 1, 20), index=3),
        ]
    )

    assert ("A", "B") in pairs(result)
    assert ("C", "B") not in pairs(result)


def test_low_confidence_rows_are_excluded_from_inference():
    """We are unsure which task it is, so we are certainly unsure what it follows."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 10), index=1),
            row(
                "~anon-abc",
                start=date(2026, 1, 12),
                end=date(2026, 1, 20),
                confidence=CONFIDENCE_LOW,
                index=2,
            ),
        ]
    )

    assert result.edges == []


def test_rows_without_dates_are_skipped_rather_than_ordered_by_position():
    """Sheet order is not schedule order, and must never be used as a fallback."""
    result = resolve_edges([row("A", index=1), row("B", index=2)])

    assert result.edges == []


def test_an_ambiguous_text_date_is_not_guessed():
    """``03/04/2026`` is March 4th or April 3rd depending on the reader.

    Parsing it either way would silently move a schedule by a month, so it is
    treated as no date at all.
    """
    a = row("A", index=1)
    a.payload["start_date"] = "01/02/2026"
    a.payload["baseline_end"] = "03/04/2026"
    b = row("B", start=date(2026, 6, 1), end=date(2026, 6, 10), index=2)

    result = resolve_edges([a, b])

    assert result.edges == []


def test_inference_falls_back_to_planned_end_when_there_is_no_baseline():
    a = ResolvedRow(
        "A",
        CONFIDENCE_HIGH,
        {"start_date": date(2026, 1, 1), "planned_end": date(2026, 1, 10)},
        1,
    )
    b = ResolvedRow(
        "B",
        CONFIDENCE_HIGH,
        {"start_date": date(2026, 1, 10), "planned_end": date(2026, 1, 20)},
        2,
    )

    result = resolve_edges([a, b])

    assert pairs(result) == {("A", "B")}
    assert result.edges[0].lag_days == 0


def test_stated_and_inferred_are_reported_separately():
    """The evidence panel has to be able to say which edges a human vouched for."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 5), index=1),
            row("B", predecessor="A", start=date(2026, 1, 6), end=date(2026, 1, 10), index=2),
            row("C", start=date(2026, 1, 11), end=date(2026, 1, 15), index=3),
        ]
    )

    assert [e.pair for e in result.stated] == [("A", "B")]
    assert [e.pair for e in result.inferred] == [("B", "C")]


def test_every_edge_names_the_row_that_carried_it():
    """`stated_by_key` is the evidence pointer: the cell the edge came from."""
    result = resolve_edges(
        [row("A", index=1), row("B", predecessor="A", index=2)], infer_wbs=False
    )

    assert result.edges[0].stated_by_key == "B"


def test_no_edge_is_inferred_when_several_rows_claim_one_predecessor():
    """"The previous activity" is singular. Several rows equally entitled to the
    same predecessor makes it ambiguous, and the whole fan is dropped rather
    than hedged - the same choice this module makes everywhere else.

    The case that prompted it: sixteen PM-checklist tasks all starting 2 Sep
    attached themselves to one delivery task finishing 31 Aug, asserting the
    checklist waited on work it does not reference anywhere.
    """
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 10), index=1),
            row("B", start=date(2026, 1, 12), end=date(2026, 1, 20), index=2),
            row("C", start=date(2026, 1, 12), end=date(2026, 1, 21), index=3),
        ]
    )

    assert result.edges == []


def test_a_one_to_one_hand_off_is_still_inferred():
    """The guard narrows the rule; it must not switch it off. A genuine
    phase-to-phase hand-off is one predecessor and one successor."""
    result = resolve_edges(
        [
            row("A", start=date(2026, 1, 1), end=date(2026, 1, 10), index=1),
            row("B", start=date(2026, 1, 12), end=date(2026, 1, 20), index=2),
            row("C", start=date(2026, 1, 22), end=date(2026, 1, 30), index=3),
        ]
    )

    assert pairs(result) == {("A", "B"), ("B", "C")}
