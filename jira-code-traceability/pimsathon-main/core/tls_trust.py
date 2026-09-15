"""Automatic recovery from self-signed / internal-CA TLS certificate errors.

Corporate gateways (an internal LLM proxy, for example) often present a
self-signed or internally-issued certificate that isn't in the OS/certifi
trust store — every outbound HTTPS call to it would otherwise fail with
``SSLCertVerificationError: self-signed certificate in certificate chain``.

Rather than asking the user to track down and browse to a ``.pem`` file in
Settings, this captures the EXACT certificate the server presents on first
contact (TOFU — trust on first use) and pins that specific certificate for
that host from then on. This is materially safer than disabling verification
globally: a different host (or a later attacker-in-the-middle presenting a
different certificate for the same host) still fails verification — only the
one certificate actually seen and saved for that host is trusted.
"""
from __future__ import annotations

import re
import socket
import ssl
from pathlib import Path
from urllib.parse import urlparse

from ..config import CONFIG_DIR

TRUST_DIR = CONFIG_DIR / "trusted_certs"

# Substrings (lowercased) that indicate a TLS TRUST-CHAIN failure we can
# plausibly recover from by pinning the server's own certificate — NOT other
# TLS errors (expired certificate, hostname mismatch, bad protocol version)
# where silently trusting a captured certificate could hide a real problem.
_TRUST_ERROR_MARKERS = (
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
    "certificate verify failed",
    "unable to get issuer certificate",
)


def looks_like_cert_trust_error(exc: BaseException) -> bool:
    """True if ``exc`` (or any exception it wraps, via ``__cause__``/
    ``__context__``) is a TLS trust-chain failure."""
    text_parts = []
    seen = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        text_parts.append(str(cur).lower())
        cur = cur.__cause__ or cur.__context__
    text = " ".join(text_parts)
    return any(marker in text for marker in _TRUST_ERROR_MARKERS)


def _host_port(url: str) -> tuple[str, int]:
    parsed = urlparse(url)
    return parsed.hostname or "", parsed.port or 443


def _slug(host: str) -> str:
    return re.sub(r"[^a-zA-Z0-9.-]", "_", host) or "host"


def trusted_cert_path(url: str) -> Path:
    host, _port = _host_port(url)
    return TRUST_DIR / f"{_slug(host)}.pem"


def capture_and_trust(url: str, timeout: float = 10.0) -> str:
    """Fetch the certificate chain the server presents right now and save it
    as a locally-trusted PEM for this exact host. Returns '' if the TCP/TLS
    handshake itself couldn't even be attempted (host down, wrong port,
    firewall...) — nothing to pin in that case."""
    host, port = _host_port(url)
    if not host:
        return ""
    try:
        pem = ssl.get_server_certificate((host, port), timeout=timeout)
    except (socket.error, ssl.SSLError, OSError):
        return ""
    TRUST_DIR.mkdir(parents=True, exist_ok=True)
    path = trusted_cert_path(url)
    path.write_text(pem, encoding="utf-8")
    return str(path)


def verify_for(url: str, configured) -> object:
    """The ``requests`` ``verify=`` value for a call to ``url``: an
    explicitly configured CA bundle (env var / advanced override) always
    wins; otherwise a previously-pinned certificate for this host if one
    exists; otherwise normal certifi verification (``True``)."""
    if configured:
        return configured
    path = trusted_cert_path(url)
    return str(path) if path.exists() else True


def request(method: str, url: str, ca_bundle=None, **kwargs):
    """Like ``requests.get``/``requests.post``/... (dispatched by ``method``),
    with automatic self-signed/internal-CA recovery: if the server presents a
    certificate that fails normal verification, this captures and pins that
    EXACT certificate (trust on first use) and retries once — instead of the
    call failing outright with ``SSLCertVerificationError``. Skipped when an
    explicit CA bundle is already configured (a deliberate choice).

    Used by every outbound HTTPS call in the app (LLM providers, fetch_url's
    link fetcher, ...) so a corporate gateway/proxy that terminates TLS with
    its own certificate doesn't silently break internet access everywhere
    except the one call site that happened to handle it.

    Dispatches via ``requests.<method>`` (not ``requests.request``) so
    tests/callers that patch ``requests.get``/``requests.post`` directly keep
    working."""
    import requests

    call = getattr(requests, method.lower())
    kwargs["verify"] = verify_for(url, ca_bundle)
    try:
        return call(url, **kwargs)
    except requests.exceptions.SSLError as exc:
        if ca_bundle or not looks_like_cert_trust_error(exc):
            raise
        pinned = capture_and_trust(url)
        if not pinned:
            raise
        kwargs["verify"] = pinned
        return call(url, **kwargs)


def diagnose_internet(test_url: str = "https://www.google.com/generate_204",
                      timeout: float = 8.0) -> tuple[bool, str]:
    """Live check of the app's OWN outbound-HTTPS path (via :func:`request`, so
    the self-signed/internal-CA recovery is exercised too). Returns
    ``(ok, human_message)`` and never raises — for a "Test Internet Access"
    button so a user on a locked-down corporate network can see the CONCRETE
    reason a fetch fails instead of a silent dead end."""
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"'requests' library unavailable: {exc}"
    try:
        resp = request("get", test_url, timeout=timeout)
        pinned = trusted_cert_path(test_url).exists()
        note = " (via a pinned corporate-gateway certificate)" if pinned else ""
        return True, f"Internet reachable — HTTP {resp.status_code}{note}."
    except requests.exceptions.SSLError as exc:
        if looks_like_cert_trust_error(exc):
            return False, ("TLS certificate not trusted and could not be captured "
                           "automatically. Your gateway may require a corporate root "
                           f"CA installed in Windows. Detail: {exc}")
        return False, (f"TLS error (not an untrusted-CA case — e.g. expired cert / "
                       f"hostname mismatch): {exc}")
    except requests.exceptions.ProxyError as exc:
        return False, (f"Blocked by a proxy. The company gateway is refusing the "
                       f"connection: {exc}")
    except requests.exceptions.ConnectTimeout as exc:
        return False, (f"Connection timed out — a firewall/gateway is likely dropping "
                       f"outbound traffic: {exc}")
    except requests.exceptions.ConnectionError as exc:
        return False, (f"Could not connect — DNS block, firewall, or no route to the "
                       f"internet: {exc}")
    except Exception as exc:  # noqa: BLE001
        return False, f"Internet test failed: {type(exc).__name__}: {exc}"


def request_any_method(method: str, url: str, ca_bundle=None, **kwargs):
    """Same TLS auto-recovery as :func:`request`, for a caller whose HTTP verb
    is only known at runtime (e.g. a REST connector where the user configures
    GET/POST/PUT/... per call). Dispatches via ``requests.request(method, url,
    ...)`` — the single generic entry point — rather than ``requests.<method>``,
    so a caller/test that patches ``requests.request`` directly keeps working."""
    import requests

    kwargs["verify"] = verify_for(url, ca_bundle)
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.SSLError as exc:
        if ca_bundle or not looks_like_cert_trust_error(exc):
            raise
        pinned = capture_and_trust(url)
        if not pinned:
            raise
        kwargs["verify"] = pinned
        return requests.request(method, url, **kwargs)
