"""The Program <-> Project relationship, and the contention it makes computable.

Two groups of tests, and they are the two halves of one defect.

The first group pins the *identity* fix. A program's id used to contain the name
of the source system a document arrived from, so one program existed as
`excel:Program:1:DEFAULT` and `jira:Program:1:DEFAULT`, and the two source rows
of a single delivery project (invariant 7) hung off different ones. These tests
fail on that arrangement.

The second group pins the *arithmetic*, against the design document's own worked
examples. Those examples exist because each one corrects a formula that looked
right: replication instead of apportionment, the wrong denominator, a clamp
covering for a waterfall that was not one. Reproducing them here is what stops
the next edit quietly reintroducing any of it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.intelligence.contention import (
    Allocation,
    Demand,
    Window,
    apportion,
    assess,
    assess_periods,
    month_periods,
    normalize_person,
    pressure_by_project,
)
from app.units import ProgramUnits, WEEKDAY_CALENDAR, units_for

# --------------------------------------------------------------------------
# Program identity
# --------------------------------------------------------------------------


def test_a_program_id_carries_no_source_system():
    """The whole defect, in one assertion.

    `excel:` or `jira:` inside a program id means the same program gets one row
    per collector. The namespace is source-neutral precisely so it cannot.
    """
    from app.scope import PROGRAM_SOURCE, program_domain_id

    program_id = program_domain_id("DEFAULT")

    assert program_id.startswith(f"{PROGRAM_SOURCE}:")
    assert "excel" not in program_id
    assert "jira" not in program_id


def test_both_source_ids_of_one_project_resolve_to_one_program():
    """Invariant 7 at program granularity.

    `jira:Project:1:HRMS` and `excel:Project:1:HRMS` are one delivery project,
    so they belong to one program. Reading `projects.program_id` directly
    answered with whichever program the collector that wrote that row invented;
    `scope.program_for` resolves the pairing first.
    """
    from app.scope import program_for

    excel_side = program_for("excel:Project:1:HRMS")
    jira_side = program_for("jira:Project:1:HRMS")

    assert excel_side is not None
    assert excel_side == jira_side


def test_every_seeded_project_names_its_program():
    from app.scope import all_projects, find_program

    for project in all_projects():
        if project.program_id is None:
            continue
        assert find_program(project.program_id) is not None, (
                f"{project.canonical_id} points at program {project.program_id} "
                "that nothing describes"
        )


def test_projects_in_a_program_come_from_scope_not_from_the_table():
    from app.scope import program_domain_id, projects_in

    ids = {p.canonical_id for p in projects_in(program_domain_id("DEFAULT"))}

    assert "excel:Project:1:HRMS" in ids
    # The paired Jira id is not a second project in the program - it is the
    # same one, and listing it would double-count HRMS in every rollup.
    assert "jira:Project:1:HRMS" not in ids


def test_an_unknown_program_is_none_rather_than_a_guess():
    from app.scope import find_program, program_for

    assert find_program("program:Program:0:NOPE") is None
    assert program_for("excel:Project:1:DOESNOTEXIST") is None


def test_the_migration_maps_both_old_ids_onto_one_new_one():
    """The duplicate collapses, and the key is read from the data.

    `_target_id` takes the trailing key of the old id rather than knowing
    anything about either collector, which is why it needs no table of source
    names to maintain.
    """
    from scripts.migrate_programs import _target_id

    excel = _target_id("excel:Program:1:DEFAULT")
    jira = _target_id("jira:Program:1:DEFAULT")

    assert excel == jira == "program:Program:0:DEFAULT"
    # Already migrated: nothing to do, so the migration is idempotent.
    assert _target_id("program:Program:0:DEFAULT") is None


# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------


def test_an_effort_quantity_without_a_unit_is_refused():
    """Invariant I4. A default here would repeal it."""
    from app.units import UnitError, normalize_unit

    with pytest.raises(UnitError):
        normalize_unit(None)
    with pytest.raises(UnitError):
        normalize_unit("")
    with pytest.raises(UnitError):
        normalize_unit("furlongs")


def test_person_months_convert_by_the_program_factor_and_say_so():
    """The 20x error class. The factor is recorded, not implied."""
    at_20 = ProgramUnits(person_days_per_person_month=20)
    at_22 = ProgramUnits(person_days_per_person_month=22)

    assert at_20.to_effort_days(1, "人月").value == 20
    assert at_22.to_effort_days(1, "man-month").value == 22
    assert at_20.to_effort_days(1, "pm").factor_name == "person_days_per_person_month"


def test_percent_needs_a_window_and_will_not_pretend_otherwise():
    from app.units import UnitError

    with pytest.raises(UnitError):
        ProgramUnits().to_effort_days(50, "%")


def test_the_calendar_counts_golden_week_as_working_days_lost():
    """A five-day slip across Golden Week is not five working days."""
    from app.units import JP_CALENDAR

    # 2026-04-29 .. 2026-05-06 spans 昭和の日, 憲法記念日, みどりの日,
    # こどもの日 and a 振替休日.
    jp = JP_CALENDAR.working_days(date(2026, 4, 29), date(2026, 5, 6))
    plain = WEEKDAY_CALENDAR.working_days(date(2026, 4, 29), date(2026, 5, 6))

    assert jp < plain


def test_supply_is_discounted_and_names_the_discount():
    """Availability is explicit. An implicit 1.0 under-detects contention."""
    units = ProgramUnits(availability=0.8, calendar=WEEKDAY_CALENDAR)

    supply = units.supply_effort_days(20)

    assert supply.value == pytest.approx(16.0)
    assert supply.factor_name == "availability"


def test_three_concurrent_projects_cost_more_than_two():
    units = ProgramUnits(availability=1.0, calendar=WEEKDAY_CALENDAR)

    two = units.supply_effort_days(20, concurrent_projects=2)
    three = units.supply_effort_days(20, concurrent_projects=3)

    assert three.value < two.value


# --------------------------------------------------------------------------
# Contention - the design document's worked examples
# --------------------------------------------------------------------------

#: Factors that isolate the formula: full availability, no concurrency penalty,
#: plain weekdays. The document's checks assume supply 20 in a 20-day window, so
#: anything else under test here would be testing the discounts instead.
BARE = ProgramUnits(
    availability=1.0, concurrency_free_projects=99, calendar=WEEKDAY_CALENDAR
)
#: Exactly 20 weekdays.
WINDOW = Window(date(2026, 3, 2), date(2026, 3, 27))


def _demand(project_id: str, effort_days: float) -> Demand:
    return Demand(
        project_id=project_id,
        project_name=project_id,
        effort_days=effort_days,
        allocation_percent=effort_days / 20 * 100,
        overlap_days=20,
    )


def test_the_window_really_is_twenty_working_days():
    """The premise every check below rests on."""
    assert BARE.calendar.working_days(WINDOW.start, WINDOW.end) == 20


def test_mode_a_shares_the_excess_and_every_victim_shows_one_duration():
    """The document's Mode A check: E=10, each loses 5, Δ=6.7 for both.

    Identical durations are correct, not a bug: the per-project rate cancels,
    so `sᵢ ÷ (dᵢ / L)` reduces to `E · L / Dv`.
    """
    result = apportion(
        person="p", window=WINDOW, demands=[_demand("A", 15), _demand("B", 15)], units=BARE
    )

    assert result.demand_days == 30
    assert result.supply_days == 20
    assert result.excess_days == 10
    assert result.mode == "proportional"
    assert [s.effort_days for s in result.shortfalls] == [5.0, 5.0]
    assert all(s.delay_days == pytest.approx(6.67, abs=0.01) for s in result.shortfalls)


def test_mode_b_protects_the_top_and_the_victim_takes_the_whole_excess():
    """The document's Mode B check: sB=10, ΔB=13.3, A is not a victim.

    13.3 rather than 10 is the point of the corrected denominator: normalizing
    the excess by total supply instead of by victim demand understates the delay
    by `Dv / S`.
    """
    result = apportion(
        person="p",
        window=WINDOW,
        demands=[_demand("A", 15), _demand("B", 15)],
        units=BARE,
        priority=["A", "B"],
    )

    assert result.mode == "priority"
    assert [(s.project_id, s.effort_days) for s in result.shortfalls] == [("B", 10.0)]
    assert result.shortfalls[0].delay_days == pytest.approx(13.33, abs=0.01)


def test_the_overload_case_stays_inside_the_window_without_a_clamp():
    """A=25, B=5, supply 20, A ahead of B.

    The earlier formulation protected A's full 25 and produced 40 working days
    of delay inside a 20-day window, which needed a `min(E, Dv)` clamp to stay
    bounded. The waterfall makes A its own first victim, so `Σ sᵢ = E` holds and
    no clamp is reachable.
    """
    result = apportion(
        person="p",
        window=WINDOW,
        demands=[_demand("A", 25), _demand("B", 5)],
        units=BARE,
        priority=["A", "B"],
    )

    by_id = {s.project_id: s for s in result.shortfalls}
    assert by_id["A"].effort_days == pytest.approx(5.0)
    assert by_id["B"].effort_days == pytest.approx(5.0)
    assert by_id["A"].delay_days == pytest.approx(4.0, abs=0.01)
    assert by_id["B"].delay_days == pytest.approx(20.0, abs=0.01)
    assert sum(s.effort_days for s in result.shortfalls) == pytest.approx(
        result.excess_days
    )
    assert all(s.delay_days <= result.working_days for s in result.shortfalls)


def test_mode_b_is_not_mode_a_with_the_top_project_lifted_out():
    """A > B > C each demanding 10 against supply 20.

    Strict priority gives B its full 10 and C nothing. Protecting only A and
    sharing the remainder would take 5 from each of B and C - a different answer,
    and the one an order trustworthy enough to protect A does not support.
    """
    result = apportion(
        person="p",
        window=WINDOW,
        demands=[_demand("A", 10), _demand("B", 10), _demand("C", 10)],
        units=BARE,
        priority=["A", "B", "C"],
    )

    assert [(s.project_id, s.effort_days) for s in result.shortfalls] == [("C", 10.0)]


def test_the_excess_is_apportioned_not_replicated():
    """Invariant I5, and the failure it was written against.

    Three projects sharing an overloaded person must not produce more
    consequence than there was overload. Adding a project must not multiply it.
    """
    two = apportion(
        person="p", window=WINDOW, demands=[_demand("A", 15), _demand("B", 15)], units=BARE
    )
    three = apportion(
        person="p",
        window=WINDOW,
        demands=[_demand("A", 10), _demand("B", 10), _demand("C", 10)],
        units=BARE,
    )

    for result in (two, three):
        assert sum(s.effort_days for s in result.shortfalls) == pytest.approx(
            result.excess_days
        ), "a victim is absorbing more than its share of the excess"


def test_no_contention_when_supply_covers_demand():
    result = apportion(
        person="p", window=WINDOW, demands=[_demand("A", 5), _demand("B", 5)], units=BARE
    )

    assert result.contended is False
    assert result.excess_days == 0
    assert result.shortfalls == ()


def test_a_person_with_no_availability_reports_contention_without_a_duration():
    """`S = 0`. Every demand is a shortfall and no rate exists, so Δ is absent
    rather than infinite."""
    units = ProgramUnits(availability=1.0, calendar=WEEKDAY_CALENDAR)
    empty = Window(date(2026, 3, 7), date(2026, 3, 8))  # a weekend: zero working days

    result = apportion(
        person="p",
        window=empty,
        demands=[_demand("A", 5), _demand("B", 5)],
        units=units,
    )

    assert result.no_supply is True
    assert result.contended is True
    assert all(s.delay_days == 0 for s in result.shortfalls)


def test_a_project_with_no_demand_takes_no_shortfall():
    result = apportion(
        person="p",
        window=WINDOW,
        demands=[_demand("A", 25), _demand("Z", 0)],
        units=BARE,
    )

    assert "Z" not in {s.project_id for s in result.shortfalls}


# --------------------------------------------------------------------------
# Windows, and the three ways the old check was wrong
# --------------------------------------------------------------------------


def _alloc(person: str, project: str, pct: float, start: str, end: str) -> Allocation:
    return Allocation(
        person=person,
        project_id=project,
        project_name=project,
        allocation_percent=pct,
        window_start=date.fromisoformat(start),
        window_end=date.fromisoformat(end),
    )


def test_non_overlapping_allocations_are_not_a_conflict():
    """110% nominal, and nobody is asked to do two things at once.

    The old check summed `allocation_percent` with no dates at all, so this read
    as a 110% conflict. It is the false positive the windows remove.
    """
    allocations = [
        _alloc("Pham Hong D", "HRMS", 60, "2026-01-01", "2026-02-28"),
        _alloc("Pham Hong D", "EXPROJ", 50, "2026-03-01", "2026-04-30"),
    ]

    results = assess_periods(
        allocations, month_periods(date(2026, 1, 1), date(2026, 4, 30))
    )

    assert results == []


def test_simultaneous_allocations_under_a_hundred_percent_can_still_contend():
    """90% nominal over one window.

    The old check tested `> 100` and called this clean. A person is not 100%
    available to project work, so against a discounted supply it is a real, small
    shortfall - the false negative the availability factor removes.
    """
    allocations = [
        _alloc("My Nguyen", "HRMS", 50, "2026-03-01", "2026-03-31"),
        _alloc("My Nguyen", "SAIN", 40, "2026-03-01", "2026-03-31"),
    ]

    results = assess(allocations, Window(date(2026, 3, 1), date(2026, 3, 31)))

    assert len(results) == 1
    assert results[0].excess_days > 0
    assert {s.project_id for s in results[0].shortfalls} == {"HRMS", "SAIN"}


def test_a_person_on_one_project_is_never_contended():
    allocations = [_alloc("Hoach Bach", "SAIN", 70, "2026-01-01", "2026-04-30")]

    assert assess(allocations, Window(date(2026, 1, 1), date(2026, 4, 30))) == []


def test_a_pooled_window_averages_a_peak_away_and_monthly_periods_do_not():
    """Why the assessed window has to be a month, precisely.

    Pro-rata overlap already stops a long window from *inventing* contention:
    each allocation only contributes demand for the days it actually covers.
    What a long window does instead is **hide** contention, by averaging a spike
    against the slack either side of it. Here one person is fully committed to
    two projects for March and to nothing else all quarter: acute in March,
    invisible across the quarter.

    That is also why the period must be a month specifically rather than merely
    short - the overtime ceiling this model checks against is monthly, so a
    quarter's overtime compared against a month's limit would breach on
    arithmetic alone.
    """
    allocations = [
        _alloc("Tanaka", "HRMS", 100, "2026-03-01", "2026-03-31"),
        _alloc("Tanaka", "SAIN", 100, "2026-03-01", "2026-03-31"),
    ]
    quarter = Window(date(2026, 1, 1), date(2026, 3, 31))

    pooled = assess(allocations, quarter)
    per_month = assess_periods(allocations, month_periods(quarter.start, quarter.end))

    assert pooled == [], "a quarter-long window averages the March peak away"
    assert len(per_month) == 1, "the monthly assessment must still see it"
    assert per_month[0].window.start == date(2026, 3, 1)
    assert per_month[0].excess_days > 0


def test_a_long_window_does_not_invent_contention_from_sequential_work():
    """Pro-rata overlap, holding on its own.

    Two allocations that never coexist produce no excess whether they are
    assessed monthly or over the whole span, because each only claims the days it
    covers. The old check - summing `allocation_percent` with no dates at all -
    read this as 110% and flagged it.
    """
    allocations = [
        _alloc("Pham Hong D", "HRMS", 60, "2026-01-01", "2026-02-28"),
        _alloc("Pham Hong D", "EXPROJ", 50, "2026-03-01", "2026-04-30"),
    ]
    whole_span = Window(date(2026, 1, 1), date(2026, 4, 30))

    assert assess(allocations, whole_span) == []
    assert assess_periods(allocations, month_periods(whole_span.start, whole_span.end)) == []
    # And the nominal figure the old check used really is over 100.
    assert sum(a.allocation_percent for a in allocations) == 110


def test_month_periods_cover_the_span_exactly_once():
    periods = month_periods(date(2026, 1, 15), date(2026, 3, 10))

    assert [(p.start.isoformat(), p.end.isoformat()) for p in periods] == [
        ("2026-01-15", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
        ("2026-03-01", "2026-03-10"),
    ]


def test_one_person_spelled_two_ways_is_one_person():
    """A raw `group by resource_name` halves their load and finds no conflict."""
    assert normalize_person("Ｔｒａｎ　Ｑｕｏｃ　Ｂ") == normalize_person("Tran Quoc B")
    assert normalize_person("  tran  quoc  b ") == normalize_person("Tran Quoc B")

    allocations = [
        _alloc("Tran Quoc B", "HRMS", 80, "2026-03-01", "2026-03-31"),
        _alloc("Ｔｒａｎ　Ｑｕｏｃ　Ｂ", "EXPROJ", 50, "2026-03-01", "2026-03-31"),
    ]

    results = assess(allocations, Window(date(2026, 3, 1), date(2026, 3, 31)))

    assert len(results) == 1, "the two spellings were treated as two people"


def test_pressure_is_additive_across_periods():
    """`sᵢ` is an effort quantity, so totalling it is sound - unlike Δ."""
    allocations = [
        _alloc("Tran Quoc B", "HRMS", 80, "2026-02-01", "2026-04-30"),
        _alloc("Tran Quoc B", "EXPROJ", 50, "2026-02-01", "2026-04-30"),
    ]
    results = assess_periods(
        allocations, month_periods(date(2026, 2, 1), date(2026, 4, 30))
    )

    pressure = pressure_by_project(results)
    total = sum(s.effort_days for r in results for s in r.shortfalls)

    assert pressure["HRMS"] > 0 and pressure["EXPROJ"] > 0
    assert sum(pressure.values()) == pytest.approx(total, abs=0.01)
    assert pressure["HRMS"] > pressure["EXPROJ"], "the larger claim loses more"


def test_overtime_absorption_is_reported_against_the_statutory_ceiling():
    """A delay figure never travels without the assumption that produces it."""
    allocations = [
        _alloc("Tanaka", "HRMS", 100, "2026-03-01", "2026-03-31"),
        _alloc("Tanaka", "SAIN", 100, "2026-03-01", "2026-03-31"),
    ]

    result = assess(allocations, Window(date(2026, 3, 1), date(2026, 3, 31)))[0]

    assert result.absorption is not None
    assert result.absorption.overtime_hours > 0
    assert result.absorption.breaches_monthly_limit is True
    assert "overtime" in result.absorption.describe()


# --------------------------------------------------------------------------
# The program context, and what a project analysed without one reports
# --------------------------------------------------------------------------


def test_a_project_analysed_without_a_program_says_contention_is_unknown():
    """Not zero. A confident zero from an absence of evidence is the failure
    mode this flag exists to prevent."""
    from app.intelligence.context import build_context
    from app.intelligence.schedule.graph import build_graph
    from app.intelligence.schedule.impact import project_schedule

    graph = build_graph([], [])
    context = build_context(
        project_id="excel:Project:1:HRMS",
        as_of=date(2026, 3, 1),
        schedule=graph,
        impact=project_schedule(graph),
    )

    assert context.has_program_context is False
    assert context.contention_pressure_days == 0.0


def test_the_contention_rules_cannot_fire_without_program_context():
    """Every contention rule requires `has_program_context`, so a project
    analysed alone produces no contention finding at all."""
    from app.intelligence.rules.tables import DEFAULT_TABLE

    contention_rules = [
        r for r in DEFAULT_TABLE if r.category == "resource_risk"
    ]

    assert contention_rules, "the contention rules went missing"
    for rule in contention_rules:
        guards = [c for c in rule.when if c.field == "has_program_context"]
        assert guards, f"{rule.id} can fire without program context"


def test_the_rule_table_is_still_valid_with_the_contention_rules():
    """The table's own validator, including "no literal digits in a headline"."""
    from dataclasses import fields

    from app.intelligence.context import DeliveryContext
    from app.intelligence.rules.tables import DEFAULT_TABLE, validate_table

    known = {f.name for f in fields(DeliveryContext)} - {"notes"}

    assert validate_table(DEFAULT_TABLE, known_fields=known) == []


