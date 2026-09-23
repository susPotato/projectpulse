"""Registering a repository: the documentation lock, and what a failure leaves.

Two things here are worth a test each, and neither is the clone.

**The lock**, because it is the whole reason this is a registration rather
than a text field. A repository with no documentation tree has to be refused
in terms somebody can act on, and the refusal has to leave no row - the
failure mode `CLAUDE.md` section 0a spent a defect on, where a rejected
import still left a project sitting on the portfolio.

**The credential rules**, because "blank keeps, explicit clears" is exactly
the kind of convention that quietly inverts during a refactor and is then
discovered by a token going somewhere it should not.

Nothing here clones over the network. `tracelink.source.resolve` reads a
local path in place, so a temporary directory exercises the same code path
that a clone lands on - and a suite that needs GitHub fails for the wrong
reason on a train.
"""

from __future__ import annotations

import pytest

from app.ingest.sources.git import source, store


@pytest.fixture(autouse=True)
def _pipeline(monkeypatch):
    """Point at the vendored pipeline, which is where the image finds it.

    `REPO_ROOT`, not the sibling checkout: the copy under `tracelink/` is what
    `COPY tracelink ./tracelink` ships and what `PULSE_TRACELINK_HOME=/app`
    resolves to, so testing against it is testing what runs. It also means a
    clone of this repository alone has everything these tests need.

    `settings` is a frozen dataclass whose fields are read from the
    environment when the class body executes, so patching the attribute is not
    possible and patching the environment is too late. Putting the directory
    on the path is what `tracelink_home` does anyway.
    """
    from app.config import REPO_ROOT

    if not (REPO_ROOT / "tracelink" / "source.py").exists():
        pytest.skip("run `python -m scripts.vendor_tracelink --apply` first")
    monkeypatch.syspath_prepend(str(REPO_ROOT))
    return REPO_ROOT


