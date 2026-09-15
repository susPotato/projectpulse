"""Microsoft 365 sign-in — real OAuth via MSAL's device-code flow.

ZERO-CONFIG "connect like Claude": the user just clicks Sign in, opens a short
URL, enters a one-time code, and signs in with their own Microsoft account
(SSO/MFA as their org normally does) — NO Tenant ID / Client ID to type. This
works because we ship a well-known Microsoft first-party PUBLIC multi-tenant
client (``DEFAULT_CLIENT_ID`` — the Microsoft Graph PowerShell client, same
technique the Azure CLI / Graph CLI use) against the ``common`` authority, so
any work/school (or personal) account can consent to the delegated Graph
scopes interactively. Device-code flow needs no client secret and no embedded
browser / redirect URI.

Orgs that require their OWN app registration can still override tenant_id /
client_id in config (``ms365.tenant_id`` / ``ms365.client_id``); when both are
blank the bundled defaults are used. The signed-in token (+ refresh token) is
cached in the OS credential store (Windows Credential Manager / macOS Keychain
/ Linux Secret Service, via ``keyring``) — never written into config.json, and
never stored in plaintext. With no OS credential store (e.g. a headless Linux
box), it falls back to a local file at ``TOKEN_CACHE_PATH``.
"""
from __future__ import annotations

from typing import Callable, List, Optional

from ..config import CONFIG_DIR

TOKEN_CACHE_PATH = CONFIG_DIR / "ms365_token_cache.bin"  # fallback only — see module docstring
_KEYRING_SERVICE = "cowork_local_ms365"
_KEYRING_KEY = "token_cache"

# Bundled zero-config sign-in identity. This is Microsoft's OWN public,
# multi-tenant "Microsoft Graph PowerShell" client — a first-party client that
# permits the device-code public-client flow and is broadly pre-consented for
# delegated Graph scopes, so users need not register (or type) any app id. The
# same well-known-public-client approach the Azure CLI, Graph CLI and many
# tools use. NOT a secret (public clients have none). Override via config only
# if the tenant blocks it and mandates a private app registration.
DEFAULT_CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
# "common" = any Microsoft account (work/school or personal); the user picks
# which account at sign-in. Use a specific tenant id only to restrict to one org.
DEFAULT_TENANT = "common"

# One shared scope set for every connector — MSAL requests them all at sign-in
# so switching a connector on later doesn't force a second sign-in. Some
# (ChannelMessage.Send, OnlineMeetingTranscript.Read.All) need the tenant
# admin to have consented the app already.
SCOPES: List[str] = [
    "User.Read",
    "Mail.Read", "Mail.Send",
    "Calendars.Read",
    "Team.ReadBasic.All", "Channel.ReadBasic.All",
    "ChannelMessage.Read.All", "ChannelMessage.Send",
    "Files.Read.All", "Files.ReadWrite.All",
    "Sites.Read.All",
    "OnlineMeetings.Read", "OnlineMeetingTranscript.Read.All",
]


class Ms365AuthError(Exception):
    pass


def _load_cache():
    import msal

    cache = msal.SerializableTokenCache()
    serialized = None
    try:
        import keyring
        serialized = keyring.get_password(_KEYRING_SERVICE, _KEYRING_KEY)
    except Exception:  # noqa: BLE001 - no OS credential store available
        serialized = None
    if serialized is None and TOKEN_CACHE_PATH.exists():
        try:
            serialized = TOKEN_CACHE_PATH.read_text(encoding="utf-8")
        except OSError:
            serialized = None
    if serialized:
        try:
            cache.deserialize(serialized)
        except ValueError:
            pass
    return cache


def _save_cache(cache) -> None:
    if not cache.has_state_changed:
        return
    serialized = cache.serialize()
    try:
        import keyring
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_KEY, serialized)
        # Migrated to the OS credential store — drop any older plaintext file
        # so the token isn't left duplicated on disk.
        if TOKEN_CACHE_PATH.exists():
            try:
                TOKEN_CACHE_PATH.unlink()
            except OSError:
                pass
        return
    except Exception:  # noqa: BLE001 - no OS credential store available
        pass
    TOKEN_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE_PATH.write_text(serialized, encoding="utf-8")


