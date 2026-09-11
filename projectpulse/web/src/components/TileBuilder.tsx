/*
  The AI tile builder: describe the tile you want, look at it, say what is
  wrong, look again.

  One implementation, two hosts - the `Custom Tile` dialog on a dashboard
  canvas, and `Build a tile` mode on the Agent page. It lives here rather
  than in either of them because two copies of this would eventually draw two
  different charts from one conversation, the same reason `gantt.js` was never
  ported twice.

  This is the one tile type whose numbers are a person's own rather than the
  intelligence layer's - see `CustomTile`'s docstring in
  `app/models/dashboard.py` for why that is a deliberate, labelled exception
  rather than an oversight.

  The layout: **conversation on the left, one sticky stage on the right.**
  The stage shows the tile inside a mock window frame, so what a PM is
  approving looks like the thing that will land on their dashboard rather
  than a chart floating in a form. Every answer also leaves a numbered
  version in the transcript, and clicking one puts that chart on the stage -
  so the history is navigable instead of merely visible.

  Three things are deliberate and worth not undoing:

  * **The assistant's lines are not written by the model.** Every reply is
    composed server-side from a diff of the two drafts
    (`custom.diff_drafts`), so a turn cannot claim a rename while quietly
    having moved a value. The chat and the stage are one fact.
  * **The stage never blanks out.** A turn that could not be applied comes
    back carrying the previous draft with `ok: false`, so the tile on screen
    survives a model that returned nonsense.
  * **`Save` is not offered while an older version is on the stage.** It
    writes the current draft, so a stage showing v1 beside a live `Save`
    button would be the one on-screen/actual mismatch this screen exists to
    prevent.

  The host must give this a bounded flex container to grow into - it renders
  a `flex-1` row and expects the parent to own the height.
*/
import { Fragment, useEffect, useRef, useState } from "react";
import {
  load,
  send,
  type ApiProblem,
  type ChartType,
  type CustomChartDraft,
  type CustomTileListBundle,
  type CustomTileOut,
  type DashboardOut,
  type DashboardScope,
  type DraftChange,
  type TileChatMessage,
  type TileChatResponse,
  type TileOut,
} from "../api";
import { MiniChart } from "../tileRegistry";

/** Which dashboard a saved tile is added to. */
export interface TileTarget {
  scopeType: DashboardScope;
  scopeId: string;
  /** A name for it, so the button can say where the tile is going. */
  label?: string;
}

/** One line of the transcript, plus - on an answer - the chart it produced. */
interface Turn {
  role: "user" | "assistant";
  content: string;
  /** The chart as it stood after this turn. Answers only. */
  draft?: CustomChartDraft;
  changes?: DraftChange[];
  /** 1-based, counting only answers that produced a chart. */
  version?: number;
  /** False when the turn was understood but could not be applied. */
  ok?: boolean;
}

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

/**
 * Add an already-saved custom tile to a dashboard, below whatever is there.
 *
 * `existing` is the caller's view of the canvas when it has one. Without it -
 * the Agent page, which is not showing a canvas at all - the dashboard is
 * fetched, because placing a tile at y=0 would drop it on top of the tiles
 * already there.
 */
export async function addCustomTileToDashboard(
  target: TileTarget,
  tile: CustomTileOut,
  existing?: TileOut[],
) {
  const scope = `scope_type=${target.scopeType}&scope_id=${encodeURIComponent(target.scopeId)}`;
  const tiles =
    existing ?? (await load<DashboardOut>(`/api/dashboards?${scope}`)).tiles;
  const y = tiles.reduce((max, t) => Math.max(max, t.y + t.h), 0);
  await send(`/api/dashboards/tiles?${scope}`, "POST", {
    tile_key: `custom:${tile.id}`,
    settings: { title: tile.name },
    x: 0,
    y,
    ...CUSTOM_SLOT,
  });
}

