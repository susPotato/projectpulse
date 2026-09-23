/*
  A project combobox: the current project, and a search box over the rest.

  The rail's switcher was a native `<select>`, which is fine at three demo
  projects and unusable at the number a real program carries - a `<select>` has
  no filter, so finding one project means scrolling a list ordered by something
  you did not choose. This is the same control with a text filter in front of
  it, and it is a component rather than markup inside `Shell.tsx` because the
  Projects tab wants the same search over the same rows.

  Deliberately no dependency: a combobox is a listbox, a filter and four key
  handlers, and the alternative is pulling a headless-UI library into a build
  whose whole argument for existing is that it is small enough to read.
*/
import { useEffect, useMemo, useRef, useState } from "react";

export interface PickableProject {
  id: string;
  name: string;
  /** `critical` / `watch` / `healthy` / `no_data` - the dot, when known. */
  band?: string;
  /** Whatever the page wants under the name; the program, usually. */
  hint?: string;
}

/* Spelled out rather than interpolated - Tailwind scans source text, the same
   trap `Shell.tsx`'s `SPAN` table documents. */
export const BAND_DOT: Record<string, string> = {
  critical: "bg-red",
  watch: "bg-amber",
  healthy: "bg-green",
  no_data: "bg-rule",
};

/** Match on any word of the query, against the name *and* the id.

    The id is searched because it is what distinguishes two projects a program
    has given nearly the same name, and because a PM who has a link in front of
    them is holding an id, not a name. */
export function filterProjects(
  projects: PickableProject[],
  query: string,
): PickableProject[] {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return projects;
  return projects.filter((project) => {
    const haystack = `${project.name} ${project.id} ${project.hint ?? ""}`.toLowerCase();
    return terms.every((term) => haystack.includes(term));
  });
}

export function ProjectPicker({
  projects,
  value,
  onPick,
  placeholder = "Search projects...",
  label = "Project",
  className = "",
}: {
  projects: PickableProject[];
  value: string | null;
  onPick: (project: PickableProject) => void;
  placeholder?: string;
  label?: string;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement | null>(null);
  const field = useRef<HTMLInputElement | null>(null);

  const selected = projects.find((p) => p.id === value) ?? null;
  const matches = useMemo(() => filterProjects(projects, query), [projects, query]);

  /* Close on a click anywhere else, and on Escape. Both listeners are torn
     down with the popover rather than living for the page's lifetime. */
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: MouseEvent) {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  /* Opening puts the caret in the search box: the whole point of the control
     is that you can type immediately, and a click that then needs a second
     click into a field is slower than the `<select>` it replaced. */
  useEffect(() => {
    if (open) field.current?.focus();
    else {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  function choose(project: PickableProject | undefined) {
    if (!project) return;
    setOpen(false);
    onPick(project);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === "Enter") {
      event.preventDefault();
      choose(matches[active]);
    }
  }

  return (
    <div ref={box} className={`relative ${className}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        title={selected ? `${label}: ${selected.name}` : label}
        className="flex w-full max-w-[240px] cursor-pointer items-center gap-1.5 rounded-md border border-rule bg-surface px-2 py-1 text-left text-body text-ink hover:bg-bg"
      >
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${BAND_DOT[selected?.band ?? "no_data"]}`}
        />
        <span className="min-w-0 flex-1 truncate">
          {selected ? selected.name : "Select a project"}
        </span>
        <svg viewBox="0 0 24 24" className="h-3 w-3 shrink-0 text-ink-3" aria-hidden="true">
          <path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" strokeWidth="2" />
        </svg>
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-1 w-[290px] overflow-hidden rounded-lg border border-rule bg-surface shadow-xl">
          <div className="border-b border-rule p-2">
            <input
              ref={field}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setActive(0);
              }}
              onKeyDown={onKeyDown}
              placeholder={placeholder}
              className="w-full rounded-md border border-rule bg-bg px-2 py-1 text-body text-ink outline-none focus:border-navy"
            />
          </div>
          <ul role="listbox" className="m-0 max-h-[280px] list-none overflow-y-auto p-1">
            {matches.map((project, index) => (
              <li key={project.id}>
                <button
                  type="button"
                  role="option"
                  aria-selected={project.id === value}
                  onMouseEnter={() => setActive(index)}
                  onClick={() => choose(project)}
                  className={`flex w-full cursor-pointer items-center gap-2 rounded border-0 px-2 py-1.5 text-left text-body ${
                    index === active ? "bg-bg" : "bg-transparent"
                  }`}
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${BAND_DOT[project.band ?? "no_data"]}`}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-ink">{project.name}</span>
                    {project.hint && (
                      <span className="block truncate text-label text-ink-3">
                        {project.hint}
                      </span>
                    )}
                  </span>
                  {project.id === value && (
                    <span className="shrink-0 text-label font-semibold text-navy">current</span>
                  )}
                </button>
              </li>
            ))}
            {matches.length === 0 && (
              <li className="px-2 py-3 text-body text-ink-3">
                No project matches "{query}".
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
