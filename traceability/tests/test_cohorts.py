"""Finding the seam where two backlogs were appended into one sheet."""

from __future__ import annotations

from tracelink import cohorts as CH
from tracelink.artifacts import Ticket, Verdict


def _t(uid, row, status="Release it", fix="v1", component="UI", lang="en"):
    return Ticket(uid=uid, summary=f"ticket {uid}", status=status,
                  component=component, source_row=row, lang=lang,
                  extra={"fix_version": fix, "issue_type": "Task"})


def _blocked(n_a=6, n_b=4):
    """Two blocks appended: the first has a fix version, the second none."""
    a = [_t(f"A{i}", 10 + i, "Release it", "v1") for i in range(n_a)]
    b = [_t(f"B{i}", 10 + n_a + i, "To Do", "") for i in range(n_b)]
    return a + b


def _interleaved():
    """The same two values, alternating - one list, not two."""
    return [_t(f"X{i}", 10 + i, "Release it", "v1" if i % 2 else "")
            for i in range(10)]


def test_a_field_that_blocks_up_is_a_seam():
    found = CH.seams(_blocked())
    seam = next(s for s in found if s.is_seam)
    assert seam.field == "fix_version?"
    assert seam.contiguity == 1.0


def test_an_interleaved_field_is_not_a_seam():
    found = CH.seams(_interleaved())
    by_field = {s.field: s for s in found}
    assert by_field["fix_version?"].is_seam is False


def test_presence_finds_a_seam_that_the_values_hide():
    """The real case: several fix versions scattered, but 'has one' splits."""
    a = [_t(f"A{i}", 10 + i, fix=("v1" if i % 3 else "v2")) for i in range(9)]
    b = [_t(f"B{i}", 19 + i, status="To Do", fix="") for i in range(5)]
    found = {s.field: s for s in CH.seams(a + b)}
    assert found["fix_version"].is_seam is False
    assert found["fix_version?"].is_seam is True


def test_the_split_keeps_every_ticket():
    tickets = _blocked()
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    cohorts = CH.split(tickets, seam)
    assert sum(c.n for c in cohorts) == len(tickets)


def test_each_cohort_reports_where_it_sits_in_the_sheet():
    tickets = _blocked(n_a=6, n_b=4)
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    by_label = {c.label: c for c in CH.split(tickets, seam)}
    assert by_label["set"].span == (10, 15)
    assert by_label["unset"].span == (16, 19)


def test_corroboration_is_reported_per_cohort():
    tickets = _blocked(n_a=4, n_b=4)
    verdicts = ([Verdict(uid=f"A{i}", verdict="corroborated", confidence="high",
                         reasoning="") for i in range(4)]
                + [Verdict(uid=f"B{i}", verdict="unverified", confidence="high",
                           reasoning="") for i in range(4)])
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    by_label = {c.label: c for c in CH.split(tickets, seam, verdicts)}
    assert by_label["set"].corroboration == 1.0
    assert by_label["unset"].corroboration == 0.0


def test_a_cohort_with_no_verdicts_reports_none_rather_than_zero():
    """Untested is not the same as tested and found missing."""
    tickets = _blocked()
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    assert all(c.corroboration is None for c in CH.split(tickets, seam))


def test_language_is_taken_from_the_translate_stage():
    tickets = _blocked(n_a=2, n_b=2)
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    langs = {"B0": "Vietnamese", "B1": "Vietnamese"}
    by_label = {c.label: c for c in CH.split(tickets, seam, None, langs)}
    assert by_label["unset"].lang == {"Vietnamese": 2}
    assert by_label["set"].lang == {"en": 2}


def test_render_says_the_pooled_number_describes_neither():
    tickets = _blocked(n_a=4, n_b=4)
    verdicts = ([Verdict(uid=f"A{i}", verdict="corroborated", confidence="high",
                         reasoning="") for i in range(4)]
                + [Verdict(uid=f"B{i}", verdict="unverified", confidence="high",
                           reasoning="") for i in range(4)])
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    text = CH.render(seam, CH.split(tickets, seam, verdicts), len(tickets))
    assert "describes neither cohort" in text
    assert "no row is dropped or merged" in text


