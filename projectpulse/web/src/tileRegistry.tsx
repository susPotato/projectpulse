/*
  tile_key -> the component that renders it.

  Every tile is a thin adapter over a bundle the app already computes
  (`/api/insight`, `/api/programs/{id}`, `/api/risks`) - no tile here invents
  a chart from data nobody supplied. Program-scope AI tiles read the
  program's highest-priority project's own insight bundle, labelled as such,
  rather than a synthesized program-level narrative nothing computes.
*/
import { useEffect, useState, type ComponentType } from "react";
import {
  load,
  programLink,
  projectLink,
  type ApiProblem,
  type ChartType,
  type CustomTileOut,
  type ForecastBundle,
  type GanttBundle,
  type InsightBundle,
  type PortfolioBundle,
  type ProgramBundle,
  type ProgramRollupBundle,
  type RiskBundle,
  type TeamBundle,
} from "./api";
import { Problem } from "./components/Shell";
import { GanttChart } from "./components/GanttChart";

export interface TileProps {
  scopeType: "program" | "project";
  scopeId: string;
  //: The dashboard tile's own `tile_key` - most tiles ignore this (they key
  //: entirely off scope), but a custom tile's data lives at `custom:<id>`
  //: rather than in any scope's bundle, so it needs its own key to know
  //: which one to fetch.
  tileKey: string;
  settings?: Record<string, unknown> | null;
}

/* Fixed categorical order (dataviz skill: never cycled, never assigned by
   rank) - reused from the app's own token set rather than a second palette.
   A pie past this many slices folds the rest into "Other" instead of
   generating a color nobody chose. */
const CATEGORICAL_ORDER = ["navy", "blue", "orange", "green", "red", "amber", "purple"] as const;
const MAX_PIE_SLICES = CATEGORICAL_ORDER.length;

/* label/value only - a custom tile's whole chart, one hue for a magnitude
   series (bar/line), the fixed categorical order for a pie's slice identity.

   `compact`: the ~64x22px version chip in the tile builder's transcript
   (`TileBuilder.tsx`) is too small for a label next to every bar to mean
   anything - it stays the plain sparkline, no labels, same as `line`. Every
   other place this renders (the stage, a saved tile's card, the tile as it
   actually sits on a dashboard) uses the labelled version by default: a bar
   chart with no visible label next to each bar is the actual bug behind
   "everyone has the same color so I can't tell who is who" - color was never
   going to fix that, since a magnitude comparison like this should carry
   identity in its labels, not in a hue nothing else here uses that way
   (dataviz skill: color follows the entity's role, not a bar's rank, and a
   sequential/magnitude series is one hue by design). */
export function MiniChart({
  chartType,
  labels,
  values,
  compact = false,
}: {
  chartType: ChartType;
  labels: string[];
  values: number[];
  compact?: boolean;
}) {
  if (chartType === "pie") {
    const overflow = values.length > MAX_PIE_SLICES;
    const shown = overflow ? values.slice(0, MAX_PIE_SLICES - 1) : values;
    const shownLabels = overflow ? labels.slice(0, MAX_PIE_SLICES - 1) : labels;
    const rest = overflow ? values.slice(MAX_PIE_SLICES - 1).reduce((a, b) => a + b, 0) : 0;
    const slices = overflow ? [...shown, rest] : shown;
    const sliceLabels = overflow ? [...shownLabels, "Other"] : shownLabels;
    const total = slices.reduce((a, b) => a + Math.max(0, b), 0) || 1;

    let angle = -Math.PI / 2;
    const r = 30;
    const cx = 32;
    const cy = 32;
    const paths = slices.map((v, i) => {
      const fraction = Math.max(0, v) / total;
      const start = angle;
      angle += fraction * 2 * Math.PI;
      const large = fraction > 0.5 ? 1 : 0;
      const x1 = cx + r * Math.cos(start);
      const y1 = cy + r * Math.sin(start);
      const x2 = cx + r * Math.cos(angle);
      const y2 = cy + r * Math.sin(angle);
      const color = i === slices.length - 1 && overflow ? "var(--rule)" : `var(--color-${CATEGORICAL_ORDER[i % CATEGORICAL_ORDER.length]})`;
      return (
        <path
          key={i}
          d={`M${cx},${cy} L${x1.toFixed(2)},${y1.toFixed(2)} A${r},${r} 0 ${large} 1 ${x2.toFixed(2)},${y2.toFixed(2)} Z`}
          fill={color}
          stroke="var(--surface)"
          strokeWidth={2}
        >
          <title>{`${sliceLabels[i]}: ${v}`}</title>
        </path>
      );
    });

    return (
      <div className="flex items-center gap-3">
        <svg viewBox="0 0 64 64" className="h-[64px] w-[64px] shrink-0">
          {paths}
        </svg>
        <div className="grid min-w-0 flex-1 gap-1">
          {sliceLabels.map((label, i) => (
            <div key={i} className="flex items-center gap-1.5 text-[11px] text-ink-2">
              <span
                className="h-2 w-2 shrink-0 rounded-full"
                style={{
                  background:
                    i === sliceLabels.length - 1 && overflow
                      ? "var(--rule)"
                      : `var(--color-${CATEGORICAL_ORDER[i % CATEGORICAL_ORDER.length]})`,
                }}
              />
              <span className="min-w-0 flex-1 truncate">{label}</span>
              <span className="shrink-0 text-ink-3">{slices[i]}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const w = 260;
  const h = 64;
  const maxY = Math.max(1, ...values.map((v) => Math.abs(v)));

  if (chartType === "line") {
    const path = values
      .map((v, i) => {
        const x = values.length > 1 ? (i / (values.length - 1)) * w : 0;
        const y = h - (v / maxY) * h;
        return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full text-navy" preserveAspectRatio="none">
        <line x1={0} y1={h} x2={w} y2={h} stroke="var(--rule)" strokeWidth={1} />
        <path d={path} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" />
      </svg>
    );
  }

  // bar, compact: the old unlabelled sparkline - only for the transcript's
  // 64x22px version chips, where a label next to each bar could not be read
  // anyway.
  if (compact) {
    const barW = values.length > 0 ? (w / values.length) * 0.6 : 0;
    const gap = values.length > 0 ? w / values.length : 0;
    return (
      <svg viewBox={`0 0 ${w} ${h}`} className="w-full text-navy" preserveAspectRatio="none">
        <line x1={0} y1={h} x2={w} y2={h} stroke="var(--rule)" strokeWidth={1} />
        {values.map((v, i) => {
          const barH = (Math.max(0, v) / maxY) * h;
          return (
            <rect
              key={i}
              x={i * gap + (gap - barW) / 2}
              y={h - barH}
              width={barW}
              height={barH}
              rx={2}
              fill="currentColor"
            >
              <title>{`${labels[i]}: ${v}`}</title>
            </rect>
          );
        })}
      </svg>
    );
  }

  // bar, full: horizontal, one row per label - the label sits right next to
  // its own bar, so telling rows apart never depended on color.
  return (
    <div className="grid gap-1.5">
      {values.map((v, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-[92px] shrink-0 truncate text-[11px] text-ink-2" title={labels[i]}>
            {labels[i]}
          </span>
          <div className="h-[9px] flex-1 overflow-hidden rounded-sm bg-rule-2">
            <div
              className="h-full rounded-sm bg-navy"
              style={{ width: `${Math.min(100, (Math.max(0, v) / maxY) * 100)}%` }}
            />
          </div>
          <span className="w-[46px] shrink-0 text-right text-[11px] text-ink-3">{v}</span>
        </div>
      ))}
    </div>
  );
}

const BAND_STYLE: Record<string, string> = {
  critical: "bg-red text-surface",
  watch: "bg-amber text-surface",
  healthy: "bg-green text-surface",
  no_data: "bg-rule text-ink-3",
};

/* The matrix cell wash, same five steps as `pages/Risk.tsx` - a rating is a
   lookup (`app/risks/matrix.py`), so the tile and the full page must not
   shade the same cell differently. */
const CELL_STYLE: Record<string, string> = {
  "Very Low": "bg-green/10",
  Low: "bg-green/20",
  Medium: "bg-amber/20",
  High: "bg-orange/20",
  "Very High": "bg-red/20",
};

const RATING_STYLE: Record<string, string> = {
  "Very Low": "text-green",
  Low: "text-green",
  Medium: "text-amber",
  High: "text-orange",
  "Very High": "text-red",
};

/* One request per endpoint per render, not one per tile.

   Every tile fetches its own bundle, which is what keeps a tile a standalone
   adapter - but a dashboard is many tiles over *few* endpoints: the default
   project board has four tiles reading `/api/gantt` and three reading
   `/api/insight`, and each of those recomputes a dependency graph server-side.
   Uncached, opening it fired thirteen requests for six distinct bundles.

   Keyed by path and deliberately short-lived. The window only has to be long
   enough to cover one canvas mounting its tiles; anything longer would start
   serving a stale bundle to somebody who navigated away and came back, and
   these tiles have no other refresh. A rejection is never cached at all - a
   failed load must be retried on the next mount, not remembered as the
   answer. */
const BUNDLE_TTL_MS = 5_000;
const inflight = new Map<string, { at: number; promise: Promise<unknown> }>();

function loadShared<T>(path: string): Promise<T> {
  const hit = inflight.get(path);
  if (hit && Date.now() - hit.at < BUNDLE_TTL_MS) return hit.promise as Promise<T>;

  const promise = load<T>(path);
  const entry = { at: Date.now(), promise: promise as Promise<unknown> };
  inflight.set(path, entry);
  promise.catch(() => {
    if (inflight.get(path) === entry) inflight.delete(path);
  });
  return promise;
}

function useBundle<T>(path: string | null): { bundle: T | null; problem: ApiProblem | null } {
  const [bundle, setBundle] = useState<T | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    setBundle(null);
    setProblem(null);
    if (!path) return;
    //: Guarded against a path change mid-flight: two tiles differ only by the
    //: project in their query string, and without this a switch away and back
    //: can land the first response after the second.
    let live = true;
    loadShared<T>(path).then(
      (value) => live && setBundle(value),
      (error) => live && setProblem(error as ApiProblem),
    );
    return () => {
      live = false;
    };
  }, [path]);

  return { bundle, problem };
}

function TileShell({
  loading,
  problem,
  children,
}: {
  loading: boolean;
  problem: ApiProblem | null;
  children: React.ReactNode;
}) {
  if (problem) return <Problem {...problem} />;
  if (loading) return <p className="m-0 text-[12.5px] text-ink-3">Loading...</p>;
  return <>{children}</>;
}

/* ---- Program scope ---------------------------------------------------- */

function useRollup(scopeId: string) {
  return useBundle<ProgramRollupBundle>(`/api/programs/${encodeURIComponent(scopeId)}`);
}

const ProgramHealth: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <>
          <span
            className={`inline-block rounded px-2 py-0.5 text-[11px] font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[bundle.program.band]}`}
          >
            {bundle.program.band.replace("_", " ")}
          </span>
          <p className="mt-2 mb-0 text-[12.5px] text-ink-2">
            {bundle.program.project_count} project(s) in this program.
          </p>
        </>
      )}
    </TileShell>
  );
};

