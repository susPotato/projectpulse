"""Collector: a real Jira over HTTP, raw rows out.

The twin of `replay.collect`, which reads the same payloads from disk. That
module's docstring says a live connection *would* page
`GET /rest/api/N/search?expand=changelog`; this is that connection, and it
writes byte-identical rows - one `RawJiraIssues` per issue, one
`RawJiraChangelogs` per history entry, with `input` carrying the issue id
because the per-issue changelog endpoint does not repeat it.

Everything downstream is untouched. The extractor, the convertor and the
precision model were always real code driving off the raw table; the only
thing that was ever faked is where the JSON came from.

**Incremental by default, because a weekly refresh is the point.** The JQL
carries `updated >= <watermark>`, so the second run costs one page instead
of re-downloading a project. The watermark is read from the raw table
itself rather than kept in a second place that can disagree with it.

**Paced, because a corporate Jira sits behind a rate limiter.** Measured on
the instance this was built against: one request answers, four in a burst
earn a 429 and then challenge pages. Pages are requested one at a time with
a gap, which makes a full first collection slow and every later one cheap -
the right way round.

**What it does not do.** It does not decide which project to collect, store
a credential, or run on a timer. Those are decisions with owners; this is
the part that fetches.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.ingest.sources.jira import connect
from app.models.raw import RawJiraChangelogs, RawJiraIssues

log = logging.getLogger("pulse.jira.live")

#: Issues per request.
#:
#: 25, not the 100 Jira allows, because the limit that bites is not Jira's.
#: Measured against a WAF-fronted Data Center instance, after idling long
#: enough to clear the throttle:
#:
#:     GET /myself                          -> 401  (reached Jira)
#:     GET /search?maxResults=5             -> 401  (reached Jira)
#:     GET /search?maxResults=100&changelog -> 429  (refused)
#:
#: The first two prove the credential path and the URL are fine; only the
#: heavy request is refused, and `expand=changelog` on a hundred issues is
#: a large response. 25 is the size the connection preview uses and has
#: never been refused. Eight pages for a 190-issue project, which is the
#: right trade against a collection that does not finish at all.
PAGE_SIZE = 25

#: A ceiling, so a mis-typed JQL cannot walk a 40,000-issue project in one
#: request cycle. Reached rather than exceeded, and reported when it is.
MAX_PAGES = 50

#: How long to wait after a challenge page before trying that page again.
#: Longer than `PACE`, because by the time one arrives the limiter has
#: already decided we are going too fast and pacing is not enough.
BACKOFF = 20

#: Seconds between pages, separate from `connect.PACE`.
#:
#: A connection test makes at most four small requests and 1.5s between
#: them is plenty. A collection makes one per 25 issues and each carries
#: change history, so it is a heavier stream against the same limiter -
#: eight pages at 1.5s is the burst that earned a 429.
PACE = 4.0

#: Jira's own format for a JQL date literal. Minute precision on purpose:
#: `updated >=` is inclusive, so a second-precision watermark re-collects
#: the boundary issue every run, and a minute of overlap is cheaper than
#: the reasoning about whether that matters.
JQL_TIME = "%Y-%m-%d %H:%M"


@dataclass
class CollectReport:
    issues: int = 0
    changelogs: int = 0
    pages: int = 0
    total: int = 0
    truncated: bool = False
    since: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"issues": self.issues, "changelogs": self.changelogs,
                "pages": self.pages, "total": self.total,
                "truncated": self.truncated, "since": self.since}


def watermark(session, connection_id: int, project_key: str) -> datetime | None:
    """When this project was last collected, from the rows themselves.

    Read from the raw table rather than a status column somebody has to
    remember to update: the rows are the evidence that a fetch happened,
    and a watermark that can disagree with them is a watermark that will.
    """
    params = json.dumps({"connection_id": connection_id,
                         "board_key": project_key})
    return session.scalar(
        select(func.max(RawJiraIssues.fetched_at))
        .where(RawJiraIssues.params == params)
    )


def _jql(project_key: str, since: datetime | None) -> str:
    clause = f'project = "{project_key}"'
    if since is not None:
        # Quoted, because Jira wants a string literal here.
        clause += f' AND updated >= "{since.strftime(JQL_TIME)}"'
    return clause + " ORDER BY updated ASC"


def _working_scheme(base: str, email: str, token: str) -> tuple[str, str]:
    """The auth header this site actually accepts.

    `_auth` picks Basic whenever an email is present, which is right for
    Cloud and wrong for Data Center - and somebody filling in every field
    on the form gets an email stored either way. The connection test
    already solved this by trying both and reporting the winner; the
    collector took the raw guess instead, sent Basic to a bearer-only
    instance, and failed on page one against a Jira the test had just
    connected to.

    So: same ordering as the test, one cheap `myself` call, and stick with
    whatever answers. Falls back to the first candidate when neither does,
    so the failure surfaces from the page loop with its real status rather
    than from here.
    """
    candidates = [connect._auth(email, token)]
    if email:
        candidates.append(connect._auth("", token))
    cloudish = base.endswith(".atlassian.net") or ".atlassian.net/" in base
    if not cloudish:
        candidates.reverse()

    for index, (header, scheme) in enumerate(candidates):
        if index:
            time.sleep(connect.PACE)
        body, _status, _failure = connect._attempt(base + "/rest/api/2/myself",
                                                   header)
        if body is not None:
            log.info("  auth: %s", scheme)
            return header, scheme

    log.info("  auth: %s (unconfirmed - no scheme answered /myself)",
             candidates[0][1])
    return candidates[0]


def collect(
    session,
    *,
    connection_id: int,
    project_key: str,
    site: str,
    email: str,
    token: str,
    now: datetime,
    since: datetime | None = None,
    page_size: int = PAGE_SIZE,
) -> CollectReport:
    """Page one project into `_raw_jira_*`. Returns what was written.

    `since` defaults to whatever the raw table already holds, so calling
    this twice collects the changes rather than the project.
    """
    base = connect.normalise_site(site)
    header, scheme = _working_scheme(base, email, token)
    if since is None:
        since = watermark(session, connection_id, project_key)

    report = CollectReport(since=since.strftime(JQL_TIME) if since else "")
    params = json.dumps({"connection_id": connection_id,
                         "board_key": project_key})
    jql = urllib.parse.quote(_jql(project_key, since))

    log.info("jira collect: %s %s%s", base, project_key,
             f" since {report.since}" if report.since else " (everything)")

    start = 0
    while report.pages < MAX_PAGES:
        if report.pages:
            time.sleep(PACE)
        url = (f"{base}/rest/api/2/search?jql={jql}"
               f"&startAt={start}&maxResults={int(page_size)}&expand=changelog")
        body, status, failure = connect._attempt(url, header)

        # A challenge page is the rate limiter, not a refusal: the same
        # request a moment later usually answers. Back off once rather
        # than abandoning a collection that was working a second ago -
        # and only once, so a genuinely blocked host still fails fast.
        if body is None and status == connect.GATEWAY and not failure:
            log.info("  blocked before Jira - backing off %.0fs and retrying",
                     BACKOFF)
            time.sleep(BACKOFF)
            body, status, failure = connect._attempt(url, header)

        report.pages += 1
        if body is None:
            raise ConnectionError(
                f"collecting {project_key} stopped at issue {start}: "
                + (failure or connect._explain(status or 0, base,
                                               "Basic" in header))
            )

        issues = body.get("issues") or []
        report.total = body.get("total", report.total)
        log.info("  page %-2d startAt=%-5d got %-4d of %s",
                 report.pages, start, len(issues), report.total)

        for issue in issues:
            # Popped before storing, exactly as `replay.collect` does: the
            # issue row and its history rows are separate evidence, and an
            # issue row carrying its own changelog would be stored twice.
            changelog = issue.pop("changelog", {}) or {}
            session.add(RawJiraIssues(
                params=params,
                data=json.dumps(issue).encode("utf-8"),
                url=url,
                input=None,
                fetched_at=now,
            ))
            report.issues += 1

            for history in changelog.get("histories", []):
                session.add(RawJiraChangelogs(
                    params=params,
                    data=json.dumps(history).encode("utf-8"),
                    url=f"{base}/rest/api/2/issue/{issue['id']}/changelog",
                    input=json.dumps({"issue_id": issue["id"],
                                      "issue_key": issue["key"]}),
                    fetched_at=now,
                ))
                report.changelogs += 1

        start += len(issues)
        if not issues or start >= report.total:
            break
    else:
        report.truncated = True
        log.warning("  stopped at %d pages - %d of %d issues collected",
                    MAX_PAGES, report.issues, report.total)

    log.info("  %d issue(s), %d change(s), %d page(s)%s",
             report.issues, report.changelogs, report.pages,
             " [truncated]" if report.truncated else "")
    return report
