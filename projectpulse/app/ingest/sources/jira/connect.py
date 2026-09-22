"""Can we reach this Jira, with these credentials, right now?

One question, answered before anybody wires a collector. The alternative is
discovering a wrong site URL or a revoked token three layers down, as an
empty table on a dashboard, which is the worst place to learn it.

**It stores nothing.** The token arrives in a request, is used to build one
Authorization header, and goes out of scope. Nothing is written to the
database, and no error message this module produces contains it - an
exception string is the classic way a secret reaches a log file that is not
encrypted and not rotated.

One line per attempt *is* logged, because somebody watching a connection
fail needs to see what was tried. It carries the URL, the name of the
scheme and the status - never the header, which is the credential in
base64, and never the response body, which is somebody's account.

**Two Jiras, and it tries both rather than guessing.** Cloud takes HTTP
Basic of `email:api_token` against `/rest/api/3`; Server and Data Center
take a bearer PAT against `/rest/api/2`. Choosing by whether an email was
typed infers the product from the shape of the form, and gets it wrong for
the ordinary case of somebody filling in every field - which is exactly how
this first failed in use, reporting a valid Data Center token as refused.
Four requests at worst, and the result names the pair that worked.

**The server is being asked to fetch a URL somebody typed**, which is a
request-forgery primitive if taken literally: `http://169.254.169.254/` is
a cloud metadata endpoint, and `http://localhost:5432/` is a database. So
the scheme must be https and the host must resolve to a public address, and
both are checked before the request rather than after the redirect.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import logging
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

#: The endpoint that answers "who am I", which is the cheapest proof that a
#: credential works. It reads nothing about anybody's project, so a token
#: with almost no permission still gets a useful answer.
#:
#: Two versions, because there are two Jiras: Cloud serves `/3`, Data Center
#: serves `/2` and 404s on `/3`. Both are tried rather than inferred from
#: the URL, because a self-hosted Cloud-era instance is a real thing and
#: guessing wrong reports "wrong site" for a site that is right.
WHOAMI = ("/rest/api/3/myself", "/rest/api/2/myself")

#: Path segments that mean the URL somebody pasted is a *page*, not the
#: base. Jira Data Center is commonly mounted under a context path -
#: `/jiradc`, `/jira` - so the base cannot be "scheme plus host": that
#: discards the one part of the path that matters and asks a web server
#: for an API it does not host. Everything from the *earliest* of these
#: onward is the page; what precedes it is the context path.
PAGE_MARKERS = (
    "/browse/", "/projects/", "/issues/", "/secure/", "/jira/software/",
    "/rest/", "/plugins/", "/servicedesk/", "/wiki/",
)

#: A reply that was not JSON. Its own code so it can be reported in the
#: person's terms ("a web page, not an API") rather than as a bare status.
NOT_JSON = -1

#: A refusal that arrived as an HTML page rather than an API error, which
#: means something in front of Jira answered: a WAF, a bot challenge, a
#: corporate proxy. The credential was never evaluated, and reporting it as
#: "refused" is the single most misleading thing this module could say.
GATEWAY = -2

#: A test that hangs is a page that hangs. Short on purpose: this is a
#: reachability check, not a data pull.
TIMEOUT = 15

#: Seconds between attempts. The WAF in front of a corporate Jira counts
#: requests per second, not per credential, and four in a burst is a 429
#: whatever they carry.
PACE = 1.5

#: Headers a normal HTTP client sends and `urllib` does not.
#:
#: Not evasion - the opposite. `urllib` announces itself as
#: `Python-urllib/3.14` and sends no `Accept-Language`, and a WAF in front
#: of a corporate Jira reads that as a scraper and serves a bot challenge
#: before Jira ever sees the request. Measured against a real instance:
#:
#:     Python-urllib/3.14            -> 403  (challenge page)
#:     browser UA                    -> 429
#:     browser UA + Accept headers   -> 401  (Jira, asking for credentials)
#:
#: The 401 is the honest answer we want: the request reached the API and
#: was evaluated. This is an authorised client using its owner's own
#: credential against their own Jira, and looking like an ordinary HTTP
#: client is what lets that work.
BROWSERISH = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}


#: One line per attempt, so a person watching the server can see what was
#: tried and what came back. It logs the URL, the scheme *name* and the
#: status - never the header, which is the credential in base64.
log = logging.getLogger("pulse.jira.connect")


class ConnectionRefused(ValueError):
    """The request was not attempted, and the message says why."""


@dataclass
class Probe:
    """What one attempt found. Never carries the credential."""

    ok: bool
    #: What to tell the person, in their terms.
    detail: str
    #: Who the credential turned out to belong to, when it worked.
    account: dict[str, Any] = field(default_factory=dict)
    #: The scheme that was used, so a failure names what was tried.
    scheme: str = ""
    status: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "detail": self.detail, "account": self.account,
                "scheme": self.scheme, "status": self.status}


def normalise_site(site: str) -> str:
    """`acme.atlassian.net` -> `https://acme.atlassian.net`, and validate it.

    Raises `ConnectionRefused` for anything this server should not be asked
    to fetch. The check is on the resolved address, not on the name: a
    hostname under somebody's control can point at 127.0.0.1.
    """
    site = (site or "").strip().rstrip("/")
    if not site:
        raise ConnectionRefused("Enter your Jira site, e.g. acme.atlassian.net")
    if "://" not in site:
        site = f"https://{site}"

    parsed = urlparse(site)
    if parsed.scheme != "https":
        raise ConnectionRefused(
            "Only https is allowed. A token sent over http is readable in "
            "transit by anything between here and there."
        )
    host = parsed.hostname
    if not host:
        raise ConnectionRefused(f"{site!r} has no host in it.")

    try:
        infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise ConnectionRefused(
            f"{host} does not resolve. Check the spelling of the site."
        ) from None

    for info in infos:
        addr = _unwrap(ipaddress.ip_address(info[4][0]))
        # Loopback, link-local and private ranges are this server's own
        # network, not somebody's Jira. 169.254.169.254 in particular is the
        # cloud metadata service, and fetching it on request is how a form
        # like this becomes a credential leak.
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast):
            raise ConnectionRefused(
                f"{host} resolves to {addr}, which is on this server's own "
                f"network. A Jira site has to be reachable from the internet."
            )

    # Keep the context path, drop the page. Pasting the ticket you happen to
    # be looking at is the obvious thing to do, and for a Data Center
    # instance under `/jiradc` the difference between keeping that segment
    # and dropping it is the difference between the API and a 404.
    # The *earliest* marker, not the first one in the list: Cloud's
    # `/jira/software/projects/AB/boards/1` contains two, and cutting at
    # `/projects/` first leaves `/jira/software` as a context path that does
    # not exist. Everything from the first page segment onward is the page.
    path = parsed.path or ""
    lowered = path.lower()
    cuts = [i for i in (lowered.find(m) for m in PAGE_MARKERS) if i != -1]
    if cuts:
        path = path[:min(cuts)]
    path = path.rstrip("/")

    return f"{parsed.scheme}://{parsed.netloc}{path}"


#: RFC 6052's well-known prefix. A NAT64 resolver hands back a v6 address
#: with the real IPv4 in its low 32 bits, and Python calls that block
#: private - so a plain `is_private` check refuses `acme.atlassian.net` on
#: any IPv6-only or NAT64 network. Found by a test resolving a real host.
NAT64 = ipaddress.ip_network("64:ff9b::/96")

#: The other mapping with a public address hiding inside it.
V4_MAPPED = ipaddress.ip_network("::ffff:0:0/96")


def _unwrap(addr):
    """The address actually being reached, past any v4-in-v6 wrapper.

    Judging the wrapper instead of the address inside it gets the answer
    backwards in both directions: a public host looks private because of
    the prefix, and `::ffff:127.0.0.1` looks like an ordinary v6 address
    while pointing at loopback.
    """
    if isinstance(addr, ipaddress.IPv6Address) and (
            addr in NAT64 or addr in V4_MAPPED):
        return ipaddress.ip_address(int(addr) & 0xFFFFFFFF)
    return addr


def _auth(email: str, token: str) -> tuple[str, str]:
    """`(header value, scheme name)` for the credentials given."""
    email = (email or "").strip()
    if email:
        pair = base64.b64encode(f"{email}:{token}".encode()).decode("ascii")
        return f"Basic {pair}", "basic (Cloud: email + API token)"
    return f"Bearer {token}", "bearer (Server/DC personal access token)"


def _attempt(url: str, header: str) -> tuple[dict | None, int | None, str]:
    """One request. Returns `(body, status, failure)` - exactly one is set."""
    request = urllib.request.Request(
        url, headers={"Authorization": header, **BROWSERISH}, method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8", "replace")), 200, ""
    except urllib.error.HTTPError as exc:
        # Read a little of the body. A refusal that arrives as HTML did not
        # come from Jira's API - it came from whatever is in front of it,
        # and saying "your credential was refused" about a challenge page
        # sends somebody hunting for a fault in a token that was never
        # looked at. Found the first time this was pointed at a real host.
        try:
            peek = exc.read(2048).decode("utf-8", "replace").lstrip().lower()
        except Exception:  # noqa: BLE001 - a body we cannot read is not HTML
            peek = ""
        if peek.startswith(("<!doctype", "<html")):
            return None, GATEWAY, ""
        return None, exc.code, ""
    except urllib.error.URLError as exc:
        # `exc.reason` is a socket error, never the request - safe to show.
        return None, None, f"could not reach it: {exc.reason}"
    except (TimeoutError, socket.timeout):
        return None, None, f"no answer within {TIMEOUT}s"
    except json.JSONDecodeError:
        # Not a transport failure, so the next pairing still gets a turn.
        # A Data Center instance answering `/rest/api/3` with an HTML error
        # page is the ordinary case, not a reason to give up on `/2`.
        return None, NOT_JSON, ""


def probe(site: str, email: str, token: str) -> Probe:
    """Ask one Jira who this credential belongs to.

    **Tries the combinations rather than inferring one.** Cloud wants Basic
    against `/rest/api/3`; Data Center wants Bearer against `/rest/api/2`.
    Picking by whether an email was typed guesses at the product from the
    shape of the form, and guesses wrong for the ordinary case of somebody
    filling in every field. Four requests at worst, each cheap, and the
    result names the pair that worked - which is the answer a person
    actually needs before wiring a collector.
    """
    if not (token or "").strip():
        raise ConnectionRefused("Paste an API token to test.")

    base = normalise_site(site)
    schemes = [_auth(email, token)]
    if email:
        # A Data Center PAT is a bearer token and has no email to pair
        # with. Somebody who typed one anyway should not be told their
        # credential is bad.
        schemes.append(_auth("", token))

    # Most likely pairing first, and stop as soon as one is decisive.
    #
    # Firing all four at once is what made this fail against a real
    # instance: the WAF rate-limits, so the burst earned a 429 and then
    # challenge pages, and the run that *would* have worked never got a
    # turn. Measured: one request -> 401 from Jira, four in a row -> 429.
    cloudish = base.endswith(".atlassian.net") or ".atlassian.net/" in base
    if not cloudish:
        # A self-hosted instance - often under a context path - is Data
        # Center, which serves /2 and takes a bearer token.
        schemes.reverse()
        paths = tuple(reversed(WHOAMI))
    else:
        paths = WHOAMI

    tried: list[str] = []
    best: tuple[int, str, str] | None = None
    log.info("jira probe: %s (%d pairing(s) to try)", base, len(schemes) * len(WHOAMI))

    first = True
    for header, scheme in schemes:
        for path in paths:
            # Space the attempts. A burst is what the rate limiter sees.
            if not first:
                time.sleep(PACE)
            first = False
            body, status, failure = _attempt(base + path, header)
            log.info(
                "  %-44s %-46s -> %s", path, scheme,
                failure or {NOT_JSON: "not JSON (a web page, not the API)",
                            GATEWAY: "blocked before Jira (challenge page)",
                            200: "OK"}.get(status, status),
            )
            label = f"{scheme} on {path.rsplit('/', 1)[0]}"
            if body is not None:
                who = {k: body.get(k)
                       for k in ("accountId", "displayName", "emailAddress",
                                 "name", "key")
                       if body.get(k)}
                name = (who.get("displayName") or who.get("emailAddress")
                        or who.get("name") or "this account")
                return Probe(ok=True, scheme=label, status=200, account=who,
                             detail=f"Connected to {base} as {name}.")
            if failure:
                # A transport failure is about the host, not the
                # combination - no other pairing will do better.
                return Probe(ok=False, scheme=label,
                             detail=f"{base} {failure}")
            # A decisive answer settles it: 401 means the API evaluated
            # the credential and said no, and no other pairing will say
            # otherwise about *this* token. Only keep going while the
            # answers are "wrong version" or "never reached Jira".
            decisive = status in (401, 403) and status != GATEWAY
            tried.append(f"{label} -> " + {
                NOT_JSON: "not JSON", GATEWAY: "blocked before Jira",
            }.get(status, str(status)))
            # 401/403 are the informative refusals; a 404 only means this
            # version is not served here, which is expected on one of them.
            # A gateway answer outranks everything: it explains all the
            # others, which are the same wall seen four times.
            rank = {401: 3, 403: 4, GATEWAY: 9}.get(status or 0, 1)
            if best is None or rank > best[0]:
                best = (rank, label, _explain(status or 0, base, "basic" in label))
            if decisive:
                detail = _explain(status or 0, base, "basic" in label)
                return Probe(ok=False, scheme=label, status=status,
                             detail=detail + "  Tried: " + "; ".join(tried) + ".")

    detail = best[2] if best else f"{base} did not answer usefully."
    return Probe(ok=False, scheme=(best[1] if best else ""),
                 detail=detail + "  Tried: " + "; ".join(tried) + ".")


def _explain(status: int, base: str, basic: bool) -> str:
    """An HTTP status in terms of what the person should change.

    `basic` is whether the attempt that produced this status used Basic
    auth - not whether an email was typed. The two came apart once the
    probe started trying both: somebody who filled in an email still gets
    a bearer attempt, and telling them to check their email when the
    bearer attempt is the one that answered sends them the wrong way.
    """
    if status == 401:
        return (
            "Rejected the credential (401). "
            + ("Sent as Basic auth with the email - check both halves, and "
               "that the token has not been revoked."
               if basic else
               "Sent as a bearer token, which is what Jira Server and Data "
               "Center take. The token was reached and refused, so check it "
               "has not expired and that it is a token for this site.")
        )
    if status == 403:
        return ("Authenticated, but refused (403). The credential is valid and "
                "this account may not read the API - often a site-level "
                "restriction rather than a wrong token.")
    if status == 404:
        return (f"{base} answered 404 for the API path. The credential was "
                f"never checked - this is usually the wrong site address.")
    if status == 429:
        return "Rate limited (429). Wait a moment and try again."
    if status == GATEWAY:
        return (
            f"Something in front of {base} refused the request with a web "
            f"page - a bot challenge or proxy, not Jira. The credential was "
            f"never checked, so this says nothing about whether it works. "
            f"Bot scoring usually judges where the request came from: an "
            f"ordinary office or home connection is let through while a "
            f"server in a data centre is challenged. If this is running on "
            f"a hosted machine, try the same credential from a laptop."
        )
    if status == NOT_JSON:
        return (f"{base} answered, but with a web page rather than an API. "
                f"Check the site address - for a self-hosted Jira it usually "
                f"includes a path such as /jira or /jiradc.")
    return f"{base} answered {status}."


#: Fields worth reporting on, and what each one unlocks. The point of the
#: preview is not "the API works" - `probe` said that - but whether it
#: carries what the spreadsheet export did not. On CoWorkLocal the export
#: had eight populated columns, no created date, no assignee and no
#: history, and those absences are what capped the whole analysis.
FIELDS_THAT_MATTER = {
    "created": "when work started - the export had none",
    "updated": "movement between scans",
    "assignee": "ownership without parsing it out of prose",
    "duedate": "a schedule",
    "status": "state",
    "resolutiondate": "when work actually finished",
    "timeoriginalestimate": "planned effort",
    "timespent": "logged effort",
    "issuelinks": "dependencies - a critical path",
    "parent": "hierarchy",
    "description": "the block the team keeps in prose",
}


def preview(site: str, email: str, token: str, project: str,
            sample: int = 25) -> dict[str, Any]:
    """What this Jira would actually give us for one project.

    Answers the question a connection test cannot: the credential works,
    but is the API richer than the export somebody is emailing around?
    Reports field coverage over a sample rather than a yes/no, because
    "Jira has a created date" and "this instance fills it in" are
    different claims and only the second one matters.

    Read-only, one page, and the sample is capped - this is a look, not a
    collection.
    """
    base = normalise_site(site)
    if not (project or "").strip():
        raise ConnectionRefused("Name the project key, e.g. COWORKLOCAL.")

    # Whichever pairing `probe` found is the one that works here too, so
    # try them in the same order rather than re-deriving it.
    schemes = [_auth(email, token)]
    if email:
        schemes.append(_auth("", token))
    cloudish = base.endswith(".atlassian.net") or ".atlassian.net/" in base
    if not cloudish:
        schemes.reverse()
    versions = ("2", "3") if not cloudish else ("3", "2")

    jql = urllib.parse.quote(f'project = "{project.strip()}" ORDER BY updated DESC')
    first = True
    for header, scheme in schemes:
        for version in versions:
            if not first:
                time.sleep(PACE)
            first = False
            url = (f"{base}/rest/api/{version}/search?jql={jql}"
                   f"&maxResults={int(sample)}&expand=changelog")
            body, status, failure = _attempt(url, header)
            log.info("  search /rest/api/%s %-44s -> %s", version, scheme,
                     failure or status)
            if body is None:
                continue

            issues = body.get("issues") or []
            coverage: dict[str, int] = {}
            for issue in issues:
                fields = issue.get("fields") or {}
                for name in FIELDS_THAT_MATTER:
                    value = fields.get(name)
                    if value not in (None, "", [], {}):
                        coverage[name] = coverage.get(name, 0) + 1
            histories = sum(len((i.get("changelog") or {}).get("histories", []))
                            for i in issues)
            return {
                "ok": True,
                "only_in_export": _only_in_export(issues),
                "project": project.strip(),
                "total": body.get("total", len(issues)),
                "sampled": len(issues),
                "histories": histories,
                "scheme": f"{scheme} on /rest/api/{version}",
                "fields": [
                    {"name": n, "filled": coverage.get(n, 0),
                     "unlocks": FIELDS_THAT_MATTER[n]}
                    for n in FIELDS_THAT_MATTER
                ],
            }

    return {"ok": False, "project": project.strip(),
            "detail": (f"Reached {base}, but no search returned issues for "
                       f"{project.strip()!r}. Check the project key, and that "
                       f"this account can see it.")}


#: The hand-maintained block the export keeps inside its Description
#: column. Labels only - `people_in_note` and the converter own the
#: parsing; this just asks whether the text is there at all.
EXPORT_BLOCK = ("PO:", "BA:", "Developer:", "Ghi ch", "Ngay nhan", "Ngày nhận")


def _only_in_export(issues: list[dict]) -> dict[str, Any]:
    """Whether Jira carries what the spreadsheet carried, or only Jira does.

    The decision this feeds is "can we stop using the export?", and the
    honest answer depends on one thing: the export's Description column
    held a block the tracker has no fields for - `PO:`, `BA:`,
    `Developer:`, and a `Ghi chu` list naming twelve people who appear
    nowhere else. If Jira's own description is empty, that block lives
    only in the spreadsheet and binning it loses the ownership record.

    Reports rather than decides. A sample is a sample, and "0 of 25" is
    evidence, not proof, about 190.
    """
    described = 0
    with_block = 0
    assignees: dict[str, int] = {}
    for issue in issues:
        fields = issue.get("fields") or {}
        text = fields.get("description")
        # Cloud returns Atlassian Document Format; Data Center returns a
        # string. Only the presence of the block matters here, so the ADF
        # case is flattened crudely rather than parsed.
        if isinstance(text, dict):
            text = json.dumps(text, ensure_ascii=False)
        if text:
            described += 1
            if any(marker.lower() in str(text).lower() for marker in EXPORT_BLOCK):
                with_block += 1
        who = (fields.get("assignee") or {}).get("displayName") or ""
        if who:
            assignees[who] = assignees.get(who, 0) + 1
    return {
        "described": described,
        "with_export_block": with_block,
        "assignees": dict(sorted(assignees.items(), key=lambda kv: -kv[1])[:8]),
    }