const ProjectPortfolio: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <div className="grid gap-1.5">
          {bundle.projects.map((row) => (
            <a
              key={row.project_id}
              href={projectLink("/project/dashboard", row)}
              className="flex items-center gap-2 rounded px-1 py-0.5 text-[12.5px] no-underline hover:bg-bg"
              title={`Open ${row.name}'s dashboard`}
            >
              <span className={`h-2 w-2 shrink-0 rounded-full ${(BAND_STYLE[row.band] ?? "bg-rule").split(" ")[0]}`} />
              <span className="min-w-0 flex-1 truncate text-ink">{row.name}</span>
              <span className="text-ink-3">{row.days_late > 0 ? `+${row.days_late}d` : "on plan"}</span>
            </a>
          ))}
          {bundle.projects.length === 0 && (
            <p className="m-0 text-[12.5px] text-ink-3">No projects yet.</p>
          )}
        </div>
      )}
    </TileShell>
  );
};

/* All five, in `app/api/schemas/portfolio.py`'s own order.

   This listed four, and the missing one was `resource` - the dimension banded
   from cross-project contention, and the only one whose cause lives outside
   the project it marks. Dropping it made this tile actively misleading rather
   than merely incomplete: a project that is fine on its own and starved by a
   sibling reads amber there and nowhere else, so on the *program* board - the
   one screen that exists to show what projects cost each other - it read
   all-green. `pages/Portfolio.tsx` already had all five; this copy had not
   been updated with it. */
const DIMENSIONS = ["schedule", "quality", "qa", "evidence", "resource"] as const;

const ProjectHealthHeatmap: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <div className="grid gap-1.5">
          {bundle.projects.map((row) => (
            <a
              key={row.project_id}
              href={projectLink("/project/dashboard", row)}
              title={`Open ${row.name}'s dashboard`}
              className="grid grid-cols-[1fr_repeat(5,20px)] items-center gap-1.5 rounded px-1 py-0.5 no-underline hover:bg-bg"
            >
              <span className="truncate text-[12px] text-ink">{row.name}</span>
              {DIMENSIONS.map((d) => (
                <span
                  key={d}
                  title={`${d}: ${row.bands[d] ?? "no_data"}`}
                  className={`h-3.5 rounded-sm ${(BAND_STYLE[row.bands[d] ?? "no_data"] ?? "bg-rule").split(" ")[0]}`}
                />
              ))}
            </a>
          ))}
          {/* Five unlabelled squares need naming once - there is no room for a
              header row at 20px per column, and the full labelled grid is on
              the Portfolio page. */}
          <p className="m-0 border-t border-rule pt-1.5 text-[10.5px] text-ink-3">
            Left to right: {DIMENSIONS.join(", ")}. Resource is banded from
            cross-project contention, so it can be the only one that is not green.
          </p>
        </div>
      )}
    </TileShell>
  );
};

const ResourceConflictTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <div className="grid gap-2">
          {bundle.resource_conflicts.length === 0 && (
            <p className="m-0 text-[12.5px] text-ink-3">
              No one is committed beyond their capacity in any month.
            </p>
          )}
          {/*
            Leads with the excess in effort-days, not with the allocation
            percentage. The percentage is the number a PM recognises from their
            own resource plan, so it stays - but it is a label now: the test is
            windowed demand against discounted supply, and "130%" on its own says
            nothing about whether the two claims overlap in time.

            Keyed by name *and* window because contention is assessed per month,
            so one person legitimately appears once per contended month.
          */}
          {bundle.resource_conflicts.map((c) => (
            <div key={`${c.resource_name}:${c.window_label}`} className="text-[12.5px]">
              <b className="font-semibold text-orange">{c.excess_days} effort-days</b>{" "}
              <span className="text-ink">short · {c.resource_name}</span>
              <div className="text-[11px] text-ink-3">
                {c.projects.join(" + ")} · {c.window_label}
              </div>
              <div className="text-[11px] text-ink-3">
                {c.total_allocation_percent}% allocated · demand {c.demand_days} vs supply{" "}
                {c.supply_days} effort-days
              </div>
              {/* A delay figure never renders without its absorption assumption. */}
              {c.absorption && (
                <div
                  className={`text-[11px] ${
                    c.breaches_overtime_limit ? "text-orange" : "text-ink-3"
                  }`}
                >
                  {c.absorption}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </TileShell>
  );
};

const CrossProjectRisk: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle: rollup, problem: rollupProblem } = useRollup(scopeId);
  const projectIds = rollup?.projects.map((p) => p.project_id) ?? [];
  const query = projectIds.map((id) => `project=${encodeURIComponent(id)}`).join("&");
  const { bundle: risks, problem: riskProblem } = useBundle<RiskBundle>(
    rollup ? `/api/risks?${query}` : null,
  );
  const problem = rollupProblem ?? riskProblem;

  return (
    <TileShell loading={!risks && !problem} problem={problem}>
      {risks && (
        <div className="grid gap-1.5">
          {risks.risks.slice(0, 6).map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-[12.5px]">
              <span className={`font-semibold ${RATING_STYLE[r.pre_rating ?? ""] ?? "text-ink-3"}`}>
                {r.pre_rating ?? "n/a"}
              </span>
              <span className="min-w-0 flex-1 truncate">{r.title}</span>
              <span className="shrink-0 text-[11px] text-ink-3">{r.project_id.split(":").pop()}</span>
            </div>
          ))}
          {risks.risks.length === 0 && (
            <p className="m-0 text-[12.5px] text-ink-3">No risks logged across this program yet.</p>
          )}
        </div>
      )}
    </TileShell>
  );
};