def test_units_are_program_scoped():
    """A client at 20 days per 人月 and a vendor at 22 must not be pooled."""
    from app.units import configure_program

    configure_program("program:Program:0:VENDOR", ProgramUnits(person_days_per_person_month=22))

    assert units_for("program:Program:0:DEFAULT").person_days_per_person_month == 20
    assert units_for("program:Program:0:VENDOR").person_days_per_person_month == 22


# --------------------------------------------------------------------------
# Creating a program and a project from the UI
# --------------------------------------------------------------------------


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    return TestClient(__import__("app.api.main", fromlist=["app"]).app)


@pytest.fixture(autouse=True)
def _clean_registry():
    """Empty the two registries around every test in this module.

    They are database tables in one shared test database, so without this a
    program created by one test is still there for the next - and the tests that
    count rows or assert a program is absent would pass or fail depending on
    which order pytest ran them in. Cleaned before *and* after, so a test that
    fails part-way through does not poison its successors.
    """
    from sqlalchemy import delete

    from app.db import session_scope
    from app.models.uploads import RegisteredProgram, RegisteredProject

    def wipe():
        try:
            with session_scope() as session:
                session.execute(delete(RegisteredProgram))
                session.execute(delete(RegisteredProject))
        except Exception:  # noqa: BLE001 - a table that does not exist yet is fine
            pass

    wipe()
    yield
    wipe()


