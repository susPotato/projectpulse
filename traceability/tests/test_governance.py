"""Governance tickets: the two rules, and the order they run in.

Most of these assert that a finding is *not* made. That is the point of the
stage - on a real export it will mostly report that it cannot report, and
the failure mode worth testing is the one where it invents a gap.
"""

from __future__ import annotations

from tracelink import governance as GV

DONE = {"Closed"}


def _row(key="PM-1", summary="Develop CM Plan", status="Closed", desc=""):
    return {"Key": key, "Summary": summary, "Status": status,
            "Description": desc}


# Rule 1 - an open task owes nothing


def test_an_open_task_is_never_a_missing_deliverable():
    rows = [_row(status="In Progress", desc="Produce the CM Plan.")]
    t = GV.classify(rows, [], DONE)[0]
    assert t.verdict == "not-claimed-done"
    assert t.resolved == []


def test_rule_one_runs_before_any_lookup():
    """Even with a deliverable that would not resolve, an open task is quiet."""
    rows = [_row(status="To Do", desc="Deliver the Project Charter on FI2.0.")]
    t = GV.classify(rows, ["readme.md"], DONE)[0]
    assert t.verdict == "not-claimed-done"


def test_without_a_done_status_nothing_is_judged():
    """Which status means finished is not a thing to guess."""
    rows = [_row(status="Closed", desc="Produce the CM Plan.")]
    t = GV.classify(rows, [], None)[0]
    assert t.verdict == "not-claimed-done"
    assert "not a thing to guess" in t.why


# Rule 2 - unlocatable is not missing


def test_a_done_task_whose_deliverable_is_absent_is_unlocatable_not_missing():
    rows = [_row(desc="Complete the Project Plan on FI2.0.")]
    t = GV.classify(rows, ["architecture/adr-001.md"], DONE)[0]
    assert t.verdict == "unlocatable"
    assert "missing" not in t.why


def test_the_holding_system_is_named_when_known():
    rows = [_row(desc="Complete the Project Plan on FI2.0.")]
    t = GV.classify(rows, [], DONE)[0]
    assert t.systems == ["FI2.0"]
    assert "FI2.0" in t.why


def test_an_unknown_holder_still_refuses_to_call_it_missing():
    rows = [_row(desc="Produce the Handover Document.")]
    t = GV.classify(rows, [], DONE)[0]
    assert t.verdict == "unlocatable"
    assert "not visible here" in t.why


def test_a_deliverable_that_resolves_is_reported_as_delivered():
    rows = [_row(desc="Produce the CM Plan.")]
    t = GV.classify(rows, ["process/cm-plan.md"], DONE)[0]
    assert t.verdict == "delivered"
    assert t.resolved == ["process/cm-plan.md"]


def test_a_done_task_naming_nothing_is_not_a_gap():
    rows = [_row(desc="Attend and take notes.")]
    t = GV.classify(rows, [], DONE)[0]
    assert t.verdict == "no-deliverable-named"


# Extraction


def test_a_single_noun_is_not_a_deliverable_name():
    """The bug: "schedule" alone matched four unrelated files and reported
    a kickoff meeting as having delivered one."""
    assert GV.deliverables("Agree the agenda and the schedule.") == []


def test_a_lowercase_noun_still_counts_when_qualified():
    """This prose writes "tab Project plan on FI2.0"."""
    assert "Project plan" in GV.deliverables("Complete tab Project plan on FI2.0.")


def test_procedure_numbering_is_not_part_of_the_name():
    got = GV.deliverables("1. Perform the baseline. 3. Schedule the review.")
    assert not any(d[0].isdigit() or d.startswith(".") for d in got)


def test_a_leading_article_is_stripped():
    assert "CM Plan" in GV.deliverables("Update the CM Plan today.")


def test_an_uncapitalised_phrase_is_ignored():
    """Otherwise every "the plan" in running prose is a deliverable."""
    assert GV.deliverables("we agreed the plan and the list") == []


def test_urls_are_captured_as_evidence_locations():
    rows = [_row(desc="Submit on https://insight.fsoft.com/isms here.")]
    t = GV.classify(rows, [], DONE)[0]
    assert t.urls == ["https://insight.fsoft.com/isms"]


