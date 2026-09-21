"""Vendor API keys, encrypted at rest.

**What this buys, stated plainly.** Keys are sealed with a Fernet token whose
key comes from `PULSE_SECRET_KEY` - an environment variable, so a platform
secret on a deployment. A database dump, a backup file, a read replica or a
stray `SELECT * FROM llm_credentials` therefore discloses ciphertext instead of
a usable credential. That is the threat this addresses and it is a real one:
backups leave the security boundary the live database sits inside.

**What it does not buy.** Anyone who can read the process's environment can
read the keys, because the running app must be able to decrypt them to use
them. This is encryption at rest, not a secrets manager, and it does not make
the database a good place for a key that platform secrets could hold instead.
`ANTHROPIC_API_KEY` as a Fly secret remains the better path for a deployment;
this exists so a running app can be configured by hand without one, which is
what the settings page is for.

It replaces the plaintext key in `app_settings.narration`, which
`app/models/uploads.py` warned about in its own docstring. An existing
plaintext key is re-sealed here and blanked there on first use - see
`adopt_legacy`.
"""

from __future__ import annotations

import base64
import logging
import os

log = logging.getLogger(__name__)

#: The env var holding the key-encryption key. Accepts either a real Fernet key
#: (44 urlsafe-base64 characters, what `generate_secret()` prints) or any
#: passphrase, which is stretched into one. The second form exists because
#: `fly secrets set PULSE_SECRET_KEY=$(openssl rand -hex 32)` is what people
#: actually type, and rejecting it would only send them hunting for a generator.
SECRET_ENV = "PULSE_SECRET_KEY"

#: Set during a rotation: rows sealed by the previous key still decrypt, while
#: new writes use the current one. Unset it once `rotate()` reports every row
#: rewritten.
PREVIOUS_SECRET_ENV = "PULSE_SECRET_KEY_PREVIOUS"

#: Fixed, and deliberately so. HKDF's salt need not be secret, and a random one
#: would have to be stored beside the ciphertext - at which point the same
#: passphrase would derive a different key on every machine, and a database
#: restored somewhere else would be unreadable.
_KDF_SALT = b"projectpulse.llm.keys.v1"


class KeyStoreUnavailable(RuntimeError):
    """No usable `PULSE_SECRET_KEY`, so nothing can be sealed or opened.

    Raised rather than falling back to plaintext. A secret store that silently
    degrades to storing secrets in the clear is worse than one that refuses,
    because the failure is invisible in exactly the case that matters.
    """


def _fernet():
    """The cipher, or `KeyStoreUnavailable` naming the piece that is missing."""
    try:
        from cryptography.fernet import Fernet, MultiFernet
    except ModuleNotFoundError as exc:  # pragma: no cover - declared in pyproject
        raise KeyStoreUnavailable(
            "the `cryptography` package is not installed, so keys cannot be "
            "encrypted; it is a hard dependency of this app - reinstall with "
            "`pip install -e .`"
        ) from exc

    raw = os.environ.get(SECRET_ENV, "").strip()
    if not raw:
        raise KeyStoreUnavailable(
            f"{SECRET_ENV} is not set, so there is nothing to encrypt keys "
            "with. Generate one with `python -m scripts.secret` and set it as "
            "a platform secret."
        )

    keys = [Fernet(_derive(raw))]
    previous = os.environ.get(PREVIOUS_SECRET_ENV, "").strip()
    if previous:
        keys.append(Fernet(_derive(previous)))
    # MultiFernet encrypts with the first and decrypts with any of them, which
    # is the whole rotation story: add the old key, re-seal, drop the old key.
    return MultiFernet(keys)


def _derive(secret: str) -> bytes:
    """A Fernet key from whatever someone set.

    A real Fernet key passes through untouched, so a generated one round-trips
    exactly; anything else is stretched with HKDF-SHA256.
    """
    candidate = secret.encode("utf-8")
    if len(candidate) == 44:
        try:
            if len(base64.urlsafe_b64decode(candidate)) == 32:
                return candidate
        except Exception:  # noqa: BLE001 - not a Fernet key, so derive one
            pass

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    derived = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=_KDF_SALT, info=b"fernet"
    ).derive(candidate)
    return base64.urlsafe_b64encode(derived)


