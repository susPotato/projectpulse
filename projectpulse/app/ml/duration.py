"""The duration classifier. Advisory, and structurally unable to be otherwise.

Wraps `omaradly/jira-task-duration-classifier` from Hugging Face - a
scikit-learn `Pipeline` (TF-IDF over the issue text, one-hot categoricals,
scaled numerics, logistic regression) that sorts a task into one of three
duration bands.

Invariant 5 of the project is that this is advisory only, and the shape of this
module is what enforces it rather than a comment asking nicely:

**It returns a band, never a number.** `DurationAdvice.label` is one of
`Short` / `Standard` / `Long-running`. The pipeline also produces class
probabilities; those are deliberately reduced to a coarse `confidence` band
before leaving this module. A probability is a number a model invented, and a
number a model invented must never reach a page whose entire claim is that its
figures are arithmetic on dates a human typed.

**`intelligence/schedule/` must never import it.** A predicted band has no
business anywhere near the forward pass: the projection is arithmetic over
stated dates, and mixing an estimate into it would make `propagated_days`
unfalsifiable. `tests/test_ml.py` walks the AST of every module under
`app/intelligence/` and fails if one imports `app.ml`, because this is the kind
of rule that gets broken by someone being helpful.

**Everything is optional.** `scikit-learn`, `joblib` and `pandas` are an extra,
and the trained artefact is a file this repo does not carry. Absent any of them
the classifier is simply `None` and no caller has to care - the same discipline
as the language model.

⚠️ **The inputs are partial, and that is the honest reason this is advisory.**
The model was trained on Jira issue metadata: description, priority, issue type,
project category, labels, votes, watches. A spreadsheet row has a title, dates
and an owner. So for Excel-sourced tasks the prediction rests almost entirely on
the title text, and `DurationAdvice.basis` says how many real features were
available so a reader can discount it. Do not present a band from a
two-feature row as though it were a model's considered view.

⚠️ **The published artefact does not load on Python 3.14, and cannot be made
to.** It was pickled by scikit-learn 1.6.1 and unpickling it under 1.9 dies on
`sklearn.compose._column_transformer._RemainderColsList`, a private class that
no longer exists. Installing 1.6.1 to match is not an option here: it ships no
wheel for 3.14, so pip falls back to compiling it and there is no C toolchain.

So the classifier needs **Python 3.12 or 3.13**, where 1.6.x wheels exist. On
this project's 3.14 venv it downloads fine and reports itself unloadable, which
is why `try_load` returns a reason rather than a bare None.

Retraining locally to match the installed scikit-learn was considered and
rejected: the repo's `training/model_training.py` reads `final_cleaned.csv`,
which is **not published** - what ships is a 100-row `final_cleaned_sample.csv`.
A model refitted on 100 rows across three classes would carry the same name and
far less meaning, which is worse than an honest absence.

Fetch the artefact when you want it (needs `huggingface_hub`):

    python -m scripts.fetch_model
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

#: The bands the model predicts, in order. Quoted from the model card:
#: Short is 3 days or less, Standard is more than 3 up to 15, Long-running is
#: more than 15. The thresholds are recorded here for a reader; nothing in this
#: module uses them, because using them would mean turning a band into days.
DURATION_BANDS = ("Short", "Standard", "Long-running")

#: The local filename, and separately the path *inside* the Hugging Face repo.
#: They are not the same string - the artefact lives under `models/` there - and
#: conflating them is a 404 from `hf_hub_download`, which is how this was found.
ARTEFACT_NAME = "duration_logistic_regression_classifier.joblib"
HF_REPO = "omaradly/jira-task-duration-classifier"
HF_FILENAME = f"models/{ARTEFACT_NAME}"

#: The exact feature frame the pipeline was fitted on, in order, transcribed
#: from the model's own `api/main.py`. Order matters to a `ColumnTransformer`
#: selecting by name only in so far as every name must be present - but keeping
#: the declared order makes a diff against the source obvious.
FEATURE_COLUMNS = (
    "summary_text",
    "description_text",
    "total_text",
    "priority_name",
    "issuetype_name",
    "project_key",
    "project_category_name",
    "created_year",
    "created_month",
    "issue_priority",
    "summary_char_count",
    "summary_word_count",
    "description_char_count",
    "description_word_count",
    "has_description",
    "labels_count",
    "has_assignee",
    "votes_votes",
    "watches_watch_count",
    "summary_to_description_word_ratio",
)

#: What the model fills in when we have nothing. Its own serving defaults, so an
#: absent field lands where the training data's absent fields landed.
UNKNOWN = "unknown"


@dataclass(frozen=True)
class TaskFacts:
    """What we can honestly say about a task, from either source.

    A plain dataclass rather than an ORM row so this module stays importable
    and testable without a database, and so the mapping from our domain to the
    model's vocabulary is written down in one visible place.
    """

    title: str
    description: str = ""
    issue_type: str = UNKNOWN
    priority: str = UNKNOWN
    project_key: str = UNKNOWN
    project_category: str = UNKNOWN
    created: date | None = None
    labels_count: int = 0
    has_assignee: bool = False
    votes: float = 0.0
    watches: float = 0.0

    def real_feature_count(self) -> int:
        """How many inputs are actually ours rather than a default.

        Reported on the advice so a band derived from a title alone can be
        discounted. Counting is crude on purpose - a score would invite being
        read as a confidence, which it is not.
        """
        return sum(
            [
                bool(self.title.strip()),
                bool(self.description.strip()),
                self.issue_type != UNKNOWN,
                self.priority != UNKNOWN,
                self.project_category != UNKNOWN,
                self.created is not None,
                self.labels_count > 0,
                self.has_assignee,
                self.votes > 0,
                self.watches > 0,
            ]
        )


@dataclass(frozen=True)
class DurationAdvice:
    """A band, a coarse confidence, and how much was actually known.

    Deliberately carries no probability and no day count. Everything a caller
    could turn back into a number has already been removed.
    """

    label: str
    #: `low` / `medium` / `high`, banded from the winning class probability.
    confidence: str
    #: How many of the model's inputs were real, out of ten.
    basis: str

    @property
    def is_weak(self) -> bool:
        """Whether this should be shown with a caveat or not at all."""
        return self.confidence == "low"


def _band_confidence(probability: float) -> str:
    """A probability reduced to one of three words, and not returned.

    The banding is the point, not the boundaries: a caller that could read
    `0.61` would eventually render it, and a rendered model probability is a
    number the product cannot defend.
    """
    if probability >= 0.66:
        return "high"
    if probability >= 0.45:
        return "medium"
    return "low"


def build_features(facts: TaskFacts) -> dict[str, object]:
    """The model's feature frame for one task.

    Transcribed from the model's serving code, including the parts that look
    odd: `issue_priority` is a *string* joining issue type and priority, not an
    ordinal, and the word ratio falls back to the summary word count when there
    is no description rather than to zero.
    """
    summary = facts.title or ""
    description = facts.description or ""
    summary_words = len(summary.split())
    description_words = len(description.split())

    return {
        "summary_text": summary,
        "description_text": description,
        "total_text": f"{summary} {description}",
        "priority_name": facts.priority,
        "issuetype_name": facts.issue_type,
        "project_key": facts.project_key,
        "project_category_name": facts.project_category,
        "created_year": facts.created.year if facts.created else 0,
        "created_month": facts.created.month if facts.created else 0,
        "issue_priority": f"{facts.issue_type}__{facts.priority}",
        "summary_char_count": len(summary),
        "summary_word_count": summary_words,
        "description_char_count": len(description),
        "description_word_count": description_words,
        "has_description": int(description_words > 0),
        "labels_count": facts.labels_count,
        "has_assignee": int(facts.has_assignee),
        "votes_votes": facts.votes,
        "watches_watch_count": facts.watches,
        "summary_to_description_word_ratio": (
            summary_words / description_words if description_words else summary_words
        ),
    }


class DurationClassifier:
    """A loaded pipeline, and the only thing allowed to touch it."""

    def __init__(self, model) -> None:
        self._model = model

    def advise(self, facts: TaskFacts) -> DurationAdvice | None:
        """One band for one task, or None if the model could not answer.

        Never raises. A model that fails on a row is an advisory feature that
        declined, not an error worth failing a page over - the caller renders
        nothing and everything else on the page is unaffected.
        """
        try:
            import pandas as pd

            frame = pd.DataFrame([build_features(facts)], columns=list(FEATURE_COLUMNS))
            label = str(self._model.predict(frame)[0])

            confidence = "medium"
            if hasattr(self._model, "predict_proba"):
                probabilities = self._model.predict_proba(frame)[0]
                confidence = _band_confidence(float(max(probabilities)))

            return DurationAdvice(
                label=label,
                confidence=confidence,
                basis=f"{facts.real_feature_count()} of 10 inputs known",
            )
        except Exception as exc:  # noqa: BLE001 - advisory features never raise
            log.warning("duration classifier declined: %s", exc)
            return None


def model_path(explicit: str | Path | None = None) -> Path:
    """Where the artefact is expected to be."""
    if explicit:
        return Path(explicit)

    from app.config import settings

    if settings.duration_model_path:
        return Path(settings.duration_model_path)
    return Path(settings.model_root) / ARTEFACT_NAME


def try_load(
    path: str | Path | None = None,
) -> tuple[DurationClassifier | None, str]:
    """The classifier and, when there isn't one, why not in a sentence.

    The reason exists because "no duration model" is a misleading thing to print
    when the file is sitting right there. The published artefact was pickled by
    scikit-learn 1.6.1, and loading it under 1.9 fails on a private class that
    no longer exists - a real failure with a specific fix, which a caller can
    only pass on if it is told.
    """
    target = model_path(path)
    if not target.exists():
        return None, f"no artefact at {target}"

    try:
        import joblib
    except ImportError:
        return None, 'joblib is not installed (pip install -e ".[ml]")'

    try:
        return DurationClassifier(joblib.load(target)), ""
    except Exception as exc:  # noqa: BLE001
        # Whatever it is, it must not take the app down - this is advisory.
        log.warning("could not load duration model %s: %s", target, exc)
        return None, f"{target.name} would not load: {exc}{_version_hint(exc)}"


def _version_hint(exc: Exception) -> str:
    """Name the cause when the failure is the known scikit-learn mismatch.

    Without this the message is a private class name and a reader has to
    rediscover the whole trail: the artefact was pickled by 1.6.1, 1.6.x has no
    wheel for Python 3.14, and compiling it needs a toolchain that is not there.
    """
    # Fires on both shapes this takes: the private-class error when a *newer*
    # scikit-learn unpickles it, and a plain missing-module error on 3.14 where
    # the pinned version cannot be installed at all.
    message = str(exc)
    if "_RemainderColsList" not in message and "sklearn" not in message:
        return ""

    import sys

    return (
        f". This artefact was pickled by scikit-learn 1.6.x; you have "
        f"{_sklearn_version()} on Python "
        f"{sys.version_info.major}.{sys.version_info.minor}. 1.6.x publishes no "
        "wheel for 3.14, so matching it means running the classifier on Python "
        "3.12 or 3.13. See app/ml/duration.py."
    )


def _sklearn_version() -> str:
    try:
        import sklearn

        return sklearn.__version__
    except ImportError:  # pragma: no cover - only reached without the extra
        return "no scikit-learn"


def load_classifier(path: str | Path | None = None) -> DurationClassifier | None:
    """The classifier, or None - which is the ordinary case.

    None for any reason at all: no `joblib`, no `scikit-learn`, no artefact on
    disk, an artefact that will not unpickle. Every caller treats None as "this
    project has no duration advice", which is a complete and correct state.
    """
    return try_load(path)[0]
