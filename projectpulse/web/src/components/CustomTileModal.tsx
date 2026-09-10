/*
  "Custom Tile" - describe the tile you want, look at it, say what is wrong,
  look again. The AI tile builder.

  The one tile type whose numbers are a person's own rather than the
  intelligence layer's - see `CustomTile`'s docstring in
  `app/models/dashboard.py` for why that is a deliberate, labelled exception
  rather than an oversight.

  Two things here are deliberate and worth not undoing:

  * **The assistant's lines are not written by the model.** Every reply in
    the transcript is composed server-side from a diff of the two drafts
    (`custom.diff_drafts`), so a turn cannot claim a rename while quietly
    having moved a value. The chat and the preview are the same fact.
  * **The preview never blanks out.** A turn that could not be applied comes
    back carrying the previous draft with `ok: false`, so the chart on screen
    survives a model that returned nonsense.
*/
import { useEffect, useRef, useState } from "react";
import {
  load,
  send,
  type ApiProblem,
  type ChartType,
  type CustomChartDraft,
  type CustomTileListBundle,
  type CustomTileOut,
  type DashboardScope,
  type DraftChange,
  type TileChatMessage,
  type TileChatResponse,
  type TileOut,
} from "../api";
import { MiniChart } from "../tileRegistry";

type Tab = "build" | "saved";

/* Fill the box, never send - the same choice the Agent tab's presets make,
   so a click is a starting point a PM edits rather than a turn they did not
   get to read first. */
const REFINEMENTS = [
  "Make it a line chart",
  "Sort highest first",
  "Rename it to ",
  "Drop the smallest row",
  "Show it as a pie chart",
];

/* Fixed locale so the same draft reads the same on every machine, and so a
   server-rendered check sees what a browser does. */
const NUMBER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });

/* The slot a newly-saved custom tile lands in.
   The height is not cosmetic. A custom tile renders its chart, then the
   `Custom` badge and the person's own source note under it - the label that
   makes this tile type an honest exception rather than a quiet one
   (`CustomTile`'s docstring). At `h: 3` the tile body is 124px and the badge
   sits below the fold of its own scroll area, present in the DOM and
   invisible on the canvas. 5 rows clears it, and matches what the other
   chart tiles in `catalogue.py` ask for. */
const CUSTOM_SLOT = { w: 4, h: 5 };

function nextSlot(existing: TileOut[], w: number, h: number) {
  const y = existing.reduce((max, t) => Math.max(max, t.y + t.h), 0);
  return { x: 0, y, w, h };
}

/* What produced the draft on screen, said plainly. A table read by the plain
   CSV parser must never be presented as the model's own read of the data. */
