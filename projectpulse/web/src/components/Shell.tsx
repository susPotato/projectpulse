/*
  The chrome every page shares: tab bar, headings, cards, stat tiles.

  Small deliberate components rather than one big layout, because the two pages
  differ in body and agree on everything around it - and the tab bar must agree
  across the *static* pages too, so its links are spelled out here once.
*/
import { useEffect, useState, type ReactNode } from "react";
import {
  currentProject,
  load,
  projectLink,
  type PortfolioBundle,
  type ProjectRow,
} from "../api";
import { ProjectPicker } from "./ProjectPicker";

/* One list, and the hand-written pages carry the same one. `tests/test_api.py`
   asserts they match, because three copies of a nav is how the six original
   mockups ended up with two conflicting token families.

   Label text is free to change: `publish.py` matches the console link by
   `href`, not by what it is called. */
const TABS = [
  { href: "/programs", label: "Program" },
  { href: "/projects", label: "Projects" },
  { href: "/console", label: "Console" },
  { href: "/gantt", label: "Schedule" },
  { href: "/insight", label: "Insight" },
  { href: "/risk", label: "Risk" },
  { href: "/team", label: "Team" },
  { href: "/explain", label: "Calc" },
  { href: "/reports", label: "Reports" },
  { href: "/agent", label: "Agent" },
  { href: "/settings", label: "Settings" },
] as const;

/* Inline SVG on a 24px grid, stroked with `currentColor` so one definition
   follows the link's state. Never emoji: they render differently on every
   machine and the theme cannot recolour them. */
const ICONS: Record<string, ReactNode> = {
  "/programs": (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>
  ),
  /* A folder, against Program's four tiles: the Program tab is the portfolio
     at a glance, this one is the drawer you pick a single project out of. */
  "/projects": (
    <>
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
      <path d="M3 11h18" />
    </>
  ),
  "/console": <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />,
  "/gantt": <path d="M4 6h9M4 12h14M4 18h6" />,
  "/insight": (
    <>
      <rect x="3" y="3" width="7" height="9" />
      <rect x="14" y="3" width="7" height="5" />
      <rect x="14" y="12" width="7" height="9" />
      <rect x="3" y="16" width="7" height="5" />
    </>
  ),
  "/risk": (
    <>
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" />
      <path d="M12 9v4" />
      <path d="M12 17h.01" />
    </>
  ),
  "/team": (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20a6 6 0 0 1 12 0" />
      <path d="M16 5.5a3 3 0 0 1 0 5" />
      <path d="M18 20a6 6 0 0 0-2-4.5" />
    </>
  ),
  "/explain": (
    <>
      <path d="M4 4h16v16H4z" />
      <path d="M8 9h8M8 13h5" />
    </>
  ),
  "/reports": (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </>
  ),
  "/agent": (
    <>
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M8 20h8" />
      <circle cx="9" cy="10" r="1" />
      <circle cx="15" cy="10" r="1" />
      <path d="M9 14h6" />
    </>
  ),
  "/settings": (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
    </>
  ),
};

/* The app shell from the design: sections down the left, content to the right.
   Styled by `shell.css` so the built pages and the hand-written ones share one
   definition of it rather than one each. */
export function Rail({ current }: { current: string }) {
  return (
    <nav className="rail" aria-label="Sections">
      <a className="brand" href="/insight" aria-label="ProjectPulse">
        <svg viewBox="0 0 24 24">
          <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />
        </svg>
      </a>
      {TABS.map((tab) => (
        <a
          key={tab.href}
          href={tab.href}
          aria-current={tab.href === current ? "page" : undefined}
        >
          <svg viewBox="0 0 24 24">{ICONS[tab.href]}</svg>
          <span>{tab.label}</span>
        </a>
      ))}
    </nav>
  );
}

/* Within-page views, the design's second row of tabs.

   Distinct from the rail: the rail moves between *screens*, this switches what
   a screen shows about one project. Kept as state rather than URLs so a reader
   who came from the portfolio does not lose their place, and so there is still
   exactly one source of truth about which URLs exist. */
export function SubTabs({
  views,
  current,
  onSelect,
}: {
  views: readonly string[];
  current: string;
  onSelect: (view: string) => void;
}) {
  return (
    <nav className="mb-4 flex flex-wrap border-b border-rule" aria-label="Views">
      {views.map((view) => {
        const active = view === current;
        return (
          <button
            key={view}
            type="button"
            onClick={() => onSelect(view)}
            aria-current={active ? "page" : undefined}
            className={
              "-mb-px cursor-pointer border-0 border-b-2 bg-transparent px-[15px] py-2.5 text-[13px] " +
              (active
                ? "border-navy font-semibold text-ink"
                : "border-transparent text-ink-2 hover:text-ink")
            }
          >
            {view}
          </button>
        );
      })}
    </nav>
  );
}

/* Which project every project-scoped page reads - see `api.ts#currentProject`.
   Lives in the shell rather than each page so switching carries across a full
   page navigation without seven copies of the same fetch-and-redirect. Shows
   up on every page, including ones with nothing to scope yet (Program, which
   already shows all of them, and Agent, which has no project fetch of its
   own) - picking there still updates what the *next* page you open shows,
   which is the point of it living in the shell and not the page body. */
