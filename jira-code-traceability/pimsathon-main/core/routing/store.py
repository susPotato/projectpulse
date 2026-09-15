"""Persistence for model assessments — the routing feature's "loader/writer".

Assessments can be large and are rewritten wholesale on every reassess, so they
live in their OWN file (``~/.cowork_local/assessments.json`` by default) rather
than bloating the main ``config.json``. Two robustness guarantees the task
requires:

* **Atomic write** — results are written to a temp file in the same directory
  and then ``os.replace``'d over the target, so an interrupted reassess can
  never leave a half-written / corrupt store behind.
* **Versioned history** — before each overwrite, the previous store is copied
  to ``assessments_history/<timestamp>.json`` so a model that *degrades*
  between runs can be detected after the fact.

On-disk shape (JSON, mirrors the task's YAML ``assessments`` block)::

    {
      "last_updated": "2026-07-22T09:30:00+00:00",
      "policy": "balanced",
      "results": {
        "anthropic/claude-opus-4-8": { <ModelAssessment> },
        ...
      }
    }
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from .models import ModelAssessment, Policy

# Default location under the app's config dir. Imported lazily so tests can
# point the store anywhere without touching the real home directory.
_DEFAULT_STORE_NAME = "assessments.json"
_HISTORY_DIR_NAME = "assessments_history"


def utc_now_iso() -> str:
    """Current UTC time as an ISO-8601 string (used for ``last_updated``)."""
    return datetime.now(timezone.utc).isoformat()


def _fs_safe_stamp() -> str:
    """A filesystem-safe timestamp for history filenames (no ``:``)."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


class AssessmentStore:
    """Reads/writes the assessment JSON file with atomic writes + history.

    Parameters
    ----------
    store_path:
        Path to the assessments JSON file. If ``None``, defaults to
        ``~/.cowork_local/assessments.json``.
    history_dir:
        Directory for pre-overwrite backups. Defaults to a sibling
        ``assessments_history/`` next to ``store_path``.
    """

    def __init__(
        self,
        store_path: Optional[Path] = None,
        history_dir: Optional[Path] = None,
    ) -> None:
        if store_path is None:
            from ...config import CONFIG_DIR  # lazy: avoids import cost in tests
            store_path = CONFIG_DIR / _DEFAULT_STORE_NAME
        self.store_path = Path(store_path)
        self.history_dir = Path(
            history_dir or self.store_path.parent / _HISTORY_DIR_NAME
        )

    # -- read ----------------------------------------------------------- #
    def load_raw(self) -> Dict:
        """Return the raw JSON dict, or an empty skeleton if absent/corrupt.

        A corrupt store must never crash the app (same philosophy as
        ``AppConfig.load``) — we fall back to an empty result set so the next
        reassess simply rebuilds it.
        """
        if not self.store_path.exists():
            return {"last_updated": None, "policy": Policy.BALANCED.value, "results": {}}
        try:
            return json.loads(self.store_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"last_updated": None, "policy": Policy.BALANCED.value, "results": {}}

    def load(self) -> Dict[str, ModelAssessment]:
        """Return ``{candidate_key: ModelAssessment}`` parsed from disk.

        Individual malformed entries are skipped rather than failing the whole
        load — one bad row shouldn't hide every good assessment.
        """
        raw = self.load_raw()
        out: Dict[str, ModelAssessment] = {}
        for key, payload in (raw.get("results") or {}).items():
            try:
                out[key] = ModelAssessment.model_validate(payload)
            except Exception:  # noqa: BLE001 — skip a single corrupt entry
                continue
        return out

    def last_updated(self) -> Optional[str]:
        return self.load_raw().get("last_updated")

    def policy(self) -> str:
        return self.load_raw().get("policy") or Policy.BALANCED.value

    # -- write ---------------------------------------------------------- #
    def save(
        self,
        results: Dict[str, ModelAssessment],
        policy: Policy | str = Policy.BALANCED,
        *,
        last_updated: Optional[str] = None,
        backup: bool = True,
    ) -> Path:
        """Atomically write ``results`` to the store, backing up the previous
        version to history first.

        Returns the store path. Never leaves a partially-written file: the
        payload is fully serialized to a temp file and only then swapped into
        place with ``os.replace`` (atomic on the same filesystem, incl. NTFS).
        """
        if backup:
            self._backup_existing()

        policy_val = policy.value if isinstance(policy, Policy) else str(policy)
        payload = {
            "last_updated": last_updated or utc_now_iso(),
            "policy": policy_val,
            "results": {
                key: assessment.model_dump(mode="json")
                for key, assessment in results.items()
            },
        }

        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, ensure_ascii=False)

        # Temp file MUST be on the same volume as the target for os.replace to
        # be atomic — so create it in the target's own directory.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.store_path.parent),
            prefix=".assessments-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())  # durability: survive a crash right after
            os.replace(tmp_name, self.store_path)  # atomic swap
        except BaseException:
            # Clean up the temp file on any failure so we never leave litter.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return self.store_path

    def _backup_existing(self) -> Optional[Path]:
        """Copy the current store into history as ``<timestamp>.json``.

        No-op when there is nothing to back up. Best-effort: a failed backup
        must not block the (more important) new write.
        """
        if not self.store_path.exists():
            return None
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            # The wall clock can be coarse (Windows ~15ms), so two rapid
            # backups may share a timestamp — disambiguate with a counter so a
            # snapshot is never silently overwritten.
            stamp = _fs_safe_stamp()
            dst = self.history_dir / f"{stamp}.json"
            n = 1
            while dst.exists():
                dst = self.history_dir / f"{stamp}-{n}.json"
                n += 1
            dst.write_text(
                self.store_path.read_text(encoding="utf-8"), encoding="utf-8"
            )
            return dst
        except OSError:
            return None

    def history_files(self) -> list[Path]:
        """All history snapshots, oldest first (for degrade detection / UI)."""
        if not self.history_dir.exists():
            return []
        return sorted(self.history_dir.glob("*.json"))

    def prune_history(self, keep: int = 30) -> None:
        """Keep only the newest ``keep`` history snapshots; delete the rest."""
        files = self.history_files()
        for old in files[:-keep] if keep > 0 else files:
            try:
                old.unlink()
            except OSError:
                pass


__all__ = ["AssessmentStore", "utc_now_iso"]
