/*
  The `Custom Tile` dialog on a dashboard canvas: two tabs over the shared
  builder.

  The builder itself lives in `TileBuilder.tsx`, because the Agent page runs
  the same thing as one of its modes and two copies would eventually draw two
  different charts from one conversation. So does the "My Custom Tiles" list -
  same reason, and the Agent page now carries the same tab too. This file is
  just the dialog chrome around both.
*/
import { useState } from "react";
import type { DashboardScope, TileOut } from "../api";
import { SavedTiles, TileBuilder } from "./TileBuilder";

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
  const target = { scopeType, scopeId };

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

        {/* Remounted, not hidden, each time this tab is chosen - see
            `SavedTiles`'s docstring for why that matters right after a save. */}
        {tab === "saved" && (
          <SavedTiles target={target} existingTiles={existingTiles} onAdded={onAdded} />
        )}
      </div>
    </div>
  );
}
