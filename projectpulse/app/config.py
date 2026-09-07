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
    #: Where the OneDrive-synced folder lives. The Graph API path is the eventual
    #: production route, but a synced folder needs no tenant admin consent, so it
    #: is what the demo runs on.
    data_root: Path = Path(os.getenv("PULSE_DATA_ROOT", REPO_ROOT / "data" / "demo"))
    sync_interval_minutes: int = int(os.getenv("PULSE_SYNC_INTERVAL", "120"))
    echo_sql: bool = os.getenv("PULSE_ECHO_SQL", "").lower() in {"1", "true", "yes"}


settings = Settings()
