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
    #: Whether a model may *propose risks* by reading issue text. Its own
    #: switch, and emphatically not `narration_model_enabled`.
    #:
    #: The two are different permissions, not two volumes of one. Narration
    #: lets a model phrase findings it did not compute, behind a fence whose
    #: prompt contains no digit and whose output is validated token by token -
    #: the model asserts nothing. This lets a model *read free text and say
    #: something new*, which that fence exists to forbid. Turning on prose must
    #: not silently turn on claims, so a deployment can run one without the
    #: other and the demo can show either alone.
    #:
    #: Credentials are shared with narration (`narration.store`), because they
    #: are the same account - it is the permission that is separate, not the
    #: key.
    risk_drafts_enabled: bool = os.getenv("PULSE_RISK_DRAFTS", "").lower() in {
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
    #: Directory *containing* the `tracelink` package, when it is not
    #: importable already. The image copies it to `/app`, so a plain import
    #: works there and this stays empty; a developer with the pipeline
    #: checked out beside this repository sets it rather than installing.
    #: Its absence disables the git source with a named reason - the same
    #: rule the model extras follow, never an ImportError at boot.
    tracelink_home: str = os.getenv("PULSE_TRACELINK_HOME", "")
    #: Where shallow clones are cached. Container-local and disposable on
    #: purpose: `app/models/repo.py` explains why a clone is the one input
    #: this app does not keep in Postgres. Under `state_dir` so the one
    #: gitignored directory holds everything a run writes.
    repo_cache: Path = Path(
        os.getenv("PULSE_REPO_CACHE", REPO_ROOT / ".pulse" / "repos")
    )
    #: The interpreter that has CodeWiki's generator installed, for projects
    #: whose repository has no `docs/` (see `app/codewiki_docs.py`). Empty
    #: means this one, which is the image's arrangement; a host usually points
    #: it at a separate venv, e.g. `../../.venv-codewiki/Scripts/python.exe`.
    codewiki_python: str = os.getenv("PULSE_CODEWIKI_PYTHON", "")
    echo_sql: bool = os.getenv("PULSE_ECHO_SQL", "").lower() in {"1", "true", "yes"}


settings = Settings()
