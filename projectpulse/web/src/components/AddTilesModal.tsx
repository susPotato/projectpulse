/*
  "+ Add Tiles" - `Layout_Program` images 20-22: a category rail on the left,
  a grid of tile cards on the right, each with a hover "+ Add Tile" affordance
  (here, a plain visible button - hover-reveal buys nothing on a hackathon
  deadline and costs an interaction test).
*/
import { useState } from "react";
import { send, type CatalogueBundle, type DashboardScope, type TileOut } from "../api";

function nextSlot(existing: TileOut[], w: number, h: number) {
  const y = existing.reduce((max, t) => Math.max(max, t.y + t.h), 0);
  return { x: 0, y, w, h };
}

/* A shape, not real data - the picker would otherwise need to fetch every
   tile's live bundle just to draw a thumbnail. Four skeletons, one per
   `TileSpecOut.preview`, so a stat tile reads differently from a list or a
   heat-map before it is ever added. */
function TilePreview({ kind }: { kind: "stat" | "list" | "heatmap" | "brief" | "chart" }) {
  if (kind === "chart") {
    return (
      <div className="mb-2 h-[64px] rounded-md border border-rule bg-bg p-2">
        <svg viewBox="0 0 100 40" className="h-full w-full text-navy" preserveAspectRatio="none">
          <polyline
            points="0,32 15,26 30,28 45,16 60,20 75,8 100,12"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinejoin="round"
          />
        </svg>
      </div>
    );
  }
  if (kind === "stat") {
    return (
      <div className="mb-2 flex h-[64px] flex-col justify-center rounded-md border border-rule bg-bg px-3">
        <div className="h-[22px] w-[46px] rounded bg-rule/70" />
        <div className="mt-1.5 h-[7px] w-[70px] rounded bg-rule/50" />
      </div>
    );
  }
  if (kind === "heatmap") {
    return (
      <div className="mb-2 grid h-[64px] grid-cols-5 gap-1 rounded-md border border-rule bg-bg p-2">
        {["bg-green/50", "bg-green/50", "bg-amber/50", "bg-red/50", "bg-green/50",
          "bg-amber/50", "bg-green/50", "bg-green/50", "bg-green/50", "bg-red/50"].map((c, i) => (
          <div key={i} className={`rounded-sm ${c}`} />
        ))}
      </div>
    );
  }
  if (kind === "brief") {
    return (
      <div className="mb-2 flex h-[64px] flex-col justify-center gap-1.5 rounded-md border border-rule bg-bg px-3">
        <div className="h-[6px] w-[92%] rounded bg-rule/60" />
        <div className="h-[6px] w-[80%] rounded bg-rule/60" />
        <div className="h-[6px] w-[60%] rounded bg-rule/60" />
      </div>
    );
  }
  return (
    <div className="mb-2 flex h-[64px] flex-col justify-center gap-1.5 rounded-md border border-rule bg-bg px-3">
      {[1, 0.8, 0.6].map((w, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <div className="h-2 w-2 shrink-0 rounded-full bg-rule/70" />
          <div className="h-[6px] rounded bg-rule/60" style={{ width: `${w * 70}%` }} />
        </div>
      ))}
    </div>
  );
}

export function AddTilesModal({
  catalogue,
  scopeType,
  scopeId,
  existingTiles,
  onClose,
  onAdded,
}: {
  catalogue: CatalogueBundle;
  scopeType: DashboardScope;
  scopeId: string;
  existingTiles: TileOut[];
  onClose: () => void;
  onAdded: () => void;
}) {
  const tiles = catalogue.tiles.filter((t) => t.scope === scopeType);
  const categories = Array.from(new Set(tiles.map((t) => t.category)));
  const [category, setCategory] = useState<string>("All");
  const [adding, setAdding] = useState<string | null>(null);

  const shown = category === "All" ? tiles : tiles.filter((t) => t.category === category);

  async function addTile(key: string, w: number, h: number) {
    setAdding(key);
    const slot = nextSlot(existingTiles, w, h);
    try {
      await send(
        `/api/dashboards/tiles?scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`,
        "POST",
        { tile_key: key, ...slot },
      );
      onAdded();
    } finally {
      setAdding(null);
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6"
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-[860px] overflow-hidden rounded-lg border border-rule bg-surface shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="w-[200px] shrink-0 overflow-y-auto border-r border-rule p-3">
          <button
            type="button"
            onClick={() => setCategory("All")}
            className={`mb-1 block w-full cursor-pointer rounded border-0 px-2.5 py-1.5 text-left text-body ${
              category === "All" ? "bg-navy font-semibold text-surface" : "bg-transparent text-ink hover:bg-bg"
            }`}
          >
            All Categories &middot; {tiles.length}
          </button>
          {categories.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setCategory(c)}
              className={`mb-1 block w-full cursor-pointer rounded border-0 px-2.5 py-1.5 text-left text-body ${
                category === c ? "bg-navy font-semibold text-surface" : "bg-transparent text-ink hover:bg-bg"
              }`}
            >
              {c} &middot; {tiles.filter((t) => t.category === c).length}
            </button>
          ))}
        </div>
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="m-0 text-emph font-semibold">Add Tiles</h2>
            <button
              type="button"
              onClick={onClose}
              className="cursor-pointer rounded border-0 bg-transparent text-title leading-none text-ink-3 hover:text-ink"
              aria-label="Close"
            >
              &times;
            </button>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {shown.map((tile) => (
              <div key={tile.key} className="rounded-lg border border-rule p-3">
                <TilePreview kind={tile.preview} />
                <div className="mb-1 text-body font-semibold text-ink">{tile.label}</div>
                <p className="m-0 mb-2.5 text-body text-ink-3">{tile.description}</p>
                <button
                  type="button"
                  disabled={adding === tile.key}
                  onClick={() => addTile(tile.key, tile.default_w, tile.default_h)}
                  className="cursor-pointer rounded-md border border-rule bg-bg px-2.5 py-1 text-body font-semibold text-ink hover:bg-rule/40 disabled:opacity-50"
                >
                  {adding === tile.key ? "Adding..." : "+ Add Tile"}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
