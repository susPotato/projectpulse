"""Projects (workspaces) — Claude-Projects-style grouping for conversations.

A *project* groups chat threads that share one context, the way Claude's
Projects do:

* **Instructions** — free text injected into the system prompt of EVERY chat
  in the project, so all threads follow the same project context.
* **Workspace (sandbox)** — each project owns its own folder; the AI agent's
  file/command tools are confined to it (``ToolContext.resolve`` rejects any
  path outside), so one project's agent can never touch another project's
  files. Files placed at the workspace root are the project's *knowledge*:
  every chat auto-reads them as input.
* **Threads** — conversations carry a ``project_id``; History and the
  Workspace screen group them per project.

Stored one JSON file per project under ``~/.cowork_local/projects/``.

There is no longer a special, undeletable "General" project. Instead, a normal
**starter project** (id ``default`` for backward-compat, so pre-existing
conversations tagged ``default`` still attach to it) is seeded the first time
the projects folder is empty. It can be renamed and deleted like any other
project — nothing about it is special-cased in the UI.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..config import CONFIG_DIR

PROJECTS_DIR = CONFIG_DIR / "projects"
WORKSPACES_DIR = CONFIG_DIR / "workspaces"
# The id of the auto-seeded starter project. It keeps the legacy value
# ``default`` only so conversations saved before Projects existed (they were
# tagged ``project_id="default"``) still land in it. It is NOT special —
# it can be renamed and deleted like any other project.
DEFAULT_PROJECT_ID = "default"
STARTER_PROJECT_NAME = "My Workspace"


@dataclass
class Project:
    project_id: str
    name: str
    description: str = ""
    instructions: str = ""   # shared context — injected into every chat's system prompt
    output_dir: str = ""     # optional custom workspace folder; empty → managed sandbox
    created: str = ""
    # ---- Per-workspace mode overrides (Auto Model Routing + Auto-run) --------
    # Each workspace (project) remembers its OWN modes, independent of other
    # workspaces, falling back to the global defaults when unset. See
    # AppContext.project_routing_mode / project_confirm_commands.
    #   routing_modes: {surface: "off"|"auto"|"manual"}; missing/"" → follow the
    #     global routing.switch_mode. Surfaces: "cowork" | "co4e" | "ai_edit".
    routing_modes: Dict[str, str] = field(default_factory=dict)
    #   auto_run: None → follow the global agent_security.cowork_confirm_commands;
    #     True → auto-approve commands (no confirm); False → always confirm.
    auto_run: Optional[bool] = None

    def workspace_dir(self, base: Path = None) -> Path:
        """The project's sandbox root. Every chat of the project writes inside
        it (one sub-folder per session) and the agent's tools are confined to
        it. Files at this root are the project's shared knowledge."""
        if self.output_dir.strip():
            return Path(self.output_dir).expanduser()
        return (base or WORKSPACES_DIR) / self.project_id


def _starter_project() -> Project:
    """An ordinary (deletable, renamable) project seeded when the projects
    folder is empty, so the app always opens with somewhere to chat."""
    return Project(project_id=DEFAULT_PROJECT_ID, name=STARTER_PROJECT_NAME,
                   description="", instructions="",
                   created=datetime.now().isoformat(timespec="seconds"))


def ensure_starter_project(directory: Path = None) -> Project:
    """Guarantee at least one project exists. If the projects folder has no
    project files yet, seed the starter project (id ``default``) and return it;
    otherwise return the first existing project. Idempotent."""
    directory = directory or PROJECTS_DIR
    existing = list_projects(directory)
    if existing:
        return existing[0]
    project = _starter_project()
    save_project(project, directory)
    return project


def _slugify(name: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip().lower())
    s = "-".join(filter(None, s.split("-")))
    return s or "project"


def new_project(name: str, description: str = "", instructions: str = "",
                output_dir: str = "", directory: Path = None) -> Project:
    """Create + persist a new project with a unique id derived from the name."""
    directory = directory or PROJECTS_DIR
    base = _slugify(name)
    pid, n = base, 2
    while pid == DEFAULT_PROJECT_ID or (directory / f"{pid}.json").exists():
        pid = f"{base}-{n}"
        n += 1
    project = Project(project_id=pid, name=name.strip() or pid,
                      description=description, instructions=instructions,
                      output_dir=output_dir,
                      created=datetime.now().isoformat(timespec="seconds"))
    save_project(project, directory)
    return project


def save_project(project: Project, directory: Path = None) -> Path:
    directory = directory or PROJECTS_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{project.project_id}.json"
    path.write_text(json.dumps(asdict(project), ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def load_project(project_id: str, directory: Path = None) -> Optional[Project]:
    """Load one project from disk, or None if it does not exist. No project is
    special-cased any more — a missing id simply returns None (callers treat
    that as 'no project context')."""
    directory = directory or PROJECTS_DIR
    safe_id = re.sub(r"[^\w\-]", "", project_id or "")
    path = directory / f"{safe_id}.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("project_id", path.stem)
            known = {f for f in Project.__dataclass_fields__}
            return Project(**{k: v for k, v in data.items() if k in known})
        except (OSError, json.JSONDecodeError, TypeError):
            return None
    return None


def list_projects(directory: Path = None) -> List[Project]:
    """Every stored project, sorted by name. The starter project (id
    ``default``) is no longer forced to the top — it sorts like any other."""
    directory = directory or PROJECTS_DIR
    out: List[Project] = []
    if directory.exists():
        for path in sorted(directory.glob("*.json")):
            p = load_project(path.stem, directory)
            if p is not None:
                out.append(p)
    out.sort(key=lambda p: p.name.lower())
    return out


def delete_project(project_id: str, directory: Path = None) -> bool:
    """Delete a project file (any project — nothing is undeletable now). The
    project's conversations and workspace files are NOT deleted; its threads
    just stop matching a project group in History until reassigned."""
    directory = directory or PROJECTS_DIR
    safe_id = re.sub(r"[^\w\-]", "", project_id or "")
    if not safe_id:
        return False
    path = directory / f"{safe_id}.json"
    try:
        path.unlink()
        return True
    except OSError:
        return False


def project_context_text(project: Optional[Project]) -> str:
    """The system-prompt block for a project's shared instructions ('' when
    there is nothing to inject)."""
    if project is None or not project.instructions.strip():
        return ""
    return (f"## Project context — {project.name}\n"
            "Every conversation in this project follows these shared instructions:\n"
            f"{project.instructions.strip()}")