function useWorstProjectInsight(scopeId: string) {
  const { bundle: rollup, problem: rollupProblem } = useRollup(scopeId);
  const rank: Record<string, number> = { critical: 0, watch: 1, healthy: 2, no_data: 3 };
  const worst = rollup?.projects
    .slice()
    .sort((a, b) => (rank[a.band] ?? 9) - (rank[b.band] ?? 9))[0];
  const { bundle: insight, problem: insightProblem } = useBundle<InsightBundle>(
    worst ? `/api/insight?project=${encodeURIComponent(worst.project_id)}` : null,
  );
  return { worstName: worst?.name, insight, problem: rollupProblem ?? insightProblem };
}

const AiCrossProjectBrief: ComponentType<TileProps> = ({ scopeId }) => {
  const { worstName, insight, problem } = useWorstProjectInsight(scopeId);
  return (
    <TileShell loading={!insight && !problem} problem={problem}>
      {insight && (
        <>
          <p className="mt-0 mb-1.5 text-[11px] text-ink-3">for {worstName}</p>
          <p className="m-0 whitespace-pre-line text-[12.5px] text-ink-2">
            {insight.narrative || "No narrative available."}
          </p>
        </>
      )}
    </TileShell>
  );
};

const AiDetectedRisksTop: ComponentType<TileProps> = ({ scopeId }) => {
  const { worstName, insight, problem } = useWorstProjectInsight(scopeId);
  return (
    <TileShell loading={!insight && !problem} problem={problem}>
      {insight && (
        <>
          <p className="mt-0 mb-1.5 text-[11px] text-ink-3">for {worstName}</p>
          <div className="grid gap-1.5">
            {insight.findings.slice(0, 5).map((f) => (
              <div key={f.id} className="text-[12.5px]">
                <span className="mr-1.5 font-semibold uppercase text-ink-3">{f.severity}</span>
                {f.headline}
              </div>
            ))}
            {insight.findings.length === 0 && (
              <p className="m-0 text-[12.5px] text-ink-3">No findings.</p>
            )}
          </div>
        </>
      )}
    </TileShell>
  );
};

/* Everything not healthy, with the sentence the rollup already wrote for it.

   `headline` is a finding's own substituted prose (`app/api/schemas/insight.py`
   - numbers live in `facts`, and the server substitutes them), so this tile
   states a reason without composing one. `no_data` is counted separately rather
   than listed as a problem: a project nobody has synced is unknown, not late,
   and the whole product argues against colouring unknown. */
const ProjectsNeedingAttention: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  const attention = (bundle?.projects ?? []).filter(
    (p) => p.band === "critical" || p.band === "watch",
  );
  const unknown = (bundle?.projects ?? []).filter((p) => p.band === "no_data");

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {attention.map((row) => (
          <a
            key={row.project_id}
            href={projectLink("/project/dashboard", row)}
            title={`Open ${row.name}'s dashboard`}
            className="flex items-start gap-2 rounded px-1 py-0.5 text-[12.5px] no-underline hover:bg-bg"
          >
            <span
              className={`mt-[3px] h-2 w-2 shrink-0 rounded-full ${(BAND_STYLE[row.band] ?? "bg-rule").split(" ")[0]}`}
            />
            <span className="min-w-0 flex-1">
              <span className="font-semibold text-ink">{row.name}</span>
              {row.headline && <span className="text-ink-2"> · {row.headline}</span>}
            </span>
            {row.days_late > 0 && (
              <span className="shrink-0 text-[11px] text-orange">+{row.days_late}d</span>
            )}
          </a>
        ))}
        {attention.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No project in this program is critical or on watch.
          </p>
        )}
        {unknown.length > 0 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            {unknown.length} project(s) have nothing ingested yet, so they are
            unranked rather than healthy: {unknown.map((p) => p.name).join(", ")}.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* Ranked by `days_late`, which is slip the dependency chain implies and the
   sheet does not show - the one number on the portfolio screen, because it is
   a subtraction over two dates the server already holds. */
const TopDelayedProjects: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  const late = (bundle?.projects ?? [])
    .filter((p) => p.days_late > 0)
    .sort((a, b) => b.days_late - a.days_late);
  const worst = Math.max(1, ...late.map((p) => p.days_late));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-2">
        {late.map((row) => (
          <div key={row.project_id} className="flex items-center gap-2">
            <span className="w-[84px] shrink-0 truncate text-[11.5px] text-ink-2" title={row.name}>
              {row.name}
            </span>
            <div className="h-[10px] flex-1 overflow-hidden rounded-sm bg-rule-2">
              <div
                className="h-full rounded-sm bg-orange"
                style={{ width: `${Math.max(4, (row.days_late / worst) * 100)}%` }}
              />
            </div>
            <span className="w-[44px] shrink-0 text-right text-[11px] text-ink-3">
              +{row.days_late}d
            </span>
          </div>
        ))}
        {late.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No project's dependencies imply a slip past its plan.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* The apportionment, turned around: `resource_conflict` asks "who is short",
   this asks "which project pays for it".

   Only `effort_days` is added up here, and that is the whole point of the tile.
   It is the conserved quantity - the contention model asserts that a person's
   shares sum to their excess rather than merely commenting it
   (`app/intelligence/contention.py`) - so a project's total across several
   people and months is a real figure a PM can act on. `delay_days` is
   deliberately NOT summed (`ProjectShortfall` says why: it is a scenario that
   holds only if the shortfall lands in a later window with room, and totalling
   it re-creates exactly the replication error the apportionment removes), so it
   is shown per row and never accumulated. */
const ResourceContentionSplit: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  const conflicts = bundle?.resource_conflicts ?? [];

  /* One row per project, not one per (person, window) pair. Contention is
     assessed per month - because the overtime ceiling it is checked against is
     monthly - so one person contended all quarter is three results, and listing
     each of them made a three-project program an eighteen-line tile. The people
     and the month count are what a reader needs to go look; the per-month
     arithmetic is on `resource_conflict`, which is the per-person view. */
  const byProject = new Map<
    string,
    { name: string; effortDays: number; people: Set<string>; windows: number }
  >();
  for (const conflict of conflicts) {
    for (const shortfall of conflict.shortfalls) {
      const entry = byProject.get(shortfall.project_id) ?? {
        name: shortfall.project_name,
        effortDays: 0,
        people: new Set<string>(),
        windows: 0,
      };
      entry.effortDays += shortfall.effort_days;
      entry.people.add(conflict.resource_name);
      entry.windows += 1;
      byProject.set(shortfall.project_id, entry);
    }
  }
  const rows = Array.from(byProject.values()).sort((a, b) => b.effortDays - a.effortDays);
  const excess = conflicts.reduce((total, c) => total + c.excess_days, 0);
  const worst = Math.max(1, ...rows.map((r) => r.effortDays));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-2">
        {rows.map((row) => (
          <div key={row.name} className="text-[12.5px]">
            <div className="flex items-baseline gap-2">
              <b className="shrink-0 font-semibold text-orange">{row.effortDays.toFixed(2)}d</b>
              <span className="min-w-0 flex-1 truncate text-ink">{row.name}</span>
            </div>
            <div className="mt-0.5 h-[6px] overflow-hidden rounded-sm bg-rule-2">
              <div
                className="h-full rounded-sm bg-orange"
                style={{ width: `${Math.max(4, (row.effortDays / worst) * 100)}%` }}
              />
            </div>
            <div className="mt-0.5 text-[11px] text-ink-3">
              {Array.from(row.people).join(", ")} · {row.windows} contended month
              {row.windows === 1 ? "" : "s"}
            </div>
          </div>
        ))}
        {rows.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            Nobody shared between these projects is committed beyond their
            capacity in any month.
          </p>
        )}
        {rows.length > 0 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            {excess.toFixed(2)} effort-days of excess demand, apportioned in full
            - this column adds up to it. A deferral figure is per row only.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* Who is on more than one project in this program, and at what stated
   percentage on each.

   Shown as the plan states it, and labelled as nominal on purpose: two 60%
   allocations in non-overlapping quarters read as 120% here and are not a
   conflict, which is exactly why the contention test is windowed demand
   against discounted supply and not this sum. The tile that answers "is it a
   problem" is `resource_conflict`; this one answers "who is spread, and
   where". */
const TeamAllocation: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);

  const byPerson = new Map<
    string,
    { role: string | null; rows: { project: string; percent: number | null }[] }
  >();
  for (const row of bundle?.resources ?? []) {
    const entry = byPerson.get(row.resource_name) ?? { role: row.role ?? null, rows: [] };
    entry.rows.push({ project: row.project_name, percent: row.allocation_percent ?? null });
    byPerson.set(row.resource_name, entry);
  }
  //: Shared people first - the reason this tile is at program level at all.
  const people = Array.from(byPerson.entries()).sort(
    (a, b) => b[1].rows.length - a[1].rows.length || a[0].localeCompare(b[0]),
  );

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-2">
        {people.map(([name, entry]) => {
          const nominal = entry.rows.reduce((sum, r) => sum + (r.percent ?? 0), 0);
          return (
            <div key={name} className="text-[12.5px]">
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate font-semibold text-ink">
                  {name}
                  {entry.role && <span className="font-normal text-ink-3"> · {entry.role}</span>}
                </span>
                <span
                  className={`shrink-0 text-[11px] ${entry.rows.length > 1 ? "text-ink-2" : "text-ink-3"}`}
                >
                  {nominal}% over {entry.rows.length} project{entry.rows.length === 1 ? "" : "s"}
                </span>
              </div>
              <div className="text-[11px] text-ink-3">
                {entry.rows
                  .map((r) => `${r.project}${r.percent == null ? "" : ` ${r.percent}%`}`)
                  .join(" · ")}
              </div>
            </div>
          );
        })}
        {people.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No resource allocations recorded for this program's projects.
          </p>
        )}
        {people.length > 0 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            Stated percentages, summed nominally - not the contention test. Two
            allocations that never overlap add up here and are not a conflict.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* ---- Project scope ------------------------------------------------------ */

function useInsight(scopeId: string) {
  return useBundle<InsightBundle>(`/api/insight?project=${encodeURIComponent(scopeId)}`);
}

const MilestonesAtRisk: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  const value = bundle ? Number(bundle.context.milestones_at_risk ?? 0) : null;
  return (
    <TileShell loading={value === null && !problem} problem={problem}>
      <b className={`block text-[26px] leading-none font-bold ${value && value > 0 ? "text-red" : ""}`}>
        {value}
      </b>
      <span className="mt-1 block text-[11px] text-ink-3">milestone(s)</span>
    </TileShell>
  );
};