# The summary


def test_checkable_counts_only_what_could_support_a_finding():
    rows = [_row(key="A", status="In Progress", desc="Produce the CM Plan."),
            _row(key="B", desc="Produce the CM Plan."),
            _row(key="C", desc="Attend the meeting.")]
    s = GV.build(rows, [], DONE)
    assert s.checkable == 1


def test_a_run_where_nothing_is_checkable_says_so_plainly():
    rows = [_row(status="In Progress", desc="Produce the CM Plan.")]
    text = GV.render(GV.build(rows, [], DONE))
    assert "0 of 1 process tickets can support a finding" in text
    assert "would be wrong on every row" in text


def test_an_export_with_no_process_tickets_is_not_an_error():
    assert "no process tickets" in GV.render(GV.build([], [], DONE))


# Steps - the part a PM can act on


def test_numbered_steps_become_checklist_items():
    d = "1. PM assigns a member.\n2. CC completes the CM Plan.\n3. Review it."
    got = GV.steps(d)
    assert [s.n for s in got] == [1, 2, 3]
    assert got[0].owner == "PM"
    assert got[1].owner == "CC"


def test_text_before_the_first_number_is_not_a_step():
    """The kickoff ticket opens with nine agenda bullets before "1.". Those
    are not somebody's checklist items."""
    d = "This meeting includes:\n- overview\n- deliverables\n1. Prepare agenda."
    got = GV.steps(d)
    assert len(got) == 1
    assert got[0].text == "Prepare agenda."


def test_a_role_in_a_participant_list_does_not_own_the_step():
    """The bug: "Conduct kick-off meeting. Required participants are:
    Senior Manager, PM, PTL, QA" was owned by the Senior Manager."""
    d = "1. Conduct kick-off meeting. Required participants are: Senior Manager, PM, PTL, QA"
    s = GV.steps(d)[0]
    assert s.owner == ""
    assert "Senior Manager" in s.involves and "QA" in s.involves


def test_no_owner_is_preferred_to_the_wrong_owner():
    d = "1. An information CI is baselined when the Customer accepts it."
    assert GV.steps(d)[0].owner == ""


def test_a_deadline_is_captured_with_its_step():
    d = "1. CC sends for review. Resolved time follows the SLA: 3 working days"
    s = GV.steps(d)[0]
    assert s.sla == "3 working days"
    assert s.owner == "CC"


def test_wds_is_the_same_kind_of_deadline():
    assert GV.steps("1. PM shares within 25 wds of start.")[0].sla == "25 wds"


def test_a_contact_address_travels_with_its_step():
    d = "1. CC sends to CMP.Review@fpt.com for approval."
    assert GV.steps(d)[0].contacts == ["CMP.Review@fpt.com"]


def test_roles_are_matched_case_sensitively():
    """A case-insensitive "IT" matches the word "it" in every sentence."""
    assert GV.steps("1. Do it when it is ready.")[0].involves == []


def test_a_longer_role_name_wins_over_the_acronym_inside_it():
    s = GV.steps("1. Operation Manager approves the plan.")[0]
    assert s.owner == "Operation Manager"


# Mojibake


def test_utf8_read_as_cp1252_is_repaired():
    assert GV.repair("environment,â€¦") == "environment,…"


def test_text_that_was_never_mangled_is_untouched():
    for good in ("plain ascii", "déjà vu", "Tiếng Việt"):
        assert GV.repair(good) == good


# Delegation


def test_one_assignee_across_many_tickets_is_reported():
    rows = [_row(key=f"PM-{i}", status="In Progress",
                 desc="1. PM assigns a member.") for i in range(3)]
    for r in rows:
        r["Assignee"] = "Hoach Bach Van"
    s = GV.build(rows, [], DONE)
    assert s.delegation["single_assignee"] is True
    assert s.delegation["assignees"] == {"Hoach Bach Van": 3}
    assert "has not been handed out" in GV.render(s)


