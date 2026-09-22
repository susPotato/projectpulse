"""The Jira connection test: what it refuses to attempt, and what it says.

Most of these are about refusals. The endpoint makes *this server* fetch a
URL somebody typed, so the interesting behaviour is the requests it declines
to make - a test suite that only covered the happy path would leave the
request-forgery guard unexercised, which is the one thing here that has to
work every time.
"""

from __future__ import annotations

import base64
import json
import urllib.error

import pytest

from app.ingest.sources.jira import connect


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """The real pacing between attempts exists for a rate limiter that is
    not present in a test. Left in, this suite sleeps for half a minute."""
    monkeypatch.setattr(connect, "PACE", 0)


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve every name to a public address.

    The guard's own tests below use real classification on literal
    addresses; these are about what `probe` reports, and a suite that needs
    the internet to check an error message fails for the wrong reason on a
    train.
    """
    def fake(host, port, **kw):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake)


# What it refuses to fetch


def test_a_bare_host_becomes_https(public_dns):
    assert connect.normalise_site("acme.atlassian.net") == "https://acme.atlassian.net"


def test_a_trailing_slash_is_dropped(public_dns):
    assert connect.normalise_site("https://acme.atlassian.net/") == "https://acme.atlassian.net"


def test_http_is_refused():
    """A token sent over http is readable by anything in between."""
    with pytest.raises(connect.ConnectionRefused) as exc:
        connect.normalise_site("http://acme.atlassian.net")
    assert "https" in str(exc.value)


def test_an_empty_site_asks_for_one():
    with pytest.raises(connect.ConnectionRefused):
        connect.normalise_site("  ")


@pytest.mark.parametrize("site", [
    "https://localhost",
    "https://127.0.0.1",
    "https://[::1]",
    "https://169.254.169.254",   # cloud metadata
    "https://10.0.0.5",
    "https://192.168.1.10",
])
def test_this_servers_own_network_is_refused(site):
    """`169.254.169.254` is the metadata endpoint; the rest are internal.
    Fetching any of them on request is how a form like this becomes a
    credential leak."""
    with pytest.raises(connect.ConnectionRefused) as exc:
        connect.normalise_site(site)
    assert "own network" in str(exc.value)


def test_a_name_that_does_not_resolve_says_so():
    with pytest.raises(connect.ConnectionRefused) as exc:
        connect.normalise_site("https://no-such-host.invalid")
    assert "does not resolve" in str(exc.value)


def test_a_missing_token_is_refused_before_any_request(public_dns):
    with pytest.raises(connect.ConnectionRefused):
        connect.probe("acme.atlassian.net", "a@b.c", "   ")


# Which credential scheme is used


def test_an_email_selects_basic_auth():
    header, scheme = connect._auth("you@company.com", "tok")
    assert header.startswith("Basic ")
    assert base64.b64decode(header.split()[1]).decode() == "you@company.com:tok"
    assert "Cloud" in scheme


def test_no_email_selects_a_bearer_token():
    """Server and Data Center issue a PAT with no email to pair it with."""
    header, scheme = connect._auth("", "tok")
    assert header == "Bearer tok"
    assert "Server" in scheme


# What it reports


def _urlopen(monkeypatch, *, body=None, error=None):
    def fake(request, timeout=None):
        if error is not None:
            raise error

        class R:
            def read(self):
                return json.dumps(body).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(connect.urllib.request, "urlopen", fake)


def test_a_working_credential_names_the_account(monkeypatch, public_dns):
    _urlopen(monkeypatch, body={"accountId": "5b1", "displayName": "Tri L",
                                "emailAddress": "tri@example.com"})
    got = connect.probe("acme.atlassian.net", "tri@example.com", "tok")
    assert got.ok is True
    assert got.account["accountId"] == "5b1"
    assert "Tri L" in got.detail


def test_a_401_names_the_scheme_that_was_actually_tried(monkeypatch, public_dns):
    """Not whether an email was typed. Once the probe tries both, somebody
    who filled in an email still gets a bearer attempt, and telling them to
    check their email when bearer is what answered sends them the wrong
    way."""
    _urlopen(monkeypatch, error=urllib.error.HTTPError(
        "u", 401, "Unauthorized", {}, None))

    bearer = connect.probe("acme.atlassian.net", "", "tok")
    assert "bearer token" in bearer.detail and "bearer" in bearer.scheme

    basic = connect.probe("acme.atlassian.net", "a@b.c", "tok")
    assert "Basic auth" in basic.detail and "basic" in basic.scheme


def test_a_401_on_basic_points_at_both_halves(monkeypatch, public_dns):
    _urlopen(monkeypatch, error=urllib.error.HTTPError(
        "u", 401, "Unauthorized", {}, None))
    got = connect.probe("acme.atlassian.net", "a@b.c", "tok")
    assert "revoked" in got.detail and "both halves" in got.detail


def test_a_404_blames_the_site_not_the_credential(monkeypatch, public_dns):
    _urlopen(monkeypatch, error=urllib.error.HTTPError("u", 404, "NF", {}, None))
    got = connect.probe("acme.atlassian.net", "a@b.c", "tok")
    assert "never checked" in got.detail and "site address" in got.detail


def test_a_403_says_the_credential_was_fine(monkeypatch, public_dns):
    _urlopen(monkeypatch, error=urllib.error.HTTPError("u", 403, "F", {}, None))
    assert "valid" in connect.probe("acme.atlassian.net", "a@b.c", "t").detail


def test_html_instead_of_json_is_explained(monkeypatch, public_dns):
    """Pointing at the web front door rather than the API is a common typo
    and produces a parse error that means nothing on its own."""
    def fake(request, timeout=None):
        class R:
            def read(self):
                return b"<!doctype html>"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(connect.urllib.request, "urlopen", fake)
    got = connect.probe("acme.atlassian.net", "a@b.c", "tok")
    assert got.ok is False and "site address" in got.detail
    assert "/jiradc" in got.detail


# The credential never appears in what comes back


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_no_failure_message_contains_the_token(monkeypatch, public_dns, status):
    secret = "s3cr3t-token-value"
    _urlopen(monkeypatch, error=urllib.error.HTTPError("u", status, "x", {}, None))
    got = connect.probe("acme.atlassian.net", "a@b.c", secret)
    assert secret not in json.dumps(got.as_dict())


def test_a_network_failure_message_contains_no_token(monkeypatch, public_dns):
    secret = "s3cr3t-token-value"
    _urlopen(monkeypatch, error=urllib.error.URLError("connection refused"))
    got = connect.probe("acme.atlassian.net", "a@b.c", secret)
    assert secret not in json.dumps(got.as_dict())


def test_a_success_payload_carries_no_token(monkeypatch, public_dns):
    secret = "s3cr3t-token-value"
    _urlopen(monkeypatch, body={"accountId": "1", "displayName": "X"})
    got = connect.probe("acme.atlassian.net", "a@b.c", secret)
    assert secret not in json.dumps(got.as_dict())


# v4-in-v6 wrappers, both directions


def test_a_nat64_address_is_judged_by_the_ipv4_inside_it(monkeypatch):
    """A NAT64 resolver returns `64:ff9b::<v4>`, which Python calls private.
    Judging the wrapper refused `acme.atlassian.net` on any IPv6-only
    network - found by a test that resolved a real host."""
    def fake(host, port, **kw):
        return [(23, 1, 6, "", ("64:ff9b::5db8:d822", port, 0, 0))]

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake)
    assert connect.normalise_site("acme.atlassian.net") == "https://acme.atlassian.net"


def test_a_v4_mapped_loopback_is_still_loopback(monkeypatch):
    """The same wrapper in the other direction: `::ffff:127.0.0.1` reads as
    an ordinary v6 address while pointing at this machine."""
    def fake(host, port, **kw):
        return [(23, 1, 6, "", ("::ffff:127.0.0.1", port, 0, 0))]

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake)
    with pytest.raises(connect.ConnectionRefused) as exc:
        connect.normalise_site("sneaky.example.com")
    assert "own network" in str(exc.value)


def test_a_nat64_wrapped_private_address_is_still_private(monkeypatch):
    def fake(host, port, **kw):
        return [(23, 1, 6, "", ("64:ff9b::a00:5", port, 0, 0))]

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake)
    with pytest.raises(connect.ConnectionRefused):
        connect.normalise_site("sneaky.example.com")


# A context path is part of the base, a page route is not


@pytest.mark.parametrize("pasted", [
    "https://insight.example.com/jiradc/projects/COWORKLOCAL/issues/COWORKLOCAL-14?filter=allopenissues",
    "https://insight.example.com/jiradc/browse/COWORKLOCAL-14",
    "https://insight.example.com/jiradc/rest/api/2/myself",
    "https://insight.example.com/jiradc/",
])
def test_a_data_center_context_path_survives(pasted, monkeypatch):
    """Jira Data Center is commonly mounted under `/jiradc` or `/jira`.
    Taking "scheme plus host" as the base drops the one path segment that
    matters and asks a web server for an API it does not host - which is
    exactly what happened the first time this was used for real."""
    def fake(host, port, **kw):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr(connect.socket, "getaddrinfo", fake)
    assert connect.normalise_site(pasted) == "https://insight.example.com/jiradc"


def test_the_earliest_page_marker_wins(public_dns):
    """`/jira/software/projects/AB/boards/1` holds two markers. Cutting at
    `/projects/` first leaves `/jira/software`, a context path that does not
    exist."""
    got = connect.normalise_site(
        "https://acme.atlassian.net/jira/software/projects/AB/boards/1")
    assert got == "https://acme.atlassian.net"


# Trying the combinations rather than guessing one


def test_a_data_center_pairing_is_found_even_when_an_email_was_typed(monkeypatch, public_dns):
    """Somebody filling in every field should not be told their credential
    is bad. Cloud wants Basic on /3; Data Center wants Bearer on /2."""
    seen = []

    def fake(request, timeout=None):
        seen.append((request.full_url, request.headers.get("Authorization", "")))
        if "/rest/api/2/" in request.full_url and request.headers[
                "Authorization"].startswith("Bearer"):
            class R:
                def read(self):
                    return json.dumps({"name": "triltm5", "key": "triltm5"}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return R()
        raise urllib.error.HTTPError(request.full_url, 401, "no", {}, None)

    monkeypatch.setattr(connect.urllib.request, "urlopen", fake)
    got = connect.probe("https://insight.example.com/jiradc", "a@b.c", "tok")
    assert got.ok is True
    assert "bearer" in got.scheme and "/rest/api/2" in got.scheme
    assert got.account["name"] == "triltm5"


def test_a_failing_test_lists_what_it_tried(monkeypatch, public_dns):
    _urlopen(monkeypatch, error=urllib.error.HTTPError("u", 404, "NF", {}, None))
    got = connect.probe("https://insight.example.com/jiradc", "a@b.c", "tok")
    assert got.ok is False
    assert "Tried:" in got.detail and got.detail.count("->") >= 2


def test_an_unreachable_host_stops_after_one_attempt(monkeypatch, public_dns):
    """No other pairing does better against a host that is not answering."""
    calls = []

    def fake(request, timeout=None):
        calls.append(request.full_url)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(connect.urllib.request, "urlopen", fake)
    got = connect.probe("https://insight.example.com/jiradc", "a@b.c", "tok")
    assert got.ok is False
    assert len(calls) == 1


def test_a_challenge_page_is_not_reported_as_a_refused_credential(monkeypatch, public_dns):
    """The first real use of this hit Cloudflare: every pairing came back
    403 with `<!DOCTYPE html>`, including one sent with a deliberately fake
    token. Calling that "the credential is valid but refused" sends somebody
    hunting for a fault in a token nothing ever looked at."""
    class Err(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", 403, "Forbidden", {}, None)

        def read(self, n=-1):
            return b"<!DOCTYPE html><html><head><title>Just a moment...</title>"

    monkeypatch.setattr(connect.urllib.request, "urlopen",
                        lambda r, timeout=None: (_ for _ in ()).throw(Err()))
    got = connect.probe("https://insight.example.com/jiradc", "a@b.c", "tok")
    assert got.ok is False
    assert "never checked" in got.detail
    assert "bot challenge or proxy" in got.detail
    assert "valid" not in got.detail
    assert "blocked before Jira" in got.detail


def test_a_real_api_403_still_reads_as_a_permission_problem(monkeypatch, public_dns):
    """A JSON 403 from Jira itself means what it always meant."""
    class Err(urllib.error.HTTPError):
        def __init__(self):
            super().__init__("u", 403, "Forbidden", {}, None)

        def read(self, n=-1):
            return b'{"errorMessages":["no"]}'

    monkeypatch.setattr(connect.urllib.request, "urlopen",
                        lambda r, timeout=None: (_ for _ in ()).throw(Err()))
    got = connect.probe("https://acme.atlassian.net", "a@b.c", "tok")
    assert "valid" in got.detail


# What the API carries for a project


def _search(monkeypatch, issues, total=None):
    def fake(request, timeout=None):
        class R:
            def read(self):
                return json.dumps({
                    "total": total if total is not None else len(issues),
                    "issues": issues,
                }).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return R()

    monkeypatch.setattr(connect.urllib.request, "urlopen", fake)


def test_a_field_nobody_fills_is_reported_at_zero(monkeypatch, public_dns):
    """An empty column is the finding. Hiding it would report the API as
    richer than the instance actually is."""
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {"created": "2026-01-01", "status": {"name": "Open"},
                                   "assignee": None, "duedate": None}},
    ])
    got = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")
    by = {f["name"]: f["filled"] for f in got["fields"]}
    assert by["created"] == 1
    assert by["assignee"] == 0
    assert by["duedate"] == 0


def test_change_history_is_counted(monkeypatch, public_dns):
    """The export carried none at all, so any history is new information."""
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {}, "changelog": {"histories": [{}, {}]}},
        {"key": "AB-2", "fields": {}, "changelog": {"histories": [{}]}},
    ])
    got = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")
    assert got["histories"] == 3


def test_the_total_is_the_projects_size_not_the_sample(monkeypatch, public_dns):
    _search(monkeypatch, [{"key": "AB-1", "fields": {}}], total=173)
    got = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")
    assert got["total"] == 173 and got["sampled"] == 1


def test_a_missing_project_key_is_refused_before_any_request(public_dns):
    with pytest.raises(connect.ConnectionRefused):
        connect.preview("https://insight.example.com/jiradc", "", "tok", "  ")


def test_a_preview_carries_no_token(monkeypatch, public_dns):
    secret = "s3cr3t-token-value"
    _search(monkeypatch, [{"key": "AB-1", "fields": {"created": "x"}}])
    got = connect.preview("https://insight.example.com/jiradc", "", secret, "AB")
    assert secret not in json.dumps(got)


# Can the spreadsheet be retired?


def test_jira_carrying_the_export_block_is_reported(monkeypatch, public_dns):
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {
            "description": "PO: HoachBV\nBA: FSG\nDeveloper: FSG",
            "assignee": {"displayName": "Tri Le Tran Minh"}}},
    ])
    got = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")
    assert got["only_in_export"]["with_export_block"] == 1
    assert got["only_in_export"]["assignees"] == {"Tri Le Tran Minh": 1}


def test_an_empty_jira_description_means_the_block_is_only_in_the_export(
        monkeypatch, public_dns):
    """The decision this feeds: those roles and the `Ghi chu` names have no
    field in Jira, so if the description is empty they exist nowhere else."""
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {"description": None}},
        {"key": "AB-2", "fields": {"description": ""}},
    ])
    e = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")["only_in_export"]
    assert e["described"] == 0 and e["with_export_block"] == 0


def test_a_description_without_the_block_still_counts_as_described(
        monkeypatch, public_dns):
    """Ordinary prose is not the hand-maintained block, and conflating them
    would report the export as redundant when it is not."""
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {"description": "Refactor the provider layer."}},
    ])
    e = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")["only_in_export"]
    assert e["described"] == 1 and e["with_export_block"] == 0


def test_a_cloud_document_format_description_is_still_searched(
        monkeypatch, public_dns):
    """Cloud returns ADF, Data Center returns a string. Only presence of
    the block matters, so the structure is flattened rather than parsed."""
    _search(monkeypatch, [
        {"key": "AB-1", "fields": {"description": {
            "type": "doc",
            "content": [{"type": "paragraph",
                         "content": [{"type": "text", "text": "Developer: FSG"}]}]}}},
    ])
    e = connect.preview("https://insight.example.com/jiradc", "", "tok", "AB")["only_in_export"]
    assert e["with_export_block"] == 1


# Linking a Jira project to a delivery project


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.base import Base

    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, future=True)() as handle:
        yield handle


@pytest.fixture()
def sealed(monkeypatch):
    """A real Fernet, so the round trip is exercised rather than mocked."""
    import os

    from app.llm import keys

    monkeypatch.setenv(keys.SECRET_ENV, "x" * 64)
    monkeypatch.delenv(getattr(keys, "PREVIOUS_SECRET_ENV", "PULSE_PREVIOUS_SECRET_KEY"),
                       raising=False)
    assert os.environ[keys.SECRET_ENV]


def test_a_saved_connection_normalises_the_site(db, sealed, public_dns):
    """What is stored is a base the collector can use, not the ticket page
    somebody pasted. Validating on the way in means every later reader is
    already correct."""
    from app.ingest.sources.jira import store

    row = store.save(db, project_id="excel:Project:1:X",
                     site="https://insight.example.com/jiradc/browse/AB-1",
                     email="", project_key="ab", token="tok")
    assert row.site == "https://insight.example.com/jiradc"
    assert row.project_key == "COWORKLOCAL".replace("COWORKLOCAL", "AB")


def test_the_token_round_trips_and_is_never_stored_in_the_clear(db, sealed, public_dns):
    from app.ingest.sources.jira import store

    row = store.save(db, project_id="p", site="https://acme.atlassian.net",
                     email="a@b.c", project_key="AB", token="super-secret")
    assert "super-secret" not in row.ciphertext
    assert "super-secret" not in row.hint
    assert store.token_for(row) == "super-secret"


def test_editing_without_a_token_keeps_the_stored_one(db, sealed, public_dns):
    """Somebody changing the project key should not have to paste their
    credential again, and must not be able to blank it by leaving it be."""
    from app.ingest.sources.jira import store

    store.save(db, project_id="p", site="https://acme.atlassian.net",
               email="", project_key="AB", token="keep-me")
    db.flush()
    again = store.save(db, project_id="p", site="https://acme.atlassian.net",
                       email="new@b.c", project_key="AB", token="")
    assert store.token_for(again) == "keep-me"
    assert again.email == "new@b.c"


def test_a_connection_still_needs_a_project_and_a_key(db, sealed, public_dns):
    """A token is optional - a link with none is a real state. The two
    identifiers are not: without them the row binds nothing to anything.

    This replaces an earlier rule that a new connection must carry a
    token. That was right while `Collect` was the only consumer and wrong
    once a server that cannot reach Jira needed to hold the binding.
    """
    from app.ingest.sources.jira import store

    with pytest.raises(ValueError):
        store.save(db, project_id="", site="https://acme.atlassian.net",
                   email="", project_key="AB", token="t")
    with pytest.raises(ValueError):
        store.save(db, project_id="p", site="https://acme.atlassian.net",
                   email="", project_key="", token="t")


def test_a_listing_never_carries_a_credential(db, sealed, public_dns):
    from app.ingest.sources.jira import store

    store.save(db, project_id="p", site="https://acme.atlassian.net",
               email="", project_key="AB", token="super-secret")
    db.flush()
    rows = store.listing(db)
    assert len(rows) == 1
    assert "super-secret" not in json.dumps(rows)
    assert "ciphertext" not in rows[0]


def test_many_projects_each_keep_their_own_connection(db, sealed, public_dns):
    """A portfolio has many of both and they do not line up one to one."""
    from app.ingest.sources.jira import store

    store.save(db, project_id="p1", site="https://a.atlassian.net",
               email="", project_key="AB", token="t1")
    store.save(db, project_id="p2", site="https://b.atlassian.net",
               email="", project_key="CD", token="t2")
    db.flush()
    assert len(store.listing(db)) == 2
    assert [r["project_key"] for r in store.listing(db, "p2")] == ["CD"]


def test_one_project_can_draw_on_two_jira_projects(db, sealed, public_dns):
    from app.ingest.sources.jira import store

    store.save(db, project_id="p", site="https://a.atlassian.net",
               email="", project_key="AB", token="t")
    store.save(db, project_id="p", site="https://a.atlassian.net",
               email="", project_key="CD", token="t")
    db.flush()
    assert len(store.listing(db, "p")) == 2


def test_saving_without_a_secret_key_says_so_rather_than_500(monkeypatch):
    """A deployment that has not opted in is not a bad request, and it is
    not an unhandled error either. Losing the reason to a 500 is how this
    first failed: the page showed `Unexpected token 'I'` - a JSON parse
    error about the words "Internal Server Error" - and the real cause,
    an unset PULSE_SECRET_KEY, never reached the screen."""
    from fastapi.testclient import TestClient

    from app.api.main import app
    from app.llm import keys

    monkeypatch.delenv(keys.SECRET_ENV, raising=False)
    monkeypatch.setenv("PULSE_ADMIN_TOKEN", "t0ken")

    client = TestClient(app)
    r = client.post(
        "/api/jira/connections",
        headers={"X-Pulse-Admin-Token": "t0ken"},
        json={"project_id": "p", "site": "https://acme.atlassian.net",
              "email": "", "project_key": "AB", "token": "x"},
    )
    assert r.status_code == 503
    body = r.json()
    assert keys.SECRET_ENV in body["detail"]
    assert "scripts.secret" in body["detail"]


def test_a_link_with_no_token_is_allowed_and_says_why_it_cannot_fetch(db, sealed, public_dns):
    """A server Jira refuses has no use for a credential. Binding a key to
    a delivery project is all `/api/jira/ingest` needs, and a token on a
    host that could never use it is a liability with no benefit."""
    from app.ingest.sources.jira import store

    row = store.save(db, project_id="p", site="https://acme.atlassian.net",
                     email="", project_key="AB", token="")
    db.flush()
    assert row.ciphertext == ""
    with pytest.raises(store.NoCredential) as exc:
        store.token_for(row)
    assert "jira_push" in str(exc.value)


def test_adding_a_token_to_a_link_later_still_works(db, sealed, public_dns):
    from app.ingest.sources.jira import store

    store.save(db, project_id="p", site="https://acme.atlassian.net",
               email="", project_key="AB", token="")
    db.flush()
    row = store.save(db, project_id="p", site="https://acme.atlassian.net",
                     email="", project_key="AB", token="later")
    assert store.token_for(row) == "later"