def generate_secret() -> str:
    """A fresh value for `PULSE_SECRET_KEY`. Printed by `scripts.secret`."""
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode("ascii")


def available() -> bool:
    """Whether keys can be read and written at all, without raising to ask."""
    try:
        _fernet()
        return True
    except KeyStoreUnavailable:
        return False


def unavailable_reason() -> str:
    """Why the store is unusable, for the page to show. Empty when it is fine."""
    try:
        _fernet()
        return ""
    except KeyStoreUnavailable as exc:
        return str(exc)


def mask(key: str) -> str:
    """A hint that identifies a key without disclosing it.

    Short strings are hidden entirely rather than mostly shown, because a short
    secret is all suffix. The same rule `narration/store.py` used, kept
    identical so the two pages agree on what a hint looks like.
    """
    if not key:
        return ""
    if len(key) < 12:
        return "*" * len(key)
    return f"{key[:7]}...{key[-4:]}"


# --------------------------------------------------------------------------
# Storage.
# --------------------------------------------------------------------------


def save(provider: str, api_key: str, actor: str = "") -> str:
    """Seal a key and store it. Returns the hint the page may display.

    An empty `api_key` deletes the row, which is how the page clears a key -
    distinct from not submitting the field at all, which leaves it alone.
    """
    from app.db import session_scope
    from app.models.llm import LlmCredential

    provider = provider.strip().lower()
    if not api_key:
        delete(provider)
        return ""

    # Before the session opens, so a missing secret writes nothing at all.
    cipher = _fernet()
    token = cipher.encrypt(api_key.encode("utf-8")).decode("ascii")
    hint = mask(api_key)

    with session_scope() as session:
        session.merge(
            LlmCredential(
                provider=provider,
                ciphertext=token,
                hint=hint,
                key_version=_version(),
                updated_by=actor[:32],
            )
        )
    return hint


def delete(provider: str) -> None:
    from app.db import session_scope
    from app.models.llm import LlmCredential

    with session_scope() as session:
        row = session.get(LlmCredential, provider.strip().lower())
        if row is not None:
            session.delete(row)


def get(provider: str) -> str:
    """The stored key for a vendor, or `""` when there is none to read.

    Never raises. A missing secret, an unreadable row and no row at all all
    mean the same thing to a caller - fall back to the environment - and this
    sits on the path of every model call.
    """
    from app.db import session_scope
    from app.models.llm import LlmCredential

    try:
        cipher = _fernet()
        with session_scope() as session:
            row = session.get(LlmCredential, provider.strip().lower())
            if row is None or not row.ciphertext:
                return ""
            return cipher.decrypt(row.ciphertext.encode("ascii")).decode("utf-8")
    except Exception as exc:  # noqa: BLE001 - see the docstring
        log.warning("could not read the stored key for %s: %s", provider, exc)
        return ""


def _version() -> int:
    """1, or 2 while a previous key is still configured.

    Only used to tell rows sealed before and during a rotation apart on the
    status page; nothing depends on the number itself.
    """
    return 2 if os.environ.get(PREVIOUS_SECRET_ENV, "").strip() else 1


