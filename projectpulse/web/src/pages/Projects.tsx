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
  send,
  type ApiProblem,
  type CreatedProject,
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

/*
  Creating a project before any document exists for it.

  A project used to appear only as a side effect of uploading a sheet, so the
  portfolio could not be set up ahead of the documents arriving. One made here
  and never ingested shows as `no_data`, which is the honest reading: somebody
  has told us about it, we have not seen a sheet for it.

  The program is optional and defaults to none. Filling it in with a default
  would be the bug this area was fixed for - a project quietly filed under a
  program nobody chose.
*/
function AddProjectForm({
  programs,
  onDone,
  onCancel,
}: {
  programs: ProgramListBundle | null;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [programId, setProgramId] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await send<CreatedProject>("/api/projects", "POST", {
        name: name.trim(),
        program_id: programId || null,
      });
      onDone();
    } catch (error) {
      setProblem(error as ApiProblem);
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="mb-3.5 grid gap-2.5 rounded-lg border border-rule bg-surface p-3.5"
    >
      <h3 className="m-0 text-emph font-semibold text-ink">New project</h3>
      <div className="flex flex-wrap gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Project name"
          aria-label="Project name"
          autoFocus
          className="min-w-[220px] flex-1 rounded-md border border-rule bg-surface px-3 py-2 text-body text-ink outline-none focus:border-navy"
        />
        <select
          value={programId}
          onChange={(e) => setProgramId(e.target.value)}
          aria-label="Program"
          className="min-w-[200px] flex-1 rounded-md border border-rule bg-surface px-3 py-2 text-body text-ink outline-none focus:border-navy"
        >
          {/* "No program" is a real choice, not a placeholder - so it is a
              selectable option rather than a disabled prompt. */}
          <option value="">No program</option>
          {(programs?.programs ?? []).map((program) => (
            <option key={program.id} value={program.id}>
              {program.name}
            </option>
          ))}
        </select>
      </div>
      {problem && <Problem {...problem} />}
      <div className="flex gap-2">
        <button
          type="submit"
          disabled={!name.trim() || busy}
          className="action"
          style={{ cursor: name.trim() && !busy ? "pointer" : "not-allowed", border: "none" }}
        >
          {busy ? "Creating..." : "Create project"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md border border-rule bg-surface px-3 py-1.5 text-body text-ink-2"
          style={{ cursor: "pointer" }}
        >
          Cancel
        </button>
      </div>
      <p className="m-0 text-body text-ink-3">
        It will show as <b>No data</b> until a document is ingested for it. Uploading a
        schedule for the same name later fills in this project rather than creating a
        second one.
      </p>
    </form>
  );
}

export function ProjectsView({
  bundle,
  programs,
  onChanged,
}: {
  bundle: PortfolioBundle;
  programs: ProgramListBundle | null;
  onChanged?: () => void;
}) {
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);

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
      action={
        <button
          type="button"
          onClick={() => setAdding((open) => !open)}
          className="action"
          style={{ cursor: "pointer", border: "none" }}
        >
          {adding ? "Close" : "+ Add project"}
        </button>
      }
    >
      {adding && (
        <AddProjectForm
          programs={programs}
          onDone={() => {
            setAdding(false);
            onChanged?.();
          }}
          onCancel={() => setAdding(false)}
        />
      )}

      <div className="mb-3.5">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by project, program or source id..."
          aria-label="Search projects"
          className="w-full max-w-[420px] rounded-md border border-rule bg-surface px-3 py-2 text-body text-ink outline-none focus:border-navy"
        />
      </div>

      <div className="grid gap-2">
        {matched.map((row) => (
          <ProjectRowCard
            key={row.project_id}
            row={row}
            program={programOf[row.project_id]}
            onRemoved={() => onChanged?.()}
          />
        ))}
        {matched.length === 0 && (
          <p className="m-0 rounded-lg border border-dashed border-rule px-3 py-6 text-center text-body text-ink-3">
            {rows.length === 0
              ? "No projects yet. Run python -m scripts.replay, or upload a sheet from Settings > Sources."
              : `No project matches "${query}".`}
          </p>
        )}
      </div>
    </Page>
  );
}

/* What removing a project would destroy, as the server reports it.

   Fetched only when somebody presses Remove, never with the list: it is several
   counting queries per project, and a portfolio page should not run them for
   rows nobody is touching. */
interface RemovalPreview {
  name: string;
  removable: boolean;
  reason: string;
  counts: Record<string, number>;
  irreplaceable: Record<string, number>;
}

/* Remove, in two steps, because there is no undo and nothing rebuilds a risk
   somebody typed or a board somebody arranged.

   The first press asks the server what would go and shows the real numbers.
   "This deletes 17 tasks and 2 risks you typed by hand" is a decision; "are you
   sure?" is a reflex, and people click through reflexes. */
