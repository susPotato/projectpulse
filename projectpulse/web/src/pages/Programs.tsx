/*
  The Programs list - `Layout_Program` image12: a card grid, one per Program,
  each showing how many projects it holds. `/portfolio` already answers "which
  projects need attention" across every program; this answers the level above
  it - "which program", the entry point into a Program's own canvas dashboard.
*/
import { useEffect, useState } from "react";
import {
  load,
  programLink,
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

function ProgramCard({ program }: { program: ProgramSummary }) {
  return (
    <Panel span={4} className="content-start">
      <a
        href={programLink("/programs/dashboard", program.id)}
        className="block no-underline hover:underline"
      >
        <h3 className="m-0 text-[15px] font-semibold text-ink">{program.name}</h3>
      </a>
      <p className="mt-1 mb-3 text-[12.5px] text-ink-3">
        {program.project_count} project{program.project_count === 1 ? "" : "s"}
        {program.owner ? ` · ${program.owner}` : ""}
      </p>
      <span
        className={`inline-block rounded px-2 py-0.5 text-[11px] font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[program.band]}`}
      >
        {BAND_LABEL[program.band]}
      </span>
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