def status() -> list[dict]:
    """Per-vendor key state for the settings page. Never the keys themselves.

    Reports the environment separately from the database, because "no key saved
    here" must not read as "no key" - that is what sends someone pasting a
    secret into a store they did not need.
    """
    from app.db import session_scope
    from app.models.llm import LlmCredential
    from app.narration.providers import PROVIDERS
    from app.narration.store import ENV_KEYS

    rows: dict[str, LlmCredential] = {}
    try:
        with session_scope() as session:
            for row in session.query(LlmCredential).all():
                rows[row.provider] = row
    except Exception as exc:  # noqa: BLE001 - an empty table is the ordinary state
        log.warning("could not list the stored keys: %s", exc)

    # A row that exists is not the same as a key that works. One sealed by a
    # `PULSE_SECRET_KEY` this process no longer holds - a rotation done
    # without `rotate()`, a restore onto a different host - decrypts to
    # nothing, and reporting it as "saved" made the page contradict itself:
    # the key list said the vendor was configured while every feature beside
    # it said "no key". Each row is opened here so both read the same truth.
    cipher = None
    try:
        cipher = _fernet()
    except KeyStoreUnavailable:
        cipher = None

    def readable(row: LlmCredential) -> bool:
        if cipher is None or not row.ciphertext:
            return False
        try:
            cipher.decrypt(row.ciphertext.encode("ascii"))
            return True
        except Exception:  # noqa: BLE001 - sealed by a key we do not hold
            return False

    out = []
    for provider in PROVIDERS:
        row = rows.get(provider)
        env_names = ENV_KEYS.get(provider, ())
        from_env = next((n for n in env_names if os.environ.get(n)), "")
        usable = row is not None and readable(row)
        out.append(
            {
                "provider": provider,
                "stored": row is not None,
                "readable": usable,
                "hint": row.hint if row else "",
                "updated_at": row.updated_at.isoformat() if row else None,
                "updated_by": row.updated_by if row else "",
                "key_version": row.key_version if row else None,
                # Which environment variable carries a key for this vendor, if
                # any - named rather than a boolean, so the page can say where
                # to go and change it.
                "env_var": from_env,
                "from_environment": bool(from_env),
                # What a call would actually use. A stored key wins only when
                # it can actually be opened; otherwise the environment is what
                # the vendor's SDK will fall back to, which is what `resolve`
                # does too.
                "effective_source": (
                    "database" if usable else "environment" if from_env else "none"
                ),
                # Set only in the case worth explaining, so the page can say
                # what happened rather than showing a key that does nothing.
                "problem": (
                    "sealed with a different "
                    f"{SECRET_ENV} - paste the key again, or restore the "
                    "previous secret and run a rotation"
                    if row is not None and not usable and cipher is not None
                    else f"{SECRET_ENV} is not set, so this cannot be decrypted"
                    if row is not None and cipher is None
                    else ""
                ),
                "env_candidates": list(env_names),
            }
        )
    return out


def rotate() -> int:
    """Re-seal every row with the current key. Returns how many were rewritten.

    Run it after setting `PULSE_SECRET_KEY` to a new value and
    `PULSE_SECRET_KEY_PREVIOUS` to the old one. When it reports every row
    rewritten, unset the previous.
    """
    from app.db import session_scope
    from app.models.llm import LlmCredential

    cipher = _fernet()
    rewritten = 0
    with session_scope() as session:
        for row in session.query(LlmCredential).all():
            try:
                plain = cipher.decrypt(row.ciphertext.encode("ascii"))
            except Exception as exc:  # noqa: BLE001 - sealed by a key we no longer hold
                log.warning("cannot rotate %s: %s", row.provider, exc)
                continue
            row.ciphertext = cipher.encrypt(plain).decode("ascii")
            row.key_version = 1
            rewritten += 1
    return rewritten


def adopt_legacy() -> str:
    """Move a plaintext key out of `app_settings.narration` into this store.

    `narration/store.py` kept the key as a field on its JSON row. Rows like
    that still exist, so on first use the key is re-sealed here and blanked
    there, rather than left as a second, plaintext copy of the same secret.
    Returns the provider it migrated, or `""`.
    """
    from app.narration.store import load, update

    try:
        current = load()
        if not current.api_key:
            return ""
        save(current.provider, current.api_key, actor="legacy-migration")
        # `update()` alone clears it: `store.save()` strips the field on every
        # write now, so re-persisting the settings is the whole migration.
        update()
        log.info("migrated a plaintext %s key into the encrypted store", current.provider)
        return current.provider
    except Exception as exc:  # noqa: BLE001 - never block startup on this
        log.warning("could not migrate the legacy plaintext key: %s", exc)
        return ""
