/*
  Who is carrying what, and what has moved.

  Every figure comes from a column a PM actually fills in, and one panel still
  says what the source cannot support. See `api/schemas/team.py` for the full
  reasoning.

  The burn chart used to be in that "cannot support" list: the worklog had
  actual hours, no planned column, and no date on either. Both columns were
  added to the blank template we own - the same move that closed the
  dependency-edge gap by adding `Predecessor`. What was deliberately NOT done
  is derive the log dates from scan windows: a scan interval bounds when we
  noticed an edit, never when the work happened.

  Colour: the two fills are `--viz-plan` and `--viz-over`, the pair `gantt.css`
  already declares and which was validated against this surface in both modes
  (all six dataviz checks pass). Reusing them rather than introducing a
  categorical palette keeps one meaning per hue across the whole app - blue is
  always the plan, red is always the overrun the chain implies. A person is
  identified by their row label, never by a hue, so no legend is needed for
  identity and no new palette needed validating.
*/
import { useEffect, useState } from "react";
import {
  load,
  type ApiProblem,
  type BurnSeries,
  type Member,
  type MemberTask,
  type TeamBundle,
  withProject,
} from "../api";
import { Board, Card, Page, Panel, Problem, Section, Stat, Stats } from "../components/Shell";
import { HeatStrip, SplitRows } from "../components/charts";

/** Days between two ISO dates, or null if either is missing. */
function days(from?: string | null, to?: string | null): number | null {
  if (!from || !to) return null;
  return (Date.parse(to) - Date.parse(from)) / 86_400_000;
}

/* Dated ticks across the shared window.

   The chart labelled only its two ends, so a bar's position carried no date
   a reader could name: four tasks running 12-31 August sat somewhere in the
   left third of a window captioned "2026-08-10" and "2026-10-02", and the
   only way to learn when they ran was to hover one. An axis that cannot be
   read is the same as no axis.

   Weekly below ten weeks, monthly above, because a two-month window with
   month ticks alone gets two gridlines and is no better than the endpoints
   it replaced. */
function scaleTicks(start: string, span: number): { at: number; label: string }[] {
  const from = new Date(`${start}T00:00:00`);
  if (!Number.isFinite(from.getTime()) || span <= 0) return [];

  const weekly = span <= 70;
  const cursor = new Date(from);
  if (weekly) {
    // Next Monday, so ticks land on a weekday boundary a person recognises.
    cursor.setDate(cursor.getDate() + ((8 - cursor.getDay()) % 7 || 7));
  } else {
    cursor.setDate(1);
    cursor.setMonth(cursor.getMonth() + 1);
  }

  const ticks: { at: number; label: string }[] = [];
  while (ticks.length < 24) {
    const at = ((cursor.getTime() - from.getTime()) / 86_400_000 / span) * 100;
    if (at >= 100) break;
    ticks.push({
      at,
      label: weekly
        ? `${cursor.getDate()} ${cursor.toLocaleDateString("en", { month: "short" })}`
        : cursor.toLocaleDateString("en", { month: "short", year: "2-digit" }),
    });
    if (weekly) cursor.setDate(cursor.getDate() + 7);
    else cursor.setMonth(cursor.getMonth() + 1);
  }
  return ticks;
}

/* One member's dated tasks on the shared window. Bars are positioned as a
   percentage of the window so two rows are comparable by eye - which is the
   only reason to share an axis at all. */
/* Work with no drawable bar, listed rather than omitted.

   A task reaches here for one of two reasons and they mean opposite things:
   it never started, or it started before the history we hold. Saying which
   costs one word and saves the reader guessing. */
/* Ordering a roster. 190 tasks across six people is past the point where
   "the order the server sent them" is an answer, and the two questions a
   lead actually asks are "who is carrying the most" and "who is stuck". */
const MEMBER_SORTS = [
  ["load", "most work"],
  ["late", "most overdue"],
  ["dated", "most scheduled"],
  ["name", "name"],
] as const;

type MemberSort = (typeof MEMBER_SORTS)[number][0];

/* Which tasks a member is shown as carrying. `all` is the honest default -
   a filter that hides finished work makes a busy week look empty - but the
   other two are how somebody finds the thing they came for. */
const TASK_FILTERS = [
  ["all", "all tasks"],
  ["open", "not finished"],
  ["overdue", "past due"],
  ["done", "finished"],
] as const;

type TaskFilter = (typeof TASK_FILTERS)[number][0];

function keeps(task: MemberTask, filter: TaskFilter) {
  if (filter === "open") return !task.closed;
  if (filter === "done") return task.closed;
  if (filter === "overdue") {
    return !task.closed && (task.days_past_due ?? 0) > 0;
  }
  return true;
}

function matches(task: MemberTask, query: string) {
  if (!query) return true;
  return [task.label, task.title, task.phase]
    .join(" ")
    .toLowerCase()
    .includes(query);
}

function overdueCount(member: Member) {
  return member.tasks.filter((t) => !t.closed && (t.days_past_due ?? 0) > 0)
    .length;
}

function orderMembers(members: Member[], sort: MemberSort) {
  const copy = members.slice();
  if (sort === "name") {
    copy.sort((a, b) => a.name.localeCompare(b.name));
  } else if (sort === "late") {
    copy.sort((a, b) => overdueCount(b) - overdueCount(a));
  } else if (sort === "dated") {
    const dated = (m: Member) =>
      m.tasks.filter((t) => t.start && t.planned_end).length;
    copy.sort((a, b) => dated(b) - dated(a));
  } else {
    copy.sort((a, b) => b.tasks.length - a.tasks.length);
  }
  return copy;
}

function why(task: MemberTask) {
  if (task.closed) return "finished, no start recorded";
  if (task.start) return "started, no due date";
  if (task.planned_end) return `due ${task.planned_end}, not started`;
  return "no dates";
}