def test_varied_assignees_are_not_flagged():
    rows = [_row(key="A", desc="1. PM does it."), _row(key="B", desc="1. CC does it.")]
    rows[0]["Assignee"] = "Alice"
    rows[1]["Assignee"] = "Bob"
    assert GV.build(rows, [], DONE).delegation["single_assignee"] is False


def test_steps_are_counted_and_attributed():
    rows = [_row(desc="1. PM assigns.\n2. CC completes.\n3. Nobody named.")]
    s = GV.build(rows, [], DONE)
    assert s.n_steps == 3
    assert s.by_owner == {"PM": 1, "CC": 1}


def test_commitments_list_only_steps_with_a_clock():
    rows = [_row(desc="1. PM acts within 3 working days.\n2. CC acts.")]
    c = GV.build(rows, [], DONE).commitments
    assert len(c) == 1
    assert c[0]["sla"] == "3 working days" and c[0]["owner"] == "PM"


def test_the_report_refuses_to_say_whether_a_deadline_was_met():
    """There is no start event on these tickets, so it cannot know."""
    rows = [_row(desc="1. PM acts within 3 working days.")]
    assert "whether a deadline was met" in GV.render(GV.build(rows, [], DONE))


# Acceptance forms - QA ACC_Note


FORM = ("1. Tru diem (logwork): yes/no \n2. PCV data 0/ 50: yes/no \n"
        "3. >=3 Round : yes/no \n4. KPI Input : yes/no/na \n"
        "8. CSS excel: Yes_Compliant/ Yes_Non-Compliant")


def test_an_untouched_form_is_all_unanswered():
    """Every answer slot still shows the menu, which is what empty looks like."""
    got = GV.acceptance(FORM)
    assert len(got) == 5
    assert all(not a.answered for a in got)


def test_the_answer_menu_is_kept_as_the_options():
    got = {a.n: a for a in GV.acceptance(FORM)}
    assert got[4].options == ["yes", "no", "na"]
    assert got[8].options == ["Yes_Compliant", "Yes_Non-Compliant"]


def test_a_label_containing_a_slash_is_not_mistaken_for_the_answer():
    """"PCV data 0/ 50: yes/no" splits at the last colon, not the first slash."""
    got = {a.n: a for a in GV.acceptance(FORM)}
    assert got[2].label == "PCV data 0/ 50"
    assert got[2].options == ["yes", "no"]


def test_an_answered_item_is_recognised():
    got = GV.acceptance("1. Estimation: yes \n2. KPI Input: na")
    assert [a.answer for a in got] == ["yes", "na"]
    assert all(a.answered for a in got)


def test_a_partly_filled_form_counts_only_what_is_open():
    rows = [_row(status="In Progress")]
    rows[0]["QA ACC_Note"] = "1. Estimation: yes \n2. KPI Input : yes/no/na"
    a = GV.build(rows, [], DONE).acceptance_open
    assert a["asked"] == 2 and a["unanswered"] == 1


def test_the_same_form_on_many_tickets_counts_once_per_ticket():
    """Seventeen copies of an eight-item form is 136 things to answer."""
    rows = [_row(key=f"PM-{i}", status="In Progress") for i in range(3)]
    for r in rows:
        r["QA ACC_Note"] = FORM
    a = GV.build(rows, [], DONE).acceptance_open
    assert a["asked"] == 15 and a["unanswered"] == 15 and a["tickets"] == 3


def test_labels_are_left_in_their_own_language():
    got = GV.acceptance("7. Cắt quyền server dự án: yes/no")
    assert got[0].label == "Cắt quyền server dự án"


def test_a_ticket_with_no_form_contributes_nothing():
    a = GV.build([_row(status="In Progress")], [], DONE).acceptance_open
    assert a["asked"] == 0 and a["tickets"] == 0


# Selecting the process tickets — the rule that used to be "it has a key"

def _typed(key, issue_type):
    return {"Key": key, "Summary": "s", "Status": "To Do",
            "Issue Type": issue_type}


def test_management_types_are_selected_and_delivery_types_are_not():
    rows = [_typed("A-1", "PM Task"), _typed("A-2", "Planning Task"),
            _typed("A-3", "Story"), _typed("A-4", "Task"),
            _typed("A-5", "Bug")]
    picked, how = GV.select(rows, type_column="Issue Type")
    assert [r["Key"] for r in picked] == ["A-1", "A-2"]
    assert "Issue Type" in how


