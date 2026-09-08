"""Guards for the risk rating lookup. Pure, no database."""

from __future__ import annotations

from app.risks.matrix import IMPACTS, LIKELIHOODS, cells, rating_for


def test_a_known_cell_matches_the_mockup_data():
    # Layout/fpt-pm-risk.html's own "Scope Creep" row: Likely x Severe -> Very High.
    assert rating_for("Likely", "Severe") == "Very High"


def test_the_lowest_corner_is_the_lowest_rating():
    assert rating_for("Rare", "Insignificant") == "Very Low"


def test_the_highest_corner_is_the_highest_rating():
    assert rating_for("Almost Certain", "Severe") == "Very High"


def test_an_unset_likelihood_has_no_rating():
    assert rating_for(None, "Severe") is None


def test_an_unset_impact_has_no_rating():
    assert rating_for("Likely", None) is None


def test_an_unknown_value_has_no_rating_rather_than_a_guess():
    assert rating_for("Somewhat Likely", "Severe") is None


def test_cells_covers_the_whole_grid_exactly_once():
    all_cells = cells()
    assert len(all_cells) == len(LIKELIHOODS) * len(IMPACTS)
    assert len({(lik, imp) for lik, imp, _ in all_cells}) == len(all_cells)


def test_every_cell_agrees_with_rating_for():
    for likelihood, impact, rating in cells():
        assert rating_for(likelihood, impact) == rating