/* How late, as its own figure. `why` buried this: a row twenty days past its
   date read "due 2026-08-31, not started", which is the same sentence a row
   due tomorrow gets. The number is the whole triage. */
function lateDays(task: MemberTask): number {
  return task.closed ? 0 : (task.days_past_due ?? 0);
}

/* How many undated rows to print before folding the rest away. Eleven
   Vietnamese titles at full length filled a screen on their own, and this
   panel is a summary of one person's week, not a backlog export. */
const FEW = 3;

function UndatedList({
  tasks,
  expanded = false,
}: {
  tasks: MemberTask[];
  /* Opened by the caller when the reader asked for exactly this list. Folding
     a list somebody clicked a number to see is asking them to click twice for
     one answer. */
  expanded?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const showAll = open || expanded;
  const shown = showAll ? tasks : tasks.slice(0, FEW);
  const rest = tasks.length - shown.length;

  /* Worst first. A list of sixteen late rows in sheet order makes the reader
     scan for the big number; sorted, the first line is the answer. */
  const ordered = shown
    .slice()
    .sort((a, b) => lateDays(b) - lateDays(a));

  return (
    <div className="grid gap-0.5 py-1">
      {ordered.map((t) => (
        <div key={t.entity_id} className="truncate text-body text-ink-3">
          {lateDays(t) > 0 && (
            <b className="mr-1.5 font-semibold text-red tabular-nums">
              {lateDays(t)}d late
            </b>
          )}
          <b className="font-semibold text-ink-2">{t.label}</b>
          {t.title && t.title !== t.label && (
            <span className="ml-1.5">{t.title}</span>
          )}
          <span className="ml-1.5 italic">{why(t)}</span>
        </div>
      ))}
      {!expanded && (rest > 0 || open) && (
        <button
          type="button"
          onClick={() => setOpen(!open)}
          className="w-fit cursor-pointer border-0 bg-transparent p-0 text-label text-blue underline"
        >
          {open ? "show fewer" : `${rest} more`}
        </button>
      )}
    </div>
  );
}


function WorkloadRow({
  member,
  start,
  span,
  filter,
  query,
  expanded = false,
}: {
  member: Member;
  start: string;
  span: number;
  filter: TaskFilter;
  query: string;
  expanded?: boolean;
}) {
  /* Filter once, here, so the bars and the folded list below them are the
     same set of tasks. Filtering them separately is how a row comes to say
     "2 dated" above a list of eleven. */
  const tasks = member.tasks.filter(
    (t) => keeps(t, filter) && matches(t, query),
  );
  const dated = tasks.filter((t) => t.start && t.planned_end);
  /* Everything else. A bar needs both ends, but "has no start date" is not
     the same as "has no work": thirteen To Do tasks three weeks past their
     due date were reading as an empty row, which is the one reading that
     makes an unstarted backlog look like nothing to worry about. */
  const undated = tasks.filter((t) => !(t.start && t.planned_end));

  return (
    <div className="flex items-start gap-3 border-b border-rule-2 py-2 last:border-b-0">
      <div className="w-[150px] shrink-0">
        <div className="truncate text-body font-semibold">{member.name}</div>
        <div className="mt-0.5 text-label text-ink-3">
          {dated.length > 0
            ? `${dated.length} dated task(s)`
            : member.qa_items > 0
              ? `${member.qa_items} QA item(s)`
              : "nothing dated"}
        </div>
      </div>

      {/* Space reserved on the right for the end label. A bar finishing near
          the window's end otherwise pushed its own label off the panel - the
          worst slip was the one figure that could not be read, which is the
          same mistake the Gantt already fixed once. */}
      <div className="min-w-0 flex-1 pr-[76px]">
        {dated.length === 0 && undated.length === 0 ? (
          <div className="py-2 text-body text-ink-3 italic">
            No task recorded for this person
            {member.qa_items > 0 && " - this person appears only on the worklog"}
          </div>
        ) : dated.length === 0 ? (
          <UndatedList tasks={undated} expanded={expanded} />
        ) : (
          <div className="grid gap-1.5">
            {dated.map((task) => {
              const offset = ((days(start, task.start) ?? 0) / span) * 100;
              const planned = ((days(task.start, task.planned_end) ?? 0) / span) * 100;

              /* Two different tails, and they must not be added together.
                 `propagated_days` is forward-looking: the chain says this
                 row will land later than its own dates admit.
                 `days_past_due` is backward-looking and factual: the date
                 went by and the row is still open. Both start at the
                 planned finish, so drawing both would double-count the same
                 stretch of calendar. The longer one is drawn and named; the
                 tooltip carries whichever is not. */
              const chain =
                task.propagated_days && task.propagated_days > 0
                  ? task.propagated_days
                  : 0;
              const late = task.days_past_due && task.days_past_due > 0
                ? task.days_past_due
                : 0;
              const isLate = late > chain;
              const over = (Math.max(chain, late) / span) * 100;

              /* A finished task is green. `days_past_due` is only ever set on
                 an open row, so a closed one has no red tail to draw and the
                 whole bar is the work that got done.

                 Landing late is still green, deliberately: the bar says what
                 happened, and "finished, 3 days after the date" is a
                 completed task however it reads on a report. The label
                 carries the number so the fact is not lost in the colour. */
              const done = task.closed && !!task.actual_end;
              const early = task.finished_early_days;
              const fill = done ? "var(--viz-done)" : "var(--viz-plan)";

              return (
                <div key={task.entity_id} className="relative h-[18px]">
                  <div
                    className="absolute top-[5px] flex h-[8px] items-stretch"
                    style={{ left: `${offset}%`, width: `${planned + over}%` }}
                    title={`${task.label} ${task.title ?? ""} · plan ${task.start} to ${task.planned_end}${
                      chain > 0 ? ` · chain implies ${task.projected_end}` : ""
                    }${late > 0 ? ` · ${late}d past due, still open` : ""}`}
                  >
                    {/* 4px rounded data-ends; a 2px surface gap between the two
                        fills so the boundary reads as a boundary. */}
                    <span
                      className="rounded-l-[4px]"
                      style={{
                        width: `${(planned / (planned + over)) * 100}%`,
                        background: fill,
                      }}
                    />
                    {over > 0 && (
                      <span
                        className="rounded-r-[4px] border-l-2 border-surface"
                        style={{
                          width: `${(over / (planned + over)) * 100}%`,
                          /* Past due is the muted fill: it is a fact about
                             a date that has gone by, not the forecast the
                             brighter red is reserved for. */
                          background: "var(--viz-over)",
                          opacity: isLate ? 0.62 : 1,
                        }}
                      />
                    )}
                  </div>
                  {/* A keyless row is labelled by its summary, and a summary
                      can be a paragraph: one here is 302 characters of
                      deliverable spec that ran off the panel and over the
                      next column. Clipped to a readable stub; the title
                      attribute above still carries the whole thing. */}
                  <span
                    className="absolute top-0 max-w-[280px] overflow-hidden text-label text-ellipsis whitespace-nowrap text-ink-3"
                    style={{ left: `calc(${offset + planned + over}% + 6px)` }}
                  >
                    <b className="font-semibold text-ink-2">{task.label}</b>
                    {task.title && task.title !== task.label && (
                      <span className="ml-1.5">{task.title}</span>
                    )}
                    {done && early != null && early > 0 ? (
                      <b className="ml-1 font-semibold text-green">
                        {early}d early
                      </b>
                    ) : done && early != null && early < 0 ? (
                      <b className="ml-1 font-semibold text-ink-3">
                        done {-early}d late
                      </b>
                    ) : done ? (
                      <b className="ml-1 font-semibold text-green">on time</b>
                    ) : isLate ? (
                      <b className="ml-1 font-semibold text-red">{late}d late</b>
                    ) : (
                      chain > 0 && (
                        <b className="ml-1 font-semibold text-red">+{chain}d</b>
                      )
                    )}
                    {/* People the row names in its note field. Shown beside
                        the owner, not merged into it: the source says they
                        are named on this work and does not say who they
                        report to. */}
                    {task.also_named.length > 0 && (
                      <span className="ml-1.5 text-ink-3 italic">
                        with {task.also_named.join(", ")}
                      </span>
                    )}
                  </span>
                </div>
              );
            })}
            {undated.length > 0 && (
              <UndatedList tasks={undated} expanded={expanded} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* Logged against planned, per owner.

   Two bars on one scale, not a stack: they are the same quantity measured two
   ways, and stacking would read as a total of 107 hours where the honest
   reading is 84 planned of which 11 are done. The planned bar is a recessive
   track and the logged bar sits inside it, so "how much of my estimate have I
   burned" is answerable without arithmetic. */
/* ---- who is carrying what ---------------------------------------------

   The question this page kept failing to answer: one person is doing most of
   the work - is that a problem, and when did it happen?

   Two panels, because those are two questions and one chart cannot hold
   both. `Concentration` is "how is the load split, and in what state".
   `Closures` is "when did it actually land", which is the one that catches a
   hundred tickets being closed in an afternoon.
*/

/* Three states that sum to the total, so a stacked bar is honest arithmetic
   rather than three overlapping filters. A row is finished, or it is open
   and within its date, or it is open and past it - never two of those. */
function split(member: Member): [number, number, number] {
  let done = 0;
  let open = 0;
  let late = 0;
  for (const task of member.tasks) {
    if (task.closed) done += 1;
    else if ((task.days_past_due ?? 0) > 0) late += 1;
    else open += 1;
  }
  return [done, open, late];
}

function Concentration({
  bundle,
  onPick,
}: {
  bundle: TeamBundle;
  onPick: (member: string, filter: TaskFilter) => void;
}) {
  const ranked = bundle.members
    .map((m) => ({ member: m, values: split(m), total: m.tasks.length }))
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total);

  if (ranked.length === 0) return null;

  const all = ranked.reduce((n, r) => n + r.total, 0);
  const top = ranked[0];
  if (!top) return null;
  const share = Math.round((top.total / all) * 100);

  /* A threshold, not a vibe. Half the backlog on one person is the point at
     which "they are busy" becomes "the plan has a single point of failure",
     and the sentence says which of the two this is rather than leaving a
     bar chart to imply it. */
  const concentrated = share >= 50 && ranked.length > 1;

  return (
    <Panel caption="Who is carrying the work" span={12}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <b className={concentrated ? "text-emph font-semibold text-amber" : "text-emph font-semibold text-ink"}>
          {share}% of the work sits with {top.member.name}
        </b>
        <span className="text-body text-ink-2 tabular-nums">
          {top.total} of {all} items, across {ranked.length} people
        </span>
      </div>

      <SplitRows
        /* Segment index -> the filter that isolates it, matching the order
           of `parts` below. -1 is the row label: everything that person
           carries, unfiltered. */
        onPick={(name, part) =>
          onPick(name, part === 0 ? "done" : part === 1 ? "open" : part === 2 ? "overdue" : "all")
        }
        ariaLabel={`Work items per person: ${ranked
          .map((r) => `${r.member.name} ${r.total}`)
          .join(", ")}`}
        parts={[
          { label: "finished", tone: "good" },
          { label: "open, within date", tone: "info" },
          { label: "open, past due", tone: "bad" },
        ]}
        rows={ranked.map((r) => ({
          key: r.member.name,
          label: r.member.name,
          values: r.values,
          title: `${r.member.name}: ${r.values[0]} finished, ${r.values[1]} open, ${r.values[2]} past due`,
        }))}
      />

      <p className="mt-2 mb-0 text-label text-ink-3">
        Click a name for everything they carry, or a coloured segment for
        just that part of it.
      </p>

      {concentrated && (
        <p className="mt-3 mb-0 border-l-2 border-amber pl-3 text-body text-ink-2">
          Bars are drawn against the largest, so the others are as small as
          they look. Before reading this as one person out-delivering the
          team, check the timeline below: a large share closed on a single
          day is a tracker being tidied up, not a week of work.
        </p>
      )}
    </Panel>
  );
}

/* ---- when the work actually landed ------------------------------------ */

/** ISO day, UTC, so bucketing never shifts a date across a timezone. */
function isoDay(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/* Days while the window is short enough to read, weeks after that. The
   boundary is about labels, not about data: past a fortnight the day ticks
   collide and the strip stops being a timeline and becomes texture. */
function buckets(from: number, to: number): { keys: string[]; labels: string[]; byWeek: boolean } {
  const DAY = 86_400_000;
  const spanDays = Math.round((to - from) / DAY) + 1;
  const byWeek = spanDays > 21;
  const step = byWeek ? 7 * DAY : DAY;
  const keys: string[] = [];
  const labels: string[] = [];
  for (let t = from; t <= to; t += step) {
    keys.push(isoDay(t));
    labels.push(byWeek ? isoDay(t).slice(5) : isoDay(t).slice(8));
  }
  return { keys, labels, byWeek };
}

function Closures({ bundle }: { bundle: TeamBundle }) {
  const finished = bundle.members.flatMap((m) =>
    m.tasks
      .filter((t) => t.actual_end)
      .map((t) => ({ who: m.name, at: Date.parse(t.actual_end as string) })),
  );

  const withStart = bundle.members.reduce(
    (n, m) => n + m.tasks.filter((t) => t.start).length,
    0,
  );
  const allTasks = bundle.members.reduce((n, m) => n + m.tasks.length, 0);

  if (finished.length === 0) {
    return (
      <Panel caption="When the work landed" span={12}>
        <p className="m-0 text-body text-ink-2">
          No task in this project has a recorded completion date, so there is
          nothing to place on a timeline. Completion dates come from the
          tracker&rsquo;s changelog &mdash; a project imported from a
          spreadsheet alone will not have them.
        </p>
      </Panel>
    );
  }

  const from = Math.min(...finished.map((f) => f.at));
  const to = Math.max(...finished.map((f) => f.at));
  const { keys, labels, byWeek } = buckets(from, to);
  const index = new Map(keys.map((k, i) => [k, i]));

  function bucketOf(at: number): number {
    if (!byWeek) return index.get(isoDay(at)) ?? 0;
    // Weeks: fall into the last bucket that starts on or before this day.
    let last = 0;
    for (let i = 0; i < keys.length; i += 1) {
      if (Date.parse(keys[i] as string) <= at) last = i;
    }
    return last;
  }

  const rows = bundle.members
    .map((m) => {
      const cells = new Array(keys.length).fill(0) as number[];
      for (const task of m.tasks) {
        if (!task.actual_end) continue;
        // Clamped rather than trusted: `bucketOf` derives an index from a
        // date, and a date outside the window it was built from would write
        // past the end of the row and silently drop the count.
        const at = Math.min(keys.length - 1, Math.max(0, bucketOf(Date.parse(task.actual_end))));
        cells[at] = (cells[at] ?? 0) + 1;
      }
      return {
        key: m.name,
        label: m.name,
        cells,
        total: cells.reduce((a, b) => a + b, 0),
      };
    })
    .filter((r) => r.total > 0)
    .sort((a, b) => b.total - a.total);

  /* Absence is a result. A person with tasks and no completions drops out
     of the strip entirely, and a chart that quietly omits the people who
     have delivered nothing is a chart that flatters the team. */
  const silent = bundle.members
    .filter((m) => m.tasks.length > 0 && !m.tasks.some((t) => t.actual_end))
    .map((m) => `${m.name} (${m.tasks.length})`);

  /* The finding this panel exists to surface. A person whose completions are
     nearly all in one bucket did not work through them one at a time, and a
     PM reading "151 done" without this would credit a month of delivery to
     an afternoon of housekeeping. */
  const spikes = rows
    .map((r) => {
      const peak = Math.max(...r.cells);
      // The full date, not the axis tick. A tick reads "20" because the
      // column beside it reads "19"; a sentence quoting "(20)" on its own
      // names nothing.
      return { name: r.label, peak, total: r.total, at: keys[r.cells.indexOf(peak)] };
    })
    /* Two conditions, and the volume one is what stops this crying wolf.
       "5 of 5 in one day" is a plausible good day and the first draft
       flagged it in the same words as "134 of 135", which is the fastest
       way to teach a reader to skip a warning. Ten is the point where a
       single day stops being a plausible amount of finished work. */
    .filter((s) => s.peak >= 10 && s.peak / s.total >= 0.6);

  return (
    <Panel caption="When the work landed" span={12}>
      <p className="m-0 mb-3 text-body text-ink-2">
        Each cell is one {byWeek ? "week" : "day"}, shaded by how many items
        that person finished in it &mdash;{" "}
        <b className="font-semibold text-ink tabular-nums">{isoDay(from)}</b> to{" "}
        <b className="font-semibold text-ink tabular-nums">{isoDay(to)}</b>.
      </p>

      <HeatStrip
        rows={rows}
        columns={labels}
        ariaLabel={`Completions per person over ${labels.length} ${byWeek ? "weeks" : "days"}: ${rows
          .map((r) => `${r.label} ${r.total}`)
          .join(", ")}`}
      />

      {silent.length > 0 && (
        <p className="mt-3 mb-0 border-l-2 border-red pl-3 text-body text-ink-2">
          <b className="text-ink">
            Nothing finished in this window: {silent.join(", ")}
          </b>
          . They are not on the chart because they have no completions to
          plot &mdash; which is the finding, not a gap in the data.
        </p>
      )}

      {spikes.map((spike) => (
        <p
          key={spike.name}
          className="mt-3 mb-0 border-l-2 border-amber pl-3 text-body text-ink-2"
        >
          <b className="text-ink">
            {spike.name}: {spike.peak} of {spike.total} finished in one{" "}
            {byWeek ? "week" : "day"}
          </b>{" "}
          ({spike.at}). That is a batch update to the tracker, not{" "}
          {spike.peak} separate pieces of work &mdash; so their completion
          count says when the board was tidied, not when the work happened.
        </p>
      ))}

      {/* The limitation, stated where the chart is, not in a footnote at the
          bottom of the page. This is the panel a reader would otherwise
          mistake for a Gantt. */}
      <p className="mt-3 mb-0 text-label text-ink-3">
        Completions only. {withStart} of {allTasks} tasks carry a recorded
        start date, so no duration can be drawn for the rest &mdash; a bar
        from an assumed start would be a picture of an assumption.
      </p>
    </Panel>
  );
}

function Hours({ members }: { members: Member[] }) {
  const withEffort = members.filter((m) => m.hours_planned > 0 || m.hours_logged > 0);
  if (withEffort.length === 0) {
    return (
      <p className="m-0 text-body text-ink-2">
        No source carried an <code className="font-mono">Estimate</code> or{" "}
        <code className="font-mono">Hours</code> column with anything in it, so there is
        no effort to show.
      </p>
    );
  }

  /* One scale across every row, taken from the largest planned figure, so two
     rows are comparable by eye. Scaling each row to its own maximum would make
     everybody look equally busy. */
  const most = Math.max(...withEffort.map((m) => Math.max(m.hours_planned, m.hours_logged)));

  return (
    <div className="grid gap-2">
      {withEffort.map((member) => (
        <div key={member.name} className="flex items-center gap-3">
          <span className="w-[104px] shrink-0 truncate text-body text-ink-2">
            {member.name}
          </span>
          <span className="relative h-[14px] min-w-0 flex-1">
            <span
              className="absolute top-[3px] h-[8px] rounded-[4px] bg-[var(--rule)]"
              style={{ width: `${(member.hours_planned / most) * 100}%` }}
              title={`${member.hours_planned}h planned`}
            />
            <span
              className="absolute top-[3px] h-[8px] rounded-[4px]"
              style={{
                width: `${(member.hours_logged / most) * 100}%`,
                background: "var(--viz-plan)",
              }}
              title={`${member.hours_logged}h logged`}
            />
          </span>
          <b className="w-[74px] shrink-0 text-right text-body font-semibold tabular-nums">
            {member.hours_logged}
            <span className="font-normal text-ink-3">/{member.hours_planned}h</span>
          </b>
        </div>
      ))}
      <div className="mt-1 flex flex-wrap gap-4 border-t border-rule pt-2">
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span className="h-2 w-4 rounded-[4px]" style={{ background: "var(--viz-plan)" }} />
          logged
        </span>
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span className="h-2 w-4 rounded-[4px] bg-[var(--rule)]" />
          planned
        </span>
      </div>
    </div>
  );
}

/* Cumulative logged effort against the plan it is burning.

   One axis - hours. The blocked count and the rows added are on wholly
   different scales and arrive as markers on the point that observed them,
   never as a second series with a second y-scale, which is the one chart
   mistake this project will not make.

   Two series, so a legend is always present: the logged line in `--viz-plan`
   and the planned total as a recessive dashed reference. The trailing stretch
   where nothing was logged is washed in `--viz-over` - the same red that means
   "later than planned" everywhere else in the app, because a stall is exactly
   that. No new hue is introduced, so no new palette needs validating. */
function Burn({ burn }: { burn: BurnSeries }) {
  if (burn.points.length < 2) {
    return (
      <p className="m-0 text-body text-ink-2">
        {burn.points.length === 0
          ? "The worklog sheet has not been scanned yet, so there is nothing to plot."
          : "One observation is a total, not a series. A second scan is what makes a burn chart possible."}
      </p>
    );
  }

  /* Wide and shallow on purpose. The logged line sits in the bottom fifth of
     the plot because 28 of 125 hours really is the bottom fifth, and the scale
     stays anchored to the plan for exactly that reason - auto-scaling to the
     data would fill the panel and hide the gap the chart is about. A tall
     viewBox turned that honest gap into a panel of empty air, so the aspect
     ratio absorbs it instead of the scale. */
  const W = 720;
  const H = 168;
  const PAD = { top: 16, right: 14, bottom: 26, left: 42 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  /* Scaled to the plan, not to the highest logged figure. Auto-scaling to the
     data would fill the panel with the logged line and hide the very thing
     the chart is for - how far short of the plan it is. */
  const top = Math.max(burn.planned_hours, burn.logged_hours) || 1;

  const x = (i: number) => PAD.left + (i / (burn.points.length - 1)) * plotW;
  const y = (hours: number) => PAD.top + plotH - (hours / top) * plotH;

  const line = burn.points.map((p, i) => `${x(i)},${y(p.logged_hours)}`).join(" ");
  const area = `${PAD.left},${y(0)} ${line} ${x(burn.points.length - 1)},${y(0)}`;

  const stallIndex = burn.stalled_from
    ? burn.points.findIndex((p) => p.observed_at === burn.stalled_from)
    : -1;

  /* Gridlines on a round step, not on fractions of the maximum: quartering
     125 gives 31 / 63 / 94, three numbers nobody would choose and all of them
     rounded away from where the line actually sits. `step` is the smallest of
     5/10/25/50/100 that keeps the axis under about six labels. */
  const step = [5, 10, 25, 50, 100, 250].find((n) => top / n <= 6) ?? 500;
  const ticks: number[] = [];
  for (let hours = 0; hours <= top; hours += step) ticks.push(hours);

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
        aria-label={`Cumulative logged effort reaching ${burn.logged_hours} of ${burn.planned_hours} planned hours`}>
        {ticks.map((hours) => (
          <g key={hours}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(hours)} y2={y(hours)}
              stroke="var(--rule)" strokeWidth={1} />
            <text x={PAD.left - 6} y={y(hours) + 3.5} textAnchor="end"
              className="fill-[var(--ink-3)] text-label tabular-nums">{hours}</text>
          </g>
        ))}

        {/* The stall, washed in behind everything. Drawn before the marks so it
            never sits on top of a value a reader is trying to read. */}
        {stallIndex > 0 && (
          <>
            <rect x={x(stallIndex)} y={PAD.top} width={x(burn.points.length - 1) - x(stallIndex)}
              height={plotH} fill="var(--viz-over)" opacity={0.13} />
            <line x1={x(stallIndex)} x2={x(stallIndex)} y1={PAD.top} y2={PAD.top + plotH}
              stroke="var(--viz-over)" strokeWidth={1} strokeDasharray="3 3" />
          </>
        )}

        {/* Planned effort: one value, because we never observed the plan change. */}
        <line x1={PAD.left} x2={W - PAD.right} y1={y(burn.planned_hours)} y2={y(burn.planned_hours)}
          stroke="var(--ink-3)" strokeWidth={2} strokeDasharray="6 4" />

        <polygon points={area} fill="var(--viz-plan)" opacity={0.16} />
        <polyline points={line} fill="none" stroke="var(--viz-plan)" strokeWidth={2}
          strokeLinejoin="round" />

        {burn.points.map((p, i) => (
          <g key={p.observed_at}>
            <circle cx={x(i)} cy={y(p.logged_hours)} r={4.5} fill="var(--viz-plan)"
              stroke="var(--panel)" strokeWidth={2}>
              <title>
                {`${p.observed_at}: ${p.logged_hours}h logged` +
                  (p.delta_hours ? ` (+${p.delta_hours}h)` : p.changed ? " (no effort logged)" : " (sheet untouched)") +
                  (p.items_added ? `, ${p.items_added} row(s) added` : "") +
                  (p.blocked_added ? `, ${p.blocked_added} newly blocked` : "")}
              </title>
            </circle>
            {/* Direct-labelled selectively: the ends and any point that moved.
                A number on every point is noise, not information. */}
            {(i === 0 || i === burn.points.length - 1 || p.delta_hours > 0) && (
              /* The end labels anchor inwards. Centred, the first sits on the
                 y-axis and the last runs past the plot edge - both read as
                 clipping rather than as a value. */
              <text x={x(i)} y={y(p.logged_hours) - 9}
                textAnchor={
                  i === 0 ? "start" : i === burn.points.length - 1 ? "end" : "middle"
                }
                className="fill-[var(--ink-1)] text-label font-semibold tabular-nums">
                {p.logged_hours}
              </text>
            )}
            <text x={x(i)} y={H - 8}
              textAnchor={
                i === 0 ? "start" : i === burn.points.length - 1 ? "end" : "middle"
              }
              className="fill-[var(--ink-3)] text-label tabular-nums">
              {p.observed_at.slice(5)}
            </text>
          </g>
        ))}
      </svg>

      <div className="mt-2 flex flex-wrap gap-4 border-t border-rule pt-2.5">
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span className="h-0.5 w-4" style={{ background: "var(--viz-plan)" }} />
          logged - cumulative, from observed changes
        </span>
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span className="h-0.5 w-4" style={{
            background: "repeating-linear-gradient(90deg, var(--ink-3) 0 6px, transparent 6px 10px)",
          }} />
          planned - {burn.planned_hours}h of estimates
        </span>
        {stallIndex > 0 && (
          <span className="flex items-center gap-1.5 text-label text-ink-3">
            <span className="h-2.5 w-4 rounded-sm" style={{ background: "var(--viz-over)", opacity: 0.4 }} />
            nothing logged since {burn.stalled_from}
          </span>
        )}
      </div>
    </>
  );
}

/* What actually moved, per week. The honest neighbour of a burn-down: it comes
   from the differ rather than from a self-reported percentage. Split by how
   precisely each change is dated, because that distinction is the product. */
function Activity({ bundle }: { bundle: TeamBundle }) {
  if (bundle.activity.length === 0) {
    return <p className="m-0 text-body text-ink-2">No state changes observed yet.</p>;
  }

  const most = Math.max(...bundle.activity.map((w) => w.changes));

  return (
    <>
      <div className="flex items-end gap-3" style={{ height: 130 }}>
        {bundle.activity.map((week) => (
          /* `h-full` is load-bearing: the column's height is a percentage, and
             a percentage resolves against the parent's height. Without it the
             parent is auto-height, every column computed to zero, and the panel
             showed its value labels above nothing at all. */
          <div
            key={week.week_start}
            className="flex h-full min-w-0 flex-1 flex-col items-center justify-end"
          >
            <b className="mb-1 text-label font-semibold tabular-nums">{week.changes}</b>
            <div
              className="flex w-full max-w-[52px] flex-col justify-end"
              style={{ height: `${(week.changes / most) * 100}%` }}
              title={`week of ${week.week_start}: ${week.exact} exact, ${week.bounded} bounded`}
            >
              {week.bounded > 0 && (
                <span
                  className="rounded-t-[4px]"
                  style={{
                    height: `${(week.bounded / week.changes) * 100}%`,
                    background: "var(--viz-plan)",
                  }}
                />
              )}
              {week.exact > 0 && (
                <span
                  className="border-t-2 border-surface"
                  style={{
                    height: `${(week.exact / week.changes) * 100}%`,
                    background: "var(--viz-over)",
                  }}
                />
              )}
            </div>
            <span className="mt-1.5 text-label whitespace-nowrap text-ink-3">
              {week.week_start.slice(5)}
            </span>
          </div>
        ))}
      </div>
      {/* Two series, so a legend is always present. */}
      <div className="mt-3 flex flex-wrap gap-4 border-t border-rule pt-2.5">
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span
            className="h-2.5 w-4 rounded-sm"
            style={{ background: "var(--viz-plan)" }}
          />
          bounded - seen by comparing two snapshots
        </span>
        <span className="flex items-center gap-1.5 text-label text-ink-3">
          <span
            className="h-2.5 w-4 rounded-sm"
            style={{ background: "var(--viz-over)" }}
          />
          exact - timestamped by a changelog
        </span>
      </div>
    </>
  );
}

export function TeamView({ bundle }: { bundle: TeamBundle }) {
  const [query, setQuery] = useState("");
  const [memberSort, setMemberSort] = useState<MemberSort>("load");
  const [taskFilter, setTaskFilter] = useState<TaskFilter>("all");
  /* Who the workload panel is narrowed to, set by clicking a bar above.
     Empty is everyone, which is the honest default - this is a team page
     before it is a person page. */
  const [focus, setFocus] = useState("");

  /* One number, one list. Clicking "16 past due" on Quan's bar has to land
     the reader on those sixteen rows and nothing else; anything short of
     that leaves them scrolling a hundred and fifty-one. The scroll is part
     of the answer: the list is a screen further down, and a filter that
     changes something off-screen reads as a control that did nothing. */
  function drillTo(member: string, filter: TaskFilter) {
    setFocus(member);
    setTaskFilter(filter);
    setQuery("");
    requestAnimationFrame(() => {
      document
        .getElementById("workload")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  const needle = query.trim().toLowerCase();
  const shownMembers = orderMembers(bundle.members, memberSort)
    .filter((m) => !focus || m.name === focus)
    .filter(
      (m) =>
        m.tasks.some((t) => keeps(t, taskFilter) && matches(t, needle)) ||
        (!needle && taskFilter === "all" && !focus),
    );
  const totalTasks = bundle.members.reduce((n, m) => n + m.tasks.length, 0);
  /* Counted over the members actually on screen, not over the whole team.
     Focused on one person's past-due rows this said "34" - the team's total
     - directly beside that person's name, which is the one number a reader
     would have quoted straight into a status report. */
  const shownTasks = shownMembers.reduce(
    (n, m) =>
      n + m.tasks.filter((t) => keeps(t, taskFilter) && matches(t, needle)).length,
    0,
  );

  const span = days(bundle.window_start, bundle.window_end) ?? 1;
  const dated = bundle.members.filter((m) =>
    m.tasks.some((t) => t.start && t.planned_end),
  );

  return (
    <Page
      current="/team"
      title="Team"
      scope={`${bundle.members.length} people named in the tracker`}
      asof={
        bundle.window_start
          ? `${bundle.window_start} to ${bundle.window_end}`
          : "nothing dated"
      }
    >
      <Stats>
        <Stat value={String(bundle.members.length)} label="people named" />
        <Stat value={String(dated.length)} label="carrying dated work" />
        <Stat
          value={`${bundle.burn.logged_hours} / ${bundle.burn.planned_hours}h`}
          label="effort logged of planned"
        />
        <Stat
          value={
            bundle.burn.stalled_from
              ? `since ${bundle.burn.stalled_from.slice(5)}`
              : "still moving"
          }
          label="nothing logged"
          bad={Boolean(bundle.burn.stalled_from)}
        />
        <Stat
          value={String(bundle.members.reduce((n, m) => n + m.qa_blocked, 0))}
          label="blocked QA items"
          bad={bundle.members.some((m) => m.qa_blocked > 0)}
        />
      </Stats>

      <Board className="mb-6">
        <Concentration bundle={bundle} onPick={drillTo} />
        <Closures bundle={bundle} />
        <Panel caption="Workload on one window" span={12} className="scroll-mt-4">
          <div id="workload" />
          {/* Why this list is shorter than the whole team. Same pattern as
              the Traceability page's filter chip, and for the same reason:
              a list narrowed by a click somewhere else, with nothing saying
              so, gets read as the total. */}
          {(focus || taskFilter !== "all") && (
            <div className="fade-in mb-2.5 flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-full border border-rule bg-bg py-1 pr-1 pl-3 text-body text-ink-2">
                Showing{" "}
                <b className="font-semibold text-ink">
                  {focus || "everyone"}
                  {taskFilter !== "all" &&
                    ` · ${TASK_FILTERS.find(([v]) => v === taskFilter)?.[1]}`}
                </b>
                <span className="tabular-nums text-ink-3">{shownTasks}</span>
                <button
                  type="button"
                  onClick={() => {
                    setFocus("");
                    setTaskFilter("all");
                  }}
                  className="cursor-pointer rounded-full border-0 bg-rule-2 px-2.5 py-1 text-label text-ink-2 transition-colors duration-100 hover:bg-red hover:text-surface"
                >
                  Clear
                </button>
              </span>
            </div>
          )}
          {/* Controls above the window, not inside it: they change which
              rows exist, and a control that sits among the rows it removes
              moves as you use it. */}
          <div className="mb-2.5 flex flex-wrap items-center gap-2">
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search key, title, type..."
              aria-label="Search tasks"
              className="min-w-[220px] flex-1 rounded-md border border-rule bg-surface px-2.5 py-1.5 text-body text-ink"
            />
            <select
              value={memberSort}
              onChange={(e) => setMemberSort(e.target.value as MemberSort)}
              aria-label="Sort people"
              className="rounded-md border border-rule bg-surface px-2.5 py-1.5 text-body text-ink"
            >
              {MEMBER_SORTS.map(([value, label]) => (
                <option key={value} value={value}>
                  Sort: {label}
                </option>
              ))}
            </select>
            <select
              value={taskFilter}
              onChange={(e) => setTaskFilter(e.target.value as TaskFilter)}
              aria-label="Filter tasks by status"
              className="rounded-md border border-rule bg-surface px-2.5 py-1.5 text-body text-ink"
            >
              {TASK_FILTERS.map(([value, label]) => (
                <option key={value} value={value}>
                  Show: {label}
                </option>
              ))}
            </select>
            <span className="ml-auto text-body text-ink-3">
              {shownTasks === totalTasks
                ? `${totalTasks} tasks`
                : `${shownTasks} of ${totalTasks} tasks`}
            </span>
          </div>
          {bundle.window_start ? (
            <>
              {/* Same geometry as WorkloadRow - a 150px name gutter, gap-3,
                  and 76px reserved on the right for a bar's end label - or
                  the ticks would name dates the bars below do not sit on. */}
              <div className="mb-1 flex items-end gap-3">
                <div className="w-[150px] shrink-0 text-label text-ink-3">
                  {bundle.window_start}
                </div>
                <div className="min-w-0 flex-1 pr-[76px]">
                  <div className="relative h-[14px]">
                    {scaleTicks(bundle.window_start, span).map((tick) => (
                      <span
                        key={tick.label}
                        className="absolute top-0 -translate-x-1/2 text-label whitespace-nowrap text-ink-3"
                        style={{ left: `${tick.at}%` }}
                      >
                        {tick.label}
                      </span>
                    ))}
                    <span className="absolute top-0 left-full ml-1.5 text-label whitespace-nowrap text-ink-3">
                      {bundle.window_end}
                    </span>
                  </div>
                </div>
              </div>
              {shownMembers.length === 0 ? (
                <p className="m-0 py-3 text-body text-ink-3 italic">
                  Nothing matches that. Clear the search to see everyone.
                </p>
              ) : (
                shownMembers.map((member) => (
                  <WorkloadRow
                    key={member.name}
                    member={member}
                    start={bundle.window_start!}
                    span={span}
                    filter={taskFilter}
                    query={query.trim().toLowerCase()}
                    /* Unfolded when the reader narrowed to this person on
                       purpose. Folding a list somebody clicked a number to
                       see makes them click twice for one answer. */
                    expanded={Boolean(focus)}
                  />
                ))
              )}
              <div className="mt-3 flex flex-wrap gap-4 border-t border-rule pt-2.5">
                <span className="flex items-center gap-1.5 text-label text-ink-3">
                  <span
                    className="h-2.5 w-4 rounded-sm"
                    style={{ background: "var(--viz-plan)" }}
                  />
                  plan - what the tracker says
                </span>
                <span className="flex items-center gap-1.5 text-label text-ink-3">
                  <span
                    className="h-2.5 w-4 rounded-sm"
                    style={{ background: "var(--viz-over)" }}
                  />
                  later than planned - the slip the chain implies
                </span>
                <span className="flex items-center gap-1.5 text-label text-ink-3">
                  <span
                    className="h-2.5 w-4 rounded-sm"
                    style={{ background: "var(--viz-over)", opacity: 0.62 }}
                  />
                  past due - the date went by and the row is still open
                </span>
              </div>
            </>
          ) : (
            <p className="m-0 text-body text-ink-2">
              No task carries both a start and a planned finish, so there is no window to
              draw against.
            </p>
          )}
        </Panel>

        <Panel caption="Effort logged against plan" span={12}>
          <Burn burn={bundle.burn} />
          <p className="mt-2.5 mb-0 text-body text-ink-3">
            One point per scan of the work log. Every rise is a{" "}
            <code className="font-mono text-label">hours_spent</code> change the differ
            detected, so a flat stretch is evidence that nothing was logged &mdash; not
            evidence that nobody looked. The planned line is one value because{" "}
            <code className="font-mono text-label">estimate_hours</code> is not tracked:
            we have never observed the plan change and will not draw where it used to be.
          </p>
        </Panel>

        <Panel caption="Logged against planned, by owner" span={6} className="content-start">
          <Hours members={bundle.members} />
          <p className="mt-3 mb-0 border-t border-rule pt-2.5 text-body text-ink-3">
            Both halves are columns a person filled in, so this is a variance and its
            derivation can be shown. It is not a productivity figure: that would need
            output per unit of effort, and the only output measure here is a
            self-reported percentage.
          </p>
        </Panel>

        <Panel caption="What moved, by week" span={6} className="content-start">
          <Activity bundle={bundle} />
        </Panel>
      </Board>

      <Section title="What this data cannot show">
        <Card className="grid gap-2.5">
          <div>
            <b className="font-semibold">A planned line that moves.</b>{" "}
            <span className="text-body text-ink-2">
              <code className="font-mono text-body">estimate_hours</code> is not a
              tracked field, so no scan has ever recorded the plan changing. It is drawn
              as one reference value; sloping it would be inventing the history of a
              number we only know the present of. Scope growth is reported instead as the
              count of rows we actually saw appear.
            </span>
          </div>
          <div>
            <b className="font-semibold">Productivity, as output per unit of effort.</b>{" "}
            <span className="text-body text-ink-2">
              The effort half now exists &mdash; that is the panel above. The output half
              does not: <code className="font-mono text-body">progress</code> is a
              self-reported percentage, and a ratio built on it would inherit that and
              present it as measurement. Effort variance is served in its place, because
              both of its halves are columns somebody filled in.
            </span>
          </div>
          <div>
            <b className="font-semibold">Capacity.</b>{" "}
            <span className="text-body text-ink-2">
              The bars above are the calendar span someone&rsquo;s work covers, not how
              full their days are. No allocation data has been ingested, so
              &ldquo;overloaded&rdquo; is not a claim this can make.
            </span>
          </div>
        </Card>
      </Section>
    </Page>
  );
}

export function Team() {
  const [bundle, setBundle] = useState<TeamBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<TeamBundle>(withProject("/api/team")).then(setBundle, setProblem);
  }, []);

  if (problem) {
    return (
      <Page current="/team" title="Team" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return <Page current="/team" title="Team" subtitle="Loading..." children={null} />;
  }
  return <TeamView bundle={bundle} />;
}