def test_a_program_id_is_derived_from_the_name_never_accepted(client):
    """The form has no id field, and this is why.

    A typed id is how a source system's name got inside a program's identity.
    The route derives it, so a caller cannot reintroduce the defect by posting
    `{"id": "excel:Program:1:MINE"}`.
    """
    created = client.post("/api/programs", json={"name": "Cloud Migration FY27"})

    assert created.status_code == 201
    program_id = created.json()["program_id"]
    assert program_id.startswith("program:Program:0:")
    assert "excel" not in program_id and "jira" not in program_id


def test_creating_the_same_program_twice_does_not_make_two(client):
    """Two rows a person cannot tell apart on the list is worse than a rename."""
    first = client.post("/api/programs", json={"name": "Cloud Migration"})
    second = client.post("/api/programs", json={"name": "cloud   migration"})

    assert first.json()["program_id"] == second.json()["program_id"]
    assert first.json()["existed"] is False
    assert second.json()["existed"] is True

    listed = client.get("/api/programs").json()["programs"]
    matching = [p for p in listed if p["id"] == first.json()["program_id"]]
    assert len(matching) == 1


def test_a_created_program_is_listed_and_openable_before_any_ingest(client):
    """It has no `programs` row yet, and must not 404 or vanish.

    The `programs` table is materialized by a collector; `app/scope.py` is where
    a program is declared. Reading only the table would make the button that
    creates a program look like it had done nothing.
    """
    program_id = client.post("/api/programs", json={"name": "Greenfield"}).json()[
        "program_id"
    ]

    listed = {p["id"]: p for p in client.get("/api/programs").json()["programs"]}
    assert program_id in listed
    assert listed[program_id]["project_count"] == 0
    assert listed[program_id]["band"] == "no_data"

    rollup = client.get(f"/api/programs/{program_id}")
    assert rollup.status_code == 200
    assert rollup.json()["program"]["name"] == "Greenfield"


