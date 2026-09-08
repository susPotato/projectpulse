"""The probability x impact heat-map a risk's rating is read off.

Pure lookup, not PM input: a person types a likelihood and an impact; the
rating badge shown everywhere else - the table, the drawer, the heat-map - is
this table applied to that pair, never typed independently. Design of record:
`Layout/fpt-pm-risk.html`'s `MATRIX_DATA`, reproduced here rather than
duplicated per screen so there is one grid to argue with, not one per
renderer.
"""

from __future__ import annotations

LIKELIHOODS: tuple[str, ...] = ("Almost Certain", "Likely", "Possible", "Unlikely", "Rare")
IMPACTS: tuple[str, ...] = ("Insignificant", "Minor", "Moderate", "Major", "Severe")
RATINGS: tuple[str, ...] = ("Very Low", "Low", "Medium", "High", "Very High")

#: Row = likelihood, column = impact (same order as `IMPACTS`).
_MATRIX: dict[str, tuple[str, str, str, str, str]] = {
    "Almost Certain": ("Low", "Medium", "High", "Very High", "Very High"),
    "Likely": ("Low", "Medium", "High", "High", "Very High"),
    "Possible": ("Very Low", "Low", "Medium", "High", "High"),
    "Unlikely": ("Very Low", "Very Low", "Low", "Medium", "Medium"),
    "Rare": ("Very Low", "Very Low", "Very Low", "Low", "Low"),
}


def rating_for(likelihood: str | None, impact: str | None) -> str | None:
    """The rating for one (likelihood, impact) pair, or `None` if either is unset.

    `None` rather than a guessed default: a risk with an assessed likelihood
    but no chosen impact (or the reverse) has no rating yet, and showing one
    would claim a judgement nobody made.
    """
    if likelihood not in _MATRIX or impact not in IMPACTS:
        return None
    return _MATRIX[likelihood][IMPACTS.index(impact)]


def cells() -> list[tuple[str, str, str]]:
    """Every (likelihood, impact, rating) triple, in the grid's own row-major order.

    What the heat-map renders: 25 cells, each already carrying its rating, so
    a caller only has to overlay how many risks landed in it.
    """
    return [
        (likelihood, impact, _MATRIX[likelihood][i])
        for likelihood in LIKELIHOODS
        for i, impact in enumerate(IMPACTS)
    ]