function ProjectSwitcher() {
  const [bundle, setBundle] = useState<PortfolioBundle | null>(null);

  useEffect(() => {
    load<PortfolioBundle>("/api/portfolio").then(setBundle, () => setBundle(null));
  }, []);

  if (!bundle || bundle.projects.length === 0) return null;

  const selectedId = currentProject()?.id ?? bundle.projects[0]?.project_id;

  function goTo(row: ProjectRow) {
    window.location.href = projectLink(window.location.pathname, row);
  }

  return (
    <ProjectPicker
      projects={bundle.projects.map((row) => ({
        id: row.project_id,
        name: row.name,
        band: row.band,
        hint: row.days_late > 0 ? `+${row.days_late}d against commitment` : undefined,
      }))}
      value={selectedId ?? null}
      onPick={(picked) => {
        const row = bundle.projects.find((p) => p.project_id === picked.id);
        if (row) goTo(row);
      }}
    />
  );
}

export function Page({
  current,
  title,
  scope,
  asof,
  subtitle,
  action,
  children,
  wide = false,
}: {
  current: string;
  title: string;
  scope?: ReactNode;
  asof?: ReactNode;
  subtitle?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  //: Tables and prose read better capped at 1280px; a canvas of tiles just
  //: loses screen space to it. Opt in per page rather than widening every
  //: page, since most of them are the former.
  wide?: boolean;
}) {
  return (
    <div className="shell">
      <Rail current={current} />
      <main>
        <div className={`mx-auto pt-5 pb-12 ${wide ? "max-w-[1880px] px-4" : "max-w-[1280px] px-6"}`}>
      <div className="mb-4 flex flex-wrap items-baseline gap-x-3.5 gap-y-2">
        <h1 className="m-0 text-[18px] font-semibold tracking-[-0.01em]">{title}</h1>
        {scope && (
          <span className="border-l border-rule pl-3.5 text-[12px] text-ink-3">{scope}</span>
        )}
        <div className="flex-1" />
        <ProjectSwitcher />
        {asof && <span className="text-[12px] text-ink-3">{asof}</span>}
        {action}
      </div>
      {subtitle && <p className="mt-0 mb-4 text-[13px] text-ink-2">{subtitle}</p>}
      {children}
        </div>
      </main>
    </div>
  );
}

/* A 12-column board, so panels sit side by side the way a dashboard does
   instead of stacking full width. Collapses to one column when narrow -
   `col` spans are ignored below the breakpoint by `Panel`. */
export function Board({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={`grid gap-3.5 md:[grid-template-columns:repeat(12,minmax(0,1fr))] ${className}`}
    >
      {children}
    </div>
  );
}

/* One panel on the board: a captioned card spanning `span` of 12 columns. The
   caption is the micro-label the whole app uses, defined once here. */
export function Panel({
  caption,
  span = 12,
  children,
  className = "",
}: {
  caption?: string;
  span?: number;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`rounded-lg border border-rule bg-surface p-4 ${SPAN[span] ?? ""} ${className}`}
    >
      {caption && <div className="mb-3 text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">{caption}</div>}
      {children}
    </div>
  );
}

/* Spelled out rather than interpolated: Tailwind scans source text, so a
   computed `md:col-span-${n}` produces no CSS at all. */
const SPAN: Record<number, string> = {
  3: "md:col-span-3",
  4: "md:col-span-4",
  5: "md:col-span-5",
  6: "md:col-span-6",
  7: "md:col-span-7",
  8: "md:col-span-8",
  9: "md:col-span-9",
  12: "md:col-span-12",
};

export function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="mb-6">
      {title && (
        <h2 className="mt-6 mb-2 text-xs font-bold tracking-[0.08em] text-ink-3 uppercase">
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-rule bg-surface p-4 ${className}`}>
      {children}
    </div>
  );
}

export function Stats({ children }: { children: ReactNode }) {
  return (
    <div className="mb-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
      {children}
    </div>
  );
}

/* `tabular-nums` is deliberately absent on these: equal-width digits make a
   large standalone figure look mechanical. */
export function Stat({
  value,
  label,
  bad = false,
}: {
  value: ReactNode;
  label: string;
  bad?: boolean;
}) {
  return (
    <div className="rounded-lg border border-rule bg-surface px-3.5 py-3">
      <b className={`block text-[23px] leading-none font-bold ${bad ? "text-red" : ""}`}>
        {value}
      </b>
      <span className="mt-1.5 block text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
        {label}
      </span>
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-[12.5px]">
      {children}
    </div>
  );
}

export function Problem({
  title,
  detail,
  fix,
}: {
  title: string;
  detail: string;
  fix?: string;
}) {
  return (
    <Card>
      <p className="m-0 font-semibold text-red">{title}</p>
      <p className="mt-1 mb-0 text-[13px] text-ink-2">{detail}</p>
      {fix && (
        <pre className="mt-3 mb-0 overflow-x-auto rounded bg-bg p-2.5 font-mono text-xs text-ink">
          {fix}
        </pre>
      )}
    </Card>
  );
}