def _app(tenant_id: str, client_id: str):
    try:
        import msal
    except ImportError as exc:
        raise Ms365AuthError(
            "The 'msal' package isn't installed. Run: pip install msal") from exc
    # Zero-config: blank tenant/client → the bundled public client + "common"
    # authority, so sign-in works with no Azure app registration to enter.
    tenant_id = (tenant_id or "").strip() or DEFAULT_TENANT
    client_id = (client_id or "").strip() or DEFAULT_CLIENT_ID
    cache = _load_cache()
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    try:
        # validate_authority=False: skip MSAL's authority-discovery network call at
        # construction time — the host is always our own hardcoded, trusted
        # login.microsoftonline.com, so there is nothing to validate. Without this,
        # simply building the app object (e.g. to check "is anyone signed in?")
        # would silently reach out to Microsoft even when the user turned "Allow
        # external internet access" off, and a mistyped tenant id would raise here
        # instead of at an explicit sign-in action.
        app = msal.PublicClientApplication(
            client_id, authority=authority, token_cache=cache, validate_authority=False)
    except Exception as exc:  # noqa: BLE001 - malformed tenant/client id, etc.
        raise Ms365AuthError(f"Invalid Tenant ID / Client ID: {exc}") from exc
    return app, cache


def signed_in_account(tenant_id: str, client_id: str) -> Optional[dict]:
    """The cached account, if any — a local cache lookup, no network call."""
    try:
        app, _cache = _app(tenant_id, client_id)
    except Ms365AuthError:
        return None
    accounts = app.get_accounts()
    return accounts[0] if accounts else None


def sign_in_device_code(tenant_id: str, client_id: str, on_code: Callable[[dict], None]) -> dict:
    """Blocking device-code sign-in — call this off the UI thread.

    ``on_code`` is invoked once with the MSAL device-flow dict so the caller can
    both auto-open the browser and show a copyable code. Useful keys:
    ``user_code`` (the code to enter), ``verification_uri`` (the page to open),
    ``verification_uri_complete`` (URL with the code pre-filled, when the tenant
    returns it) and ``message`` (the full human-readable instruction). Returns
    the MSAL token result dict; raises Ms365AuthError on failure/timeout."""
    app, cache = _app(tenant_id, client_id)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise Ms365AuthError(flow.get("error_description") or "Could not start device sign-in.")
    on_code(flow)
    result = app.acquire_token_by_device_flow(flow)  # blocks, polling until done/expired
    _save_cache(cache)
    if not result or "access_token" not in result:
        desc = (result or {}).get("error_description") or "Sign-in failed."
        raise Ms365AuthError(desc)
    return result


def identity_from_result(result: dict) -> str:
    """The signed-in user's UPN/email from a ``sign_in_device_code()`` result —
    used by ``login_dialog.py`` to map an SSO sign-in to a provisioned
    ``accounts.Account`` by username. MSAL requests ``openid``/``profile``
    implicitly on every token request, so ``id_token_claims`` is present
    alongside the resource scopes in ``SCOPES``."""
    claims = (result or {}).get("id_token_claims") or {}
    return claims.get("preferred_username") or claims.get("email") or ""


def get_access_token(tenant_id: str, client_id: str) -> str:
    """Silently reuse the cached sign-in. Raises Ms365AuthError when there is
    no valid session — the caller (a Graph call) should surface that as a
    normal tool failure telling the user to sign in again from Settings."""
    app, cache = _app(tenant_id, client_id)
    accounts = app.get_accounts()
    if not accounts:
        raise Ms365AuthError("Not signed in to Microsoft 365 — sign in from Settings first.")
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    _save_cache(cache)
    if not result or "access_token" not in result:
        raise Ms365AuthError("Microsoft 365 sign-in expired — sign in again from Settings.")
    return result["access_token"]


# ---- zero-config convenience wrappers (use the bundled default identity) ----
# The UI calls these with no args for the "connect like Claude" flow; they read
# the optional config overrides so a custom Azure app still works.
def _ids(config=None):
    ms365 = (config.ms365 if config is not None else {}) or {}
    return ms365.get("tenant_id", ""), ms365.get("client_id", "")


def current_identity(config=None) -> str:
    """Signed-in account's UPN/email, or '' if not signed in (no network)."""
    acc = signed_in_account(*_ids(config))
    return (acc or {}).get("username", "") if acc else ""


def is_signed_in(config=None) -> bool:
    return signed_in_account(*_ids(config)) is not None


def sign_in(on_code: Callable[[dict], None], config=None) -> dict:
    """Zero-config device-code sign-in — blocking, call off the UI thread.
    ``on_code`` receives the MSAL device-flow dict (user_code/verification_uri/…)."""
    return sign_in_device_code(*_ids(config), on_code)


def sign_out_default(config=None) -> None:
    sign_out(*_ids(config))


def sign_out(tenant_id: str, client_id: str) -> None:
    try:
        app, cache = _app(tenant_id, client_id)
        for acc in app.get_accounts():
            app.remove_account(acc)
        _save_cache(cache)
    except Ms365AuthError:
        pass
    try:
        import keyring
        keyring.delete_password(_KEYRING_SERVICE, _KEYRING_KEY)
    except Exception:  # noqa: BLE001 - nothing stored there, or no credential store
        pass
    try:
        if TOKEN_CACHE_PATH.exists():
            TOKEN_CACHE_PATH.unlink()
    except OSError:
        pass
