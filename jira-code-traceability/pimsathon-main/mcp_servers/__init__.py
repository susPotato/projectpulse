"""Built-in MCP servers this app HOSTS (as opposed to ``core/mcp_client.py``,
which CONNECTS to servers). Each module here is runnable stdio-style via
``python -m cowork_local.mcp_servers.<name>`` and is auto-registered by
``AppContext.build_mcp_tools`` when its feature is enabled — no Settings
entry needed, no Node.js dependency."""
