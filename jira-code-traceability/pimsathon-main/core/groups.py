"""Groups — the org unit a Sub-admin manages (tree: Group -> Sub-admin ->
members). Stored one JSON file per group under ``<shared_dir>/groups/``, the
same shared folder as ``accounts.py`` (see ``config.py``'s ``auth.shared_dir``).
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass
class Group:
    group_id: str
    name: str
    subadmin_username: str = ""
    member_usernames: List[str] = field(default_factory=list)
    created: str = ""


def groups_dir(shared_dir: str) -> Path:
    return Path(shared_dir).expanduser() / "groups"


def new_group(name: str, subadmin_username: str = "") -> Group:
    return Group(group_id=uuid.uuid4().hex, name=name.strip() or "Group",
                subadmin_username=subadmin_username,
                created=datetime.now().isoformat(timespec="seconds"))


def save_group(group: Group, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{group.group_id}.json"
    path.write_text(json.dumps(asdict(group), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_group(group_id: str, directory: Path) -> Optional[Group]:
    safe_id = re.sub(r"[^\w\-]", "", group_id or "")
    path = directory / f"{safe_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in Group.__dataclass_fields__}
        return Group(**{k: v for k, v in data.items() if k in known})
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_groups(directory: Path) -> List[Group]:
    if not directory.exists():
        return []
    out: List[Group] = []
    for path in sorted(directory.glob("*.json")):
        g = load_group(path.stem, directory)
        if g is not None:
            out.append(g)
    out.sort(key=lambda g: g.name.lower())
    return out


def delete_group(group_id: str, directory: Path) -> bool:
    safe_id = re.sub(r"[^\w\-]", "", group_id or "")
    if not safe_id:
        return False
    path = directory / f"{safe_id}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def find_or_create_by_name(name: str, directory: Path) -> Group:
    """The group named ``name`` (case-insensitive match), creating one if it
    doesn't exist yet — used by the Excel bulk-import AND by login-time
    department auto-grouping so both paths land in the exact same group
    rather than creating near-duplicate "FA.PDS"/"fa.pds" groups."""
    name = (name or "").strip()
    for g in list_groups(directory):
        if g.name.strip().lower() == name.lower():
            return g
    group = new_group(name)
    save_group(group, directory)
    return group


def ensure_member(group: Group, username: str, directory: Path) -> None:
    """Add ``username`` to ``group``'s members if not already the subadmin or
    a member; no-op (and no rewrite) otherwise."""
    uname = (username or "").strip().lower()
    if not uname or group.subadmin_username.strip().lower() == uname:
        return
    if uname in {m.strip().lower() for m in group.member_usernames}:
        return
    group.member_usernames.append(username)
    save_group(group, directory)


def group_for_user(username: str, directory: Path) -> Optional[Group]:
    """The group a Sub-admin manages, or the group a member belongs to."""
    uname = (username or "").strip().lower()
    if not uname:
        return None
    for g in list_groups(directory):
        members = {m.strip().lower() for m in g.member_usernames}
        if g.subadmin_username.strip().lower() == uname or uname in members:
            return g
    return None