const BlockingQa: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  const blocked = bundle ? Number(bundle.context.qa_blocked ?? 0) : null;
  const total = bundle ? Number(bundle.context.qa_count ?? 0) : null;
  return (
    <TileShell loading={blocked === null && !problem} problem={problem}>
      <b className={`block text-[26px] leading-none font-bold ${blocked && blocked > 0 ? "text-red" : ""}`}>
        {blocked} / {total}
      </b>
      <span className="mt-1 block text-[11px] text-ink-3">QA items blocked</span>
    </TileShell>
  );
};

const QualityHealth: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  const findings = bundle?.findings.filter((f) => f.category === "quality") ?? [];
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {findings.slice(0, 4).map((f) => (
          <div key={f.id} className="text-[12.5px]">
            <span className="mr-1.5 font-semibold uppercase text-ink-3">{f.severity}</span>
            {f.headline}
          </div>
        ))}
        {findings.length === 0 && <p className="m-0 text-[12.5px] text-ink-3">No quality findings.</p>}
      </div>
    </TileShell>
  );
};

/* The actual 5x5 heat-map, which this tile claimed to be and was not.

   It used to render a ranked *list* of risks - the catalogue described it as
   "the 5x5 likelihood x impact heat-map", its picker swatch was the heat-map
   skeleton, and a person who added it got a list. That list is now its own
   tile (`risk_register`), which is what it always was, and this draws the grid.

   Cells come from the server (`build_matrix`), which is the same rating lookup
   a risk's own badge uses, so a cell here cannot disagree with a badge in the
   register beside it. Counts are pre-treatment, the register's own convention.
   No axis labels at tile size: five impact names do not fit across a 6-column
   tile, so the axes are named once in the footnote and each cell carries its
   own `title` - the full labelled grid is on the Risk page. */
const RiskMatrixTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<RiskBundle>(
    `/api/risks?project=${encodeURIComponent(scopeId)}`,
  );
  const cellAt = (likelihood: string, impact: string) =>
    bundle?.matrix.find((c) => c.likelihood === likelihood && c.impact === impact);
  const assessed = (bundle?.matrix ?? []).reduce((total, c) => total + c.risk_count, 0);

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <>
          <div
            className="grid gap-[3px]"
            style={{ gridTemplateColumns: `repeat(${bundle.impacts.length}, minmax(0, 1fr))` }}
          >
            {bundle.likelihoods.map((likelihood) =>
              bundle.impacts.map((impact) => {
                const cell = cellAt(likelihood, impact);
                return (
                  <div
                    key={`${likelihood}:${impact}`}
                    title={`${likelihood} x ${impact} = ${cell?.rating ?? "unrated"}${
                      cell?.risk_count ? ` · ${cell.risk_count} risk(s)` : ""
                    }`}
                    className={`flex h-[26px] items-center justify-center rounded-sm text-[11px] font-bold text-ink ${
                      CELL_STYLE[cell?.rating ?? ""] ?? "bg-rule/20"
                    }`}
                  >
                    {cell?.risk_count ? cell.risk_count : ""}
                  </div>
                );
              }),
            )}
          </div>
          <p className="mt-2 mb-0 text-[10.5px] text-ink-3">
            Rows: likelihood, {bundle.likelihoods[0]} to{" "}
            {bundle.likelihoods[bundle.likelihoods.length - 1]}. Columns: impact,{" "}
            {bundle.impacts[0]} to {bundle.impacts[bundle.impacts.length - 1]}.
          </p>
          <p className="m-0 text-[10.5px] text-ink-3">
            {assessed} risk(s) placed, pre-treatment.
            {assessed < bundle.risks.length &&
              ` ${bundle.risks.length - assessed} not assessed, so unplaced.`}
          </p>
        </>
      )}
    </TileShell>
  );
};

const AiManagementBrief: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <p className="m-0 whitespace-pre-line text-[12.5px] text-ink-2">
        {bundle?.narrative || "No narrative available."}
      </p>
    </TileShell>
  );
};

const AiDetectedRisks: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {bundle?.findings.slice(0, 6).map((f) => (
          <div key={f.id} className="text-[12.5px]">
            <span className="mr-1.5 font-semibold uppercase text-ink-3">{f.severity}</span>
            {f.headline}
          </div>
        ))}
        {bundle && bundle.findings.length === 0 && (
          <p className="m-0 text-[12.5px] text-ink-3">No findings.</p>
        )}
      </div>
    </TileShell>
  );
};

