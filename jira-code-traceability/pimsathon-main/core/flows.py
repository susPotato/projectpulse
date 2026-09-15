"""Flows: multi-step "Requirement → Demo" pipelines for the Code tab.

A flow is an ordered list of steps. Each step carries a prompt, an optional
skill to apply, an optional AI agent (provider), and a hint. Flows can be saved
as reusable templates and executed step-by-step by the Code agent.

Stored as one JSON file per flow under ``~/.cowork_local/flows/``.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from ..config import CONFIG_DIR

FLOWS_DIR = CONFIG_DIR / "flows"


@dataclass
class SubAgent:
    """One concurrent worker inside a "parallel" stage (see FlowStep.parallel_agents)."""
    name: str
    prompt: str = ""   # task for this sub-agent; falls back to the step's own prompt if empty
    agent: str = ""    # AI provider key override ("" = use the step's/default provider)
    model: str = ""    # model override within that provider ("" = provider default)


@dataclass
class FlowStep:
    name: str
    prompt: str = ""
    skill: str = ""    # skill name to apply on this step ("" = none)
    agent: str = ""    # AI provider key ("" = dùng provider đang chọn)
    model: str = ""    # model (Agent) within the provider ("" = provider default)
    hint: str = ""     # gợi ý để thực thi
    attachments: List[str] = field(default_factory=list)  # files fed to this stage's prompt
    compact_after_run: bool = False   # trim old history before the NEXT stage starts
    self_verify: bool = False         # ask the agent to confirm completeness before handoff
    review_retries: int = 0           # re-run this stage up to N times if self-verify fails
    parallel_agents: List[SubAgent] = field(default_factory=list)  # non-empty = fan-out stage

    @property
    def is_parallel(self) -> bool:
        return bool(self.parallel_agents)


@dataclass
class Flow:
    name: str
    description: str = ""
    steps: List[FlowStep] = field(default_factory=list)


# Live run-status of a flow's steps (rendered by the Workflow view in the Code
# tab's preview panel). Kept here, free of any Qt import, so it is unit-testable.
STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_DONE = "done"
STEP_ERROR = "error"


@dataclass
class FlowRunStatus:
    """Tracks how far a running flow has progressed.

    ``done`` = number of finished steps; the step at index ``done`` is the one
    currently running (until ``finished``). Advance once per completed turn.
    ``substeps`` holds the running stage's sub-plan (``[{title, status}]``) so the
    Plan checklist nests under each Workflow stage."""
    step_names: List[str]
    done: int = 0
    finished: bool = False
    last_error: bool = False
    substeps: List[dict] = field(default_factory=list)

    def state_of(self, i: int) -> str:
        if i < self.done:
            if self.last_error and i == self.done - 1:
                return STEP_ERROR
            return STEP_DONE
        if i == self.done and not self.finished:
            return STEP_RUNNING
        return STEP_PENDING

    def advance(self, error: bool = False) -> bool:
        """Mark the current step complete; the next becomes running. Returns
        True once the whole flow is finished. Never advances past the last step."""
        if self.finished:
            return True
        self.last_error = error
        self.done = min(self.done + 1, len(self.step_names))
        self.finished = self.done >= len(self.step_names)
        return self.finished


def _slug(name: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip().lower())
    return "-".join(filter(None, s.split("-"))) or "flow"


def flows_dir() -> Path:
    return FLOWS_DIR


def default_req_to_demo() -> Flow:
    """Built-in template: from requirement to demo."""
    return Flow(
        name="Req → Demo",
        description="Sample pipeline: from requirement to a working demo.",
        steps=[
            FlowStep("Analyze requirements",
                     "Read and analyze the requirements; list the work items and acceptance criteria."),
            FlowStep("Design the solution",
                     "Propose the design/architecture and the list of files to create or edit."),
            FlowStep("Generate code",
                     "Implement the code per the design; create/edit files in the working folder."),
            FlowStep("Write tests",
                     "Write meaningful unit tests for what was implemented."),
            FlowStep("Run & demo",
                     "Run/launch to verify, fix any issues, then describe how to demo it."),
        ],
    )


def to_dict(flow: Flow) -> dict:
    return {"name": flow.name, "description": flow.description,
            "steps": [asdict(s) for s in flow.steps]}


def from_dict(data: dict) -> Flow:
    steps = []
    for raw in data.get("steps", []):
        raw = dict(raw)
        sub_raw = raw.pop("parallel_agents", None) or []
        step = FlowStep(**{**{"name": ""}, **raw})
        step.parallel_agents = [SubAgent(**{**{"name": ""}, **sa}) for sa in sub_raw]
        steps.append(step)
    return Flow(name=data.get("name", "Flow"), description=data.get("description", ""), steps=steps)


def list_flows(directory: Path = FLOWS_DIR) -> List[Flow]:
    if not directory.exists():
        return []
    flows: List[Flow] = []
    for path in sorted(directory.glob("*.json")):
        try:
            flows.append(from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return flows


def save_flow(flow: Flow, directory: Path = FLOWS_DIR, old_name: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if old_name and old_name != flow.name:
        delete_flow(old_name, directory)
    path = directory / f"{_slug(flow.name)}.json"
    path.write_text(json.dumps(to_dict(flow), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def delete_flow(name: str, directory: Path = FLOWS_DIR) -> None:
    path = directory / f"{_slug(name)}.json"
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def build_step_prompt(step: FlowStep, index: int, total: int, skill_text: str = "") -> str:
    """Compose the message sent to the Code agent for one step."""
    lines = [f"[Stage {index}/{total}: {step.name}]"]
    if step.hint:
        lines.append(f"Hint: {step.hint}")
    if step.prompt:
        lines.append(step.prompt)
    if step.skill and skill_text:
        lines.append(f"\n(Applied skill — {step.skill})\n{skill_text}")
    return "\n".join(lines)


def generate_task_prompt(provider, stage_name: str = "", hint: str = "", cancel=None) -> str:
    """Best-effort: expand a stage name + short hint into a concrete task prompt
    for the Code agent. Returns '' on any error (so the UI never breaks)."""
    parts = []
    if stage_name:
        parts.append(f"Stage: {stage_name}")
    if hint:
        parts.append(f"Hint: {hint}")
    if not parts:
        return ""
    messages = [
        {"role": "system", "content":
            "You write a task prompt for a coding agent. Given a stage name and a short hint, "
            "expand them into ONE concise, actionable instruction (2–4 sentences) describing exactly "
            "what to do. Reply with ONLY the task text — no preamble, no markdown heading."},
        {"role": "user", "content": "\n".join(parts)},
    ]
    try:
        a = provider.chat(messages, tools=None, on_text=None, cancel=cancel)
    except Exception:  # noqa: BLE001 - generation must never break the dialog
        return ""
    return (a.get("content") or "").strip()


# --------------------------------------------------------------------------
# Per-step options that a flat prompt queue can't express: self-verify /
# review-completeness retry / compact-after-run need to inspect a step's
# OUTCOME before deciding what to run next, so a stateful driver (FlowRunner)
# replaces the old "enqueue every step's prompt up front" approach. Free of
# any Qt import so the decision logic is unit-testable on its own.
# --------------------------------------------------------------------------
_VERIFY_MARKER = re.compile(r"VERIFY_RESULT:\s*(PASS|FAIL)\b(.*)", re.IGNORECASE | re.DOTALL)


def build_verify_prompt(step: FlowStep) -> str:
    """A follow-up prompt asking the agent to self-check the stage it just ran,
    ending with a strict machine-parseable marker line (see parse_verify_result)."""
    goal = step.prompt or step.hint or step.name
    return (
        f"Review the work you just did for stage \"{step.name}\" against its goal:\n{goal}\n\n"
        "Check completeness — did you actually finish everything asked, with no missing "
        "pieces, TODOs, or placeholder code? Give a short assessment, then end your reply "
        "with EXACTLY one line, nothing after it:\n"
        "VERIFY_RESULT: PASS\n"
        "or:\n"
        "VERIFY_RESULT: FAIL - <one short reason>"
    )


def parse_verify_result(text: str) -> Optional[bool]:
    """True = passed, False = failed, None = no marker found at all — treated
    as a pass by the caller so a model that forgets the exact marker never
    blocks the flow forever."""
    m = _VERIFY_MARKER.search(text or "")
    if not m:
        return None
    return m.group(1).upper() == "PASS"


@dataclass
class FlowAction:
    """What the UI layer should do next, returned by :class:`FlowRunner`."""
    kind: str                                    # "run" | "parallel" | "done"
    prompt: str = ""                             # for kind == "run"
    attachments: List[str] = field(default_factory=list)
    compact: bool = False                        # trim history before running this action
    step: Optional[FlowStep] = None              # for kind == "parallel" (has .parallel_agents)


@dataclass
class FlowRunner:
    """Drives one Flow's steps sequentially, one turn at a time.

    Usage: ``action = runner.start()``; run it; when the turn finishes, call
    ``action = runner.on_turn_finished(last_assistant_text)`` and run THAT
    action; repeat until ``action.kind == "done"``. A parallel stage
    (``kind == "parallel"``) has no single "last assistant text" — the caller
    fans it out itself and calls ``on_parallel_finished()`` instead."""
    flow: Flow
    skill_map: dict = field(default_factory=dict)   # step.skill name -> instructions text
    _index: int = 0
    _phase: str = "step"          # "step" | "verify"
    _retries_used: int = 0

    @property
    def step_index(self) -> int:
        return self._index

    def current_step(self) -> Optional[FlowStep]:
        if 0 <= self._index < len(self.flow.steps):
            return self.flow.steps[self._index]
        return None

    def start(self) -> FlowAction:
        if self.current_step() is None:
            return FlowAction(kind="done")
        return self._step_action()

    def _step_action(self) -> FlowAction:
        step = self.current_step()
        self._phase = "step"
        if step.is_parallel:
            return FlowAction(kind="parallel", step=step)
        skill_text = self.skill_map.get(step.skill, "")
        prompt = build_step_prompt(step, self._index + 1, len(self.flow.steps), skill_text)
        return FlowAction(kind="run", prompt=prompt, attachments=list(step.attachments))

    def on_turn_finished(self, last_text: str) -> FlowAction:
        """Call after a normal ("run") stage's turn completes."""
        step = self.current_step()
        if step is None:
            return FlowAction(kind="done")
        if self._phase == "step":
            if step.self_verify or step.review_retries > 0:
                self._phase = "verify"
                return FlowAction(kind="run", prompt=build_verify_prompt(step))
            return self._advance(compact=step.compact_after_run)
        # self._phase == "verify": last_text is the verify agent's reply.
        passed = parse_verify_result(last_text)
        if passed is False and self._retries_used < step.review_retries:
            self._retries_used += 1
            return self._step_action()   # retry the SAME stage, no compact yet
        self._retries_used = 0
        return self._advance(compact=step.compact_after_run)

    def skip_step(self) -> FlowAction:
        """Move past the current stage WITHOUT self-verify/retry — used when
        the underlying turn itself failed/errored outright, so a later stage
        still gets a chance to run instead of retrying a broken turn forever."""
        step = self.current_step()
        self._retries_used = 0
        compact = step.compact_after_run if step else False
        return self._advance(compact=compact)

    def on_parallel_finished(self) -> FlowAction:
        """Call after a "parallel" stage's sub-agents have all finished and
        their consolidated result has already been fed back as one more
        normal turn by the caller (see code_tab.py) — this just advances."""
        step = self.current_step()
        compact = step.compact_after_run if step else False
        return self._advance(compact=compact)

    def _advance(self, compact: bool) -> FlowAction:
        self._index += 1
        if self.current_step() is None:
            return FlowAction(kind="done", compact=compact)
        action = self._step_action()
        action.compact = compact
        return action
