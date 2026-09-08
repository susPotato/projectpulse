"""Sign-in for Microsoft Graph, the OneDrive source's auth.

Zero-config, the same trick the Azure CLI and Microsoft Graph PowerShell use:
MSAL's device-code flow against Microsoft's own public, pre-consented
"Microsoft Graph PowerShell" client id and the ``common`` authority, so a
person signs in with their own Microsoft account - no Azure app
registration, and no FPT tenant admin consent, because ``Files.Read`` is a
delegated scope this client is already trusted for. This is exactly the
lead-time item `ProjectPulseAI_Architecture.md` §DEPLOY flagged as blocking a
live Graph connection; the well-known-public-client route sidesteps it.

The signed-in session is cached in Postgres
(:class:`app.models.onedrive.OneDriveTokenCache`), not on disk or in the OS
credential store: a Fly deployment's filesystem is ephemeral and a machine
that stopped overnight would otherwise need signing in again on every cold
start, and the session has to be visible to whichever machine is warm when a
sync actually runs.
"""

from __future__ import annotations

from typing import Callable

from app.models.onedrive import OneDriveTokenCache

_CACHE_ID = "default"

#: Microsoft's own public, multi-tenant "Microsoft Graph PowerShell" client.
#: Not a secret - a public client has none - and broadly pre-consented for
#: delegated scopes, which is what lets sign-in skip an Azure app
#: registration and tenant admin consent entirely.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
DEFAULT_TENANT = "common"

#: Read-only, and only files - this app never writes to a PM's OneDrive.
SCOPES = ["Files.Read", "Files.Read.All"]


class GraphAuthError(RuntimeError):
    """Sign-in failed, or nobody has signed in yet."""


def _load_cache(session):
    import msal

    cache = msal.SerializableTokenCache()
    row = session.get(OneDriveTokenCache, _CACHE_ID)
    if row is not None:
        try:
            cache.deserialize(row.serialized)
        except ValueError:
            pass
    return cache


def _save_cache(session, cache) -> None:
    if not cache.has_state_changed:
        return
    session.merge(OneDriveTokenCache(id=_CACHE_ID, serialized=cache.serialize()))


def _app(session):
    try:
        import msal
    except ImportError as exc:
        raise GraphAuthError(
            "the 'msal' package is not installed; pip install -e \".[onedrive]\""
        ) from exc

    cache = _load_cache(session)
    authority = f"https://login.microsoftonline.com/{DEFAULT_TENANT}"
    # validate_authority=False: the host is always our own hardcoded, trusted
    # login.microsoftonline.com, so there is nothing to discover - and
    # skipping it means merely checking "is anyone signed in?" never reaches
    # out to Microsoft on its own.
    app = msal.PublicClientApplication(
        DEFAULT_CLIENT_ID,
        authority=authority,
        token_cache=cache,
        validate_authority=False,
    )
    return app, cache


def signed_in_account(session) -> dict | None:
    """The cached account, if any - a local cache lookup, no network call."""
    app, _cache = _app(session)
    accounts = app.get_accounts()
    return accounts[0] if accounts else None


def signed_in_identity(session) -> str | None:
    account = signed_in_account(session)
    return (account or {}).get("username") if account else None


def sign_in_device_code(session, on_code: Callable[[dict], None]) -> dict:
    """Blocking device-code sign-in. Call from a script, never from a request.

    ``on_code`` is invoked once with MSAL's device-flow dict - ``user_code``
    and ``verification_uri`` are what a person needs to complete sign-in in a
    browser, anywhere, not necessarily on this machine. Returns the MSAL
    token result; raises :class:`GraphAuthError` on failure or timeout.
    """
    app, cache = _app(session)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise GraphAuthError(
            flow.get("error_description") or "could not start device sign-in"
        )
    on_code(flow)
    result = app.acquire_token_by_device_flow(flow)  # blocks until done/expired
    _save_cache(session, cache)
    if not result or "access_token" not in result:
        raise GraphAuthError((result or {}).get("error_description") or "sign-in failed")
    return result


def sign_out(session) -> None:
    app, cache = _app(session)
    for account in app.get_accounts():
        app.remove_account(account)
    _save_cache(session, cache)
    row = session.get(OneDriveTokenCache, _CACHE_ID)
    if row is not None:
        session.delete(row)


def get_access_token(session) -> str:
    """Silently reuse the cached sign-in, refreshing if needed.

    Raises :class:`GraphAuthError` when nobody has signed in, or the session
    cannot be refreshed - the caller (:mod:`graph_source`) turns that into a
    sheet that could not be fetched, the same way a missing file is not
    fetched rather than a hard failure.
    """
    app, cache = _app(session)
    accounts = app.get_accounts()
    if not accounts:
        raise GraphAuthError(
            "not signed in to Microsoft Graph - run: "
            "python -m scripts.sync onedrive login"
        )
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(session, cache)
    if not result or "access_token" not in result:
        raise GraphAuthError(
            "Microsoft Graph sign-in expired - run: python -m scripts.sync onedrive login"
        )
    return result["access_token"]
