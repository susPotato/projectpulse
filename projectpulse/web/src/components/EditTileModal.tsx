/*
  "Edit" on a tile's "..." menu - the honest version of the mockup's per-tile
  Settings dialog (`Layout_Program` image24): one field for now, a custom
  title, stored in `DashboardTile.settings_json` and rendered instead of the
  catalogue label whenever it is set (`DashboardCanvas`'s `labelFor` call).
*/
import { useState } from "react";
import { send, type TileOut } from "../api";

export function EditTileModal({
  tile,
  defaultLabel,
  onClose,
  onSaved,
}: {
  tile: TileOut;
  defaultLabel: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [title, setTitle] = useState(
    tile.settings?.title ? String(tile.settings.title) : "",
  );
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const trimmed = title.trim();
      await send(`/api/dashboards/tiles/${tile.id}`, "PATCH", {
        settings: trimmed ? { ...tile.settings, title: trimmed } : null,
      });
      onSaved();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/40 p-6" onClick={onClose}>
      <div
        className="w-full max-w-[420px] rounded-lg border border-rule bg-surface p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="m-0 text-emph font-semibold">Edit Tile</h2>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded border-0 bg-transparent text-title leading-none text-ink-3 hover:text-ink"
            aria-label="Close"
          >
            &times;
          </button>
        </div>
        <label className="mb-1 block text-label font-bold tracking-[0.06em] text-ink-3 uppercase">
          Title
        </label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder={defaultLabel}
          autoFocus
          className="w-full rounded-md border border-rule bg-bg px-2.5 py-1.5 text-body text-ink"
        />
        <p className="mt-1.5 mb-0 text-label text-ink-3">
          Leave blank to use the catalogue name ({defaultLabel}).
        </p>
        <div className="mt-3 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-body font-semibold text-ink hover:bg-bg"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={save}
            className="cursor-pointer rounded-md border border-navy bg-navy px-2.5 py-1 text-body font-semibold text-surface disabled:opacity-50"
          >
            {busy ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
