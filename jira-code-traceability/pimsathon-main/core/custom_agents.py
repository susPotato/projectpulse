"""Custom Agent presets: reusable, user-defined sub-agents.

An *agent* here is a named preset — a task prompt (+ optional provider
override) that the user builds once in the Agent Manager tab and then reuses
as a parallel sub-agent from any Flow stage, instead of retyping the same
name/task by hand every time.

Stored as one JSON file per agent under ``~/.cowork_local/agents/``.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List

from ..config import CONFIG_DIR

AGENTS_DIR = CONFIG_DIR / "agents"


@dataclass
class CustomAgent:
    name: str
    description: str = ""
    prompt: str = ""     # default task; a Flow sub-agent can still override it
    provider: str = ""   # AI provider key override ("" = use the step's/default provider)
    model: str = ""      # model (Agent) within that provider ("" = provider default)

    @property
    def slug(self) -> str:
        keep = "-_"
        s = "".join(c if (c.isalnum() or c in keep) else "-" for c in self.name.strip().lower())
        return "-".join(filter(None, s.split("-"))) or "agent"


def agents_dir() -> Path:
    return AGENTS_DIR


def list_agents(directory: Path = AGENTS_DIR) -> List[CustomAgent]:
    if not directory.exists():
        return []
    agents: List[CustomAgent] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            agents.append(CustomAgent(
                name=data.get("name", path.stem),
                description=data.get("description", ""),
                prompt=data.get("prompt", ""),
                provider=data.get("provider", ""),
                model=data.get("model", ""),
            ))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return agents


def save_agent(agent: CustomAgent, directory: Path = AGENTS_DIR, old_name: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if old_name and old_name != agent.name:
        delete_agent(old_name, directory)
    path = directory / f"{agent.slug}.json"
    path.write_text(json.dumps(asdict(agent), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_agent(name: str, directory: Path = AGENTS_DIR) -> None:
    path = directory / f"{CustomAgent(name=name).slug}.json"
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def generate_agent_prompt(provider, name: str = "", description: str = "", cancel=None) -> str:
    """Best-effort: turn a short description into the default task PROMPT of
    a reusable Agent preset. Returns '' on any error (so the dialog never
    breaks)."""
    name, description = (name or "").strip(), (description or "").strip()
    if not name and not description:
        return ""
    user = (f"Agent name: {name}\n" if name else "") + f"Short description: {description}"
    messages = [
        {"role": "system", "content":
            "You write the default TASK PROMPT for a reusable sub-agent preset. Given a short "
            "name/description, produce ONE concise, actionable instruction (2-4 sentences) telling "
            "a coding/assistant agent exactly what to do whenever this preset is used. Reply with "
            "ONLY the task text — no preamble, no markdown heading."},
        {"role": "user", "content": user},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001 - generation must never break the dialog
        return ""
    return (a.get("content") or "").strip()
