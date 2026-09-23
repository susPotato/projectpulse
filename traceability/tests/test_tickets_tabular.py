

def test_a_column_is_found_by_the_name_that_means_it():
    from tracelink.adapters.tickets_tabular import column_named
    assert column_named(["Key", "Summary", "Issue Type"], "issue_type") == "Issue Type"


def test_the_precise_alias_wins_over_a_vaguer_one_earlier_in_the_sheet():
    """This export carries twenty-odd headers containing 'Type', all empty,
    before the 'Issue Type' that holds the value. Scanning the sheet in
    order would return `Risk Type`."""
    from tracelink.adapters.tickets_tabular import column_named
    headers = ["Risk Type", "Enabler Type", "Type", "Issue Type"]
    assert column_named(headers, "issue_type") == "Issue Type"


def test_a_column_that_is_not_there_is_none_not_a_guess():
    from tracelink.adapters.tickets_tabular import column_named
    assert column_named(["Key", "Summary"], "issue_type") is None