const AiRootCauseImpact: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  const top = bundle?.findings[0];
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {top ? (
        <>
          <p className="m-0 text-[12.5px] text-ink-2">{top.headline}</p>
          {top.recommendation && (
            <p className="mt-2 mb-0 text-[12.5px] text-ink">
              <span className="font-semibold">Recommended: </span>
              {top.recommendation}
            </p>
          )}
        </>
      ) : (
        <p className="m-0 text-[12.5px] text-ink-3">No finding to explain.</p>
      )}
    </TileShell>
  );
};

/* ---- Graph tiles --------------------------------------------------------
   The three real charts: a reused Gantt, and two small hand-rolled SVGs
   (dataviz skill: thin marks, one hue per magnitude series, direct labels
   instead of a legend where there's only one series, native <title> as the
   hover layer - a custom tooltip is more chrome than a canvas tile earns). */

const ScheduleGanttTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<GanttBundle>(
    `/api/gantt?project=${encodeURIComponent(scopeId)}`,
  );
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && bundle.rows.length > 0 ? (
        <GanttChart bundle={bundle} showKey={false} />
      ) : (
        <p className="m-0 text-[12.5px] text-ink-3">No schedule rows yet.</p>
      )}
    </TileShell>
  );
};

/* Bar length = days late (0 = on time); one hue, because color-by-rank on a
   single measure is exactly the anti-pattern the skill calls out - position
   and length already carry the magnitude. */
const DeliveryForecastTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<ForecastBundle>(
    `/api/forecast?project=${encodeURIComponent(scopeId)}`,
  );
  if (bundle && !bundle.available) {
    return (
      <TileShell loading={false} problem={null}>
        <p className="m-0 text-[12.5px] text-ink-3">{bundle.reason || "No forecast available."}</p>
      </TileShell>
    );
  }
  const points = bundle?.points ?? [];
  const maxLate = Math.max(1, ...points.map((p) => p.days_late));
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-2">
        {points.map((p) => (
          <div key={p.percentile} className="flex items-center gap-2" title={`P${p.percentile}: ${p.finish}`}>
            <span className="w-[28px] shrink-0 text-[11px] text-ink-3">P{p.percentile}</span>
            <div className="h-[10px] flex-1 overflow-hidden rounded-sm bg-rule-2">
              <div
                className="h-full rounded-sm bg-navy"
                style={{ width: `${Math.max(4, (p.days_late / maxLate) * 100)}%` }}
              />
            </div>
            <span className="w-[70px] shrink-0 text-right text-[11px] text-ink-2">
              {p.days_late > 0 ? `+${p.days_late}d` : "on time"}
            </span>
          </div>
        ))}
        {points.length === 0 && <p className="m-0 text-[12.5px] text-ink-3">No forecast points.</p>}
      </div>
      {bundle?.committed_end && (
        <p className="mt-2.5 mb-0 text-[11px] text-ink-3">committed {bundle.committed_end}</p>
      )}
    </TileShell>
  );
};

/* Cumulative hours logged over time - one series, because `planned_hours` is
   a single total in this bundle, not a time series; drawing a second line
   for it would be a straight line standing in for data nobody observed. */
const EffortBurnTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<TeamBundle>(
    `/api/team?project=${encodeURIComponent(scopeId)}`,
  );
  const points = bundle?.burn.points ?? [];
  const w = 260;
  const h = 64;
  const maxY = Math.max(1, ...points.map((p) => p.logged_hours));
  const path = points
    .map((p, i) => {
      const x = points.length > 1 ? (i / (points.length - 1)) * w : 0;
      const y = h - (p.logged_hours / maxY) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const first = points.length > 1 ? points[0] : undefined;
  const last = points.length > 1 ? points[points.length - 1] : undefined;

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {first && last ? (
        <>
          <svg viewBox={`0 0 ${w} ${h}`} className="w-full text-navy" preserveAspectRatio="none">
            <title>Cumulative hours logged over time</title>
            <line x1={0} y1={h} x2={w} y2={h} stroke="var(--rule)" strokeWidth={1} />
            <path d={path} fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" />
          </svg>
          <div className="mt-1.5 flex items-baseline justify-between text-[11px] text-ink-3">
            <span>{first.observed_at}</span>
            <span className="font-semibold text-ink">
              {last.logged_hours}h logged
            </span>
            <span>{last.observed_at}</span>
          </div>
        </>
      ) : (
        <p className="m-0 text-[12.5px] text-ink-3">Not enough logged history yet.</p>
      )}
      {bundle && (
        <div className="mt-2.5 grid grid-cols-2 gap-2 border-t border-rule pt-2 text-[11px] text-ink-3">
          <span>planned {bundle.burn.planned_hours}h</span>
          <span className="text-right">remaining {bundle.burn.remaining_hours}h</span>
        </div>
      )}
    </TileShell>
  );
};

/* Real logged hours per person, compared against what was planned for them -
   the answer to "track each employee's productivity" that does not require
   anyone to paste a number: `/api/team` already computes `hours_logged` /
   `hours_planned` per member from the worklog. One hue for the bar
   (magnitude, `hours_logged`), a dashed reference tick at `hours_planned` -
   not a second bar, which would read as a second series needing its own
   legend for what is really one comparison per person. */
const TeamEffortTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<TeamBundle>(
    `/api/team?project=${encodeURIComponent(scopeId)}`,
  );
  const members = (bundle?.members ?? [])
    .slice()
    .sort((a, b) => b.hours_logged - a.hours_logged)
    .slice(0, 8);
  const maxHours = Math.max(1, ...members.map((m) => Math.max(m.hours_logged, m.hours_planned)));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {members.length > 0 ? (
        <div className="grid gap-2">
          {members.map((m) => (
            <div key={m.name} className="flex items-center gap-2">
              <span className="w-[84px] shrink-0 truncate text-[11.5px] text-ink-2" title={m.name}>
                {m.name}
              </span>
              <div className="relative h-[10px] flex-1 overflow-hidden rounded-sm bg-rule-2">
                <div
                  className="h-full rounded-sm bg-navy"
                  style={{ width: `${Math.min(100, (m.hours_logged / maxHours) * 100)}%` }}
                />
                {m.hours_planned > 0 && (
                  <div
                    className="absolute top-0 h-full w-px bg-ink-3"
                    style={{ left: `${Math.min(100, (m.hours_planned / maxHours) * 100)}%` }}
                    title={`planned ${m.hours_planned}h`}
                  />
                )}
              </div>
              <span className="w-[70px] shrink-0 text-right text-[11px] text-ink-3">
                {m.hours_logged}h / {m.hours_planned}h
              </span>
            </div>
          ))}
          <p className="m-0 text-[10.5px] text-ink-3">bar = logged, tick = planned</p>
        </div>
      ) : (
        <p className="m-0 text-[12.5px] text-ink-3">No logged hours yet.</p>
      )}
    </TileShell>
  );
};

/* Every project's finish on one shared window.

   Deliberately NOT a merged Gantt. Stacking several projects' task bars into
   one chart would imply a schedule they do not share: `driving_path` and
   `project_end_projected` are per-project forward-pass results, and there is no
   single critical chain across projects that only compete for people. Drawing
   one would invent a dependency structure nobody stated - the same class of
   claim `app/scope.py` refuses at the identity level.

   What a program level can honestly say about schedule is *when each project
   lands, side by side*: the committed date as a tick, and the overrun the
   chain implies as a bar past it. Both come straight off the rollup rows the
   Programs list already computes, so this cannot disagree with the heatmap or
   Top Delayed beside it. The full task-level chart stays one click away, on
   each project's own Schedule page. */
