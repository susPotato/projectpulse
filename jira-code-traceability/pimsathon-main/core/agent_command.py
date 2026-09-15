"""Parse a ``/agent`` command typed in a chat box (Cowork parity with Co4E).

An *agent* directive applies a named agent PERSONA to the turn: its role +
instructions are prepended to the request as a system-style prefix, exactly the
way ``skills.parse_skill_command`` applies a skill. Only the WORK agents a user
composes with are callable here:

  * built-in Co4E personas (``co4e_builtins.BUILTIN_AGENTS``) — includes the
    delivery-lifecycle + Security agents;
  * per-user custom Co4E agents (``co4e.list_custom_agents``).

Admin-defined agents (Monitoring → Agents Admin) are DELIBERATELY excluded:
they are system-management presets (help agent, task executors, …), not agents
to pick in Cowork/Co4E — so they never appear in the ``/agent`` list or the
Agent picker.

Only the persona (prompt) is applied — NOT a per-turn model/provider switch:
a Cowork panel runs several turns concurrently, so a shared model override would
race between them; the Agent picker remains the way to pin a model/provider.
Pure logic (no Qt) so it's unit-testable.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Same shape as skills.parse_skill_command: the command may sit mid-sentence, so
# trailing punctuation must not break recognition.
_CMD = re.compile(r"(?<!\S)/agent(?::([\w\-.]+))?(?=$|[\s.,;:!?)\]}»”’'\"、。」])",
                  re.DOTALL)


def _slug(name: str) -> str:
    from .co4e import slugify
    return slugify(name)


def collect_agents(shared_dir: str = "") -> List[dict]:
    """Every callable WORK agent as ``{slug, name, desc, persona, source}``,
    de-duped by slug (built-in > custom). Admin agents are intentionally NOT
    included — they are system-management presets, not Cowork/Co4E choices (see
    the module docstring). ``shared_dir`` is kept for signature stability. Never
    raises — a broken source is skipped so the command still works."""
    out: List[dict] = []
    seen: set[str] = set()

    def _add(slug: str, name: str, desc: str, persona: str, source: str) -> None:
        if not slug or slug in seen or not persona.strip():
            return
        seen.add(slug)
        out.append({"slug": slug, "name": name, "desc": desc,
                    "persona": persona.strip(), "source": source})

    try:
        from .co4e_builtins import BUILTIN_AGENTS
        for a in BUILTIN_AGENTS:
            _add(a.slug, a.name, a.role,
                 f"You are the {a.role} agent — {a.name}.\n{a.instructions}", "builtin")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import co4e
        for ca in co4e.list_custom_agents():
            _add(_slug(ca.name), ca.name, ca.role,
                 f"You are the {ca.role} agent — {ca.name}.\n{ca.instructions}", "custom")
    except Exception:  # noqa: BLE001
        pass
    return out


def _persona_block(agent: dict) -> str:
    return f"## Agent: {agent['name']}\n{agent['persona']}"


def parse_agent_command(text: str, shared_dir: str = "") -> Tuple[str, str, Optional[str]]:
    """Parse a ``/agent`` command anywhere in ``text``.

    Returns ``(prefix, request, info)``:
      * ``prefix``  – agent persona to prepend to the agent prompt ('' if none)
      * ``request`` – the message with the command stripped
      * ``info``    – when not None, answer this inline (no agent turn)

    Forms: ``/agent`` (list) · ``/agent:<name> <req>`` (apply that agent).
    """
    raw = (text or "").strip()
    m = _CMD.search(raw)
    if not m:
        return "", text, None
    slug = m.group(1)
    if slug:
        slug = slug.rstrip(".")   # "/agent:name." — the dot was sentence punctuation
    rest = (raw[:m.start()] + " " + raw[m.end():]).strip()
    rest = re.sub(r"[ \t]{2,}", " ", rest)
    agents = collect_agents(shared_dir)

    if slug:
        low = slug.lower()
        match = next((a for a in agents if a["slug"] == low or a["name"].lower() == low), None)
        if match is None:
            return "", text, (f"Agent `{slug}` not found. "
                              "Type `/agent` to see the available agents.")
        if not rest:
            return "", text, (f"Agent **{match['name']}** selected — add your request, e.g. "
                              f"`/agent:{match['slug']} review this file`.")
        return _persona_block(match), rest, None

    # Bare /agent → list what's available.
    if not agents:
        return "", text, ("No agents found. Add one in Agents Admin, or a custom Flow agent.")
    listing = "\n".join(
        f"- `/agent:{a['slug']}` — **{a['name']}**"
        + (f"  _({a['desc']})_" if a.get("desc") else "")
        for a in agents)
    return "", text, ("**Available agents**\n" + listing
                      + "\n\nApply one with `/agent:<name> <your request>`.")
