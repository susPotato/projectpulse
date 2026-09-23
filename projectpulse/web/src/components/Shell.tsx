/*
  The chrome every page shares: tab bar, headings, cards, stat tiles.

  Small deliberate components rather than one big layout, because the two pages
  differ in body and agree on everything around it - and the tab bar must agree
  across the *static* pages too, so its links are spelled out here once.
*/
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  currentProject,
  load,
  projectLink,
  rememberProject,
  withProject,
  type PortfolioBundle,
  type ProjectRow,
} from "../api";
import { initials, session, signOut } from "../auth";
import { ProjectPicker } from "./ProjectPicker";
import { CountUp, Skeleton } from "./motion";

/* One list, and the hand-written pages carry the same one. `tests/test_api.py`
   asserts they match, because three copies of a nav is how the six original
   mockups ended up with two conflicting token families.

   The retriever console and the Calc page used to sit in here. Both are
   gone - the console because it POSTed schema-dropping actions from an
   unauthenticated page, Calc because the arithmetic it showed is now only
   read where it is used, in the report's projection section. */
const TABS = [
  { href: "/programs", label: "Program" },
  { href: "/projects", label: "Projects" },
  { href: "/gantt", label: "Schedule" },
  { href: "/insight", label: "Insight" },
  /* Traceability is served as a hand-written page, not by this bundle (it
     reads another repository's run directory - see app/api/tracelink_view.py).
     It still belongs in the rail: a page reachable only by typing its URL is
     a page nobody opens. A plain <a> leaves the bundle and that is correct. */
  { href: "/traceability", label: "Trace" },
  /* Also hand-written, and for the same reason: it reads the tool layer
     directly to show what Jira said before this product maps it. */
  { href: "/jira", label: "Jira" },
  { href: "/risk", label: "Risk" },
  { href: "/team", label: "Team" },
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
  "/reports": (
    <>
      <path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" />
      <path d="M14 3v5h5M9 13h6M9 17h4" />
    </>
  ),
  "/traceability": (
    <>
      <path d="M9 6h11M9 12h11M9 18h11" />
      <circle cx="4" cy="6" r="1.6" />
      <circle cx="4" cy="12" r="1.6" />
      <circle cx="4" cy="18" r="1.6" />
    </>
  ),
  "/jira": (
    <>
      <rect x="3" y="4" width="18" height="6" rx="1" />
      <rect x="3" y="14" width="18" height="6" rx="1" />
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

/* Sun and moon, matching theme.js's icons so the switch looks identical
   whether React drew it or a hand-written page did. Each shows the mode a
   click switches *to*, not the one currently active. */
const SUN_ICON = (
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </>
);
const MOON_ICON = <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />;

/* Reads its initial state from `<html data-theme>`, which the inline script
   in index.html (and each hand-written page's <head>) has already set
   before this component mounts - so there is no flash of the wrong palette
   and no duplicate "which theme is it" logic here. */
function ThemeToggle() {
  const [theme, setTheme] = useState<"light" | "dark">(() =>
    typeof document !== "undefined" &&
    document.documentElement.getAttribute("data-theme") === "dark"
      ? "dark"
      : "light",
  );

  function toggle() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try {
      localStorage.setItem("pulse-theme", next);
    } catch {
      // Private browsing / storage disabled: the switch still works for
      // this page view, it just will not be remembered.
    }
    setTheme(next);
  }

  const label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggle}
      aria-pressed={theme === "dark"}
      aria-label={label}
      title={label}
    >
      <svg viewBox="0 0 24 24">{theme === "dark" ? SUN_ICON : MOON_ICON}</svg>
    </button>
  );
}

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
      <ThemeToggle />
      {/* `withProject`, not a bare href. Every rail click used to drop
          `?project=` from the address bar. The selection survived in storage so
          the next page still showed the right project, which is exactly why
          nobody noticed: what broke was the *URL*, which stopped describing
          what was on screen, so a link sent to a colleague opened on whatever
          the server defaulted to rather than on what the sender was looking
          at. `/settings` and `/agent` ignore the param; carrying it anyway
          keeps one rule here instead of a list of exceptions to maintain. */}
      {TABS.map((tab) => (
        <a
          key={tab.href}
          href={withProject(tab.href)}
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
  /* `.subtabs` from `shell.css`, not a second copy of it in Tailwind. The
     hand-written pages already had this row and this one had drifted from
     it by two pixels of padding; more to the point, the sliding underline
     now lives in that stylesheet, so a Tailwind reimplementation here would
     be a tab bar that moves on Traceability and blinks on Insight. */
  return (
    <nav className="subtabs" aria-label="Views">
      {views.map((view) => (
        <button
          key={view}
          type="button"
          onClick={() => onSelect(view)}
          aria-current={view === current ? "page" : undefined}
        >
          {view}
        </button>
      ))}
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

  /* Resolving a default is not the same as *having* one. Showing
     `projects[0]` while the page fetched with no `?project=` left the server
     free to answer about a different project entirely - see
     `default_project_id` in `app/api/main.py`. Writing it down the moment the
     portfolio arrives makes the displayed name and the fetched numbers the
     same claim, and costs one storage write per first visit. */
  useEffect(() => {
    if (!bundle || bundle.projects.length === 0) return;
    if (currentProject()) return;
    const first = bundle.projects[0];
    if (!first) return;
    rememberProject({
      id: first.project_id,
      also: first.source_ids.filter((id) => id !== first.project_id),
    });
  }, [bundle]);

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

/* Who is signed in, at the right-hand end of the app bar.

   Styled by `shell.css` rather than Tailwind, for the same reason `.appbar`
   and `.theme-toggle` are: the hand-written pages draw this same control from
   `auth.js`, and two implementations of one piece of chrome drift. The initials
   are `auth.ts`'s, so the avatar here and the one on Schedule cannot disagree
   about how a name becomes two letters.

   A menu rather than a bare sign-out button: the avatar's job is to say *who*,
   and a control that logs you out when you click it to check your own name is
   a trap. */
function Avatar() {
  const user = session();
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  /* Pointer down, not click: a `click` listener fires after the button's own
     handler has already re-opened the menu, so an outside click while open
     would close and reopen in the same gesture. */
  useEffect(() => {
    if (!open) return;
    function away(event: PointerEvent) {
      if (!wrap.current?.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  // The gate in `main.tsx` means this is never null in practice. Rendering
  // nothing rather than an empty circle is still the right answer if some
  // future page mounts the shell outside it.
  if (!user) return null;

  return (
    <div className="avatar-wrap" ref={wrap}>
      <button
        type="button"
        className="avatar"
        onClick={() => setOpen((was) => !was)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`Signed in as ${user.name}`}
        title={`Signed in as ${user.name}`}
      >
        {initials(user.name)}
      </button>
      {open && (
        <div className="avatar-menu" role="menu">
          <div className="avatar-who">
            <b>{user.name}</b>
            <span>{user.role ? `${user.role} · ${user.id}` : user.id}</span>
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              signOut();
              /* A full navigation, not a state change. Signing out has to
                 clear whatever the current page has already fetched about a
                 project, and `location.replace` keeps the signed-in page out
                 of the back history. */
              window.location.replace("/");
            }}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
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
  // Every page routes its title and scope through here, so this is the one
  // place the browser tab needs to know about either - no per-page copy to
  // forget. `scope` is typed as `ReactNode` for callers that might someday
  // pass markup, but every current one passes a plain string; a future JSX
  // one just leaves the tab on `title` alone rather than stringifying markup
  // into it.
  useEffect(() => {
    const withScope = typeof scope === "string" && scope ? `${title} · ${scope}` : title;
    document.title = `${withScope} — ProjectPulseAI`;
  }, [title, scope]);

  return (
    <div className="shell">
      <Rail current={current} />
      <main>
        <div className={`mx-auto pt-5 pb-12 ${wide ? "max-w-[1880px] px-4" : "max-w-[1280px] px-6"}`}>
      {/* `.appbar` from the shared stylesheet, not a second copy of it in
          Tailwind. This header and the hand-written pages' header were the
          same bar written twice, and they had drifted: this one aligned on
          `baseline` and carried `mb-4`, that one aligned on `center` and
          carried 16px of margin top *and* bottom. The result was a title and
          a project picker that moved 16px down the page when you clicked
          Schedule and back up when you left it. One rule cannot drift from
          itself. */}
      <div className="appbar">
        <h1>{title}</h1>
        {scope && <span className="scope">{scope}</span>}
        <span className="spacer" />
        <ProjectSwitcher />
        {asof && <span className="asof">{asof}</span>}
        {action}
        {/* Last in the bar, after the page's own action. Identity is chrome,
            not a thing this page does. */}
        <Avatar />
      </div>
      {subtitle && <p className="mt-0 mb-4 text-body text-ink-2">{subtitle}</p>}
      {/* The body arrives as one block rather than per-panel. A page whose
          every card fades in separately reads as a page still loading; one
          settle, once, reads as a page that has arrived. Keyed on the title
          so moving between screens replays it - which is the only cue this
          app gives that a navigation happened at all, the rail being
          otherwise identical on both sides of the click. */}
      <div key={title} className="rise">{children}</div>
        </div>
      </main>
    </div>
  );
}

/* A 12-column board, so panels sit side by side the way a dashboard does
   instead of stacking full width. Collapses to one column when narrow -
   `col` spans are ignored below the breakpoint by `Panel`.

   `stagger` puts the panels on screen in reading order, about a twentieth of
   a second apart. It is the one place this app uses a sequence rather than a
   single settle, and the reason is that a board is a *set* - the order they
   land in is the order they should be read in, and a simultaneous arrival
   says they are interchangeable. */
export function Board({
  children,
  className = "",
  stagger = true,
}: {
  children: ReactNode;
  className?: string;
  stagger?: boolean;
}) {
  return (
    <div
      className={`grid gap-3.5 md:[grid-template-columns:repeat(12,minmax(0,1fr))] ${
        stagger ? "stagger" : ""
      } ${className}`}
    >
      {children}
    </div>
  );
}

/* One panel on the board: a captioned card spanning `span` of 12 columns. The
   caption is the micro-label the whole app uses, defined once here.

   `action` is the slot that stopped panels growing their own headers. Three
   of them had a link floated to the right of the caption, each with its own
   spelling of the same flex row. */
export function Panel({
  caption,
  span = 12,
  action,
  children,
  className = "",
}: {
  caption?: string;
  span?: number;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      /* `min-w-0` is the grid-item fix, and it is load-bearing rather than
         defensive. A grid item's default `min-width: auto` refuses to go
         below its content's intrinsic width, so one un-truncatable child -
         a long ticket title, a file path - pushes the panel past its track
         and the whole document scrolls sideways. On a phone that was 793px
         of horizontal scroll on the Insight page alone. Zero lets the
         track win and lets the `truncate` inside actually truncate. */
      className={`lift min-w-0 rounded-lg border border-rule bg-surface p-4 ${SPAN[span] ?? ""} ${className}`}
    >
      {(caption || action) && (
        <div className="mb-3 flex items-baseline justify-between gap-3">
          {caption && <div className={MICRO_LABEL}>{caption}</div>}
          {action}
        </div>
      )}
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

/* The micro-label, once. Panel captions, section headings, column headers
   and KPI labels are the same typographic object and were four near-copies
   of it, differing by a hundredth of an em of tracking. */
export const MICRO_LABEL =
  "text-label font-bold tracking-[0.05em] text-ink-3 uppercase";

export function Section({
  title,
  action,
  children,
}: {
  title?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mb-6">
      {(title || action) && (
        <div className="mt-6 mb-2 flex items-baseline justify-between gap-3">
          {title && <h2 className={`m-0 ${MICRO_LABEL}`}>{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

export function Card({
  children,
  className = "",
  lift = false,
}: {
  children: ReactNode;
  className?: string;
  /* Off by default. A card that reacts to the pointer is making a promise
     that it does something when clicked, so only the ones that do get it. */
  lift?: boolean;
}) {
  return (
    <div
      className={`min-w-0 rounded-lg border border-rule bg-surface p-4 ${lift ? "lift" : ""} ${className}`}
    >
      {children}
    </div>
  );
}

export function Stats({ children }: { children: ReactNode }) {
  return (
    <div className="stagger mb-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(164px,1fr))]">
      {children}
    </div>
  );
}

const STAT_TONE: Record<string, string> = {
  good: "text-green",
  warn: "text-amber",
  bad: "text-red",
  plain: "text-ink",
};

/* One figure, its name, and - where there is one - the way into the rows
   behind it.

   Three things changed here and each was a real complaint about the old
   tile. The figure counts up, because a dashboard of eight numbers that all
   appear at once is eight numbers nobody's eye lands on first. It is
   `tabular-nums` after all, which the old comment argued against: a figure
   that is *animating* and not monospaced reflows the tile on every frame,
   and a row of tiles whose labels jitter is far worse than a slightly
   mechanical digit. And `href` makes the number a door - the single most
   requested thing on this product, from a PM who could see "5 unassigned"
   and had no way to ask which five.

   `bad` is kept as an alias for `tone="bad"` so the existing call sites do
   not all have to change in the same commit. */
export function Stat({
  value,
  label,
  bad = false,
  tone,
  hint,
  href,
  foot,
}: {
  value: ReactNode;
  label: string;
  bad?: boolean;
  tone?: "good" | "warn" | "bad" | "plain";
  /* Method, caveat, breakdown - whatever a reader would otherwise need the
     documentation for. On the tile as a tooltip, never as a second line of
     body text competing with the figure. */
  hint?: string;
  href?: string;
  foot?: ReactNode;
}) {
  const shade = STAT_TONE[tone ?? (bad ? "bad" : "plain")] ?? "text-ink";
  const body = (
    <>
      <b className={`block text-kpi font-bold ${shade}`}>
        {typeof value === "number" ? <CountUp value={value} /> : value}
      </b>
      <span className={`mt-1.5 block ${MICRO_LABEL}`}>{label}</span>
      {foot && <span className="mt-1 block text-label text-ink-3">{foot}</span>}
    </>
  );

  const shell = "block min-w-0 rounded-lg border border-rule bg-surface px-3.5 py-3";
  return href ? (
    <a href={href} title={hint} className={`lift ${shell} no-underline`}>
      {body}
    </a>
  ) : (
    <div title={hint} className={shell}>
      {body}
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-body">
      {children}
    </div>
  );
}

/* Rule 7's third and fourth states, told apart in the markup rather than
   left to the sentence inside them.

   `NoInput` is "this was never supplied" - a dashed edge, because a dashed
   edge is what a placeholder looks like everywhere else in software.
   `AllClear` is "we looked and it is fine" - a solid card with a green mark.
   A PM must never have to read the sentence to know which of the two they
   are looking at. */
export function NoInput({ what, how }: { what: string; how?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-rule bg-bg px-4 py-5">
      <p className="m-0 text-body font-semibold text-ink-2">{what}</p>
      {how && <p className="mt-1 mb-0 text-body text-ink-3">{how}</p>}
    </div>
  );
}

export function AllClear({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2.5 rounded-lg border border-green/40 bg-green/8 px-4 py-3">
      <svg
        viewBox="0 0 24 24"
        aria-hidden="true"
        className="h-4 w-4 shrink-0 fill-none stroke-green stroke-2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="m5 13 4 4L19 7" />
      </svg>
      <span className="text-body text-ink-2">{children}</span>
    </div>
  );
}

/* Rule 7's first state, re-exported so a page never has to reach past the
   shell for it. */
export { Skeleton, SkeletonStats } from "./motion";

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
      <p className="mt-1 mb-0 text-body text-ink-2">{detail}</p>
      {fix && (
        <pre className="mt-3 mb-0 overflow-x-auto rounded bg-bg p-2.5 font-mono text-label text-ink">
          {fix}
        </pre>
      )}
    </Card>
  );
}

/* The waiting state for a whole page, at the shape of one. Used by every
   page's `if (!bundle)` branch, which until now returned the word
   "Loading..." under a title and let the layout jump when the real content
   landed. */
export function PageSkeleton() {
  return (
    <>
      <div className="mb-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(164px,1fr))]">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="rounded-lg border border-rule bg-surface px-3.5 py-3">
            <div className="skeleton h-7 w-16" />
            <div className="skeleton mt-2 h-2.5 w-24" />
          </div>
        ))}
      </div>
      <div className="grid gap-3.5 md:[grid-template-columns:repeat(12,minmax(0,1fr))]">
        {[7, 5].map((span) => (
          <div
            key={span}
            className={`rounded-lg border border-rule bg-surface p-4 ${SPAN[span]}`}
          >
            <div className="skeleton mb-3 h-2.5 w-28" />
            <Skeleton rows={4} />
          </div>
        ))}
      </div>
    </>
  );
}