const ProgramTimeline: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRollup(scopeId);
  const rows = (bundle?.projects ?? []).filter((p) => p.committed_end);

  const day = (iso: string | null | undefined) =>
    iso ? new Date(`${iso}T00:00:00Z`).getTime() : null;

  const starts = rows.map((r) => day(r.committed_end)!).filter(Boolean);
  const ends = rows.map((r) => day(r.projected_end) ?? day(r.committed_end)!).filter(Boolean);
  const min = Math.min(...starts, ...ends);
  const max = Math.max(...starts, ...ends);
  //: A single-project program, or every project landing the same day, gives a
  //: zero-width window - which would divide by zero and put every tick at the
  //: same pixel. Pad it to a day so the row still draws.
  const span = Math.max(max - min, 86_400_000);
  const at = (ms: number) => ((ms - min) / span) * 100;

  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {rows.length > 0 ? (
        <div className="grid gap-2">
          <div className="flex justify-between text-[10.5px] text-ink-3">
            <span>{iso(min)}</span>
            <span>{iso(max)}</span>
          </div>
          {rows.map((row) => {
            const committed = day(row.committed_end)!;
            const projected = day(row.projected_end) ?? committed;
            const late = row.days_late > 0;
            return (
              <a
                key={row.project_id}
                href={projectLink("/gantt", row)}
                title={`${row.name}: committed ${row.committed_end}${
                  late ? `, chain implies ${row.projected_end}` : ""
                } - open its schedule`}
                className="flex items-center gap-2 rounded px-1 py-0.5 no-underline hover:bg-bg"
              >
                <span className="w-[84px] shrink-0 truncate text-[11.5px] text-ink-2">
                  {row.name}
                </span>
                <span className="relative h-[14px] flex-1 rounded-sm bg-rule-2">
                  {/* The overrun, drawn only when there is one - a zero-width
                      bar at every project reads as a rendering fault. */}
                  {late && (
                    <span
                      className="absolute top-[3px] h-[8px] rounded-sm bg-red"
                      style={{
                        left: `${at(committed)}%`,
                        width: `${Math.max(1, at(projected) - at(committed))}%`,
                      }}
                    />
                  )}
                  {/* Committed finish. A tick, not a bar: we know the date it
                      was promised for, not when the work started. */}
                  <span
                    className="absolute top-0 h-full w-[2px] bg-ink-2"
                    style={{ left: `${at(committed)}%` }}
                  />
                </span>
                <span
                  className={`w-[46px] shrink-0 text-right text-[11px] ${
                    late ? "text-red" : "text-ink-3"
                  }`}
                >
                  {late ? `+${row.days_late}d` : "on plan"}
                </span>
              </a>
            );
          })}
          <p className="m-0 border-t border-rule pt-1.5 text-[10.5px] text-ink-3">
            Tick = committed finish, bar = overrun the chain implies. Each
            project's own critical chain is on its Schedule page - there is no
            shared one to draw here.
          </p>
        </div>
      ) : (
        bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No project in this program has a committed finish date yet.
          </p>
        )
      )}
    </TileShell>
  );
};

/* ---- Project scope, added from the PM's own tiles list ------------------ */

function useGantt(scopeId: string) {
  return useBundle<GanttBundle>(`/api/gantt?project=${encodeURIComponent(scopeId)}`);
}

function useRisks(scopeId: string) {
  return useBundle<RiskBundle>(`/api/risks?project=${encodeURIComponent(scopeId)}`);
}

/* This project's own row out of the portfolio, rather than a second summary
   computed here. `/api/portfolio` folds every source id of one delivery
   project into one row (invariant 7), so reading the row is also the only way
   to get counts that agree with the Programs list and the heat-map. */
function usePortfolioRow(scopeId: string) {
  const { bundle, problem } = useBundle<PortfolioBundle>("/api/portfolio");
  const row = bundle?.projects.find(
    (p) => p.project_id === scopeId || p.source_ids.includes(scopeId),
  );
  return { bundle, row, problem };
}

/* Band, what set it, the two finish dates, and the counts - the context a
   person needs on opening a project, none of it recomputed here. */
const ProjectSummary: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, row, problem } = usePortfolioRow(scopeId);
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {row ? (
        <>
          <div className="flex items-baseline justify-between gap-2">
            <span className="min-w-0 truncate text-[13px] font-semibold text-ink">{row.name}</span>
            <span
              className={`shrink-0 rounded px-2 py-0.5 text-[10.5px] font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[row.band]}`}
            >
              {row.band.replace("_", " ")}
            </span>
          </div>
          {row.worst_severity && (
            <p className="mt-1 mb-0 text-[11px] text-ink-3">
              set by a {row.worst_severity} finding, of {row.findings}
            </p>
          )}
          <div className="mt-2 grid gap-1 border-t border-rule pt-2 text-[11.5px] text-ink-2">
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">committed</span>
              <span>{row.committed_end ?? "-"}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">projected</span>
              <span className={row.days_late > 0 ? "font-semibold text-orange" : ""}>
                {row.projected_end ?? "-"}
                {row.days_late > 0 ? ` (+${row.days_late}d)` : ""}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">tasks / QA blocked</span>
              <span>
                {row.task_count} / {row.qa_blocked} of {row.qa_count}
              </span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">milestones at risk</span>
              <span className={row.milestones_at_risk > 0 ? "font-semibold text-red" : ""}>
                {row.milestones_at_risk}
              </span>
            </div>
          </div>
          {/* Said here rather than left for someone to discover: a projected
              date resting on edges we inferred is a weaker claim than one
              resting on stated ones. */}
          {row.depends_on_inferred_edges && (
            <p className="mt-2 mb-0 text-[11px] text-amber">
              The projected date rests partly on inferred dependency edges.
            </p>
          )}
        </>
      ) : (
        bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            This project is not in the portfolio - nothing has been ingested for
            it yet.
          </p>
        )
      )}
    </TileShell>
  );
};

/* The relationship, read from below.

   A project cannot answer any of this from its own data, and that is the point:
   membership is *declared* in `app/scope.py` rather than derived from whichever
   collector wrote the row (which is how one program came to exist under two
   ids), and contention is apportioned at program level because only that level
   can see a person committed to two projects at once.

   Three things it therefore says out loud:

   * **Which program, or none.** `program_id = NULL` is a legitimate state - a
     project registered by upload before anyone chose its program - and is never
     filled in with an invented default. So "no program" is rendered as an
     answer, not as a blank.
   * **Which source systems are this one project.** `also` is the pairing that
     makes two rows one delivery project (invariant 7).
   * **What the siblings are taking from it.** This project's own share of every
     shared person's excess demand - the same apportionment the program board
     shows, filtered to here. */
