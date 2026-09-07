"""Domain ids are the basis of idempotent re-runs, so they must be exact."""

from __future__ import annotations

import pytest

from app.ids import domain_id, parse_domain_id, source_of


def test_shape():
    assert domain_id("jira", "Issue", 1, 10023) == "jira:Issue:1:10023"


def test_is_a_pure_function():
    """Re-running a sync must regenerate identical ids, or upserts become inserts."""
    assert domain_id("excel", "Task", 2, "WBS-114") == domain_id(
        "excel", "Task", 2, "WBS-114"
    )


def test_sources_do_not_collide():
    assert domain_id("jira", "Task", 1, "X-1") != domain_id("excel", "Task", 1, "X-1")


def test_connections_do_not_collide():
    assert domain_id("jira", "Issue", 1, 7) != domain_id("jira", "Issue", 2, 7)


def test_composite_keys():
    assert domain_id("jira", "Changelog", 1, 99887, "status") == (
        "jira:Changelog:1:99887:status"
    )


def test_a_colon_in_a_key_does_not_split_the_id():
    """A human-typed Task ID may contain a colon; two tasks must not merge."""
    a = domain_id("excel", "Task", 1, "PHASE:1")
    b = domain_id("excel", "Task", 1, "PHASE", "1")
    assert a != b
    assert parse_domain_id(a)[3] == ["PHASE:1"]
    assert parse_domain_id(b)[3] == ["PHASE", "1"]


def test_round_trip():
    value = domain_id("excel", "Task", 3, "WBS-1")
    assert parse_domain_id(value) == ("excel", "Task", 3, ["WBS-1"])


def test_source_of():
    assert source_of("jira:Issue:1:10023") == "jira"


def test_rejects_missing_key():
    with pytest.raises(ValueError, match="at least one primary key"):
        domain_id("excel", "Task", 1)


def test_rejects_empty_component():
    with pytest.raises(ValueError, match="must not be empty"):
        domain_id("excel", "Task", 1, "")


def test_rejects_malformed():
    with pytest.raises(ValueError, match="not a domain id"):
        parse_domain_id("jira:Issue")