def test_a_keyed_delivery_row_is_not_process_work():
    """The regression this whole change exists for.

    Read out of a spreadsheet the delivery rows were unkeyed, so "has a key"
    happened to select only management. Read out of Jira every row is a real
    issue with a real key, and the old rule took the entire backlog.
    """
    rows = [_typed("COWORKLOCAL-2", "PM Task")]
    rows += [_typed(f"COWORKLOCAL-{i}", "Story") for i in range(18, 30)]
    picked, _ = GV.select(rows, type_column="Issue Type")
    assert [r["Key"] for r in picked] == ["COWORKLOCAL-2"]


def test_product_is_not_swept_in_by_default():
    """'Product Backlog Item' is delivery in Scrum trackers, so the word
    cannot be treated as management on its own."""
    rows = [_typed("A-1", "Product"), _typed("A-2", "Product Backlog Item")]
    picked, _ = GV.select(rows, type_column="Issue Type")
    assert picked == []


def test_an_explicit_type_list_overrides_the_default():
    rows = [_typed("A-1", "Product"), _typed("A-2", "Story")]
    picked, how = GV.select(rows, type_column="Issue Type",
                            types={"Product"})
    assert [r["Key"] for r in picked] == ["A-1"]
    assert "command line" in how


def test_an_explicit_type_is_matched_regardless_of_case():
    rows = [_typed("A-1", "PM TASK")]
    picked, _ = GV.select(rows, type_column="Issue Type",
                          types={"pm task"})
    assert len(picked) == 1


def test_no_issue_type_column_refuses_to_decide():
    """None is not 'nothing matched' - it tells the caller to fall back.

    Returning an empty list here would silently report 'no process tickets'
    for every export that happens not to name a type."""
    rows = [{"Key": "A-1", "Summary": "s", "Status": "To Do"}]
    picked, why = GV.select(rows, type_column=None)
    assert picked is None
    assert "no issue type" in why


def test_the_rule_used_is_always_reported():
    """A count of process tickets is unreadable without knowing how they
    were chosen: 17 and 190 are equally plausible from the same export."""
    for kwargs in ({"type_column": "Issue Type"},
                   {"type_column": "Issue Type", "types": {"Product"}},
                   {"type_column": None}):
        _, how = GV.select([], **kwargs)
        assert how and isinstance(how, str)


# Where the acceptance form is read from

def test_the_form_is_found_under_the_trackers_own_header():
    row = {"Key": "A-1", "Summary": "s", "Status": "To Do",
           "QA ACC_Note": "1. KPI Input : yes/no"}
    assert "KPI Input" in GV.acceptance_text(row)


def test_the_same_form_is_found_when_the_export_spells_it_differently():
    """A form that is present but unread reports zero open questions, which
    reads exactly like a form everybody answered."""
    for header in ("QA Acceptance", "QA ACC Note", "Acceptance Criteria"):
        row = {"Key": "A-1", header: "1. KPI Input : yes/no"}
        assert "KPI Input" in GV.acceptance_text(row), header


def test_a_row_with_no_form_yields_no_text():
    assert GV.acceptance_text({"Key": "A-1", "Description": "prose"}) == ""


def test_an_empty_form_column_does_not_shadow_a_filled_one():
    row = {"QA ACC_Note": "", "QA Acceptance": "1. KPI Input : yes/no"}
    assert "KPI Input" in GV.acceptance_text(row)


def test_mangled_text_is_repaired_on_the_way_in():
    """An em dash that travelled through cp1252 reaches the page as
    `â€"`. Repairing it where text enters means every consumer of a run
    gets it clean, rather than each one repairing its own copy."""
    from tracelink.adapters.tickets_tabular import repair

    assert repair("[Project code] â€“ Request review") == "[Project code] – Request review"


def test_text_that_was_never_mangled_survives_the_repair():
    from tracelink.adapters.tickets_tabular import repair

    assert repair("Cắt quyền server dự án") == "Cắt quyền server dự án"
