

"""Reading the hand-maintained block a Jira export keeps in its Description.

This instance has no column for a developer, a received date or a list of
members, so the team writes them as `Label: value` inside the description.
The converter already reads the first two; these cover the third.
"""

from __future__ import annotations


# The note field: a list of people, not one


def test_names_are_read_from_a_flattened_description():
    """`Task.description` reaches the API as one line, so a value runs to
    the next label rather than to end-of-line."""
    from app.ingest.sources.jira.export_sheet import people_in_note

    text = ("PO: HoachBV BA: FSG/QuanDh14 Developer: FSG "
            "Ngày nhận: 46246 Ghi chú: TaiPH9,LocLP3,HieuHV1")
    assert people_in_note(text) == ["TaiPH9", "LocLP3", "HieuHV1"]


def test_a_two_word_label_does_not_swallow_the_previous_value():
    """A generic `word:` splitter breaks at ` nhận:` and hands `Ngày` back
    as part of the Developer value. These labels are two words by default."""
    from app.ingest.sources.jira.export_sheet import people_in_note

    text = "Ngày nhận: 46246 Ghi chú: NamPDT,HiepHV3"
    assert people_in_note(text) == ["NamPDT", "HiepHV3"]


def test_the_note_still_reads_when_the_block_has_newlines():
    from app.ingest.sources.jira.export_sheet import people_in_note

    text = "PO: HoachBV\nDeveloper: FSG\nGhi chú: HoaNH1,VuDT15,ThangNQ53"
    assert people_in_note(text) == ["HoaNH1", "VuDT15", "ThangNQ53"]


def test_a_note_holding_prose_contributes_nobody():
    """The label colliding with a sentence must not invent people."""
    from app.ingest.sources.jira.export_sheet import people_in_note

    assert people_in_note(
        "Ghi chú: This is a long explanatory sentence about the work."
    ) == []


def test_a_row_with_no_note_yields_an_empty_list():
    from app.ingest.sources.jira.export_sheet import people_in_note

    assert people_in_note("PO: HoachBV BA: FSG") == []
    assert people_in_note("") == []
    assert people_in_note(None) == []


def test_duplicates_are_collapsed_in_written_order():
    from app.ingest.sources.jira.export_sheet import people_in_note

    assert people_in_note("Note: bob, alice, bob") == ["bob", "alice"]