function sourceBadge(draft: CustomChartDraft): { label: string; tone: string } {
  if (draft.source === "ai") return { label: "AI draft", tone: "text-purple bg-purple/15" };
  if (draft.source === "local_edit") return { label: "Applied directly", tone: "text-navy bg-navy/15" };
  return { label: "Read as a table", tone: "text-amber bg-amber/15" };
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
  const [tab, setTab] = useState<Tab>("build");

  const [messages, setMessages] = useState<TileChatMessage[]>([]);
  const [draft, setDraft] = useState<CustomChartDraft | null>(null);
  const [changes, setChanges] = useState<DraftChange[]>([]);
  const [rawData, setRawData] = useState("");
  const [showData, setShowData] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [turnError, setTurnError] = useState<string | null>(null);
  const [showRows, setShowRows] = useState(false);
  const [saving, setSaving] = useState(false);

  const [saved, setSaved] = useState<CustomTileOut[] | null>(null);
  const [savedProblem, setSavedProblem] = useState<ApiProblem | null>(null);

  const transcriptEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (tab === "saved" && saved === null) {
      load<CustomTileListBundle>("/api/custom-tiles").then(
        (b) => setSaved(b.tiles),
        setSavedProblem,
      );
    }
  }, [tab, saved]);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [messages.length]);

  async function sendTurn(text: string) {
    const asked = text.trim();
    if (!asked || sending) return;

    const withUser: TileChatMessage[] = [...messages, { role: "user", content: asked }];
    setMessages(withUser);
    setInput("");
    setSending(true);
    setTurnError(null);
    try {
      const result = await send<TileChatResponse>("/api/custom-tiles/chat", "POST", {
        messages: withUser,
        draft,
        raw_data: rawData.trim() ? rawData : null,
      });
      setDraft(result.draft);
      setChanges(result.changes);
      setMessages([...withUser, { role: "assistant", content: result.reply }]);
      if (!result.ok) setTurnError(result.error ?? null);
    } catch (err) {
      const detail =
        (err as ApiProblem).detail ?? "Could not build a chart from that.";
      setMessages([...withUser, { role: "assistant", content: detail }]);
      setTurnError(detail);
    } finally {
      setSending(false);
    }
  }

  function patchDraft(update: Partial<CustomChartDraft>) {
    if (!draft) return;
    setDraft({ ...draft, ...update });
    setChanges([]);
  }

  function updateRow(i: number, field: "label" | "value", value: string) {
    if (!draft) return;
    const labels = [...draft.labels];
    const values = [...draft.values];
    if (field === "label") labels[i] = value;
    else values[i] = Number(value) || 0;
    patchDraft({ labels, values });
  }

  function removeRow(i: number) {
    if (!draft) return;
    patchDraft({
      labels: draft.labels.filter((_, idx) => idx !== i),
      values: draft.values.filter((_, idx) => idx !== i),
    });
  }

  function addRow() {
    if (!draft) return;
    patchDraft({ labels: [...draft.labels, "New"], values: [...draft.values, 0] });
  }

  function startOver() {
    setDraft(null);
    setMessages([]);
    setChanges([]);
    setTurnError(null);
    setInput("");
    setShowRows(false);
  }

  async function addExistingToDashboard(tile: CustomTileOut) {
    await send(
      `/api/dashboards/tiles?scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`,
      "POST",
      {
        tile_key: `custom:${tile.id}`,
        settings: { title: tile.name },
        ...nextSlot(existingTiles, CUSTOM_SLOT.w, CUSTOM_SLOT.h),
      },
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
        // What they asked for, kept verbatim - the only provenance a pasted
        // number has, and what the canvas labels the tile with.
        source_note: messages.find((m) => m.role === "user")?.content ?? null,
      });
      await addExistingToDashboard(created);
    } finally {
      setSaving(false);
    }
  }

  const badge = draft ? sourceBadge(draft) : null;

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6" onClick={onClose}>
      <div
        className="flex max-h-[88vh] w-full max-w-[680px] flex-col overflow-hidden rounded-lg border border-rule bg-surface shadow-xl"
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
          <>
            {/* The preview. Pinned above the conversation so a refinement and
                its effect are on screen at the same time. */}
            {draft && (
              <div className="shrink-0 border-b border-rule bg-bg/40 p-4">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="min-w-0 truncate text-[13px] font-semibold text-ink">
                    {draft.title}
                  </span>
                  {badge && (
                    <span
                      className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-extrabold tracking-[0.05em] uppercase ${badge.tone}`}
                    >
                      {badge.label}
                    </span>
                  )}
                </div>
                <div className="rounded-lg border border-rule bg-surface p-3">
                  <MiniChart
                    chartType={draft.chart_type}
                    labels={draft.labels}
                    values={draft.values}
                  />
                  {/* The rows, spelled out under the chart. `MiniChart` is a
                      sparkline sized for a canvas tile - it carries no axis,
                      and a line with no labels cannot be checked. This panel
                      exists to be checked before the tile is saved, so it
                      prints what it is drawing, the same reason the forecast
                      panel prints its sample beside its percentiles. */}
                  <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1 border-t border-rule pt-2">
                    {draft.labels.map((label, i) => (
                      <span key={i} className="text-[11px] text-ink-3">
                        {label}{" "}
                        <span className="font-semibold text-ink-2">
                          {NUMBER.format(draft.values[i] ?? 0)}
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
                {changes.length > 0 && (
                  <p className="mt-2 mb-0 text-[11px] text-ink-3">
                    <span className="font-bold tracking-[0.06em] uppercase">Changed</span>{" "}
                    {changes.map((c) => c.summary).join("; ")}
                  </p>
                )}
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {!draft && (
                <>
                  <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                    Paste your data (optional)
                  </label>
                  <textarea
                    value={rawData}
                    onChange={(e) => setRawData(e.target.value)}
                    placeholder={"Jan, 12000\nFeb, 15500\nMar, 14200\n\nor any table, list, or a sentence with numbers in it"}
                    rows={5}
                    className="w-full resize-none rounded-md border border-rule bg-bg p-2 font-mono text-[12px] text-ink"
                  />
                  <p className="mt-2 mb-0 text-[11.5px] text-ink-3">
                    Then say what you want below. The numbers stay yours - this
                    builder only ever reads and rearranges what you give it.
                  </p>
                </>
              )}

              {/* The transcript. */}
              {messages.length > 0 && (
                <div className="grid gap-2">
                  {messages.map((message, i) => (
                    <div
                      key={i}
                      className={
                        message.role === "user"
                          ? "justify-self-end rounded-lg rounded-br-sm border border-navy/40 bg-navy/10 px-2.5 py-1.5 text-[12.5px] text-ink max-w-[85%]"
                          : "justify-self-start rounded-lg rounded-bl-sm border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink-2 max-w-[85%]"
                      }
                    >
                      {message.content}
                    </div>
                  ))}
                  <div ref={transcriptEnd} />
                </div>
              )}

              {sending && (
                <p className="mt-2 mb-0 text-[11.5px] text-ink-3">Working on it...</p>
              )}

              {/* Hand editing, beside the conversation rather than instead of
                  it: changing one number is faster typed than described. */}
              {draft && (
                <div className="mt-3 border-t border-rule pt-3">
                  <button
                    type="button"
                    onClick={() => setShowRows((v) => !v)}
                    className="cursor-pointer rounded border-0 bg-transparent p-0 text-[11.5px] font-semibold text-navy hover:underline"
                  >
                    {showRows ? "Hide" : "Edit"} title, type and rows by hand
                  </button>
                  {showRows && (
                    <div className="mt-2.5">
                      <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                        Title
                      </label>
                      <input
                        value={draft.title}
                        onChange={(e) => patchDraft({ title: e.target.value })}
                        className="mb-2.5 w-full rounded-md border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink"
                      />
                      <label className="mb-1 block text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
                        Chart type
                      </label>
                      <select
                        value={draft.chart_type}
                        onChange={(e) => patchDraft({ chart_type: e.target.value as ChartType })}
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
                        className="mt-2 cursor-pointer rounded border-0 bg-transparent p-0 text-[11.5px] font-semibold text-navy hover:underline"
                      >
                        + Add row
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* The composer. */}
            <div className="shrink-0 border-t border-rule p-4">
              {turnError && (
                <p className="mt-0 mb-2 text-[11.5px] text-amber">{turnError}</p>
              )}
              {draft && (
                <div className="mb-2 flex flex-wrap gap-1.5">
                  {REFINEMENTS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => setInput(preset)}
                      className="cursor-pointer rounded-full border border-rule bg-bg px-2 py-0.5 text-[11px] text-ink-2 hover:text-ink"
                    >
                      {preset.trim()}
                    </button>
                  ))}
                </div>
              )}
              <div className="flex gap-2">
                <input
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      sendTurn(input);
                    }
                  }}
                  placeholder={
                    draft
                      ? "What should change?"
                      : "e.g. monthly spend as a bar chart"
                  }
                  className="min-w-0 flex-1 rounded-md border border-rule bg-bg px-2.5 py-1.5 text-[12.5px] text-ink"
                />
                <button
                  type="button"
                  disabled={sending || !input.trim()}
                  onClick={() => sendTurn(input)}
                  className="cursor-pointer rounded-md border border-purple/60 bg-purple/15 px-3 py-1.5 text-[12.5px] font-semibold text-purple disabled:opacity-50"
                >
                  {sending ? "..." : draft ? "Send" : "Draft it"}
                </button>
              </div>
              {draft && (
                <div className="mt-2.5 flex gap-2">
                  <button
                    type="button"
                    onClick={startOver}
                    className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
                  >
                    Start over
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowData((v) => !v)}
                    className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
                  >
                    {showData ? "Hide data" : "Source data"}
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
              )}
              {draft && showData && (
                <textarea
                  value={rawData}
                  onChange={(e) => setRawData(e.target.value)}
                  placeholder="The data this chart was built from - every later turn is held to it."
                  rows={4}
                  className="mt-2 w-full resize-none rounded-md border border-rule bg-bg p-2 font-mono text-[12px] text-ink"
                />
              )}
            </div>
          </>
        )}

        {tab === "saved" && (
          <div className="overflow-y-auto p-4">
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
          </div>
        )}
      </div>
    </div>
  );
}
