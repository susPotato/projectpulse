/*
  "Custom Tile" - paste your own data, AI drafts a chart from it, you review
  and save it, then it's addable to any dashboard from now on. The one tile
  type whose numbers are a person's own rather than the intelligence layer's
  - see `CustomTile`'s docstring in `app/models/dashboard.py` for why that is
  a deliberate, labelled exception rather than an oversight.
*/
import { useEffect, useState } from "react";
import {
  load,
  send,
  type ApiProblem,
  type ChartType,
  type CustomTileListBundle,
  type CustomTileOut,
  type DashboardScope,
  type TileOut,
} from "../api";
import { MiniChart } from "../tileRegistry";

type Tab = "new" | "saved";

interface Draft {
  title: string;
  chart_type: ChartType;
  labels: string[];
  values: number[];
  note: string | null;
}

function nextSlot(existing: TileOut[], w: number, h: number) {
  const y = existing.reduce((max, t) => Math.max(max, t.y + t.h), 0);
  return { x: 0, y, w, h };
}

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
  const [tab, setTab] = useState<Tab>("new");
  const [rawData, setRawData] = useState("");
  const [hint, setHint] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [draftNote, setDraftNote] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const [saved, setSaved] = useState<CustomTileOut[] | null>(null);
  const [savedProblem, setSavedProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    if (tab === "saved" && saved === null) {
      load<CustomTileListBundle>("/api/custom-tiles").then(
        (b) => setSaved(b.tiles),
        setSavedProblem,
      );
    }
  }, [tab, saved]);

  async function requestDraft() {
    if (!rawData.trim()) return;
    setDrafting(true);
    setDraftError(null);
    try {
      const result = await send<{
        title: string;
        chart_type: ChartType;
        labels: string[];
        values: number[];
        source: "ai" | "csv_fallback";
        fallback_reason: string | null;
      }>("/api/custom-tiles/draft", "POST", { raw_data: rawData, hint: hint || null });
      setDraft({
        title: result.title,
        chart_type: result.chart_type,
        labels: result.labels,
        values: result.values,
        note: null,
      });
      setDraftNote(
        result.source === "csv_fallback"
          ? `Read as a plain table${result.fallback_reason ? ` (${result.fallback_reason})` : ""} - review the numbers below.`
          : null,
      );
    } catch (err) {
      setDraftError((err as ApiProblem).detail ?? "Could not draft a chart from that data.");
    } finally {
      setDrafting(false);
    }
  }

  function updateRow(i: number, field: "label" | "value", value: string) {
    if (!draft) return;
    const labels = [...draft.labels];
    const values = [...draft.values];
    if (field === "label") labels[i] = value;
    else values[i] = Number(value) || 0;
    setDraft({ ...draft, labels, values });
  }

  function removeRow(i: number) {
    if (!draft) return;
    setDraft({
      ...draft,
      labels: draft.labels.filter((_, idx) => idx !== i),
      values: draft.values.filter((_, idx) => idx !== i),
    });
  }

  function addRow() {
    if (!draft) return;
    setDraft({ ...draft, labels: [...draft.labels, "New"], values: [...draft.values, 0] });
  }

  async function addExistingToDashboard(tile: CustomTileOut) {
    await send(
      `/api/dashboards/tiles?scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`,
      "POST",
      { tile_key: `custom:${tile.id}`, settings: { title: tile.name }, ...nextSlot(existingTiles, 4, 3) },
    );
    onAdded();
  }

  async function deleteSaved(id: number) {
    await send(`/api/custom-tiles/${id}`, "DELETE");
    setSaved((prev) => (prev ? prev.filter((t) => t.id !== id) : prev));
  }

  async function saveAndAdd() {
    if (!draft || draft.labels.length === 0) return;
    setSaving(true);
    try {
      const created = await send<CustomTileOut>("/api/custom-tiles", "POST", {
        name: draft.title,
        chart_type: draft.chart_type,
        labels: draft.labels,
        values: draft.values,
        source_note: draft.note,
      });
      await addExistingToDashboard(created);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6" onClick={onClose}>
      <div
        className="flex max-h-[85vh] w-full max-w-[560px] flex-col overflow-hidden rounded-lg border border-rule bg-surface shadow-xl"
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
            ["new", "Create New"],
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

        <div className="overflow-y-auto p-4">
          {tab === "new" && (
            <div>
              {!draft ? (
                <>
                  <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    Paste your data
                  </label>
                  <textarea
                    value={rawData}
                    onChange={(e) => setRawData(e.target.value)}
                    placeholder={"Jan, 12000\nFeb, 15500\nMar, 14200\n\nor any table, list, or a sentence with numbers in it"}
                    rows={6}
                    className="w-full resize-none rounded-md border border-rule bg-bg p-2 font-mono text-[12px] text-ink"
                  />
                  <label className="mt-2.5 mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    What should this show? (optional)
                  </label>
                  <input
                    value={hint}
                    onChange={(e) => setHint(e.target.value)}
                    placeholder="e.g. monthly spend as a bar chart"
                    className="w-full rounded-md border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink"
                  />
                  {draftError && <p className="mt-2 mb-0 text-[11.5px] text-red">{draftError}</p>}
                  <button
                    type="button"
                    disabled={drafting || !rawData.trim()}
                    onClick={requestDraft}
                    className="mt-3 w-full cursor-pointer rounded-md border border-purple/60 bg-purple/15 px-3 py-1.5 text-[12.5px] font-semibold text-purple disabled:opacity-50"
                  >
                    {drafting ? "Drafting..." : "Draft with AI"}
                  </button>
                </>
              ) : (
                <>
                  {draftNote && (
                    <p className="mt-0 mb-2.5 text-[11.5px] text-amber">{draftNote}</p>
                  )}
                  <div className="mb-3 rounded-lg border border-rule p-3">
                    <MiniChart chartType={draft.chart_type} labels={draft.labels} values={draft.values} />
                  </div>
                  <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    Title
                  </label>
                  <input
                    value={draft.title}
                    onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                    className="mb-2.5 w-full rounded-md border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink"
                  />
                  <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    Chart type
                  </label>
                  <select
                    value={draft.chart_type}
                    onChange={(e) => setDraft({ ...draft, chart_type: e.target.value as ChartType })}
                    className="mb-2.5 w-full cursor-pointer rounded-md border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink"
                  >
                    <option value="bar">Bar</option>
                    <option value="line">Line</option>
                    <option value="pie">Pie</option>
                  </select>
                  <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    Data
                  </label>
                  <div className="grid gap-1.5">
                    {draft.labels.map((label, i) => (
                      <div key={i} className="flex items-center gap-1.5">
                        <input
                          value={label}
                          onChange={(e) => updateRow(i, "label", e.target.value)}
                          className="min-w-0 flex-1 rounded-md border border-rule bg-bg px-2 py-1 text-[12px] text-ink"
                        />
                        <input
                          type="number"
                          value={draft.values[i]}
                          onChange={(e) => updateRow(i, "value", e.target.value)}
                          className="w-[90px] rounded-md border border-rule bg-bg px-2 py-1 text-[12px] text-ink"
                        />
                        <button
                          type="button"
                          onClick={() => removeRow(i)}
                          className="cursor-pointer rounded border-0 bg-transparent px-1.5 text-[13px] text-ink-3 hover:text-red"
                          aria-label="Remove row"
                        >
                          &times;
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={addRow}
                    className="mt-2 cursor-pointer rounded border-0 bg-transparent text-[11.5px] font-semibold text-navy hover:underline"
                  >
                    + Add row
                  </button>
                  <div className="mt-3 flex gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setDraft(null);
                        setDraftNote(null);
                      }}
                      className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
                    >
                      Back
                    </button>
                    <button
                      type="button"
                      disabled={saving || draft.labels.length === 0}
                      onClick={saveAndAdd}
                      className="flex-1 cursor-pointer rounded-md border border-navy bg-navy px-3 py-1.5 text-[12.5px] font-semibold text-surface disabled:opacity-50"
                    >
                      {saving ? "Saving..." : "Save & Add to Dashboard"}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {tab === "saved" && (
            <div className="grid gap-2.5">
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
                    onClick={() => addExistingToDashboard(tile)}
                    className="mt-2.5 w-full cursor-pointer rounded-md border border-rule bg-bg px-2.5 py-1 text-[11.5px] font-semibold text-ink hover:bg-rule/40"
                  >
                    + Add to Dashboard
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
