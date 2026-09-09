/*
  The program screen: every delivery project, ranked, with bands not scores.

  The design shows a health score of 68. This shows a band, for the reason the
  whole product exists: a score has to weight a blocked QA queue against a
  slipped milestone with coefficients nobody can defend, and it would be
  computed on partial data besides. `worst_severity` says which finding set the
  band, so a reader can go and check it.

  Grey is a band of its own and never green. A project with no ingested sheet is
  unknown, and colouring unknown healthy is the failure this product argues
  against.
*/
import { useEffect, useState } from "react";
import {
  load,
  projectLink,
  type ApiProblem,
  type PortfolioBundle,
  type ProjectRow,
} from "../api";
import { Board, Page, Panel, Problem, Stat, Stats } from "../components/Shell";

const DIMENSIONS = ["schedule", "quality", "qa", "evidence"] as const;

/* Spelled out rather than interpolated: Tailwind scans source text, so a class
   assembled at runtime gets no CSS emitted for it. Same trap as the board's
   column spans - see `Shell.tsx`. */
const CELL: Record<string, string> = {
  critical: "bg-red",
  watch: "bg-amber",
  healthy: "bg-green",
  no_data: "bg-rule",
};

const BAND_LABEL: Record<string, string> = {
  critical: "Critical",
  watch: "Watch",
  healthy: "Healthy",
  no_data: "No data",
};

function Legend() {
  return (
    <div className="mt-3.5 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-rule pt-3">
      {(["critical", "watch", "healthy", "no_data"] as const).map((band) => (
        <span key={band} className="flex items-center gap-1.5 text-[11px] text-ink-3">
          <span className={`h-3 w-3 rounded-sm ${CELL[band]}`} />
          {band === "no_data" ? "no sheet ingested" : band}
        </span>
      ))}
      <span className="flex-1" />
      <span className="text-[11px] text-ink-3 italic">
        Grey is deliberate: a project with no data gets no colour rather than a green one.
      </span>
    </div>
  );
}

function Row({ row }: { row: ProjectRow }) {
  const known = row.band !== "no_data";

  return (
    <>
      <div className="min-w-0">
        <a
          href={projectLink("/insight", row)}
          className="block truncate text-[13px] font-semibold no-underline hover:underline"
        >
          {row.name}
        </a>
        <div className="mt-0.5 truncate text-[11px] text-ink-3">
          {known
            ? `${row.source_ids.length} source(s) · ${row.findings} finding(s)`
            : "no sheet ingested"}
        </div>
      </div>
      {DIMENSIONS.map((dimension) => (
        <div
          key={dimension}
          title={`${dimension}: ${row.bands[dimension] ?? "no_data"}`}
          className={`h-[26px] rounded ${CELL[row.bands[dimension] ?? "no_data"]}`}
        />
      ))}
      <div className="text-right">
        {known && row.days_late > 0 ? (
          <span className="text-[13px] font-semibold text-orange">+{row.days_late}d</span>
        ) : (
          <span className="text-[12px] text-ink-3">{known ? "on plan" : "no data"}</span>
        )}
      </div>
    </>
  );
}

