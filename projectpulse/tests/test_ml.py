"""Guards for the advisory duration classifier.

Two kinds of test here, and the first matters more than the second.

The **boundary** test walks the import graph and fails if anything under
`app/intelligence/` reaches `app.ml`. That is invariant 5, and it is the kind of
rule a docstring cannot hold: mixing a predicted band into the forward pass
would make `propagated_days` unfalsifiable, and it is exactly the change someone
makes while trying to be helpful.

The **adapter** tests build a real scikit-learn pipeline over the model's own
feature columns, pickle it with joblib, and load it back through
`load_classifier`. That proves the path works without needing the published
artefact, which this repo does not carry. It cannot prove the published
pipeline expects these columns - only that ours does, transcribed from its
serving code.
"""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest

from app.ml.duration import (
    DURATION_BANDS,
    FEATURE_COLUMNS,
    DurationAdvice,
    TaskFacts,
    build_features,
    load_classifier,
)

APP = Path(__file__).resolve().parent.parent / "app"


# --------------------------------------------------------------------------
# The boundary. Invariant 5.
# --------------------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_the_intelligence_layer_never_imports_the_model():
    """The forward pass is arithmetic over stated dates, and must stay that way.

    Walked rather than trusted. A predicted band inside `propagated_days` would
    turn a number a PM can check on paper into one they cannot, and it would not
    look like a bug from the outside - the page would still render a figure.
    """
    offenders = []
    for module in sorted((APP / "intelligence").rglob("*.py")):
        for name in _imported_modules(module):
            if name == "app.ml" or name.startswith("app.ml."):
                offenders.append(f"{module.relative_to(APP.parent)} imports {name}")

    assert offenders == [], "invariant 5 broken:\n" + "\n".join(offenders)


def test_nothing_in_the_ml_package_imports_the_intelligence_layer():
    """The other direction, so the dependency cannot be inverted either.

    If `app.ml` imported the schedule engine, a later refactor could route the
    forward pass through here and the test above would still pass.
    """
    offenders = []
    for module in sorted((APP / "ml").rglob("*.py")):
        for name in _imported_modules(module):
            if name.startswith("app.intelligence"):
                offenders.append(f"{module.relative_to(APP.parent)} imports {name}")

    assert offenders == []


# --------------------------------------------------------------------------
# The feature frame
# --------------------------------------------------------------------------


def test_the_feature_frame_matches_the_models_own_serving_code():
    """Column names and order, transcribed from the model's api/main.py.

    A missing column is a hard failure inside the fitted ColumnTransformer, so
    this is checked here where the message is legible.
    """
    features = build_features(TaskFacts(title="Environment Setup"))

    assert tuple(features) == FEATURE_COLUMNS


def test_issue_priority_is_a_string_join_not_an_ordinal():
    """The oddest transcribed detail, and the easiest to 'fix' wrongly.

    The pipeline one-hot encodes this, so replacing it with a number would
    produce a category the model has never seen rather than an error.
    """
    features = build_features(
        TaskFacts(title="x", issue_type="Bug", priority="High")
    )

    assert features["issue_priority"] == "Bug__High"


def test_the_word_ratio_falls_back_to_the_summary_count_not_to_zero():
    """Also transcribed rather than reasoned about. A spreadsheet row never has
    a description, so this branch is the one our data always takes."""
    features = build_features(TaskFacts(title="one two three"))

    assert features["description_word_count"] == 0
    assert features["summary_to_description_word_ratio"] == 3


def test_a_spreadsheet_row_reports_how_little_was_known():
    """The honest reason this is advisory.

    A title and an owner is two of ten inputs. The band is reported with that
    count so a reader can discount it instead of treating it as considered.
    """
    facts = TaskFacts(title="Environment Setup", has_assignee=True)

    assert facts.real_feature_count() == 2


def test_a_jira_issue_reports_more():
    facts = TaskFacts(
        title="Environment Setup",
        description="Provision the staging environment.",
        issue_type="Task",
        priority="High",
        project_category="Platform",
        created=date(2026, 2, 16),
        labels_count=2,
        has_assignee=True,
    )

    assert facts.real_feature_count() == 8


# --------------------------------------------------------------------------
# Loading and predicting
# --------------------------------------------------------------------------


def test_an_absent_artefact_disables_advice_rather_than_failing(tmp_path):
    """The ordinary state. Nothing is installed, nothing is downloaded."""
    assert load_classifier(tmp_path / "nothing.joblib") is None


def _tiny_pipeline():
    """A real sklearn pipeline over the model's real column names.

    Deliberately fitted on the same feature frame `build_features` produces, so
    what is exercised is our column contract and not a toy.
    """
    sklearn = pytest.importorskip("sklearn", reason="scikit-learn is an optional extra")
    pytest.importorskip("pandas")
    from sklearn.compose import ColumnTransformer
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    assert sklearn  # imported for the skip, used for nothing else

    import pandas as pd

    rows = [
        build_features(TaskFacts(title="tiny fix typo")),
        build_features(TaskFacts(title="build the whole reporting subsystem")),
        build_features(TaskFacts(title="medium sized piece of work here")),
    ]
    frame = pd.DataFrame(rows, columns=list(FEATURE_COLUMNS))
    labels = list(DURATION_BANDS)

    pre = ColumnTransformer(
        [
            ("text", TfidfVectorizer(), "total_text"),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                ["priority_name", "issuetype_name", "issue_priority"],
            ),
            ("num", StandardScaler(), ["summary_word_count", "summary_char_count"]),
        ],
        remainder="drop",
    )
    pipeline = Pipeline(
        [("pre", pre), ("clf", LogisticRegression(max_iter=200))]
    )
    pipeline.fit(frame, labels)
    return pipeline


def test_the_adapter_round_trips_a_real_pipeline(tmp_path):
    """Fit, pickle, load through our loader, predict. The whole path."""
    joblib = pytest.importorskip("joblib")

    artefact = tmp_path / "duration.joblib"
    joblib.dump(_tiny_pipeline(), artefact)

    classifier = load_classifier(artefact)
    assert classifier is not None

    advice = classifier.advise(TaskFacts(title="tiny fix typo", has_assignee=True))
    assert isinstance(advice, DurationAdvice)
    assert advice.label in DURATION_BANDS
    assert advice.confidence in ("low", "medium", "high")
    assert "of 10 inputs known" in advice.basis


def test_the_advice_carries_no_number_a_caller_could_render(tmp_path):
    """Invariant 5 at the type level.

    The pipeline produces class probabilities. If one reached the page it would
    be a figure a model invented, on a page whose whole claim is that its
    figures are arithmetic over dates a human typed. So the band is a word, the
    confidence is a word, and there is nothing else numeric to reach for.
    """
    joblib = pytest.importorskip("joblib")

    artefact = tmp_path / "duration.joblib"
    joblib.dump(_tiny_pipeline(), artefact)

    classifier = load_classifier(artefact)
    assert classifier is not None
    advice = classifier.advise(TaskFacts(title="tiny fix typo"))

    assert advice is not None
    numeric = [
        name
        for name, value in vars(advice).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    assert numeric == [], f"advice exposes a number: {numeric}"


def test_a_model_that_raises_declines_rather_than_propagating(tmp_path):
    """An advisory feature must never cost a page.

    A version mismatch between the sklearn that pickled the artefact and the one
    installed shows up as an exception from `predict`, and the correct outcome is
    no advice at all.
    """

    class Broken:
        def predict(self, frame):
            raise RuntimeError("feature names do not match")

    from app.ml.duration import DurationClassifier

    assert DurationClassifier(Broken()).advise(TaskFacts(title="x")) is None
