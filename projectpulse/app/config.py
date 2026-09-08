"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg://pulse:pulse@localhost:5433/projectpulse",
    )
    #: Where the OneDrive-synced folder lives. Only read when `excel_transport`
    #: is "local" - the demo route, needing no sign-in.
    data_root: Path = Path(os.getenv("PULSE_DATA_ROOT", REPO_ROOT / "data" / "demo"))
    #: "local" (a synced folder, the demo route) or "graph" (Microsoft Graph,
    #: reading a person's OneDrive directly after `scripts.sync onedrive
    #: login` - see `ingest/sources/excel/graph_source.py`). Local by default:
    #: switching this on is a decision, not a side effect of installing the
    #: `onedrive` extra.
    excel_transport: str = os.getenv("PULSE_EXCEL_TRANSPORT", "local")
    #: Folder within the signed-in account's OneDrive to watch, e.g.
    #: "Documents/ProjectPulse". Empty means the OneDrive root itself. Only
    #: read when `excel_transport` is "graph".
    onedrive_folder: str = os.getenv("PULSE_ONEDRIVE_FOLDER", "")
    sync_interval_minutes: int = int(os.getenv("PULSE_SYNC_INTERVAL", "120"))
    #: Whether the served bundle asks a model to phrase its narrative. Off by
    #: default and deliberately so: the deterministic narrative is complete on
    #: its own, so the model is an improvement to opt into rather than a
    #: dependency to discover missing. Needs the `llm` extra and credentials.
    narration_model_enabled: bool = os.getenv("PULSE_NARRATION", "").lower() in {
        "1",
        "true",
        "yes",
    }
    #: Which vendor phrases it. The fence around the model is identical for all
    #: three, so this is a one-word change rather than a code path.
    narration_provider: str = os.getenv("PULSE_NARRATION_PROVIDER", "anthropic")
    #: Empty means that vendor's default from `narration.providers`.
    narration_model: str = os.getenv("PULSE_NARRATION_MODEL", "")
    #: Where runtime state a person edits is kept - currently the narration
    #: settings and the API key. Gitignored; see `narration/store.py` for why
    #: it is plaintext and what is done about that.
    state_dir: Path = Path(os.getenv("PULSE_STATE_DIR", REPO_ROOT / ".pulse"))
    #: Where trained model artefacts live. Nothing in the repo, and nothing
    #: downloaded automatically - `scripts.fetch_model` puts them here on
    #: request, and their absence disables advisory features rather than
    #: breaking anything.
    model_root: Path = Path(os.getenv("PULSE_MODEL_ROOT", REPO_ROOT / "models"))
    #: Full path to the duration classifier, if it is not in `model_root`.
    duration_model_path: str = os.getenv("PULSE_DURATION_MODEL", "")
    echo_sql: bool = os.getenv("PULSE_ECHO_SQL", "").lower() in {"1", "true", "yes"}


settings = Settings()
