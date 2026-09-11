/*
  The Projects tab: every project, searchable, and nothing else.

  The Programs list answers "how is the portfolio doing" - cards, bands,
  rollups - and reaching one project through it means finding the program that
  owns it first. This page answers the other question a PM actually has at the
  start of a session: *open my project*. So it is a flat, filterable list of
  every project, including any that no program owns (an uploaded one), and
  every row does one thing - select that project and open it.

  It reads `/api/portfolio`, which is the flat every-project ranking, and
  layers each project's program name on top from `/api/programs` when that
  call succeeds. The program label is a hint, never a requirement: a project
  that no program claims still has to be reachable here, which is exactly the
  case `/api/programs` cannot answer.
*/
import { useEffect, useMemo, useState } from "react";
import {
  load,
  projectLink,
  type ApiProblem,
  type PortfolioBundle,
  type ProgramListBundle,
  type ProjectRow,
} from "../api";
import { Page, Problem } from "../components/Shell";
import { BAND_DOT, filterProjects } from "../components/ProjectPicker";

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

/* Worst first. A list you open to pick a project is also a list you glance at
   to see which one needs you, and alphabetical order hides that. */
const BAND_RANK: Record<string, number> = { critical: 0, watch: 1, healthy: 2, no_data: 3 };

export function ProjectsView({
  bundle,
  programs,
}: {
  bundle: PortfolioBundle;
  programs: ProgramListBundle | null;
}) {
  const [query, setQuery] = useState("");

  /* Which program owns each project, for the hint under its name - and for
     the search, so typing a program's name narrows to its projects. */
  const programOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const program of programs?.programs ?? []) {
      for (const row of program.projects) map[row.project_id] = program.name;
    }
    return map;
  }, [programs]);

  const rows = useMemo(
    () =>
      bundle.projects
        .slice()
        .sort(
          (a, b) =>
            (BAND_RANK[a.band] ?? 9) - (BAND_RANK[b.band] ?? 9) ||
            b.days_late - a.days_late ||
            a.name.localeCompare(b.name),
        ),
    [bundle],
  );

  /* One filter definition for the whole app: the same function the rail's
     picker uses, so a query that finds a project in one finds it in the
     other. */
  const matched = useMemo(() => {
    const pickable = rows.map((row) => ({
      id: row.project_id,
      name: row.name,
      band: row.band,
      hint: programOf[row.project_id],
    }));
    const keep = new Set(filterProjects(pickable, query).map((p) => p.id));
    return rows.filter((row) => keep.has(row.project_id));
  }, [rows, query, programOf]);

  return (
    <Page
      current="/projects"
      title="Projects"
      subtitle="Every project in the portfolio. Pick one to open its dashboard - the selection carries into Insight, Schedule, Risk and the rest."
      asof={`${matched.length} of ${rows.length} shown`}
    >
      <div className="mb-3.5">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by project, program or source id..."
          aria-label="Search projects"
          className="w-full max-w-[420px] rounded-md border border-rule bg-surface px-3 py-2 text-[13px] text-ink outline-none focus:border-navy"
        />
      </div>

      <div className="grid gap-2">
        {matched.map((row) => (
          <ProjectRowCard key={row.project_id} row={row} program={programOf[row.project_id]} />
        ))}
        {matched.length === 0 && (
          <p className="m-0 rounded-lg border border-dashed border-rule px-3 py-6 text-center text-[13px] text-ink-3">
            {rows.length === 0
              ? "No projects yet. Run python -m scripts.replay, or upload a sheet from Settings > Sources."
              : `No project matches "${query}".`}
          </p>
        )}
      </div>
    </Page>
  );
}

function ProjectRowCard({ row, program }: { row: ProjectRow; program?: string }) {
  return (
    <a
      href={projectLink("/project/dashboard", row)}
      title={`Open ${row.name}`}
      className="flex items-center gap-3 rounded-lg border border-rule bg-surface px-3.5 py-3 no-underline hover:border-navy"
    >
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${BAND_DOT[row.band] ?? "bg-rule"}`} />

      <span className="min-w-0 flex-1">
        <span className="block truncate text-[14px] font-semibold text-ink">{row.name}</span>
        <span className="block truncate text-[11.5px] text-ink-3">
          {program ? `${program} · ` : ""}
          {row.project_id}
        </span>
      </span>

      {/* The one number on this row, for the same reason it is the one number
          on the portfolio: it is a subtraction over two dates, not a score. */}
      <span className="shrink-0 text-right text-[12px]">
        <span className={row.days_late > 0 ? "font-semibold text-red" : "text-ink-3"}>
          {row.days_late > 0 ? `+${row.days_late}d` : "on plan"}
        </span>
        <span className="block text-[11px] text-ink-3">
          {row.findings} finding{row.findings === 1 ? "" : "s"}
        </span>
      </span>

      <span
        className={`shrink-0 rounded px-2 py-0.5 text-[10.5px] font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[row.band]}`}
      >
        {BAND_LABEL[row.band]}
      </span>
    </a>
  );
}

export function Projects() {
  const [bundle, setBundle] = useState<PortfolioBundle | null>(null);
  const [programs, setPrograms] = useState<ProgramListBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<PortfolioBundle>("/api/portfolio").then(setBundle, setProblem);
    // A hint, so its failure must not take the page down with it.
    load<ProgramListBundle>("/api/programs").then(setPrograms, () => setPrograms(null));
  }, []);

  if (problem) {
    return (
      <Page current="/projects" title="Projects" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return <Page current="/projects" title="Projects" subtitle="Loading..." children={null} />;
  }
  return <ProjectsView bundle={bundle} programs={programs} />;
}