const ProgramContext: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle: config, problem: configProblem } = useBundle<ProgramBundle>("/api/program");
  const entry = config?.scope.find(
    (e) => e.canonical_id === scopeId || e.also.includes(scopeId),
  );
  const programId = entry?.program_id || "";
  const siblings = (config?.scope ?? []).filter(
    (e) => programId && e.program_id === programId && e.canonical_id !== entry?.canonical_id,
  );

  const { bundle: rollup } = useBundle<ProgramRollupBundle>(
    programId ? `/api/programs/${encodeURIComponent(programId)}` : null,
  );
  //: Every id this project may have been filed under, because a shortfall is
  //: keyed by the source id the `Resource` row carried - the same widening
  //: `scope.source_ids_for` does server-side.
  const ownIds = new Set(entry ? [entry.canonical_id, ...entry.also] : [scopeId]);
  //: Rolled up per person, not per (person, month). Contention is assessed
  //: monthly - the overtime ceiling it is checked against is monthly - so one
  //: person contended all quarter is three results, and listing each of them
  //: filled this tile with arithmetic instead of the point it is making. The
  //: month count carries that a shortfall is recurring rather than one-off.
  const byPerson = new Map<string, { effortDays: number; windows: number }>();
  for (const conflict of rollup?.resource_conflicts ?? []) {
    for (const shortfall of conflict.shortfalls) {
      if (!ownIds.has(shortfall.project_id)) continue;
      const entry = byPerson.get(conflict.resource_name) ?? { effortDays: 0, windows: 0 };
      entry.effortDays += shortfall.effort_days;
      entry.windows += 1;
      byPerson.set(conflict.resource_name, entry);
    }
  }
  const mine = Array.from(byPerson.entries()).sort((a, b) => b[1].effortDays - a[1].effortDays);
  const owed = mine.reduce((total, [, m]) => total + m.effortDays, 0);

  return (
    <TileShell loading={!config && !configProblem} problem={configProblem}>
      {entry ? (
        <>
          {programId ? (
            <>
              <a
                href={programLink("/programs/dashboard", programId)}
                className="text-[13px] font-semibold text-ink no-underline hover:underline"
                title="Open this program's dashboard"
              >
                {entry.program_name || programId}
              </a>
              <p className="mt-1 mb-0 text-[11.5px] text-ink-3">
                {siblings.length === 0
                  ? "The only project in this program."
                  : `Shares this program with ${siblings.map((s) => s.name).join(", ")}.`}
              </p>
            </>
          ) : (
            <>
              <span className="text-[13px] font-semibold text-ink">No program</span>
              <p className="mt-1 mb-0 text-[11.5px] text-ink-3">
                Nobody has assigned this project to a program yet. It is not
                filed under a default one, so it has no cross-project rollup.
              </p>
            </>
          )}

          <div className="mt-2 border-t border-rule pt-2 text-[11.5px] text-ink-2">
            {entry.also.length > 0 ? (
              <>
                Tracked in {entry.also.length + 1} source systems, analysed as one
                project:{" "}
                <span className="text-ink-3">
                  {[entry.canonical_id, ...entry.also]
                    .map((id) => id.split(":")[0])
                    .join(" + ")}
                </span>
              </>
            ) : (
              <span className="text-ink-3">One source system feeds this project.</span>
            )}
          </div>

          {mine.length > 0 && (
            <div className="mt-2 border-t border-rule pt-2">
              <p className="m-0 text-[11.5px] text-ink-2">
                <b className="font-semibold text-orange">{owed.toFixed(2)} effort-days</b> of
                this project's demand is lost to people a sibling also needs:
              </p>
              {mine.map(([person, m]) => (
                <div key={person} className="text-[11px] text-ink-3">
                  {person}: {m.effortDays.toFixed(2)}d over {m.windows} contended month
                  {m.windows === 1 ? "" : "s"}
                </div>
              ))}
            </div>
          )}
        </>
      ) : (
        config && (
          <p className="m-0 text-[12.5px] text-ink-3">
            This project id is not declared in the project registry, so its
            program cannot be resolved.
          </p>
        )
      )}
    </TileShell>
  );
};

/* Two kinds of slip, side by side, because they are two different facts and
   the distinction is the one this product exists to make.

   `recorded_slip_days` is a person moving a date in their own sheet - they know
   about it. `propagated_days` is what the dependency chain implies and the sheet
   does not show - nobody has written it down. Summing them into one "variance"
   figure would destroy exactly the claim worth making. */
const ScheduleVariance: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useGantt(scopeId);
  const rows = bundle?.rows ?? [];
  const recorded = rows.filter((r) => (r.recorded_slip_days ?? 0) > 0);
  const propagated = rows.filter((r) => (r.propagated_days ?? 0) > 0);
  const worstRecorded = Math.max(0, ...recorded.map((r) => r.recorded_slip_days ?? 0));
  const worstPropagated = Math.max(0, ...propagated.map((r) => r.propagated_days ?? 0));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <div>
              <b className="block text-[22px] leading-none font-bold text-ink">
                {worstRecorded > 0 ? `+${worstRecorded}d` : "0d"}
              </b>
              <span className="mt-1 block text-[10.5px] text-ink-3">
                recorded against baseline &middot; {recorded.length} task(s)
              </span>
            </div>
            <div>
              <b
                className={`block text-[22px] leading-none font-bold ${
                  worstPropagated > 0 ? "text-orange" : "text-ink"
                }`}
              >
                {worstPropagated > 0 ? `+${worstPropagated}d` : "0d"}
              </b>
              <span className="mt-1 block text-[10.5px] text-ink-3">
                implied, not written down &middot; {propagated.length} task(s)
              </span>
            </div>
          </div>
          <div className="mt-2 grid gap-1 border-t border-rule pt-2 text-[11.5px] text-ink-2">
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">project end, planned</span>
              <span>{bundle.project_end_planned ?? "-"}</span>
            </div>
            <div className="flex justify-between gap-2">
              <span className="text-ink-3">project end, projected</span>
              <span>{bundle.project_end_projected ?? "-"}</span>
            </div>
          </div>
          {/* Worst task, not a total: the two columns are per-task maxima and
              adding slips along a chain would double-count the same delay. */}
          <p className="mt-2 mb-0 text-[10.5px] text-ink-3">
            Worst single task in each column, never a sum.
          </p>
        </>
      )}
    </TileShell>
  );
};

/* The evidence behind the variance: which tasks carry the slip, and whether
   they sit on the chain that sets the project's finish - the only sequence a
   PM can shorten to pull the date in. */
const DelayedTasks: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useGantt(scopeId);
  const late = (bundle?.rows ?? [])
    .filter((r) => (r.propagated_days ?? 0) > 0 || (r.recorded_slip_days ?? 0) > 0)
    .sort(
      (a, b) =>
        Math.max(b.propagated_days ?? 0, b.recorded_slip_days ?? 0) -
        Math.max(a.propagated_days ?? 0, a.recorded_slip_days ?? 0),
    );

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {late.slice(0, 10).map((row) => (
          <div key={row.entity_id} className="flex items-baseline gap-2 text-[12.5px]">
            <span className="min-w-0 flex-1 truncate text-ink" title={row.title ?? row.label}>
              {row.title ?? row.label}
              {row.on_driving_path && (
                <span
                  className="ml-1.5 rounded bg-navy/15 px-1 py-0.5 text-[9px] font-extrabold uppercase tracking-[0.05em] text-navy"
                  title="On the chain that sets this project's finish date"
                >
                  driving
                </span>
              )}
            </span>
            <span className="shrink-0 text-[11px] text-ink-3">
              {(row.recorded_slip_days ?? 0) > 0 && <span>+{row.recorded_slip_days}d recorded</span>}
              {(row.recorded_slip_days ?? 0) > 0 && (row.propagated_days ?? 0) > 0 && " · "}
              {(row.propagated_days ?? 0) > 0 && (
                <span className="text-orange">+{row.propagated_days}d implied</span>
              )}
            </span>
          </div>
        ))}
        {late.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">No task is late on either measure.</p>
        )}
        {late.length > 10 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            {late.length - 10} more, worst first - the full list is on Schedule.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* Each milestone's planned date against the baseline it was committed to.

   Soonest first, and both dates always shown: a marker that has moved is only
   legible beside the one it moved from. `at_risk` is the server's flag, not a
   comparison redone here. */
const UpcomingMilestones: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useGantt(scopeId);
  const milestones = (bundle?.milestones ?? [])
    .slice()
    .sort((a, b) => (a.planned_date ?? "9999").localeCompare(b.planned_date ?? "9999"));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {milestones.map((m) => (
          <div key={m.id} className="flex items-baseline gap-2 text-[12.5px]">
            <span
              className={`mt-[1px] h-2 w-2 shrink-0 rounded-full ${m.at_risk ? "bg-red" : "bg-green"}`}
              title={m.at_risk ? "At risk" : "On track"}
            />
            <span className="min-w-0 flex-1 truncate text-ink" title={m.name}>
              {m.name}
            </span>
            <span className="shrink-0 text-[11px] text-ink-3">
              {m.planned_date ?? "undated"}
              {!!m.slipped_days && m.slipped_days > 0 && (
                <span className="text-orange"> +{m.slipped_days}d vs {m.baseline_date}</span>
              )}
            </span>
          </div>
        ))}
        {milestones.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">No milestones in this project's schedule.</p>
        )}
      </div>
    </TileShell>
  );
};

/* The register as a table - rating, status, owner - which is what a PM reads
   beside the matrix rather than instead of it. The rating badge is the server's
   lookup (`app/risks/matrix.py`), never typed, so it cannot disagree with the
   cell the same risk lands in on `risk_matrix`. */
