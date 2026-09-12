/*
  "New Dashboard" - `Layout_Program` images 17-19: three tabs, three ways to
  populate a dashboard. Replaces the current one for this scope (see
  `app/dashboard/service.py` - one dashboard per scope, not a saved list).
*/
import { useState } from "react";
import { send, type CatalogueBundle, type DashboardOut, type DashboardScope } from "../api";

type Tab = "fit" | "ai" | "templates" | "blank";

/* What `POST /api/dashboards/fit` reports back.

   Declared here rather than taken from the generated types: the server types it
   as an open object, which is the right shape on the wire - the signal
   vocabulary is the catalogue's to grow - and an unusable one to render from. */
interface FitResult {
  signals: string[];
  placed: number;
  of: number;
  missing: Record<string, string[]>;
}

const SAMPLE_PROMPTS = [
  "Create a simple project cost/budget report",
  "Give me a visual monthly dashboard with bullet summaries",
  "Prepare a sprint delivery report with KPIs",
  "Give me a board/executive 1-pager in FPT brand colours",
];

export function NewDashboardModal({
  catalogue,
  scopeType,
  scopeId,
  onClose,
  onCreated,
}: {
  catalogue: CatalogueBundle;
  scopeType: DashboardScope;
  scopeId: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [tab, setTab] = useState<Tab>("fit");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [fallback, setFallback] = useState<string | null>(null);
  const [fit, setFit] = useState<FitResult | null>(null);
  const query = `scope_type=${scopeType}&scope_id=${encodeURIComponent(scopeId)}`;

  async function createWithAi() {
    if (!prompt.trim()) return;
    setBusy(true);
    setFallback(null);
    try {
      const dashboard = await send<DashboardOut>("/api/dashboards/generate", "POST", {
        scope_type: scopeType, scope_id: scopeId, prompt,
      });
      if (dashboard.fallback_reason) {
        setFallback(dashboard.fallback_reason);
        setBusy(false);
        return;
      }
      onCreated();
    } catch {
      setBusy(false);
    }
  }

  /* Fit the board to what this project's data actually carries.

     Does not close on success the way the other three do. The interesting half
     of fitting a board is what was *left off*, and closing immediately would
     throw that away - a person would see a shorter dashboard and have no idea
     whether that is the data or the product. So the result stays up until they
     dismiss it. */
  async function useFit() {
    setBusy(true);
    try {
      const dashboard = await send<DashboardOut>(`/api/dashboards/fit?${query}`, "POST");
      setFit((dashboard.fit as unknown as FitResult) ?? null);
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  async function useBlank() {
    setBusy(true);
    try {
      await send(`/api/dashboards/blank?${query}`, "POST");
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  async function useTemplate(name: string) {
    setBusy(true);
    try {
      await send(`/api/dashboards/apply-template?${query}&template=${encodeURIComponent(name)}`, "POST");
      onCreated();
    } finally {
      setBusy(false);
    }
  }

  const templates = Object.entries(catalogue.templates);

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-ink/40 p-6" onClick={onClose}>
      <div
        className="w-full max-w-[560px] rounded-lg border border-rule bg-surface p-4 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="m-0 text-[15px] font-semibold">New Dashboard</h2>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded border-0 bg-transparent text-[18px] leading-none text-ink-3 hover:text-ink"
            aria-label="Close"
          >
            &times;
          </button>
        </div>

        <div className="mb-3 flex border-b border-rule">
          {([
            ["fit", "Fit to this data"],
            ["ai", "Create with AI"],
            ["templates", "Browse Templates"],
            ["blank", "Blank Canvas"],
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

        {tab === "fit" && (
          <div>
            <p className="mt-0 mb-2 text-[12.5px] text-ink-2">
              Places every tile this project&rsquo;s data can actually fill, and
              leaves out the ones whose inputs are not there.
            </p>
            <p className="mt-0 mb-3 text-[11.5px] text-ink-3">
              A template assumes a baseline, a dependency graph and a worklog. A
              project imported from a single issue export has none of them, and
              gets a board of empty tiles &mdash; which looks exactly like a
              project with nothing wrong.
            </p>
            {fit && (
              <div className="mb-3 rounded-md border border-rule bg-bg p-2.5">
                <p className="m-0 text-[12px] text-ink">
                  Placed <b>{fit.placed}</b> of {fit.of} tiles &middot; this data
                  carries {(fit.signals ?? []).join(", ") || "nothing yet"}.
                </p>
                {Object.entries(fit.missing ?? {}).map(([signal, tiles]) => (
                  <p key={signal} className="mt-1.5 mb-0 text-[11px] text-ink-3">
                    No <b>{signal}</b> &mdash; would add{" "}
                    {tiles.join(", ")}.
                  </p>
                ))}
              </div>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={useFit}
              className="w-full cursor-pointer rounded-md border border-navy bg-navy px-3 py-1.5 text-[12.5px] font-semibold text-surface disabled:opacity-50"
            >
              {busy ? "Fitting..." : fit ? "Fit again" : "Fit to this data"}
            </button>
          </div>
        )}

        {tab === "ai" && (
          <div>
            <div className="mb-2 flex items-center gap-1.5">
              <span className="text-[13px] font-semibold text-ink">AI Dashboard Creator</span>
              <span className="rounded bg-navy px-1.5 py-0.5 text-[9px] font-extrabold uppercase text-surface">
                Beta
              </span>
            </div>
            <p className="mt-0 mb-2 text-[12px] text-ink-3">
              Describe the dashboard you want and AI will create it for you.
            </p>
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Describe your dashboard..."
              rows={3}
              className="w-full resize-none rounded-md border border-rule bg-bg p-2 text-[12.5px] text-ink"
            />
            <div className="mt-2 flex flex-wrap gap-1.5">
              {SAMPLE_PROMPTS.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPrompt(p)}
                  className="cursor-pointer rounded-full border border-rule bg-bg px-2.5 py-1 text-[11px] text-ink-2 hover:bg-rule/30"
                >
                  {p}
                </button>
              ))}
            </div>
            {fallback && (
              <p className="mt-2 mb-0 text-[11.5px] text-amber">
                Used the default layout instead: {fallback}.
              </p>
            )}
            <button
              type="button"
              disabled={busy || !prompt.trim()}
              onClick={createWithAi}
              className="mt-3 w-full cursor-pointer rounded-md border border-navy bg-navy px-3 py-1.5 text-[12.5px] font-semibold text-surface disabled:opacity-50"
            >
              {busy ? "Creating..." : "Create Dashboard"}
            </button>
          </div>
        )}

        {tab === "templates" && (
          <div className="grid gap-2">
            {templates.map(([name, keys]) => (
              <button
                key={name}
                type="button"
                disabled={busy}
                onClick={() => useTemplate(name)}
                className="cursor-pointer rounded-lg border border-rule p-3 text-left hover:bg-bg disabled:opacity-50"
              >
                <div className="text-[12.5px] font-semibold text-ink">
                  {name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                </div>
                <div className="mt-0.5 text-[11px] text-ink-3">{keys.length} tile(s)</div>
              </button>
            ))}
          </div>
        )}

        {tab === "blank" && (
          <div>
            <p className="mt-0 mb-3 text-[12.5px] text-ink-2">
              Start from an empty canvas and add tiles yourself.
            </p>
            <button
              type="button"
              disabled={busy}
              onClick={useBlank}
              className="w-full cursor-pointer rounded-md border border-rule bg-surface px-3 py-1.5 text-[12.5px] font-semibold text-ink hover:bg-bg disabled:opacity-50"
            >
              Use Blank Canvas
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
