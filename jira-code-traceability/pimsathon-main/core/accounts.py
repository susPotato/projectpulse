"""Accounts + RBAC — Admin/Sub-admin/User identities shared across machines.

Stored one JSON file per account under ``<shared_dir>/accounts/`` (a plain
shared folder path — network share or a locally-synced OneDrive folder, see
``config.py``'s ``auth.shared_dir``). Deliberately NOT routed through the
Microsoft Graph API: Graph has no write access to an arbitrary share link,
only to the signed-in user's own drive, so a shared mutable store has to be
plain file I/O against a configured path instead.

Login validates a 12-character access code issued by an Admin (``code``),
or — for SSO — an already-verified company identity is matched to an
existing account by username (see ``ms365_auth.py``); SSO never creates an
account on its own, an Admin always provisions it first.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set, Tuple

from ..config import CONFIG_DIR

ROLES = ("admin", "subadmin", "user")

# A small LOCAL (never shared-folder) cache of the last successful login's
# identity — username + role only, never the access code — so a laptop that's
# off-VPN/off-network can still open the app as its last-known role. A
# revoked/edited account only takes effect once the shared folder is
# reachable again; see login_dialog.py.
_LAST_LOGIN_PATH = CONFIG_DIR / "last_login.json"


def save_last_login(username: str, role: str) -> None:
    try:
        _LAST_LOGIN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_LOGIN_PATH.write_text(
            json.dumps({"username": username, "role": role}), encoding="utf-8")
    except OSError:
        pass


def load_last_login() -> Optional[Tuple[str, str]]:
    try:
        data = json.loads(_LAST_LOGIN_PATH.read_text(encoding="utf-8"))
        username, role = data.get("username", ""), data.get("role", "")
        if username and role in ROLES:
            return username, role
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None

# Unambiguous alphanumeric alphabet for issued access codes — excludes
# characters easy to mis-type/mis-read (0/O, 1/I).
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 12


@dataclass
class Account:
    username: str
    role: str
    display_name: str = ""
    department: str = ""
    email: str = ""
    group_id: str = ""
    code: str = ""
    created: str = ""
    created_by: str = ""


def accounts_dir(shared_dir: str) -> Path:
    return Path(shared_dir).expanduser() / "accounts"


def _safe_username(username: str) -> str:
    """Normalize to lowercase alnum/./- only — matches the login screen's
    own auto-lowercase behavior, so a username is a stable, safe filename."""
    return re.sub(r"[^\w.\-]", "", (username or "").strip().lower())


def generate_code(existing_codes: Optional[Set[str]] = None) -> str:
    """A random, non-repeating 12-character access code."""
    existing = existing_codes or set()
    for _ in range(1000):
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if code not in existing:
            return code
    raise RuntimeError("Could not generate a unique access code.")


def save_account(account: Account, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_username(account.username)}.json"
    path.write_text(json.dumps(asdict(account), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_account(username: str, directory: Path) -> Optional[Account]:
    path = directory / f"{_safe_username(username)}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in Account.__dataclass_fields__}
        return Account(**{k: v for k, v in data.items() if k in known})
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_accounts(directory: Path) -> List[Account]:
    if not directory.exists():
        return []
    out: List[Account] = []
    for path in sorted(directory.glob("*.json")):
        acc = load_account(path.stem, directory)
        if acc is not None:
            out.append(acc)
    out.sort(key=lambda a: a.username)
    return out


def delete_account(username: str, directory: Path) -> bool:
    path = directory / f"{_safe_username(username)}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def find_by_username(username: str, directory: Path) -> Optional[Account]:
    return load_account(username, directory)


def verify_login(username: str, code: str, directory: Path) -> Optional[Account]:
    """The matching Account when ``username``/``code`` are a valid pair."""
    account = load_account(username, directory)
    if account is None or not code or not account.code:
        return None
    return account if account.code == code else None


def new_account(username: str, role: str, display_name: str = "", department: str = "",
                email: str = "", group_id: str = "", created_by: str = "",
                existing_codes: Optional[Set[str]] = None) -> Account:
    """A fresh Account with a freshly generated, unique access code."""
    return Account(
        username=_safe_username(username),
        role=role if role in ROLES else "user",
        display_name=display_name,
        department=department,
        email=email,
        group_id=group_id,
        code=generate_code(existing_codes),
        created=datetime.now().isoformat(timespec="seconds"),
        created_by=created_by,
    )


# ---- single-admin invariant -------------------------------------------------
# The app allows exactly ONE account with role="admin" per shared folder. The
# helpers below are how every create/promote path checks and (for the
# first-run bootstrap) atomically claims that slot.

def admin_exists(directory: Path, exclude_username: str = "") -> bool:
    """True when some account other than ``exclude_username`` already holds
    the admin role."""
    return any(a.role == "admin" and a.username != _safe_username(exclude_username)
               for a in list_accounts(directory))


def claim_admin_slot(directory: Path) -> bool:
    """Atomically claim the right to create THE admin account.

    ``list-then-write`` alone leaves a race window: two never-configured
    machines pointed at the same share can both see an empty accounts folder
    and both create an "admin". An exclusive-create marker file closes it —
    ``open(..., "x")`` either succeeds for exactly one caller or raises for
    everyone else (also for later callers after a crash mid-bootstrap, which
    is fine: the marker plus admin_exists() are both checked by the caller).
    Returns True when this caller won the claim."""
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / ".admin_claimed"
    try:
        with open(marker, "x", encoding="utf-8") as fh:
            fh.write(datetime.now().isoformat(timespec="seconds"))
        return True
    except FileExistsError:
        return False
    except OSError:
        # Filesystems that can't do exclusive create (rare) — fall back to
        # the plain existence check so bootstrap isn't bricked entirely.
        return not admin_exists(directory)