const RiskRegister: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRisks(scopeId);
  const rank: Record<string, number> = {
    "Very High": 0,
    High: 1,
    Medium: 2,
    Low: 3,
    "Very Low": 4,
  };
  const risks = (bundle?.risks ?? [])
    .slice()
    .sort((a, b) => (rank[a.pre_rating ?? ""] ?? 9) - (rank[b.pre_rating ?? ""] ?? 9));

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {risks.map((r) => (
          <div key={r.id} className="flex items-baseline gap-2 text-[12.5px]">
            <span
              className={`w-[62px] shrink-0 text-[11px] font-semibold ${RATING_STYLE[r.pre_rating ?? ""] ?? "text-ink-3"}`}
            >
              {r.pre_rating ?? "not assessed"}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink" title={r.title}>
              {r.risk_no ? <span className="text-ink-3">{r.risk_no} </span> : null}
              {r.title}
            </span>
            <span className="shrink-0 text-[11px] text-ink-3">
              {r.status}
              {r.responsible ? ` · ${r.responsible}` : ""}
            </span>
          </div>
        ))}
        {risks.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">No risks logged for this project yet.</p>
        )}
      </div>
    </TileShell>
  );
};

/* Does the mitigation somebody wrote down actually move the assessment?

   Only risks carrying *both* assessments appear. A post-treatment rating is a
   judgement a person makes after deciding on an action, so a risk without one
   has not been mitigated yet rather than been mitigated to no effect - and
   showing it as unchanged would claim the second thing. The count of those is
   reported instead, because "nobody has reassessed 8 of 10 risks" is itself the
   finding a reader wants. */
const MitigationEffect: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useRisks(scopeId);
  const all = bundle?.risks ?? [];
  const treated = all.filter((r) => r.pre_rating && r.post_rating);
  const untreated = all.length - treated.length;

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-1.5">
        {treated.map((r) => {
          const moved = r.pre_rating !== r.post_rating;
          const days =
            r.pre_delay_days != null && r.post_delay_days != null
              ? `${r.pre_delay_days}d → ${r.post_delay_days}d`
              : null;
          return (
            <div key={r.id} className="text-[12.5px]">
              <div className="flex items-baseline gap-2">
                <span className="min-w-0 flex-1 truncate text-ink" title={r.title}>
                  {r.title}
                </span>
                <span className="shrink-0 text-[11px]">
                  <span className={RATING_STYLE[r.pre_rating ?? ""] ?? "text-ink-3"}>
                    {r.pre_rating}
                  </span>
                  <span className="text-ink-3"> → </span>
                  <span className={RATING_STYLE[r.post_rating ?? ""] ?? "text-ink-3"}>
                    {r.post_rating}
                  </span>
                </span>
              </div>
              <div className="text-[11px] text-ink-3">
                {moved ? "mitigation moves the rating" : "mitigation does not move the rating"}
                {days ? ` · delay exposure ${days}` : ""}
              </div>
            </div>
          );
        })}
        {treated.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No risk here carries both a pre- and a post-mitigation assessment yet.
          </p>
        )}
        {untreated > 0 && treated.length > 0 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            {untreated} more risk(s) have no post-mitigation assessment, so they
            are absent rather than shown as unchanged.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* The decision half of every finding, on its own so it can be worked through.

   `recommendation` is substituted server-side from the finding's own `facts`,
   the same as `headline` - so an action naming a number names the number the
   rule fired on. A finding with no recommendation is skipped rather than given
   a generic one. */
const AiRecommendedActions: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useInsight(scopeId);
  const order: Record<string, number> = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };
  const actionable = (bundle?.findings ?? [])
    .filter((f) => f.recommendation)
    .sort((a, b) => (order[a.severity] ?? 9) - (order[b.severity] ?? 9));
  const silent = (bundle?.findings ?? []).length - actionable.length;

  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      <div className="grid gap-2">
        {actionable.map((f, i) => (
          <div key={f.id} className="flex gap-2 text-[12.5px]">
            <span className="shrink-0 text-[11px] font-bold text-ink-3">{i + 1}</span>
            <span className="min-w-0 flex-1">
              <span className="text-ink">{f.recommendation}</span>
              <span className="block text-[11px] text-ink-3">
                <span className="font-semibold uppercase">{f.severity}</span> · {f.headline}
              </span>
            </span>
          </div>
        ))}
        {actionable.length === 0 && bundle && (
          <p className="m-0 text-[12.5px] text-ink-3">
            No finding here carries a recommended action.
          </p>
        )}
        {silent > 0 && actionable.length > 0 && (
          <p className="m-0 border-t border-rule pt-1.5 text-[11px] text-ink-3">
            {silent} further finding(s) have no recommendation attached.
          </p>
        )}
      </div>
    </TileShell>
  );
};

/* ---- Custom tiles -------------------------------------------------------
   Keyed `custom:<id>`, not a fixed catalogue entry - `DashboardCanvas`
   special-cases the prefix and renders this directly rather than looking the
   key up in `TILE_REGISTRY`. Labelled plainly as custom: this is the one
   tile type whose numbers came from a person, not the intelligence layer. */

export const CustomChartTile: ComponentType<TileProps> = ({ tileKey }) => {
  const id = tileKey.split(":")[1];
  const { bundle, problem } = useBundle<CustomTileOut>(
    id ? `/api/custom-tiles/${id}` : null,
  );
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <>
          <MiniChart chartType={bundle.chart_type} labels={bundle.labels} values={bundle.values} />
          <div className="mt-2 flex items-center gap-1.5">
            <span className="rounded bg-purple/15 px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-[0.05em] text-purple">
              Custom
            </span>
            {bundle.live_source && (
              <span
                className="rounded bg-green/15 px-1.5 py-0.5 text-[9px] font-extrabold uppercase tracking-[0.05em] text-green"
                title="Refreshes from the project's own data every time this tile is viewed"
              >
                Live
              </span>
            )}
            {bundle.source_note && (
              <span className="min-w-0 flex-1 truncate text-[10.5px] text-ink-3" title={bundle.source_note}>
                {bundle.source_note}
              </span>
            )}
          </div>
        </>
      )}
    </TileShell>
  );
};

export const TILE_REGISTRY: Record<string, ComponentType<TileProps>> = {
  program_health: ProgramHealth,
  project_portfolio: ProjectPortfolio,
  project_health_heatmap: ProjectHealthHeatmap,
  cross_project_risk: CrossProjectRisk,
  resource_conflict: ResourceConflictTile,
  ai_cross_project_brief: AiCrossProjectBrief,
  ai_detected_risks_top: AiDetectedRisksTop,
  milestones_at_risk: MilestonesAtRisk,
  blocking_qa: BlockingQa,
  quality_health: QualityHealth,
  risk_matrix: RiskMatrixTile,
  ai_management_brief: AiManagementBrief,
  ai_detected_risks: AiDetectedRisks,
  ai_root_cause_impact: AiRootCauseImpact,
  schedule_gantt: ScheduleGanttTile,
  delivery_forecast: DeliveryForecastTile,
  effort_burn: EffortBurnTile,
  team_effort: TeamEffortTile,

  // Added from the PM's own (Program)/(Project) tiles lists. Every key here
  // must exist in `app/dashboard/catalogue.py` too: that module is the
  // allow-list the AI generator validates against, and a tile rendered here
  // but absent there can never be chosen, while the reverse renders as
  // "Unknown tile".
  projects_needing_attention: ProjectsNeedingAttention,
  top_delayed_projects: TopDelayedProjects,
  resource_contention_split: ResourceContentionSplit,
  team_allocation: TeamAllocation,
  program_timeline: ProgramTimeline,
  project_summary: ProjectSummary,
  program_context: ProgramContext,
  schedule_variance: ScheduleVariance,
  delayed_tasks: DelayedTasks,
  upcoming_milestones: UpcomingMilestones,
  risk_register: RiskRegister,
  mitigation_effect: MitigationEffect,
  ai_recommended_actions: AiRecommendedActions,
};
