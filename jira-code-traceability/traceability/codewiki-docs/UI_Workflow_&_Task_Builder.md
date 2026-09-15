# UI_Workflow_&_Task_Builder

## Purpose

The `UI_Workflow_&_Task_Builder` module provides the graphical front-end for designing, editing, and orchestrating automated work in the application. It exposes the visual tooling that lets users:

- Build and configure **CO4E** ("Chain of 4 Experts" / multi-agent collaboration) sessions through a dedicated builder UI (`ui.co4e`).
- Construct and edit **flows** — multi-step, node-based automation pipelines — via a dedicated dialog (`ui.flow`, backed by `ui/flow_dialog.py`).
- Create and modify individual **tasks** that agents or flows execute, using a focused task editor dialog (`ui.task_editor`, backed by `ui/task_editor_dialog.py`).

In essence, this module is the visual authoring layer that sits directly on top of the orchestration and execution primitives defined in the `Agent_Orchestration_&_Execution_Engine` module (`core.co4e`, `core.flows`, `core.worker`, `core.skills`, `core.tools`). It translates user intent expressed through dialogs and forms into the structured configuration objects (flow definitions, task specifications, agent chains) that the execution engine consumes at runtime.

This module is typically invoked from the broader UI shell (`Application_Shell_&_Global_State`) and works alongside the workspace/conversational surfaces (`UI_Workspace_&_Conversational_Interface`) and administration panels (`UI_Administration_&_Management_Panels`) to give users a complete authoring-to-execution experience.

## Architecture

### Component Composition

```mermaid
graph TD
    subgraph "UI_Workflow_&_Task_Builder"
        CO4E_UI["ui.co4e<br/>(CO4E Builder UI)"]
        FLOW_UI["ui.flow<br/>(flow_dialog.py)"]
        TASK_UI["ui.task_editor<br/>(task_editor_dialog.py)"]
    end

    subgraph "Agent_Orchestration_&_Execution_Engine"
        CORE_CO4E["core.co4e"]
        CORE_FLOWS["core.flows"]
        CORE_WORKER["core.worker"]
        CORE_SKILLS["core.skills"]
        CORE_TOOLS["core.tools"]
    end

    subgraph "Identity,_Accounts_&_Permissions"
        PERMS["core.permissions"]
    end

    subgraph "Application_Shell_&_Global_State"
        STATE["state.py"]
        APP["app.py"]
    end

    CO4E_UI -->|configures & triggers| CORE_CO4E
    FLOW_UI -->|defines & saves| CORE_FLOWS
    TASK_UI -->|defines & saves| CORE_WORKER
    FLOW_UI -->|references| CORE_SKILLS
    FLOW_UI -->|references| CORE_TOOLS
    TASK_UI -->|references| CORE_SKILLS
    TASK_UI -->|references| CORE_TOOLS

    CO4E_UI -->|reads/writes| STATE
    FLOW_UI -->|reads/writes| STATE
    TASK_UI -->|reads/writes| STATE

    CO4E_UI -.->|permission checks| PERMS
    FLOW_UI -.->|permission checks| PERMS
    TASK_UI -.->|permission checks| PERMS

    APP -->|launches| CO4E_UI
    APP -->|launches| FLOW_UI
    APP -->|launches| TASK_UI
```

### Typical Interaction Flow

```mermaid
sequenceDiagram
    participant User
    participant Shell as Application Shell (app.py)
    participant FlowDlg as ui.flow (FlowDialog)
    participant TaskDlg as ui.task_editor (TaskEditorDialog)
    participant State as Global State
    participant Engine as Orchestration Engine (core.flows / core.worker)

    User->>Shell: Open "Build Workflow"
    Shell->>FlowDlg: Instantiate FlowDialog
    FlowDlg->>State: Load existing flow definitions
    User->>FlowDlg: Add/edit steps, agents, tasks
    FlowDlg->>TaskDlg: Open task editor for a step
    User->>TaskDlg: Configure task (skill, tool, params)
    TaskDlg->>State: Persist task definition
    TaskDlg-->>FlowDlg: Return configured task
    User->>FlowDlg: Save flow
    FlowDlg->>Engine: Submit flow definition
    Engine-->>State: Update execution/run status
    State-->>Shell: Refresh UI with new state
```

## Core Components

| Component | Location | Responsibility |
|-----------|----------|-----------------|
| `ui.co4e` | `ui/` | Builder interface for configuring CO4E multi-agent collaboration sessions; bridges user configuration to `core.co4e`. |
| `ui.flow` | `ui/flow_dialog.py` | Dialog for creating and editing multi-step flows (automation pipelines), integrating with `core.flows`, `core.skills`, and `core.tools`. |
| `ui.task_editor` | `ui/task_editor_dialog.py` | Dialog for defining and editing individual tasks (execution units), feeding definitions into `core.worker` and related execution components. |

## Related Modules

- **Agent_Orchestration_&_Execution_Engine** — Consumes the flow and task definitions authored here; provides the runtime (`core.co4e`, `core.flows`, `core.worker`, `core.skills`, `core.tools`) that actually executes what is built in this module.
- **Application_Shell_&_Global_State** — Hosts and launches these dialogs, and provides shared application state (`state.py`) that the builder dialogs read from and write to.
- **Identity,_Accounts_&_Permissions** — Enforces permission checks (`core.permissions`) controlling who may create, edit, or run workflows and tasks.
- **UI_Workspace_&_Conversational_Interface** and **UI_Administration_&_Management_Panels** — Surround this module in the broader UI, providing entry points (e.g., from the workspace or admin tabs) into the workflow/task building dialogs.