def _repo(tmp_path, *, docs="docs", files=("guide.md",)):
    """A directory shaped like a checkout, with or without documents."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.py").write_text("def go():\n    pass\n", encoding="utf-8")
    if docs:
        tree = root / docs
        tree.mkdir(parents=True)
        for name in files:
            (tree / name).write_text("# a document\n", encoding="utf-8")
    return root


# The lock


def test_a_repository_without_a_docs_tree_is_refused(tmp_path):
    root = _repo(tmp_path, docs=None)
    with pytest.raises(source.DocsMissing) as caught:
        source.fetch(repo_url=str(root), docs_path="docs")
    assert "no 'docs' directory" in str(caught.value)


def test_the_refusal_names_the_directories_that_are_there(tmp_path):
    """A refusal that only says 'not found' sends somebody to guess.

    The workbook upload settled this: name what was looked for and what is
    actually present, so the next attempt is informed.
    """
    root = _repo(tmp_path, docs=None)
    with pytest.raises(source.DocsMissing) as caught:
        source.fetch(repo_url=str(root), docs_path="docs")
    assert "src" in str(caught.value)


def test_a_docs_tree_with_no_markdown_is_refused(tmp_path):
    """An asset directory is not a documentation tree.

    The adapters read .md, so accepting this would register a project whose
    every documentation stage then reports nothing - which reads as a bug in
    the pipeline rather than as an empty directory.
    """
    root = _repo(tmp_path, files=())
    (root / "docs" / "logo.png").write_bytes(b"\x89PNG")
    with pytest.raises(source.DocsMissing) as caught:
        source.fetch(repo_url=str(root), docs_path="docs")
    assert "no markdown" in str(caught.value)


def test_a_docs_tree_is_accepted_and_counted(tmp_path):
    root = _repo(tmp_path, files=("a.md", "b.md"))
    (root / "docs" / "deep").mkdir()
    (root / "docs" / "deep" / "c.md").write_text("# c\n", encoding="utf-8")
    out = source.fetch(repo_url=str(root), docs_path="docs")
    assert out["doc_count"] == 3
    assert out["docs_dir"] == (root / "docs").resolve()


def test_the_tree_can_live_anywhere_the_registration_says(tmp_path):
    """A monorepo does not put them at the root, and the lock is per project."""
    root = _repo(tmp_path, docs="backend/documentation")
    out = source.fetch(repo_url=str(root), docs_path="backend/documentation")
    assert out["doc_count"] == 1


def test_a_docs_path_cannot_escape_the_repository(tmp_path):
    """`docs_path` arrives from a form, so containment is checked.

    Without this the server would walk and count a directory outside the
    checkout, and report it as the project's documentation.
    """
    root = _repo(tmp_path)
    with pytest.raises(source.DocsMissing) as caught:
        source.fetch(repo_url=str(root), docs_path="../../etc")
    assert "outside the repository" in str(caught.value)


def test_a_windows_style_path_resolves_to_the_same_tree(tmp_path):
    root = _repo(tmp_path, docs="backend/docs")
    assert store.normalise_docs_path("backend\\docs") == "backend/docs"
    out = source.fetch(repo_url=str(root),
                       docs_path=store.normalise_docs_path("backend\\docs"))
    assert out["doc_count"] == 1


def test_skip_directories_are_not_counted(tmp_path):
    """A vendored tree is not the team's documentation, and walking one on a
    large repository is minutes rather than seconds."""
    root = _repo(tmp_path, files=("real.md",))
    nested = root / "docs" / "node_modules" / "pkg"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("# vendor\n", encoding="utf-8")
    out = source.fetch(repo_url=str(root), docs_path="docs")
    assert out["doc_count"] == 1


# What a fetch reports


def test_an_unreadable_source_is_reported_not_raised_raw(tmp_path):
    with pytest.raises(source.RepoUnavailable) as caught:
        source.fetch(repo_url=str(tmp_path / "nowhere"), docs_path="docs")
    assert "not a directory" in str(caught.value)


def test_a_checkout_reports_its_revision(tmp_path):
    """A real checkout yields a real commit.

    The commit is the point of deferring to `tracelink.source`: it is what
    `moved()` compares against later to answer whether a run is stale. Built
    here rather than borrowed from a sibling repository, so the assertion is
    about this code and not about what happens to be cloned next door.
    """
    import shutil
    import subprocess

    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")

    root = _repo(tmp_path, files=("guide.md",))
    for args in (
        ["init", "-q", "-b", "main"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"],
        ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "first"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True,
                       capture_output=True)

    out = source.fetch(repo_url=str(root), docs_path="docs")
    assert len(out["commit"]) == 40
    assert out["dirty"] is False

    # An uncommitted change is reported, never refused - analysing work in
    # flight is ordinary; forgetting you did is not.
    (root / "src" / "main.py").write_text("changed = 1\n", encoding="utf-8")
    assert source.fetch(repo_url=str(root), docs_path="docs")["dirty"] is True


# The credential


def test_a_token_is_lent_to_the_pipeline_and_taken_back(monkeypatch):
    """The pipeline reads a token from the environment by design.

    Here it comes from a database row instead, so the environment is only
    how it gets there - and one project's credential must not still be in
    scope while another project's fetch runs.
    """
    import os

    name = source._tracelink_source().TOKEN_ENV[0]
    monkeypatch.delenv(name, raising=False)
    with source._token_in_env("secret-value"):
        assert os.environ[name] == "secret-value"
    assert name not in os.environ


def test_an_ambient_token_is_withheld_from_a_public_repository(monkeypatch):
    """An unrelated token set on the host has no business being sent to a
    repository this registration says is public."""
    import os

    name = source._tracelink_source().TOKEN_ENV[0]
    monkeypatch.setenv(name, "somebody-elses")
    with source._token_in_env(""):
        assert name not in os.environ
    assert os.environ[name] == "somebody-elses"
