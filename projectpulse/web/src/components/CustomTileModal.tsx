/*
  The `Custom Tile` dialog on a dashboard canvas: two tabs over the shared
  builder.

  The builder itself lives in `TileBuilder.tsx`, because the Agent page runs
  the same thing as one of its modes and two copies would eventually draw two
  different charts from one conversation. This file is the dialog chrome plus
  the library of already-saved tiles.
*/
import { useEffect, useState } from "react";
import {
  load,
  send,
  type ApiProblem,
  type CustomTileListBundle,
  type CustomTileOut,
  type DashboardScope,
  type TileOut,
} from "../api";
import { MiniChart } from "../tileRegistry";
import { TileBuilder, addCustomTileToDashboard } from "./TileBuilder";

type Tab = "build" | "saved";

export function CustomTileModal({
  scopeType,
  scopeId,
  existingTiles,
  onClose,
  onAdded,
}: {
  scopeType: DashboardScope;
  scopeId: string;
  existingTiles: TileOut[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const [tab, setTab] = useState<Tab>("build");
  const [saved, setSaved] = useState<CustomTileOut[] | null>(null);
  const [savedProblem, setSavedProblem] = useState<ApiProblem | null>(null);

  const target = { scopeType, scopeId };

  useEffect(() => {
    if (tab === "saved" && saved === null) {
      load<CustomTileListBundle>("/api/custom-tiles").then(
        (b) => setSaved(b.tiles),
        setSavedProblem,
      );
    }
  }, [tab, saved]);

  async function deleteSaved(id: number) {
    await send(`/api/custom-tiles/${id}`, "DELETE");
    setSaved((prev) => (prev ? prev.filter((t) => t.id !== id) : prev));
  }

  async function addSaved(tile: CustomTileOut) {
    await addCustomTileToDashboard(target, tile, existingTiles);
    onAdded();
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6" onClick={onClose}>
      <div
        className="flex max-h-[88vh] w-full max-w-[1040px] flex-col overflow-hidden rounded-lg border border-rule bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-rule p-4 pb-0">
          <h2 className="m-0 mb-3 text-[15px] font-semibold">Custom Tile</h2>
          <button
            type="button"
            onClick={onClose}
            className="mb-3 cursor-pointer rounded border-0 bg-transparent text-[18px] leading-none text-ink-3 hover:text-ink"
            aria-label="Close"
          >
            &times;
          </button>
        </div>
        <div className="flex border-b border-rule px-4">
          {([
            ["build", "Build with AI"],
            ["saved", "My Custom Tiles"],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => setTab(id)}
              className={`-mb-px cursor-pointer border-0 border-b-2 bg-transparent px-3 py-2 text-[12.5px] ${
                tab === id ? "border-navy font-semibold text-ink" : "border-transparent text-ink-2 hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "build" && (
          <TileBuilder target={target} existingTiles={existingTiles} onAdded={onAdded} />
        )}

        {tab === "saved" && (
          <div className="overflow-y-auto p-4">
            <div className="grid gap-2.5 sm:grid-cols-2">
              {savedProblem && <p className="m-0 text-[12.5px] text-red">{savedProblem.title}</p>}
              {saved && saved.length === 0 && (
                <p className="m-0 text-[12.5px] text-ink-3">No custom tiles saved yet.</p>
              )}
              {saved?.map((tile) => (
                <div key={tile.id} className="rounded-lg border border-rule p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="text-[12.5px] font-semibold text-ink">{tile.name}</span>
                    <button
                      type="button"
                      onClick={() => deleteSaved(tile.id)}
                      className="cursor-pointer rounded border-0 bg-transparent text-[11.5px] text-ink-3 hover:text-red"
                    >
                      Delete
                    </button>
                  </div>
                  <MiniChart chartType={tile.chart_type} labels={tile.labels} values={tile.values} />
                  <button
                    type="button"
                    onClick={() => addSaved(tile)}
                    className="mt-2.5 w-full cursor-pointer rounded-md border border-rule bg-bg px-2.5 py-1 text-[11.5px] font-semibold text-ink hover:bg-rule/40"
                  >
                    + Add to Dashboard
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
