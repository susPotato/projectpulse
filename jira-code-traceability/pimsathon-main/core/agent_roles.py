"""🤖 Agent Core — role registry.

Cowork Local runs one engine per surface (Cowork tab, Schedule Task,
GraphRAG codebase-memory chat) rather than a hand-off pipeline between
separate agent processes. This registry gives each already-existing
behavior one of the reference role names, purely so audit-log entries
and the Monitoring Dashboard can group activity by role — it introduces
no new orchestration logic.

PLANNER   — the ``update_plan`` tool call inside ``chat_agent.run_cowork``'s
            loop.
REASONING — the model's streamed ``on_reasoning`` output (chat_agent /
            code_agent). Deliberately NOT written to the audit log (that
            would be one entry per streamed token) — the Monitoring
            Dashboard's Agent Status panel instead reads live AgentWorker
            state, which already reflects a run in progress.
CODE      — ``code_agent.run_code``'s tool-execution loop.
KNOWLEDGE — GraphRAG's codebase-memory "Ask" (``_ask`` in
            ``structure_graph_view.py``).
TASK      — Schedule Task's unattended ``_run_agent`` loop.
COWORK    — the Cowork tab's own interactive tool-use loop — the same
            engine TASK's ``cowork``-type tasks run, but ``run_cowork``'s
            default role when no override is given.
"""
from __future__ import annotations

from typing import Dict, NamedTuple

PLANNER = "planner"
REASONING = "reasoning"
CODE = "code"
KNOWLEDGE = "knowledge"
TASK = "task"
COWORK = "cowork"
SECURITY = "security"
HELP = "help"


class AgentRole(NamedTuple):
    key: str
    label: str
    description: str


ROLES: Dict[str, AgentRole] = {
    PLANNER: AgentRole(PLANNER, "Planner Agent", "Builds/updates the step plan (update_plan)."),
    REASONING: AgentRole(REASONING, "Reasoning Agent", "The model's streamed reasoning output."),
    CODE: AgentRole(CODE, "Code Agent", "Sandboxed run_code tool-execution loop."),
    KNOWLEDGE: AgentRole(KNOWLEDGE, "Knowledge Agent", "GraphRAG codebase-memory Ask / feature management."),
    TASK: AgentRole(TASK, "Task Agent", "Schedule Task's unattended run."),
    COWORK: AgentRole(COWORK, "Cowork Agent", "The Cowork tab's interactive tool-use loop."),
    SECURITY: AgentRole(SECURITY, "Security Agent",
                        "System-management agent: Agent Security's prompt/attachment/command "
                        "validation (core/agent_security.py). Runs inline on the active turn's "
                        "provider before a request/command is allowed."),
    HELP: AgentRole(HELP, "Help Agent",
                    "The floating in-app assistant (ui/help_agent_widget.py): answers "
                    "how-to-use-the-app questions only, no tools, replies in the user's "
                    "own language."),
}


def label_for(role_key: str) -> str:
    role = ROLES.get(role_key)
    return role.label if role else (role_key or "—")