function RemoveProject({ row, onRemoved }: { row: ProjectRow; onRemoved: () => void }) {
  const [preview, setPreview] = useState<RemovalPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function ask() {
    setBusy(true);
    setError(null);
    try {
      setPreview(
        await load<RemovalPreview>(
          `/api/projects/${encodeURIComponent(row.project_id)}/removal`,
        ),
      );
    } catch (e) {
      setError((e as ApiProblem).detail ?? "could not read what would be removed");
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    try {
      await send(`/api/projects/${encodeURIComponent(row.project_id)}`, "DELETE");
      onRemoved();
    } catch (e) {
      setError((e as ApiProblem).detail ?? "could not remove this project");
      setBusy(false);
    }
  }

  if (!preview && !error) {
    return (
      <button
        type="button"
        onClick={ask}
        disabled={busy}
        title={`Remove ${row.name}`}
        className="shrink-0 cursor-pointer rounded-md border border-rule bg-surface px-2 py-1 text-body text-ink-3 hover:border-red hover:text-red disabled:opacity-50"
      >
        {busy ? "..." : "Remove"}
      </button>
    );
  }

  return (
    <div className="w-full rounded-md border border-rule bg-bg p-2.5">
      {error && <p className="m-0 text-body text-red">{error}</p>}
      {preview && !preview.removable && (
        <p className="m-0 text-body text-ink-2">{preview.reason}</p>
      )}
      {preview?.removable && (
        <>
          <p className="m-0 text-body text-ink">
            Remove <b>{preview.name}</b> and everything about it?
          </p>
          <p className="mt-1 mb-0 text-body text-ink-3">
            {Object.entries(preview.counts)
              .filter(([, n]) => n > 0)
              .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
              .join(", ") || "nothing has been ingested for it"}
            .
          </p>
          {/* Led with, because it is the half no sync brings back. */}
          {Object.keys(preview.irreplaceable).length > 0 && (
            <p className="mt-1 mb-0 text-body font-semibold text-red">
              Includes{" "}
              {Object.entries(preview.irreplaceable)
                .map(([k, n]) => `${n} ${k.replace(/_/g, " ")}`)
                .join(" and ")}{" "}
              that no sync will rebuild.
            </p>
          )}
        </>
      )}
      <div className="mt-2 flex gap-2">
        {preview?.removable && (
          <button
            type="button"
            onClick={confirm}
            disabled={busy}
            className="cursor-pointer rounded-md border border-red bg-red px-2.5 py-1 text-body font-semibold text-surface disabled:opacity-50"
          >
            {busy ? "Removing..." : "Remove permanently"}
          </button>
        )}
        <button
          type="button"
          onClick={() => {
            setPreview(null);
            setError(null);
          }}
          className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-body text-ink-2"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

function ProjectRowCard({
  row,
  program,
  onRemoved,
}: {
  row: ProjectRow;
  program?: string;
  onRemoved: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-lg border border-rule bg-surface px-3.5 py-3 hover:border-navy">
    <a
      href={projectLink("/project/dashboard", row)}
      title={`Open ${row.name}`}
      className="flex min-w-0 flex-1 items-center gap-3 no-underline"
    >
      <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${BAND_DOT[row.band] ?? "bg-rule"}`} />

      <span className="min-w-0 flex-1">
        <span className="block truncate text-emph font-semibold text-ink">{row.name}</span>
        <span className="block truncate text-body text-ink-3">
          {program ? `${program} · ` : ""}
          {row.project_id}
        </span>
      </span>

      {/* The one number on this row, for the same reason it is the one number
          on the portfolio: it is a subtraction over two dates, not a score. */}
      <span className="shrink-0 text-right text-body">
        <span className={row.days_late > 0 ? "font-semibold text-red" : "text-ink-3"}>
          {row.days_late > 0 ? `+${row.days_late}d` : "on plan"}
        </span>
        <span className="block text-label text-ink-3">
          {row.findings} finding{row.findings === 1 ? "" : "s"}
        </span>
      </span>

      <span
        className={`shrink-0 rounded px-2 py-0.5 text-label font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[row.band]}`}
      >
        {BAND_LABEL[row.band]}
      </span>
    </a>
      <RemoveProject row={row} onRemoved={onRemoved} />
    </div>
  );
}

export function Projects() {
  const [bundle, setBundle] = useState<PortfolioBundle | null>(null);
  const [programs, setPrograms] = useState<ProgramListBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  //: Bumped after a create, to re-read both lists. A counter rather than
  //: pushing the new row into local state: the portfolio row carries bands and
  //: a finding count the server computes, so a locally invented one would
  //: disagree with what a refresh shows.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    load<PortfolioBundle>("/api/portfolio").then(setBundle, setProblem);
    // A hint, so its failure must not take the page down with it.
    load<ProgramListBundle>("/api/programs").then(setPrograms, () => setPrograms(null));
  }, [version]);

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
  return (
    <ProjectsView
      bundle={bundle}
      programs={programs}
      onChanged={() => setVersion((n) => n + 1)}
    />
  );
}
