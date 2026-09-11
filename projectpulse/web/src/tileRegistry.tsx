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
  projectLink,
  type ApiProblem,
  type ChartType,
  type CustomTileOut,
  type ForecastBundle,
  type GanttBundle,
  type InsightBundle,
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

const RATING_STYLE: Record<string, string> = {
  "Very Low": "text-green",
  Low: "text-green",
  Medium: "text-amber",
  High: "text-orange",
  "Very High": "text-red",
};

function useBundle<T>(path: string | null): { bundle: T | null; problem: ApiProblem | null } {
  const [bundle, setBundle] = useState<T | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    setBundle(null);
    setProblem(null);
    if (!path) return;
    load<T>(path).then(setBundle, setProblem);
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

const DIMENSIONS = ["schedule", "quality", "qa", "evidence"] as const;

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
              className="grid grid-cols-[1fr_repeat(4,20px)] items-center gap-1.5 rounded px-1 py-0.5 no-underline hover:bg-bg"
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
            <p className="m-0 text-[12.5px] text-ink-3">No one is overallocated across projects.</p>
          )}
          {bundle.resource_conflicts.map((c) => (
            <div key={c.resource_name} className="text-[12.5px]">
              <b className="font-semibold text-orange">{c.total_allocation_percent}%</b>{" "}
              <span className="text-ink">{c.resource_name}</span>
              <div className="text-[11px] text-ink-3">{c.projects.join(" + ")}</div>
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

const RiskMatrixTile: ComponentType<TileProps> = ({ scopeId }) => {
  const { bundle, problem } = useBundle<RiskBundle>(
    `/api/risks?project=${encodeURIComponent(scopeId)}`,
  );
  return (
    <TileShell loading={!bundle && !problem} problem={problem}>
      {bundle && (
        <div className="grid gap-1.5">
          {bundle.risks.slice(0, 6).map((r) => (
            <div key={r.id} className="flex items-center gap-2 text-[12.5px]">
              <span className={`font-semibold ${RATING_STYLE[r.pre_rating ?? ""] ?? "text-ink-3"}`}>
                {r.pre_rating ?? "n/a"}
              </span>
              <span className="min-w-0 flex-1 truncate">{r.title}</span>
            </div>
          ))}
          {bundle.risks.length === 0 && (
            <p className="m-0 text-[12.5px] text-ink-3">No risks logged yet.</p>
          )}
        </div>
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
};