def test_a_program_nobody_declared_or_materialized_is_still_a_404(client):
    """The fallback must not turn every string into a program."""
    assert client.get("/api/programs/program:Program:0:NOPE").status_code == 404


def test_a_project_can_be_created_before_any_document_exists(client):
    created = client.post("/api/projects", json={"name": "Identity Platform"})

    assert created.status_code == 201
    body = created.json()
    assert body["canonical_id"] == "excel:Project:upload:identity-platform"
    assert body["program_id"] is None

    rows = {r["project_id"]: r for r in client.get("/api/portfolio").json()["projects"]}
    assert body["canonical_id"] in rows
    # Known, nothing ingested. `no_data`, never green.
    assert rows[body["canonical_id"]]["band"] == "no_data"


def test_a_created_project_lands_in_the_program_it_names(client):
    program_id = client.post("/api/programs", json={"name": "Greenfield"}).json()[
        "program_id"
    ]
    client.post(
        "/api/projects", json={"name": "Identity Platform", "program_id": program_id}
    )

    rollup = client.get(f"/api/programs/{program_id}").json()

    assert [p["name"] for p in rollup["projects"]] == ["Identity Platform"]
    assert rollup["program"]["project_count"] == 1


def test_a_project_naming_an_unknown_program_is_refused_not_invented(client):
    """A typo must not create a program. That is how a portfolio grows rows
    nobody meant, which is the whole defect one level up."""
    response = client.post(
        "/api/projects",
        json={"name": "Stray", "program_id": "program:Program:0:NOPE"},
    )

    assert response.status_code == 400
    assert "NOPE" in response.json()["detail"]
    assert not [
        p
        for p in client.get("/api/programs").json()["programs"]
        if p["id"] == "program:Program:0:NOPE"
    ]


def test_a_nameless_project_or_program_is_refused(client):
    assert client.post("/api/projects", json={"name": "   "}).status_code == 400
    assert client.post("/api/programs", json={"name": ""}).status_code == 400


def test_adding_a_project_by_name_then_uploading_for_it_is_one_project(client):
    """The two id derivations must not drift.

    The upload route and this route both turn a typed name into a canonical id.
    If their spellings differ, uploading a schedule for a project somebody
    already added fills in a *second* project beside it - the duplicate this
    module exists to prevent, arriving through the front door.
    """
    from app import scope

    created = client.post("/api/projects", json={"name": "Identity Platform"}).json()

    assert created["canonical_id"] == f"excel:Project:upload:{scope.slugify('Identity Platform')}"


def test_two_japanese_names_do_not_collapse_onto_one_project(client):
    """`slugify` strips non-ASCII, so both names would otherwise slug to the
    same constant and the second project would silently become the first."""
    first = client.post("/api/projects", json={"name": "工数管理"}).json()
    second = client.post("/api/projects", json={"name": "予算管理"}).json()

    assert first["canonical_id"] != second["canonical_id"]
    assert first["existed"] is False and second["existed"] is False
