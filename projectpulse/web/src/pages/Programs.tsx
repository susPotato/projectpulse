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
  send,
  type ApiProblem,
  type CreatedProgram,
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
          <h3 className="m-0 text-emph font-semibold text-ink">{program.name}</h3>
        </a>
        <span
          className={`shrink-0 rounded px-2 py-0.5 text-label font-extrabold tracking-[0.04em] uppercase ${BAND_STYLE[program.band]}`}
        >
          {BAND_LABEL[program.band]}
        </span>
      </div>
      <p className="mt-1 mb-2.5 text-body text-ink-3">
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
              className="flex items-center gap-1.5 rounded px-1 py-0.5 text-body no-underline hover:bg-bg"
            >
              <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_STYLE[row.band] ?? "bg-rule"}`} />
              <span className="min-w-0 flex-1 truncate text-ink">{row.name}</span>
              <span className="shrink-0 text-label text-ink-3">
                {row.days_late > 0 ? `+${row.days_late}d` : "on plan"}
              </span>
            </a>
          ))}
        </div>
      )}
    </Panel>
  );
}

/*
  Creating a program, which until now nothing could do.

  A program could only arrive from the built-in seed or be invented by a
  collector mid-ingest - which is exactly how one program came to exist under
  two ids. Naming one is a deliberate act, so it belongs on a form.

  Note what the form does *not* ask for: an id. It is derived from the name
  server-side, because a typed id is how a source system's name got inside a
  program's identity in the first place.
*/
function AddProgramForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [name, setName] = useState("");
  const [owner, setOwner] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy) return;
    setBusy(true);
    setProblem(null);
    try {
      await send<CreatedProgram>("/api/programs", "POST", {
        name: name.trim(),
        owner: owner.trim() || null,
      });
      onDone();
    } catch (error) {
      setProblem(error as ApiProblem);
      setBusy(false);
    }
  }

  return (
    <Panel span={12} className="content-start">
      <form onSubmit={submit} className="grid gap-2.5">
        <h3 className="m-0 text-emph font-semibold text-ink">New program</h3>
        <div className="flex flex-wrap gap-2">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Program name"
            aria-label="Program name"
            autoFocus
            className="min-w-[220px] flex-1 rounded-md border border-rule bg-surface px-3 py-2 text-body text-ink outline-none focus:border-navy"
          />
          <input
            value={owner}
            onChange={(e) => setOwner(e.target.value)}
            placeholder="Owner (optional)"
            aria-label="Program owner"
            className="min-w-[180px] flex-1 rounded-md border border-rule bg-surface px-3 py-2 text-body text-ink outline-none focus:border-navy"
          />
        </div>
        {problem && <Problem {...problem} />}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={!name.trim() || busy}
            className="action"
            style={{ cursor: name.trim() && !busy ? "pointer" : "not-allowed", border: "none" }}
          >
            {busy ? "Creating..." : "Create program"}
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
        {/* Said here rather than discovered later: a program with no projects
            is a legitimate row, not a half-finished one. */}
        <p className="m-0 text-body text-ink-3">
          A new program starts empty. Add projects to it from the Projects tab, or by
          uploading a document for one.
        </p>
      </form>
    </Panel>
  );
}

export function ProgramsView({
  bundle,
  onChanged,
}: {
  bundle: ProgramListBundle;
  onChanged?: () => void;
}) {
  const [adding, setAdding] = useState(false);

  return (
    <Page
      current="/programs"
      title="Programs"
      asof={`${bundle.programs.length} program(s)`}
      action={
        <button
          type="button"
          onClick={() => setAdding((open) => !open)}
          className="action"
          style={{ cursor: "pointer", border: "none" }}
        >
          {adding ? "Close" : "+ Add program"}
        </button>
      }
    >
      <Board>
        {adding && (
          <AddProgramForm
            onDone={() => {
              setAdding(false);
              onChanged?.();
            }}
            onCancel={() => setAdding(false)}
          />
        )}
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
  //: Bumped after a create, to re-read the list. A counter rather than pushing
  //: the new program into local state: the list carries bands and project
  //: counts the server computes, so inventing a row here would show a program
  //: that disagrees with the one a refresh produces.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    load<ProgramListBundle>("/api/programs").then(setBundle, setProblem);
  }, [version]);

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
  return <ProgramsView bundle={bundle} onChanged={() => setVersion((n) => n + 1)} />;
}
