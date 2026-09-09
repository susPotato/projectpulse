/*
  The Programs list - `Layout_Program` image12, extended: a card grid, one
  per Program, but each card also lists its own projects directly, clickable.

  This is the app's single entry point into the Program -> Project hierarchy
  (there is deliberately no separate "Project" tab) - so picking a project
  has to be possible right here, not only after opening its program's own
  dashboard first. `/portfolio` still answers "which projects need attention"
  as one flat, cross-program ranking for anyone who wants that cut instead.
*/
import { useEffect, useState } from "react";
import {
  load,
  programLink,
  projectLink,
  type ApiProblem,
  type ProgramListBundle,
  type ProgramSummary,
} from "../api";
import { Board, Page, Panel, Problem } from "../components/Shell";

const BAND_LABEL: Record<string, string> = {
  critical: "Critical",
  watch: "Watch",
  healthy: "Healthy",
  no_data: "No data",
};

const BAND_STYLE: Record<string, string> = {
  critical: "bg-red text-surface",
  watch: "bg-amber text-surface",
  healthy: "bg-green text-surface",
  no_data: "bg-rule text-ink-3",
};

const DOT_STYLE: Record<string, string> = {
  critical: "bg-red",
  watch: "bg-amber",
  healthy: "bg-green",
  no_data: "bg-rule",
};

function ProgramCard({ program }: { program: ProgramSummary }) {
  return (
    <Panel span={4} className="content-start">
      <div className="flex items-start justify-between gap-2">
        <a
          href={programLink("/programs/dashboard", program.id)}
          className="block no-underline hover:underline"
          title="Open this program's dashboard"
        >
          <h3 className="m-0 text-[15px] font-semibold text-ink">{program.name}</h3>
        </a>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-[10.5px] font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[program.band]}`}
        >
          {BAND_LABEL[program.band]}
        </span>
      </div>
      <p className="mt-1 mb-2.5 text-[12.5px] text-ink-3">
        {program.project_count} project{program.project_count === 1 ? "" : "s"}
        {program.owner ? ` · ${program.owner}` : ""}
      </p>

      {program.projects.length > 0 && (
        <div className="grid gap-1 border-t border-rule pt-2.5">
          {program.projects.map((row) => (
            <a
              key={row.project_id}
              href={projectLink("/project/dashboard", row)}
              title={`Open ${row.name}'s dashboard`}
              className="flex items-center gap-1.5 rounded px-1 py-0.5 text-[12px] no-underline hover:bg-bg"
            >
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_STYLE[row.band] ?? "bg-rule"}`} />
              <span className="min-w-0 flex-1 truncate text-ink">{row.name}</span>
              <span className="shrink-0 text-[11px] text-ink-3">
                {row.days_late > 0 ? `+${row.days_late}d` : "on plan"}
              </span>
            </a>
          ))}
        </div>
      )}
    </Panel>
  );
}

export function ProgramsView({ bundle }: { bundle: ProgramListBundle }) {
  return (
    <Page
      current="/programs"
      title="Programs"
      asof={`${bundle.programs.length} program(s)`}
    >
      <Board>
        {bundle.programs.map((program) => (
          <ProgramCard key={program.id} program={program} />
        ))}
      </Board>
    </Page>
  );
}

export function Programs() {
  const [bundle, setBundle] = useState<ProgramListBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<ProgramListBundle>("/api/programs").then(setBundle, setProblem);
  }, []);

  if (problem) {
    return (
      <Page current="/programs" title="Programs" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return <Page current="/programs" title="Programs" subtitle="Loading..." children={null} />;
  }
  return <ProgramsView bundle={bundle} />;
}
