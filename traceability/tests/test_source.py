"""Where the code came from, and whether a run is still about it."""

from __future__ import annotations

import subprocess

import pytest

from tracelink import source as SRC


def git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False)


@pytest.fixture
def repo(tmp_path):
    """A real one-commit repository."""
    root = tmp_path / "repo"
    root.mkdir()
    git("init", "-q", cwd=root)
    git("config", "user.email", "t@example.com", cwd=root)
    git("config", "user.name", "t", cwd=root)
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "-A", cwd=root)
    git("commit", "-qm", "one", cwd=root)
    return root


# Telling a URL from a path


@pytest.mark.parametrize("spec", [
    "https://github.com/a/b.git", "git@github.com:a/b.git",
    "ssh://git@host/a/b", "git://host/a/b", "some/repo.git",
])
def test_these_are_urls_to_clone(spec):
    assert SRC.looks_like_url(spec) is True


def test_an_existing_path_is_never_a_url(tmp_path):
    """A directory that exists wins, whatever it is called - otherwise a
    folder named `repo.git` gets cloned from itself."""
    odd = tmp_path / "repo.git"
    odd.mkdir()
    assert SRC.looks_like_url(str(odd)) is False


# Reading a revision


def test_a_checkout_reports_its_commit(repo):
    rev = SRC.revision_of(repo)
    assert rev.known and len(rev.commit) == 40
    assert rev.dirty is False


def test_an_uncommitted_change_is_reported_not_refused(repo):
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert SRC.revision_of(repo).dirty is True


def test_a_directory_with_no_repository_is_not_a_revision(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    rev = SRC.revision_of(plain)
    assert not rev.known
    assert "not a git repository" in rev.describe()


def test_an_untracked_drop_inside_a_checkout_borrows_no_commit(repo):
    """git answers from the nearest enclosing repository, which is a
    different question. A zip unpacked inside a checkout and never added is
    not versioned by it, and taking that commit would stamp the run with a
    revision describing somebody else's code."""
    drop = repo / "vendor_drop"
    drop.mkdir()
    (drop / "thing.py").write_text("y = 1\n", encoding="utf-8")
    assert not SRC.revision_of(drop).known


def test_a_tracked_subdirectory_does_use_the_repository_commit(repo):
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("z = 1\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "two", cwd=repo)
    assert SRC.revision_of(sub).known


def test_dirt_elsewhere_in_a_monorepo_does_not_dirty_this_directory(repo):
    sub = repo / "pkg"
    sub.mkdir()
    (sub / "mod.py").write_text("z = 1\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "two", cwd=repo)
    (repo / "app.py").write_text("changed\n", encoding="utf-8")

    assert SRC.revision_of(repo).dirty is True
    assert SRC.revision_of(sub).dirty is False


# Resolving a source


def test_a_local_path_is_read_in_place(repo):
    path, rev = SRC.resolve(str(repo))
    assert path == repo
    assert rev.known


def test_a_missing_path_says_what_to_pass(tmp_path):
    with pytest.raises(SRC.SourceError) as exc:
        SRC.resolve(str(tmp_path / "nope"))
    assert "git URL" in str(exc.value)


# Has the code moved since the run?


def test_a_run_of_the_same_commit_has_not_moved(repo):
    rev = SRC.revision_of(repo)
    assert SRC.moved(rev.as_dict(), repo) == ""


def test_a_new_commit_is_reported_as_movement(repo):
    before = SRC.revision_of(repo).as_dict()
    (repo / "app.py").write_text("x = 3\n", encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-qm", "two", cwd=repo)

    why = SRC.moved(before, repo)
    assert "repository has moved" in why
    assert before["commit"][:12] in why


def test_new_uncommitted_work_is_reported_as_movement(repo):
    before = SRC.revision_of(repo).as_dict()
    (repo / "app.py").write_text("x = 4\n", encoding="utf-8")
    assert "uncommitted" in SRC.moved(before, repo)


def test_a_run_that_recorded_no_revision_is_never_called_stale(repo):
    """A zip drop cannot be stale in this sense, and saying so on every
    check is how the line that matters gets ignored."""
    assert SRC.moved(None, repo) == ""
    assert SRC.moved({"commit": ""}, repo) == ""


def test_a_source_that_stopped_being_a_repository_is_not_movement(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert SRC.moved({"commit": "a" * 40}, plain) == ""


# Private repositories


def test_no_token_means_no_auth_argument(monkeypatch):
    for name in SRC.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    assert SRC._auth_args("https://github.com/a/b.git") == []


def test_a_token_is_sent_as_a_header_not_in_the_url(monkeypatch):
    """A token in the URL is written into `.git/config` by the clone and
    stays there - it then shows up in `git remote -v`, in any error
    quoting the remote, and in whatever backs that directory up."""
    import base64

    monkeypatch.setenv("TRACELINK_GIT_TOKEN", "ghp_secret")
    args = SRC._auth_args("https://github.com/a/b.git")
    assert args[0] == "-c"
    assert args[1].startswith("http.extraHeader=Authorization: Basic ")
    decoded = base64.b64decode(args[1].split("Basic ")[1]).decode()
    assert decoded == "x-access-token:ghp_secret"


def test_an_ssh_url_carries_no_http_header(monkeypatch):
    """ssh authenticates with a key; an HTTP header would do nothing and
    would put the token on the command line for no reason."""
    monkeypatch.setenv("TRACELINK_GIT_TOKEN", "ghp_secret")
    assert SRC._auth_args("git@github.com:a/b.git") == []


def test_the_first_token_variable_set_is_the_one_used(monkeypatch):
    for name in SRC.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    assert "fallback" in SRC._token()
    monkeypatch.setenv("TRACELINK_GIT_TOKEN", "preferred")
    assert SRC._token() == "preferred"


def test_a_private_repo_failure_says_it_might_be_private(monkeypatch, tmp_path):
    """GitHub answers the same way for a private repository and one that
    does not exist, so the message has to name both."""
    for name in SRC.TOKEN_ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SRC.SourceError) as exc:
        SRC.resolve("https://github.com/nobody/no-such-repo-xyz-123.git",
                    cache=tmp_path)
    message = str(exc.value)
    assert "private" in message
    assert "TRACELINK_GIT_TOKEN" in message