def test_a_single_valued_field_is_never_a_seam():
    tickets = [_t(f"A{i}", 10 + i) for i in range(5)]
    assert all(s.field != "status" for s in CH.seams(tickets))


def test_rows_without_a_sheet_position_keep_their_order():
    """An adapter that has no row numbers must not crash or reorder."""
    tickets = [Ticket(uid="A", summary="a", status="To Do",
                      extra={"fix_version": "v1"}),
               Ticket(uid="B", summary="b", status="Done",
                      extra={"fix_version": ""})]
    seam = next((s for s in CH.seams(tickets) if s.is_seam), None)
    assert seam is not None
    assert all(c.span is None for c in CH.split(tickets, seam))


# Who the rows name - the export has no assignee column


def _owned(uid, row, po, ba, dev, fix="v1"):
    t = _t(uid, row, fix=fix)
    t.inline = {k: v for k, v in (("PO", po), ("BA", ba), ("Developer", dev)) if v}
    return t


def test_a_key_most_rows_carry_is_learned_as_a_role():
    tickets = [_owned(f"A{i}", 10 + i, "alice", "alice", "alice") for i in range(6)]
    assert CH.role_keys(tickets) == ["BA", "Developer", "PO"]


def test_an_occasional_note_is_not_a_role():
    """`Ngay nhan` sits on 4 rows of 173. That is a note, not a role."""
    tickets = [_owned(f"A{i}", 10 + i, "alice", "alice", "alice") for i in range(10)]
    tickets[0].inline["Ngay nhan"] = "46244"
    assert "Ngay nhan" not in CH.role_keys(tickets)


def test_one_person_in_every_role_is_counted():
    tickets = [_owned(f"A{i}", 10 + i, "alice", "alice", "alice") for i in range(5)]
    own = CH.ownership(tickets)
    assert own["solo"] == 5
    assert own["top"] == "alice" and own["share"] == 1.0


def test_a_row_missing_a_role_is_not_counted_as_one_person():
    """The bug: a ticket with no PO scored as "one person holds all three"."""
    tickets = [_owned("A", 10, None, "fsg", "fsg")]
    assert CH.ownership(tickets, ["PO", "BA", "Developer"])["solo"] == 0


def test_distributed_work_scores_zero():
    tickets = [_owned(f"A{i}", 10 + i, "alice", "bob", "carol") for i in range(4)]
    assert CH.ownership(tickets)["solo"] == 0


def test_cohorts_are_measured_against_the_same_roles():
    """Learned per cohort, the roadmap block drops `PO` and its solo count
    is taken over two roles against the other cohort's three - two numbers
    printed side by side that do not mean the same thing."""
    a = [_owned(f"A{i}", 10 + i, "alice", "alice", "alice") for i in range(8)]
    b = [_owned(f"B{i}", 20 + i, None, "fsg", "fsg", fix="") for i in range(4)]
    tickets = a + b
    seam = next(s for s in CH.seams(tickets) if s.is_seam)
    by = {c.label: c for c in CH.split(tickets, seam)}
    assert by["set"].owners["solo"] == 8
    assert by["unset"].owners["solo"] == 0
    # measured over the whole-backlog role set, not each cohort's own
    assert by["unset"].owners["roles"] == by["set"].owners["roles"]


def test_a_backlog_with_no_inline_names_reports_nothing():
    tickets = [_t(f"A{i}", 10 + i) for i in range(4)]
    own = CH.ownership(tickets)
    assert own["roles"] == [] and own["top"] is None and own["solo"] == 0


def test_render_names_the_busiest_person():
    tickets = [_owned(f"A{i}", 10 + i, "alice", "alice", "alice") for i in range(6)]
    seam = next((s for s in CH.seams(tickets) if s.is_seam), None)
    if seam is None:
        own = CH.ownership(tickets)
        assert own["top"] == "alice"
        return
    text = CH.render(seam, CH.split(tickets, seam), len(tickets))
    assert "alice" in text