/**
 * "My Custom Tiles": every tile ever saved, across every project - `CustomTile`
 * carries no project or scope of its own (see its docstring), so this is not
 * filtered to `target` even when one is given.
 *
 * One implementation, two hosts, same reason as the builder above: the
 * `Custom Tile` dialog's second tab, and now the Agent page's "Build a tile"
 * mode. It used to exist only in the dialog - the Agent page had no way to
 * see a tile it had just saved, which read as "it said saved but it is not
 * there" when what had actually happened is there was nowhere on that page
 * to look. Fetches fresh on every mount rather than caching across the
 * host's lifetime: a tab switch away and back is the moment this needs to be
 * right, right after a save.
 */
export function SavedTiles({
  target,
  existingTiles,
  onAdded,
}: {
  /** Where "+ Add to Dashboard" sends a tile. Null hides that action - the
      Agent page, before a project has resolved, has nowhere to put one. */
  target: TileTarget | null;
  existingTiles?: TileOut[];
  onAdded?: () => void;
}) {
  const [saved, setSaved] = useState<CustomTileOut[] | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<CustomTileListBundle>("/api/custom-tiles").then(
      (b) => setSaved(b.tiles),
      setProblem,
    );
  }, []);

  async function deleteSaved(id: number) {
    await send(`/api/custom-tiles/${id}`, "DELETE");
    setSaved((prev) => (prev ? prev.filter((t) => t.id !== id) : prev));
  }

  async function addSaved(tile: CustomTileOut) {
    if (!target) return;
    await addCustomTileToDashboard(target, tile, existingTiles);
    onAdded?.();
  }

  return (
    <div className="overflow-y-auto p-4">
      <div className="grid gap-2.5 sm:grid-cols-2">
        {problem && <p className="m-0 text-[12.5px] text-red">{problem.title}</p>}
        {saved && saved.length === 0 && (
          <p className="m-0 text-[12.5px] text-ink-3">No custom tiles saved yet.</p>
        )}
        {saved?.map((tile) => (
          <div key={tile.id} className="rounded-lg border border-rule p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <span className="min-w-0 flex-1 truncate text-[12.5px] font-semibold text-ink">
                {tile.name}
              </span>
              {tile.live_source && (
                <span
                  className="shrink-0 rounded bg-green/15 px-1.5 py-0.5 text-[9px] font-extrabold tracking-[0.05em] text-green uppercase"
                  title="Refreshes from the project's own data every time this tile is viewed"
                >
                  Live
                </span>
              )}
              <button
                type="button"
                onClick={() => deleteSaved(tile.id)}
                className="shrink-0 cursor-pointer rounded border-0 bg-transparent text-[11.5px] text-ink-3 hover:text-red"
              >
                Delete
              </button>
            </div>
            <MiniChart chartType={tile.chart_type} labels={tile.labels} values={tile.values} />
            {target && (
              <button
                type="button"
                onClick={() => addSaved(tile)}
                className="mt-2.5 w-full cursor-pointer rounded-md border border-rule bg-bg px-2.5 py-1 text-[11.5px] font-semibold text-ink hover:bg-rule/40"
              >
                + Add to Dashboard
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

/* What produced the draft, said plainly. A table read by the plain CSV
   parser must never be presented as the model's own read of the data. */
function sourceBadge(draft: CustomChartDraft): { label: string; tone: string } {
  if (draft.source === "ai") return { label: "AI draft", tone: "text-purple bg-purple/15" };
  if (draft.source === "local_edit") return { label: "Applied directly", tone: "text-navy bg-navy/15" };
  return { label: "Read as a table", tone: "text-amber bg-amber/15" };
}

/*
  `MiniChart` for "line" is a 260x64 sparkline with `preserveAspectRatio=
  "none"`, so it grows to whatever height its box allows - ~150px at this
  width, which is why its height is pinned rather than left to the intrinsic
  ratio. Never applied to "pie" or the non-compact "bar": both return a
  variable-height list (a legend, or one row per label) rather than a
  stretchable svg, and a fixed height would only clip them - see
  `MiniChart`'s own docstring for why "bar" is a row list at all now.
*/
function chartBox(chartType: ChartType, height: string) {
  return chartType === "line" ? `${height} [&>svg]:h-full` : "";
}

/**
 * The stage: the tile as it will appear on the dashboard, in a mock window.
 *
 * The frame is not decoration. A PM approving a tile is approving a thing
 * that will sit on a canvas beside the computed tiles, so the preview shows
 * it with the chrome and the `Custom` label it will actually carry there -
 * including the label, because a custom tile's numbers are the person's own
 * and the canvas says so.
 */
function TileStage({
  draft,
  changes,
  note,
  version,
  isCurrent,
  onBackToCurrent,
}: {
  draft: CustomChartDraft;
  changes?: DraftChange[];
  note: string | null;
  version?: number;
  isCurrent: boolean;
  onBackToCurrent: () => void;
}) {
  const badge = sourceBadge(draft);
  return (
    <div>
      <div className="mb-2 flex items-center justify-between gap-2">
        <span className="text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
          {isCurrent ? "Preview" : `Version ${version}`}
        </span>
        {!isCurrent && (
          <button
            type="button"
            onClick={onBackToCurrent}
            className="cursor-pointer rounded border-0 bg-transparent p-0 text-[11.5px] font-semibold text-navy hover:underline"
          >
            Back to current
          </button>
        )}
      </div>

      <div className="overflow-hidden rounded-lg border border-rule bg-surface shadow-lg">
        {/* Window chrome, so this reads as the tile rather than as a form field. */}
        <div className="flex items-center gap-2 border-b border-rule bg-bg/60 px-3 py-2">
          <span className="flex gap-1">
            <span className="h-2 w-2 rounded-full bg-rule" />
            <span className="h-2 w-2 rounded-full bg-rule" />
            <span className="h-2 w-2 rounded-full bg-rule" />
          </span>
          <span className="min-w-0 flex-1 truncate text-[10px] font-bold tracking-[0.06em] text-ink-3 uppercase">
            On your dashboard
          </span>
          {draft.live_source && (
            <span
              className="shrink-0 rounded bg-green/15 px-1.5 py-0.5 text-[9px] font-extrabold tracking-[0.05em] text-green uppercase"
              title="Read straight from the project's own data - refreshes every time this tile is viewed, not frozen at save"
            >
              Live
            </span>
          )}
          <span
            className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-extrabold tracking-[0.05em] uppercase ${badge.tone}`}
          >
            {badge.label}
          </span>
        </div>

        <div className="p-3">
          <div className="mb-2 truncate text-[11px] font-bold tracking-[0.06em] text-ink-3 uppercase">
            {draft.title}
          </div>
          <div className={chartBox(draft.chart_type, "h-[132px]")}>
            <MiniChart chartType={draft.chart_type} labels={draft.labels} values={draft.values} />
          </div>
          {/* The rows, spelled out. `MiniChart` carries no axis, so a line
              with no labels cannot be checked - and being checked before it
              is saved is the whole job of this panel. Same reason the
              forecast panel prints its sample beside its percentiles. */}
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
          {/* Exactly what the canvas will render under the chart. */}
          <div className="mt-2.5 flex items-center gap-1.5 border-t border-rule pt-2">
            <span className="rounded bg-purple/15 px-1.5 py-0.5 text-[9px] font-extrabold tracking-[0.05em] text-purple uppercase">
              Custom
            </span>
            {note && (
              <span className="min-w-0 flex-1 truncate text-[10.5px] text-ink-3" title={note}>
                {note}
              </span>
            )}
          </div>
        </div>
      </div>

      {changes && changes.length > 0 && (
        <p className="mt-2 mb-0 text-[11px] text-ink-3">
          <span className="font-bold tracking-[0.06em] uppercase">Changed</span>{" "}
          {changes.map((c) => c.summary).join("; ")}
        </p>
      )}
    </div>
  );
}

export function TileBuilder({
  target,
  existingTiles,
  onAdded,
}: {
  /** Where `Save` puts the tile. `null` saves it to the library only, which
      is what the Agent page does when no project has been selected. */
  target: TileTarget | null;
  /** The host's view of the canvas, when it is showing one. */
  existingTiles?: TileOut[];
  /** Called after a tile has been added to a dashboard - the dialog closes
      and reloads on this; the Agent page has nothing to close. */
  onAdded?: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState<CustomChartDraft | null>(null);
  const [changes, setChanges] = useState<DraftChange[]>([]);
  /** Which earlier version the stage is showing; null means the current one. */
  const [viewing, setViewing] = useState<number | null>(null);
  const [rawData, setRawData] = useState("");
  const [showData, setShowData] = useState(false);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [turnError, setTurnError] = useState<string | null>(null);
  const [showRows, setShowRows] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedMessage, setSavedMessage] = useState<string | null>(null);

  const transcriptEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ block: "nearest" });
  }, [turns.length, sending]);

  /* What the PM said first, which is the provenance a pasted number gets and
     the note the canvas prints under the tile. */
  const note = turns.find((t) => t.role === "user")?.content ?? null;

  /* The newest version produced so far. `viewing === null` means "the
     current draft", which is the newest version plus any hand edits since. */
  const latest = turns.reduce(
    (max, t) => (t.version !== undefined && t.version > max ? t.version : max),
    0,
  );
  const viewed = viewing === null ? null : turns.find((t) => t.version === viewing);
  const stageDraft = viewed?.draft ?? draft;
  const stageChanges = viewed ? viewed.changes : changes;

  async function sendTurn(text: string) {
    const asked = text.trim();
    if (!asked || sending) return;

    const withUser: Turn[] = [...turns, { role: "user", content: asked }];
    setTurns(withUser);
    setInput("");
    setSending(true);
    setTurnError(null);
    setSavedMessage(null);
    setViewing(null); // a new answer is what you want to be looking at
    try {
      // Only role and content go on the wire - the drafts hanging off each
      // turn are for this screen, and the current one is sent once, below.
      const messages: TileChatMessage[] = withUser.map(({ role, content }) => ({
        role,
        content,
      }));
      const result = await send<TileChatResponse>("/api/custom-tiles/chat", "POST", {
        messages,
        draft,
        raw_data: rawData.trim() ? rawData : null,
        // Lets the opening turn reach for this project's own real data
        // (effort, findings, risks, forecast) instead of asking for a paste
        // - see app/dashboard/agent.py. Program-scoped targets have no
        // single project to query, so this stays unset for those.
        scope_id: target?.scopeType === "project" ? target.scopeId : null,
      });
      const version = withUser.filter((t) => t.version !== undefined).length + 1;
      setDraft(result.draft);
      setChanges(result.changes);
      setTurns([
        ...withUser,
        {
          role: "assistant",
          content: result.reply,
          draft: result.draft,
          changes: result.changes,
          version,
          ok: result.ok,
        },
      ]);
      if (!result.ok) setTurnError(result.error ?? null);
    } catch (err) {
      const detail = (err as ApiProblem).detail ?? "Could not build a chart from that.";
      setTurns([...withUser, { role: "assistant", content: detail, ok: false }]);
      setTurnError(detail);
    } finally {
      setSending(false);
    }
  }

  /* A hand edit changes the current draft, so it changes the stage - and it
     clears the `Changed` line, which described the model's edit and no longer
     describes what is on screen. */
  function patchDraft(update: Partial<CustomChartDraft>) {
    if (!draft) return;
    setDraft({ ...draft, ...update });
    setChanges([]);
    setViewing(null);
    setSavedMessage(null);
  }

  /* A hand-edited row is no longer a pure read of the tool it came from -
     `live_source` describes exactly that read, so it stops being true the
     moment a row is typed, added, or removed here. Title and chart-type
     edits (`patchDraft` calls elsewhere) do not touch this: neither changes
     what the rows mean. */
  function updateRow(i: number, field: "label" | "value", value: string) {
    if (!draft) return;
    const labels = [...draft.labels];
    const values = [...draft.values];
    if (field === "label") labels[i] = value;
    else values[i] = Number(value) || 0;
    patchDraft({ labels, values, live_source: null });
  }

  function removeRow(i: number) {
    if (!draft) return;
    patchDraft({
      labels: draft.labels.filter((_, idx) => idx !== i),
      values: draft.values.filter((_, idx) => idx !== i),
      live_source: null,
    });
  }

  function addRow() {
    if (!draft) return;
    patchDraft({ labels: [...draft.labels, "New"], values: [...draft.values, 0], live_source: null });
  }

  /* Adopt the version being looked at, so `Save` is never a surprise. */
  function useThisVersion() {
    if (!viewed?.draft) return;
    setDraft(viewed.draft);
    setChanges([]);
    setViewing(null);
  }

  function startOver() {
    setDraft(null);
    setTurns([]);
    setChanges([]);
    setViewing(null);
    setTurnError(null);
    setSavedMessage(null);
    setInput("");
    setShowRows(false);
  }

  async function save() {
    // Always the current draft, never the version being looked at - saving
    // something other than what "Preview" says would be the one confusion
    // this whole screen exists to avoid.
    if (!draft || draft.labels.length === 0) return;
    setSaving(true);
    setTurnError(null);
    try {
      const created = await send<CustomTileOut>("/api/custom-tiles", "POST", {
        name: draft.title,
        chart_type: draft.chart_type,
        labels: draft.labels,
        values: draft.values,
        source_note: note,
        live_source: draft.live_source,
      });
      if (target) {
        await addCustomTileToDashboard(target, created, existingTiles);
        setSavedMessage(`Added "${created.name}" to the dashboard.`);
        onAdded?.();
      } else {
        setSavedMessage(
          `Saved "${created.name}" to My Custom Tiles - add it to any dashboard ` +
            `from the Custom Tile button there.`,
        );
      }
    } catch (err) {
      setTurnError((err as ApiProblem).detail ?? "Could not save that tile.");
    } finally {
      setSaving(false);
    }
  }

  const saveLabel = target
    ? target.label
      ? `Save & Add to ${target.label}`
      : "Save & Add to Dashboard"
    : "Save to My Custom Tiles";

  return (
    /* Two columns once there is room; stacked below that, because a stage
       narrower than its chart is worse than a stage underneath. */
    <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
      <div className="flex min-h-0 flex-1 flex-col lg:border-r lg:border-rule">
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {!draft && turns.length === 0 && (
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
                Then say what you want below. The tile appears beside this
                conversation and changes as you refine it, so you can see what
                each instruction did before you keep going. The numbers stay
                yours - this builder only ever reads and rearranges what you
                give it.
              </p>
            </>
          )}

          {turns.length > 0 && (
            <div className="grid gap-2">
              {turns.map((turn, i) =>
                turn.role === "user" ? (
                  <div
                    key={i}
                    className="max-w-[85%] justify-self-end rounded-lg rounded-br-sm border border-navy/40 bg-navy/10 px-2.5 py-1.5 text-[12.5px] text-ink"
                  >
                    {turn.content}
                  </div>
                ) : (
                  <Fragment key={i}>
                    <div
                      className={`max-w-[85%] justify-self-start rounded-lg rounded-bl-sm border px-2.5 py-1.5 text-[12.5px] ${
                        turn.ok === false
                          ? "border-amber/40 bg-amber/10 text-ink-2"
                          : "border-rule bg-bg text-ink-2"
                      }`}
                    >
                      {turn.content}
                    </div>
                    {/* The version this answer produced. Clicking it puts that
                        chart on the stage, so the history is navigable rather
                        than only visible - v1 next to v3 before committing. */}
                    {turn.draft && turn.version !== undefined && (
                      <button
                        type="button"
                        onClick={() =>
                          setViewing(turn.version === latest ? null : turn.version ?? null)
                        }
                        className={`flex max-w-[85%] cursor-pointer items-center gap-2 justify-self-start rounded-md border px-2 py-1 text-left ${
                          (viewing === null && turn.version === latest) ||
                          viewing === turn.version
                            ? "border-navy/50 bg-navy/10"
                            : "border-rule bg-bg hover:border-navy/40"
                        }`}
                      >
                        <span className="shrink-0 text-[10px] font-extrabold tracking-[0.05em] text-navy uppercase">
                          v{turn.version}
                        </span>
                        <span className="w-[64px] shrink-0">
                          {/* Always the small fixed box here, regardless of
                              chart type - this chip is 64x22px full stop,
                              unlike the stage which grows for "bar"/"pie". */}
                          <span className="block h-[22px] [&>svg]:h-full">
                            <MiniChart
                              chartType={turn.draft.chart_type}
                              labels={turn.draft.labels}
                              values={turn.draft.values}
                              compact
                            />
                          </span>
                        </span>
                        <span className="min-w-0 flex-1 truncate text-[10.5px] text-ink-3">
                          {turn.changes && turn.changes.length > 0
                            ? turn.changes.map((c) => c.summary).join("; ")
                            : turn.draft.title}
                        </span>
                      </button>
                    )}
                  </Fragment>
                ),
              )}
              <div ref={transcriptEnd} />
            </div>
          )}

          {sending && <p className="mt-2 mb-0 text-[11.5px] text-ink-3">Working on it...</p>}
        </div>

        {/* The composer. */}
        <div className="shrink-0 border-t border-rule p-4">
          {turnError && <p className="mt-0 mb-2 text-[11.5px] text-amber">{turnError}</p>}
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
              placeholder={draft ? "What should change?" : "e.g. monthly spend as a bar chart"}
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
        </div>
      </div>

      {/* The stage. */}
      {draft && stageDraft && (
        <aside className="w-full shrink-0 overflow-y-auto border-t border-rule bg-bg/30 p-4 lg:w-[400px] lg:border-t-0">
          <TileStage
            draft={stageDraft}
            changes={stageChanges ?? []}
            note={note}
            version={viewing ?? undefined}
            isCurrent={viewing === null}
            onBackToCurrent={() => setViewing(null)}
          />

          {/* Hand editing, under the stage it edits: changing one number is
              faster typed than described. Hidden while an older version is
              up, because it edits the current draft and not the one shown. */}
          <div className={`mt-3 border-t border-rule pt-3 ${viewing === null ? "" : "hidden"}`}>
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
                        className="w-[80px] rounded-md border border-rule bg-bg px-2 py-1 text-[12px] text-ink"
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

          <div className="mt-3 grid gap-2 border-t border-rule pt-3">
            {savedMessage && (
              <p className="m-0 rounded-md border border-green/40 bg-green/10 px-2.5 py-1.5 text-[11.5px] text-green">
                {savedMessage}
              </p>
            )}
            {viewing === null ? (
              <button
                type="button"
                disabled={saving || draft.labels.length === 0}
                onClick={save}
                className="w-full cursor-pointer rounded-md border border-navy bg-navy px-3 py-1.5 text-[12.5px] font-semibold text-surface disabled:opacity-50"
              >
                {saving ? "Saving..." : saveLabel}
              </button>
            ) : (
              <button
                type="button"
                onClick={useThisVersion}
                className="w-full cursor-pointer rounded-md border border-navy bg-navy/15 px-3 py-1.5 text-[12.5px] font-semibold text-navy"
              >
                Use version {viewing}
              </button>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={startOver}
                className="flex-1 cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
              >
                Start over
              </button>
              <button
                type="button"
                onClick={() => setShowData((v) => !v)}
                className="flex-1 cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12px] font-semibold text-ink hover:bg-bg"
              >
                {showData ? "Hide data" : "Source data"}
              </button>
            </div>
            {showData && (
              <textarea
                value={rawData}
                onChange={(e) => setRawData(e.target.value)}
                placeholder="The data this chart was built from - every later turn is held to it."
                rows={4}
                className="w-full resize-none rounded-md border border-rule bg-bg p-2 font-mono text-[12px] text-ink"
              />
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
