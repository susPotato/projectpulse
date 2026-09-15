"""Admin-managed Agent catalog — named agent presets every machine shares.

An *admin agent* is defined once by the Admin (Monitoring → Agents Admin):
a name, the app function it performs (picked from a fixed droplist —
search / monitor / cowork / graphrag / schedule / security), optional extra
instructions, and the model to run on. The model defaults to each machine's
own Settings model when left empty; when the Admin pins one, every machine
runs that agent on the pinned model.

Storage mirrors ``accounts.py``'s pattern: one JSON per agent under
``<shared_dir>/agents_admin/`` so the catalog syncs across machines through
the same OneDrive/network share the accounts already use (the Admin edits,
other machines pick the change up next refresh once the share syncs). With
no shared folder configured it falls back to a local folder so the feature
still works single-machine.

This catalog is deliberately SEPARATE from ``custom_agents.py`` (per-user
Flow sub-agent presets stored locally): these are org-wide, admin-owned, and
selectable from the Cowork tab's Agent picker and the Schedule Task editor.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..config import CONFIG_DIR

# The app functions an agent can be assigned to (droplist in the editor).
# Labels come from i18n keys ``agents_admin.kind.<kind>``.
TASK_KINDS = ("search", "monitor", "cowork", "graphrag", "schedule", "security", "help")

# Stable id for the built-in in-app Help assistant (the floating icon widget).
# Seeded once so the Admin can pick its provider/model in Agents Admin, while
# the widget always looks it up by this id.
HELP_AGENT_ID = "help-agent-builtin"

# Base instructions injected for each kind — the admin's own prompt (if any)
# is appended after these.
_KIND_PROMPTS = {
    "search": ("You are a dedicated SEARCH agent: locate the requested information in the "
               "provided files/folders/links and answer with precise findings and their "
               "sources. Do not create files unless explicitly asked."),
    "monitor": ("You are a dedicated MONITORING agent: review the provided logs/data for "
                "anomalies, errors, security events and trends, and report a concise "
                "status summary with anything needing attention first."),
    "cowork": "",
    "graphrag": ("You are a dedicated KNOWLEDGE agent: answer strictly from the project's "
                 "knowledge files/graph, citing which file each fact came from."),
    "schedule": ("You are a dedicated TASK agent executing a scheduled job: complete the "
                 "task end-to-end without asking questions, and save the deliverable."),
    "security": ("You are a dedicated SECURITY agent: review the given prompt/attachment/"
                 "command against the org security rules and decide whether it is safe to "
                 "allow. Reply strictly with the requested JSON verdict; err on the side of "
                 "blocking anything that could exfiltrate data or damage the system."),
    "help": ("You are the in-app HELP assistant for this desktop application. Your ONLY job "
             "is to help the user understand and use THIS app — its screens and features "
             "(Dashboard, Schedule, Workspace with Cowork chat and the Co4E flow studio, "
             "Monitoring, Connectors, Settings), how to get things done in it, and how to "
             "troubleshoot using it. Be concise, friendly and practical.\n"
             "STRICT RULES:\n"
             "- Answer ONLY questions about using this app. If asked to do anything else "
             "(write code for other purposes, do general research, chit-chat, run tasks, "
             "act as a general assistant), politely decline and steer back to app help.\n"
             "- You have no tools and cannot perform actions — you only explain and guide.\n"
             "- ALWAYS reply in the SAME language the user wrote their message in, "
             "regardless of the app's display language."),
}


@dataclass
class AdminAgent:
    agent_id: str
    name: str
    task_kind: str = "cowork"
    prompt: str = ""       # admin's extra instructions (appended to the kind's base)
    provider: str = ""     # "" = each machine's active provider
    model: str = ""        # "" = each machine's Settings model for that provider
    enabled: bool = True
    updated: str = ""
    updated_by: str = ""

    def effective_prompt(self) -> str:
        parts = [_KIND_PROMPTS.get(self.task_kind, ""), (self.prompt or "").strip()]
        return "\n\n".join(p for p in parts if p)


def agents_admin_dir(shared_dir: str = "") -> Path:
    """Shared catalog folder when a shared dir is configured (cross-machine
    sync), else a local fallback so the feature works single-machine too."""
    if (shared_dir or "").strip():
        return Path(shared_dir).expanduser() / "agents_admin"
    return CONFIG_DIR / "agents_admin"


def _slug(name: str) -> str:
    s = re.sub(r"[^\w\-]+", "-", (name or "").strip().lower()).strip("-")
    return s or "agent"


def new_agent(name: str, task_kind: str = "cowork", prompt: str = "",
              provider: str = "", model: str = "", updated_by: str = "") -> AdminAgent:
    return AdminAgent(
        agent_id=f"{_slug(name)}-{uuid.uuid4().hex[:6]}",
        name=name.strip(), task_kind=task_kind if task_kind in TASK_KINDS else "cowork",
        prompt=prompt, provider=provider, model=model, enabled=True,
        updated=datetime.now().isoformat(timespec="seconds"), updated_by=updated_by,
    )


def save_agent(agent: AdminAgent, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{agent.agent_id}.json"
    path.write_text(json.dumps(asdict(agent), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_agent(agent_id: str, directory: Path) -> Optional[AdminAgent]:
    path = directory / f"{agent_id}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f for f in AdminAgent.__dataclass_fields__}
        return AdminAgent(**{k: v for k, v in data.items() if k in known})
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def list_agents(directory: Path, enabled_only: bool = False) -> List[AdminAgent]:
    if not directory.exists():
        return []
    out: List[AdminAgent] = []
    for path in sorted(directory.glob("*.json")):
        agent = load_agent(path.stem, directory)
        if agent is not None and (agent.enabled or not enabled_only):
            out.append(agent)
    out.sort(key=lambda a: a.name.lower())
    return out


def ensure_help_agent(directory: Path) -> AdminAgent:
    """Return the built-in Help assistant, seeding it on first run so it shows
    up in Agents Admin for the admin to pick a provider/model. Idempotent: an
    existing entry (possibly with admin edits) is loaded and returned as-is —
    only its immutable identity (id / kind) is guaranteed. The floating Help
    widget always resolves the agent through this."""
    existing = load_agent(HELP_AGENT_ID, directory)
    if existing is not None:
        return existing
    agent = AdminAgent(
        agent_id=HELP_AGENT_ID, name="App Help Assistant", task_kind="help",
        prompt="", provider="", model="", enabled=True,
        updated=datetime.now().isoformat(timespec="seconds"), updated_by="system",
    )
    try:
        save_agent(agent, directory)
    except OSError:
        pass   # read-only share — still usable in-memory this session
    return agent


def delete_agent(agent_id: str, directory: Path) -> bool:
    try:
        (directory / f"{agent_id}.json").unlink()
        return True
    except OSError:
        return False


def build_agent_provider(ctx, agent: Optional[AdminAgent]):
    """The provider an admin agent runs on: its own pinned provider/model
    when set, else the machine's active provider with its Settings model —
    exactly the default the requirement asks for ("default là Model được
    chọn trong setting")."""
    if agent is None:
        return ctx.build_active_provider()
    return ctx.build_provider_for(agent.provider or None, agent.model or None)


def check_agent(ctx, agent: AdminAgent) -> tuple[bool, str]:
    """Best-effort OPERATIONAL health check for one admin agent: can its
    EFFECTIVE provider (its pinned provider/model, or — when unset — the
    machine's Settings provider/model) actually be reached right now?

    Probes the provider's ``list_models()`` (the same lightweight call the
    'Load models' button makes) rather than spending a real chat turn.
    Returns ``(ok, message)`` and NEVER raises, so the UI can render a status
    without a broken agent config taking the whole table down."""
    if not agent.enabled:
        return False, "disabled"
    try:
        provider = build_agent_provider(ctx, agent)
    except Exception as exc:  # noqa: BLE001 — a bad config must not crash the check
        return False, f"config error: {exc}"
    try:
        models = provider.list_models()
    except Exception as exc:  # noqa: BLE001 — gateway unreachable / auth / TLS…
        return False, str(exc)[:200]
    if not models:
        return False, "no models returned by provider"
    if agent.model and agent.model not in models:
        return True, f"reachable — note: pinned model '{agent.model}' not in provider's list"
    return True, "reachable"
