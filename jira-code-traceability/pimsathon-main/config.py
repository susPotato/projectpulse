"""Application configuration.

Stored as JSON at ``~/.cowork_local/config.json``. Environment variables
override stored values so the app can run immediately in locked-down setups:

    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL
    ANTHROPIC_API_KEY, ANTHROPIC_MODEL
    COWORK_TEAMS_WEBHOOK
    COWORK_ACTIVE_PROVIDER
    COWORK_CA_BUNDLE
"""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

CONFIG_DIR = Path.home() / ".cowork_local"
CONFIG_PATH = CONFIG_DIR / "config.json"
HISTORY_DIR = CONFIG_DIR / "history"

DEFAULT_CONFIG: Dict[str, Any] = {
    "active_provider": "openai_compat",
    "theme": "dark",
    "language": "vi",   # "en" | "ja" | "vi" — UI display language
    # Advanced/IT-managed override only (no Settings UI): path to a PEM file
    # with a corporate/internal gateway's certificate (or its issuing CA), set
    # via the COWORK_CA_BUNDLE env var. Normally unnecessary — a self-signed
    # gateway certificate (e.g. "SSLCertVerificationError: self-signed
    # certificate in certificate chain") is detected and trusted automatically
    # per-host on first contact; see core/tls_trust.py.
    "tls_ca_bundle": "",
    "providers": {
        "openai_compat": {
            "base_url": "https://your-internal-gateway/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
        "anthropic": {
            "base_url": "https://api.anthropic.com",
            "api_key": "",
            "model": "claude-sonnet-4-6",
        },
        # Local models via Ollama's OpenAI-compatible server (no key needed).
        "ollama": {
            "base_url": "http://localhost:11434/v1",
            "api_key": "ollama",     # Ollama ignores it, but some clients require a value
            "model": "llama3.1",
        },
        # GitHub Copilot chat (OpenAI-compatible endpoint; paste a Copilot token).
        "github_copilot": {
            "base_url": "https://api.githubcopilot.com",
            "api_key": "",
            "model": "gpt-4o",
        },
        # OpenAI (Codex / GPT models) — OpenAI-compatible; paste an OpenAI API key.
        "codex": {
            "base_url": "https://api.openai.com/v1",
            "api_key": "",
            "model": "gpt-4o-mini",
        },
    },
    "code": {
        "mode": "confirm",          # "confirm" | "auto"
        "default_workdir": "",
    },
    "teams": {
        "webhook_url": "",
        "notify_on_complete": True,
    },
    "history": {
        "location": "local",   # "local" | "onedrive"
        "custom_dir": "",       # optional explicit folder; overrides location
        "autosave": True,
    },
    "codebase_memory": {
        "enabled": False,
        "binary_path": "",      # empty -> resolved from PATH (codebase-memory-mcp)
        "auto_index": True,      # index the workdir automatically before the first turn
    },
    # AI-assisted agent security guardrails — configured in its own Settings
    # group next to Microsoft 365 (same screen area, but never touches the
    # ms365 dict/rules above). Each layer is independently toggleable; a
    # blocked action always notifies the admin (see core/agent_security_alert.py)
    # via the SAME signed-in Microsoft 365 account as everything else.
    "agent_security": {
        "enabled": True,              # master switch — ON by default ("chọn hết"); editing the Settings group requires an admin-account unlock
        "validate_prompt": True,      # AI reviews the user's own request against the rules below
        "validate_attachments": True,  # AI scans attachment/file content for malicious payloads
        "validate_commands": True,    # whitelist + optional AI control-agent gate on run_command/install_package
        "command_ai_check": False,    # extra AI judgement for commands not covered by the whitelist (default: off)
        "rules_onedrive_url": "",     # optional OneDrive/SharePoint SHARE LINK to a .md rules doc (admin-authored)
        "admin_email": "",            # violation alerts are emailed here via the signed-in MS365 account
        # ---- Sandbox Security Layer ----
        "cowork_confirm_commands": False,  # show the Approve/Reject dialog before Cowork runs a command (default: off)
        "resource_limit_cpu_percent": 80,  # 0 = unlimited; caps a run_command/install_package process TREE's total CPU%
        "resource_limit_memory_mb": 2048,  # 0 = unlimited; caps total RSS memory (MB)
        "resource_limit_disk_mb": 512,     # 0 = unlimited; caps total disk read+write (MB)
        "block_network": True,             # strip proxy env / point at a black-hole address for agent-run commands
        # Allow the agent's fetch_url tool to read web pages / online documents /
        # SharePoint-OneDrive share links. SEPARATE from block_network (that only
        # sandboxes agent-run shell commands) — reading a URL for info is safe and
        # useful, so this defaults ON. Toggle in Settings → Security.
        "allow_url_fetch": True,
        "sandbox_pw": "quandh14",          # default password to unlock sandbox settings
        "rulebase_path": "",               # custom RULEBASE.md — attached to every agent execution
    },
    # Legacy generic-MCP-server list. MERGED into ext_connectors["other"] as of
    # the unified "Connectors (MCP)" section — kept here only so config.load()
    # can migrate any pre-existing entries; the UI no longer writes it.
    "mcp_servers": [],
    # Unified "Connectors (MCP)" (Settings). One system for every external tool
    # source — grouped by category CAD / CAE / MS365 / Other. Each entry:
    # {"id", "name", "category", "enabled", "mode": "mcp_stdio"|"rest_api", plus
    # mode-specific fields — see core/ext_connectors.py}. No vendor SDK bundled:
    # a mcp_stdio entry points at a real MCP server the user/IT already has; a
    # rest_api entry calls a REST endpoint the app/vendor exposes. "Other" is
    # the home for generic MCP servers (what used to be the separate "MCP
    # Servers" section); MS365 additionally auto-wires the built-in MS365 MCP
    # server (see state.py::_ms365_builtin_connection).
    "ext_connectors": {
        "cad": [],
        "cae": [],
        "ms365": [],
        "other": [],
    },
    "cowork": {
        "output_dir": "",        # where Cowork saves generated files; empty -> OneDrive/CoworkLocal/output
        "max_parallel": 5,       # max messages running at once per tab; extras wait in the queue
    },
    "context": {                 # auto-compress long conversations (Cowork + Co4E)
        "auto_compact": True,    # summarize old turns when near the memory quota
        "compact_threshold": 0.8,  # trigger at 80% of the context window
        "limit_tokens": 0,       # 0 = auto per model; else a fixed token budget
    },
    "jira": {                    # Jira read connector (agent tool: jira_search / jira_get_issue)
        "base_url": "",          # e.g. https://your-domain.atlassian.net
        "email": "",             # Atlassian account email (Basic auth user)
        "api_token": "",         # Atlassian API token (id.atlassian.com → Security → API tokens)
    },
    "attachments": {
        "max_tokens": 500000,    # per attached file; content beyond this is truncated (~4 chars/token)
        "max_files": 10,         # max number of files attachable to one message
    },
    "structure": {               # Structure (RAG) graph performance caps (0 = unlimited)
        "max_nodes": 400,
        "max_edges": 400,
    },
    # Dashboard tab: unit prices (USD per 1M tokens) + display currency.
    # Editable right on the Dashboard; rates are static conversions.
    "usage": {
        "price_per_mtok_in_usd": 0.5,
        "price_per_mtok_out_usd": 1.5,
        "price_per_mtok_cache_usd": 0.1,
        "currency": "USD",            # USD | VND | JPY
        "usd_to_vnd": 25000.0,
        "usd_to_jpy": 150.0,
        "model_prices": {},           # per-model USD/1M rates: {model: {"in","out","cache"}}
        "pricing_url": "",            # reference price-list link (informational)
    },
    "auth": {
        "shared_dir": "",        # shared folder path (network share or synced OneDrive folder) holding
                                   # accounts/groups + cross-machine telemetry — plain file I/O, no Graph API
        "last_account": "",      # last successfully logged-in username, for prefill only — never the code
        "last_department": "",   # last-typed optional Department at login, for prefill only
    },
    # Microsoft 365 connections (Settings → "Kết nối Microsoft 365"). This gate
    # (unlock_code) is a LOCAL SETTINGS-PANEL LOCK ONLY — it stops someone from
    # casually flipping these switches, it is NOT how the app authenticates to
    # Microsoft. Real Outlook/Teams/OneDrive/SharePoint access still requires a
    # proper OAuth sign-in (not implemented yet) using tenant_id/client_id below.
    "ms365": {
        "unlock_code": "quandh14",
        "unlocked": False,           # runtime-only — never persisted as True, see save()
        # Auto-connect MS365/OneDrive/SharePoint: the built-in MS365 MCP server
        # launches automatically once the user is signed in (OAuth tenant/client
        # is still required for real Graph access — this only pre-arms the wiring
        # so it "just works" after sign-in, per the unified Connectors design).
        "allow_external_internet": True,
        # TEMPORARY: only OneDrive + SharePoint are enabled, and they connect via
        # the LOCALLY-SYNCED OneDrive folders (core/ms365_local.py) — no OAuth /
        # tenant / sign-in. Outlook / Teams / Meeting-transcript are OFF for now
        # because they need cloud Graph access (OAuth); re-enable them once the
        # cloud sign-in flow is turned back on.
        "connectors": {
            "outlook": False,
            "teams": False,
            "onedrive": True,
            "sharepoint": True,
            "meeting_transcript": False,
        },
        "tenant_id": "",
        "client_id": "",
        # "Paste a Teams link" convenience (Settings): a channel/chat link the
        # user connected once, so the agent can post to it without ever
        # needing a team_id/channel_id/chat_id — see ms365_graph.parse_teams_link.
        "teams_link": "",
        "teams_target": None,        # {"kind": "channel", "team_id", "channel_id"} | {"kind": "chat", "chat_id"}
        "teams_introduced": False,   # has the "Hi, I'm Co4E" self-intro already been sent for this target?
    },
    "last_session": {            # restored on next launch (crash-resilient)
        "cowork": "",
        "code": "",
    },
    "tray": {
        "minimize_on_close": True,  # closing the window keeps running in the tray
        "notify_on_done": True,      # tray notification when a task finishes/fails
    },
    # Which Monitoring tabs a Sub-admin may see (Admin always sees every tab;
    # "user" never sees Monitoring at all — unaffected by this). All default
    # True so behavior is unchanged until an Admin explicitly restricts one.
    "monitoring_visibility": {
        "security_events": True,
        "mcp_history": True,
        "action_logs": True,
        "agent_status": True,
    },
    # Agent tool governance (Monitoring → Tools). Built-in agent tools whose
    # NAME is listed here are withheld from the agent (filtered out of the tool
    # list at run time). Empty = every built-in tool available (default).
    "tools": {
        "disabled": [],
    },
    # Auto Model Assessment & Routing (core/routing/). The app periodically
    # assesses each configured model (static metadata + dynamic probes graded
    # by a fixed judge), scores them per task type, and can route each chat/
    # agent turn to the best-fit model. Assessment RESULTS live in their own
    # file (~/.cowork_local/assessments.json + assessments_history/), not here —
    # this section is only the behaviour config the user edits.
    "routing": {
        "switch_mode": "off",          # global default: "off" | "auto" | "manual"
        "policy": "balanced",          # "quality" | "cost" | "latency" | "balanced"
        "min_score_gain": 0.05,        # only switch if the new model beats current by ≥ this
        "confirm_timeout_sec": 60,     # (manual) auto-keep current if the user doesn't confirm in time
        "reassess_interval_hours": 24,  # periodic reassess cadence; 0 disables the schedule
        "per_provider_concurrency": 2,  # max concurrent probe calls per provider (rate-limit safety)
        "judge_provider": "",          # judge model's provider ("" → the active provider)
        "judge_model": "",             # fixed cheap judge model ("" → a per-provider default)
        "candidates": [],              # explicit [{provider, model_id, tier}]; empty → discover from providers
        "auto_reassess_on_add": True,  # reassess a newly-added model as soon as it's added
        # Per-surface Off/Auto/Manual toggle state (the chat-screen toggle). An
        # empty string means "follow the global switch_mode above".
        "surface_modes": {
            "cowork": "",
            "co4e": "",
            "ai_edit": "",
        },
    },
}

# Friendly labels used across the UI.
PROVIDER_LABELS = {
    "openai_compat": "OpenAI-compatible (Internal Gateway)",
    "anthropic": "Anthropic Claude",
    "ollama": "Ollama (local models)",
    "github_copilot": "GitHub Copilot",
    "codex": "OpenAI (Codex / GPT)",
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _apply_env_overrides(data: Dict[str, Any]) -> Dict[str, Any]:
    data = copy.deepcopy(data)
    oc = data["providers"]["openai_compat"]
    if os.getenv("OPENAI_API_KEY"):
        oc["api_key"] = os.environ["OPENAI_API_KEY"]
    if os.getenv("OPENAI_BASE_URL"):
        oc["base_url"] = os.environ["OPENAI_BASE_URL"]
    if os.getenv("OPENAI_MODEL"):
        oc["model"] = os.environ["OPENAI_MODEL"]

    an = data["providers"]["anthropic"]
    if os.getenv("ANTHROPIC_API_KEY"):
        an["api_key"] = os.environ["ANTHROPIC_API_KEY"]
    if os.getenv("ANTHROPIC_MODEL"):
        an["model"] = os.environ["ANTHROPIC_MODEL"]

    if os.getenv("COWORK_TEAMS_WEBHOOK"):
        data["teams"]["webhook_url"] = os.environ["COWORK_TEAMS_WEBHOOK"]
    if os.getenv("COWORK_ACTIVE_PROVIDER"):
        data["active_provider"] = os.environ["COWORK_ACTIVE_PROVIDER"]
    if os.getenv("COWORK_CA_BUNDLE"):
        data["tls_ca_bundle"] = os.environ["COWORK_CA_BUNDLE"]
    return data


def _migrate_connectors(data: Dict[str, Any]) -> None:
    """One-way migration into the unified Connectors (MCP) model, in place:
      * ext_connectors["office"]  → ext_connectors["ms365"]  (renamed category)
      * legacy top-level mcp_servers → ext_connectors["other"] as mcp_stdio
        connectors (the old standalone "MCP Servers" section was merged in).
    Idempotent: re-running does nothing once migrated. Never raises."""
    import uuid

    ext = data.setdefault("ext_connectors", {})
    for cat in ("cad", "cae", "ms365", "other"):
        ext.setdefault(cat, [])

    # office → ms365 (only migrate non-empty legacy bucket; then drop it)
    legacy_office = ext.pop("office", None)
    if legacy_office:
        seen = {c.get("id") for c in ext["ms365"]}
        for c in legacy_office:
            c["category"] = "ms365"
            if c.get("id") not in seen:
                ext["ms365"].append(c)

    # legacy generic mcp_servers → ext_connectors["other"] (mcp_stdio)
    servers = data.get("mcp_servers") or []
    if servers:
        existing = {c.get("name") for c in ext["other"]}
        for s in servers:
            name = s.get("name", "")
            if not name or name in existing:
                continue
            ext["other"].append({
                "id": f"other-{uuid.uuid4().hex[:6]}",
                "name": name,
                "category": "other",
                "enabled": bool(s.get("enabled", True)),
                "mode": "mcp_stdio",
                "command": s.get("command", ""),
                "args": s.get("args") or [],
                "env": s.get("env") or {},
            })
        data["mcp_servers"] = []   # migrated — the UI no longer manages this


@dataclass
class AppConfig:
    """In-memory view of the configuration with load/save helpers."""

    data: Dict[str, Any] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CONFIG))
    path: Path = CONFIG_PATH

    # ---- persistence -------------------------------------------------
    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "AppConfig":
        merged = copy.deepcopy(DEFAULT_CONFIG)
        if path.exists():
            try:
                stored = json.loads(path.read_text(encoding="utf-8"))
                merged = _deep_merge(merged, stored)
            except (json.JSONDecodeError, OSError):
                # Corrupt config should never block startup.
                merged = copy.deepcopy(DEFAULT_CONFIG)
        merged = _apply_env_overrides(merged)
        # "unlocked" is a runtime-only Settings-panel state (see the "ms365"
        # comment in DEFAULT_CONFIG) — never trust a stored/hand-edited value,
        # every launch starts locked.
        merged.setdefault("ms365", {})["unlocked"] = False
        _migrate_connectors(merged)   # office→ms365 + legacy mcp_servers→other
        return cls(data=merged, path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        to_write = self.data
        if self.data.get("ms365", {}).get("unlocked"):
            # Defense in depth: even if some caller saves without having gone
            # through the Settings dialog's own auto-lock-after-save flow, the
            # unlock state must never reach disk.
            to_write = copy.deepcopy(self.data)
            to_write["ms365"]["unlocked"] = False
        self.path.write_text(
            json.dumps(to_write, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ---- convenience accessors --------------------------------------
    @property
    def active_provider(self) -> str:
        # Migrate configs that still point at a removed provider (e.g. an older
        # install saved "ollama") to a supported one, so the app never tries to
        # build an unknown provider.
        val = self.data.get("active_provider", "openai_compat")
        return val if val in PROVIDER_LABELS else "openai_compat"

    @active_provider.setter
    def active_provider(self, value: str) -> None:
        self.data["active_provider"] = value

    def provider_conf(self, name: str | None = None) -> Dict[str, Any]:
        name = name or self.active_provider
        return self.data["providers"].get(name, {})

    @property
    def ca_bundle(self) -> str:
        """Path to a custom CA/certificate PEM file, or '' for normal validation.

        Used as ``requests``' ``verify=`` argument for every outbound HTTPS call
        — see the "tls_ca_bundle" comment above for when this is needed."""
        return (self.data.get("tls_ca_bundle") or "").strip()

    @ca_bundle.setter
    def ca_bundle(self, value: str) -> None:
        self.data["tls_ca_bundle"] = (value or "").strip()

    # ---- Microsoft 365 connections (Settings-panel lock, see DEFAULT_CONFIG) --
    @property
    def ms365(self) -> Dict[str, Any]:
        return self.data.setdefault("ms365", copy.deepcopy(DEFAULT_CONFIG["ms365"]))

    # ---- Login / RBAC / shared cross-machine store (see DEFAULT_CONFIG) ------
    @property
    def auth(self) -> Dict[str, Any]:
        return self.data.setdefault("auth", copy.deepcopy(DEFAULT_CONFIG["auth"]))

    @property
    def shared_dir(self) -> str:
        return (self.auth.get("shared_dir") or "").strip()

    def ms365_try_unlock(self, code: str) -> bool:
        """Unlock the MS365 Settings group for this session if ``code`` matches.

        This is a client-side UI lock (prevents casually toggling a sensitive
        section), NOT Microsoft authentication — see the DEFAULT_CONFIG
        comment. Never persisted as unlocked; see ``save()``."""
        if (code or "") and code == self.ms365.get("unlock_code", ""):
            self.data["ms365"]["unlocked"] = True
            return True
        return False

    def ms365_lock(self) -> None:
        self.data.setdefault("ms365", {})["unlocked"] = False

    @property
    def theme(self) -> str:
        return self.data.get("theme", "dark")

    @theme.setter
    def theme(self, value: str) -> None:
        self.data["theme"] = value

    @property
    def language(self) -> str:
        from .i18n import DEFAULT_LANGUAGE, LANGUAGES
        val = self.data.get("language", DEFAULT_LANGUAGE)
        return val if val in LANGUAGES else DEFAULT_LANGUAGE

    @language.setter
    def language(self, value: str) -> None:
        self.data["language"] = value

    @property
    def code(self) -> Dict[str, Any]:
        return self.data["code"]

    @property
    def tools_disabled(self) -> list:
        """Built-in agent tool names the admin has turned off (Monitoring → Tools)."""
        return self.data.setdefault("tools", {}).setdefault("disabled", [])

    def set_tool_enabled(self, name: str, enabled: bool) -> None:
        """Enable/disable a built-in agent tool by name and persist it."""
        disabled = set(self.tools_disabled)
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self.data.setdefault("tools", {})["disabled"] = sorted(disabled)
        self.save()

    @property
    def connect_external(self) -> bool:
        """Master switch (Monitoring → Tools → Connector): when off, the agent
        connects to NO external connectors (CAD/CAE/MS365/Other MCP + REST).
        Defaults ON so existing setups keep working."""
        return bool(self.data.setdefault("tools", {}).get("connect_external", True))

    def set_connect_external(self, enabled: bool) -> None:
        self.data.setdefault("tools", {})["connect_external"] = bool(enabled)
        self.save()

    # ---- one-time seeding bookkeeping (built-in skill library / flows) -------
    @property
    def seeded_library_skills(self) -> List[str]:
        """Slugs of bundled library skills already seeded into the user's Skill
        Manager — so a user-deleted one is never silently re-seeded."""
        return list(self.data.setdefault("seeded_library_skills", []))

    @seeded_library_skills.setter
    def seeded_library_skills(self, slugs) -> None:
        self.data["seeded_library_skills"] = list(dict.fromkeys(slugs or []))

    @property
    def seeded_builtin_flows(self) -> List[str]:
        """Ids of built-in Co4E flows already seeded (same respect-user-deletion
        rule as seeded_library_skills)."""
        return list(self.data.setdefault("seeded_builtin_flows", []))

    @seeded_builtin_flows.setter
    def seeded_builtin_flows(self, ids) -> None:
        self.data["seeded_builtin_flows"] = list(dict.fromkeys(ids or []))

    @property
    def teams(self) -> Dict[str, Any]:
        return self.data["teams"]

    @property
    def history(self) -> Dict[str, Any]:
        return self.data["history"]

    @property
    def codebase_memory(self) -> Dict[str, Any]:
        return self.data["codebase_memory"]

    @property
    def agent_security(self) -> Dict[str, Any]:
        return self.data["agent_security"]

    @property
    def mcp_servers(self) -> List[Dict[str, Any]]:
        return self.data.setdefault("mcp_servers", [])

    @property
    def ext_connectors(self) -> Dict[str, List[Dict[str, Any]]]:
        """Unified Connectors (MCP), grouped by category CAD/CAE/MS365/Other —
        see core/ext_connectors.py for the per-entry shape and CATEGORIES."""
        d = self.data.setdefault("ext_connectors", {"cad": [], "cae": [], "ms365": [], "other": []})
        for cat in ("cad", "cae", "ms365", "other"):
            d.setdefault(cat, [])
        return d

    @property
    def cowork(self) -> Dict[str, Any]:
        return self.data["cowork"]

    @property
    def routing(self) -> Dict[str, Any]:
        """Auto Model Assessment & Routing behaviour config (see DEFAULT_CONFIG).

        Always returns a dict with every expected key present, backfilling any
        missing sub-keys from the defaults so older configs upgrade seamlessly."""
        d = self.data.setdefault("routing", copy.deepcopy(DEFAULT_CONFIG["routing"]))
        for k, v in DEFAULT_CONFIG["routing"].items():
            d.setdefault(k, copy.deepcopy(v))
        d.setdefault("surface_modes", {})
        for surface in ("cowork", "co4e", "ai_edit"):
            d["surface_modes"].setdefault(surface, "")
        return d

    def routing_mode_for(self, surface: str) -> str:
        """Effective Off/Auto/Manual mode for a chat surface.

        A per-surface override ("auto"/"manual"/"off") wins; an empty override
        falls back to the global ``switch_mode``."""
        routing = self.routing
        override = (routing.get("surface_modes", {}) or {}).get(surface, "")
        mode = override or routing.get("switch_mode", "off")
        return mode if mode in ("off", "auto", "manual") else "off"

    def set_routing_mode_for(self, surface: str, mode: str) -> None:
        """Persist a chat surface's Off/Auto/Manual toggle selection."""
        mode = mode if mode in ("off", "auto", "manual") else "off"
        self.routing.setdefault("surface_modes", {})[surface] = mode
        self.save()

    @property
    def structure(self) -> Dict[str, Any]:
        return self.data.setdefault("structure", {"max_nodes": 400, "max_edges": 400})

    @property
    def monitoring_visibility(self) -> Dict[str, bool]:
        return self.data.setdefault(
            "monitoring_visibility", copy.deepcopy(DEFAULT_CONFIG["monitoring_visibility"]))

    def cowork_output_dir(self) -> Path:
        """Where Cowork saves generated files (OneDrive folder by default)."""
        custom = (self.cowork.get("output_dir") or "").strip()
        if custom:
            return Path(custom).expanduser()
        from . import paths  # local import avoids any import cycle
        root = paths.primary_onedrive_root()
        if root is not None:
            return root / "CoworkLocal" / "output"
        return CONFIG_DIR / "output" / "cowork"

    def history_dir(self) -> Path:
        """Resolve where conversation history is stored.

        When a project is open, its history is stored INSIDE the project's
        workspace folder (``_project_history_dir``, set by the Workspace screen)
        so that sharing/syncing that folder shares the history — another machine
        opening the same folder sees the conversations and can continue them.
        Otherwise: Local (default) or OneDrive."""
        rt = getattr(self, "_project_history_dir", None)
        if rt:
            return Path(rt)
        custom = (self.history.get("custom_dir") or "").strip()
        if custom:
            return Path(custom).expanduser()
        if self.history.get("location") == "onedrive":
            from . import paths  # local import avoids any import cycle
            root = paths.primary_onedrive_root()
            if root is not None:
                return root / "CoworkLocal" / "history"
        return HISTORY_DIR

    def model_label(self) -> str:
        return str(self.provider_conf().get("model", "?"))
