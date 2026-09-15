"""Cowork Local (branded "Cowork-Local BamBOO" — see DISPLAY_NAME) - PySide6 desktop app.

Sidebar navigation pages:
  - Dashboard:     token usage & cost statistics.
  - Schedule Task: Kanban board of scheduled agent tasks.
  - Workspace:     the home page — Claude-Projects-style projects (shared context
                   + agent sandbox), hosting per-project **Cowork** chat and
                   **GraphRAG** knowledge-graph sub-tabs (plus History).
  - Monitoring:    the Monitoring Dashboard — Overview, Security Events, MCP
                   Call History, Action Logs, Agent Status, Agents Admin.

Plus a unified "Connectors (MCP)" layer (CAD/CAE/MS365/Other — external MCP
servers + REST connectors) and a Sandbox Security Layer (resource limits,
network control, permission management, audit log). No login required —
starts directly with full admin access.
"""

__version__ = "2.26.0"
# Internal/technical name — config dir (~/.cowork_local), QSettings org keys,
# packaging scripts and docs still use this; do NOT rebrand it.
APP_NAME = "Cowork Local"
# User-facing brand shown in the window title, top bar, and tray.
DISPLAY_NAME = "Cowork-Local BamBOO"