export function PortfolioView({ bundle }: { bundle: PortfolioBundle }) {
  const worst = bundle.projects.reduce(
    (most, row) => (row.days_late > most ? row.days_late : most),
    0,
  );
  const late = bundle.projects.find((row) => row.days_late === worst && worst > 0);
  const program = bundle.projects.some((r) => r.band === "critical")
    ? "critical"
    : bundle.projects.some((r) => r.band === "watch")
      ? "watch"
      : bundle.projects.some((r) => r.band === "healthy")
        ? "healthy"
        : "no_data";

  const attention = bundle.projects
    .filter((row) => row.headline && row.band !== "healthy")
    .slice(0, 4);

  return (
    <Page
      current="/portfolio"
      title="Portfolio"
      scope={bundle.program_name}
      asof={`${bundle.projects.length} delivery project(s)`}
    >
      <Board className="mb-6">
        <Panel caption="Program status" span={4} className="content-start">
          <div className="flex items-start gap-3">
            <span
              className={`rounded px-3 py-1 text-[13px] font-extrabold tracking-[0.04em] uppercase ${CELL[program]} text-surface`}
            >
              {BAND_LABEL[program]}
            </span>
          </div>
          <p className="mt-3 mb-0 text-[12.5px] text-ink-2">
            The worst of its projects, not an average. A program with one critical project
            and nine healthy ones is not ninety per cent healthy; it has a critical
            project.
          </p>
        </Panel>

        <Panel caption="Largest unrecorded slip" span={4} className="content-start">
          {worst > 0 ? (
            <>
              <div className="flex items-baseline gap-2.5">
                <span className="text-[32px] leading-none font-bold text-orange">
                  +{worst}
                </span>
                <span className="text-[13px] text-ink-2">days · {late?.name}</span>
              </div>
              <p className="mt-2 mb-0 text-[12px] text-ink-3">
                Implied by the dependency chain and absent from the plan.{" "}
                {late?.committed_end} &rarr; {late?.projected_end}.
              </p>
            </>
          ) : (
            <p className="m-0 text-[12.5px] text-ink-2">
              No project carries slip its own dependencies imply.
            </p>
          )}
        </Panel>

        <Panel caption="Needing attention today" span={4} className="content-start">
          {attention.length === 0 ? (
            <p className="m-0 text-[12.5px] text-ink-2">Nothing breaches a threshold.</p>
          ) : (
            <div className="grid gap-2">
              {attention.map((row) => (
                <div key={row.project_id} className="flex items-start gap-2.5">
                  <span
                    className={`mt-px min-w-[54px] rounded px-1.5 py-px text-center text-[10px] font-extrabold tracking-[0.04em] uppercase ${CELL[row.band]} text-surface`}
                  >
                    {row.worst_severity ?? row.band}
                  </span>
                  <span className="min-w-0 flex-1 text-[12.5px] text-ink-2">
                    <b className="font-semibold text-ink">{row.name}</b> &mdash;{" "}
                    {row.headline}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel caption="Project portfolio" span={12}>
          <div className="grid items-center gap-2 [grid-template-columns:minmax(0,1.7fr)_repeat(4,minmax(0,1fr))_minmax(0,1fr)]">
            <div className="text-[10.5px] tracking-[0.06em] text-ink-3 uppercase" />
            {DIMENSIONS.map((dimension) => (
              <div
                key={dimension}
                className="text-center text-[10.5px] tracking-[0.06em] text-ink-3 uppercase"
              >
                {dimension}
              </div>
            ))}
            <div className="text-right text-[10.5px] tracking-[0.06em] text-ink-3 uppercase">
              slip
            </div>

            {bundle.projects.map((row) => (
              <Row key={row.project_id} row={row} />
            ))}
          </div>
          <Legend />
        </Panel>
      </Board>

      <Stats>
        <Stat value={String(bundle.projects.length)} label="projects" />
        <Stat
          value={String(bundle.projects.reduce((n, r) => n + r.findings, 0))}
          label="findings across the program"
        />
        <Stat
          value={String(bundle.projects.reduce((n, r) => n + r.milestones_at_risk, 0))}
          label="milestones at risk"
          bad={bundle.projects.some((r) => r.milestones_at_risk > 0)}
        />
        <Stat
          value={String(bundle.projects.filter((r) => r.band === "no_data").length)}
          label="projects with no data"
        />
      </Stats>
    </Page>
  );
}

export function Portfolio() {
  const [bundle, setBundle] = useState<PortfolioBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<PortfolioBundle>("/api/portfolio").then(setBundle, setProblem);
  }, []);

  if (problem) {
    return (
      <Page current="/portfolio" title="Portfolio" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return (
      <Page current="/portfolio" title="Portfolio" subtitle="Loading..." children={null} />
    );
  }
  return <PortfolioView bundle={bundle} />;
}
