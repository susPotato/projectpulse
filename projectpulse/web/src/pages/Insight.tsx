import { useEffect, useState } from "react";
import {
  dash,
  load,
  type ApiProblem,
  type CausalLink,
  type DeliveryConfidence,
  type EvidenceRef,
  type ExplainBundle,
  type Finding,
  type ForecastBundle,
  type GanttBundle,
  type InsightBundle,
  type RiskDraftBundle,
  type Scenario,
  type ScenarioBundle,
  type RuleTrace,
  withProject,
} from "../api";
import {
  AllClear,
  Board,
  Card,
  MICRO_LABEL,
  Page,
  PageSkeleton,
  Panel,
  Problem,
  Section,
  Stat,
  Stats,
  SubTabs,
} from "../components/Shell";
import { BarRows, Meter, Split, type SplitPart } from "../components/charts";
import { CountUp } from "../components/motion";

/*
  Findings, each openable to the rule that fired, the chain behind it, and the
  source rows.

  Renders and never computes. Every number arrived already formatted by
  `assembler.format_fact`; if this file did arithmetic, invariant 1 would be
  broken by the front end.
*/

const SEVERITY_ORDER = ["critical", "high", "medium", "low", "info"] as const;

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red",
  high: "bg-red",
  medium: "bg-orange",
  low: "bg-blue",
  info: "bg-ink-3",
};

/* How well evidenced a claim is, in words as well as colour - a reader must
   never have to decode a hue to know whether a cause was stated or guessed. */
const BASIS: Record<string, { label: string; className: string; title: string }> = {
  dependency_edge: {
    label: "stated dependency",
    className: "border-green/45 bg-green/10 text-green",
    title: "A person wrote this dependency down in the schedule sheet.",
  },
  dependency_path: {
    label: "dependency chain",
    className: "border-blue/45 bg-blue/10 text-blue",
    title: "Connected through the dependency graph, but not directly adjacent.",
  },
  same_entity: {
    label: "same task",
    className: "border-purple/45 bg-purple/10 text-purple",
    title: "Both changes are on the same task.",
  },
  same_project: {
    label: "same project only",
    className: "border-orange/45 bg-orange/10 text-orange",
    title: "Nothing but the project connects these. A hypothesis, not a stated fact.",
  },
  none: {
    label: "rule threshold",
    className: "border-rule bg-bg text-ink-3",
    title: "Fired on an aggregate rather than a causal chain.",
  },
};

/* Same three-band vocabulary as the portfolio's critical/watch/healthy
   (Portfolio.tsx) - a reader who has learned one learns both. */
const CONFIDENCE_STYLE: Record<string, { label: string; className: string }> = {
  high: { label: "High confidence", className: "border-green/45 bg-green/10 text-green" },
  medium: { label: "Medium confidence", className: "border-amber/45 bg-amber/10 text-amber" },
  low: { label: "Low confidence", className: "border-red/45 bg-red/10 text-red" },
};

/* The strings the stored narrative is split on. These are the wire format -
   `app/narration/fallback.py#QUESTION_HEADINGS` emits them, the LLM prompt
   asks for them by name, and `tests/test_api.py` pins this array to that
   tuple. Never reword one here. */
const QUESTIONS = [
  "What is at risk",
  "Why it is happening",
  "What it will impact",
  "What to do next",
  "What this analysis could not use",
] as const;

/* What a reader sees instead. Mirrors `narration.DISPLAY_HEADINGS`, which the
   Word export uses for the same five sections, and exists for the same
   reason: narratives already cached in the database carry the old wording, so
   the split has to keep matching it while the page stops showing it. A PM
   manages from "Key Risks", not from a question the analysis asked itself. */
const DISPLAY_HEADINGS: Record<string, string> = {
  "What is at risk": "Key Risks",
  "Why it is happening": "Likely Drivers",
  "What it will impact": "Potential Impact",
  "What to do next": "Recommended Actions",
  "What this analysis could not use": "Data Limitations",
};

function Basis({ basis }: { basis: string }) {
  const spec = BASIS[basis] ?? BASIS.none!;
  return (
    <span
      title={spec.title}
      className={`inline-block rounded-full border px-2 py-0.5 text-label font-bold ${spec.className}`}
    >
      {spec.label}
    </span>
  );
}

/* How much to trust the outlook figure above it - coverage x freshness,
   rendered as a band. Never a percentage: the scenarios panel below already
   explains why (see Scenarios' comment). The tooltip carries the two real
   inputs plus the honest gap - no precedent data yet, because retrieval is
   not built - rather than a number with nothing behind it. */
function ConfidenceChip({ confidence }: { confidence: DeliveryConfidence }) {
  const spec = CONFIDENCE_STYLE[confidence.band] ?? CONFIDENCE_STYLE.low!;
  const age =
    confidence.data_age_hours == null
      ? "never synced"
      : confidence.data_age_hours < 1
        ? "synced under an hour ago"
        : `synced ${Math.round(confidence.data_age_hours)}h ago`;
  const title =
    `coverage ${Math.round(confidence.coverage * 100)}% (tasks with a dated baseline) × ` +
    `freshness ${Math.round(confidence.freshness * 100)}% (${age}). ` +
    `precedent: no precedent data yet.`;

  return (
    <span
      title={title}
      className={`inline-block rounded-full border px-2 py-0.5 text-label font-bold ${spec.className}`}
    >
      {spec.label}
    </span>
  );
}

function BlockLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
      {children}
    </div>
  );
}

function Narrative({ bundle }: { bundle: InsightBundle }) {
  // The server emits labelled sections; splitting on the same headings keeps
  // one definition of them (`narration.QUESTION_HEADINGS`).
  const text = bundle.narrative ?? "";
  const blocks = QUESTIONS.flatMap((question) => {
    const at = text.indexOf(`${question}\n`);
    if (at < 0) return [];
    const rest = text.slice(at + question.length + 1);
    const end = rest.indexOf("\n\n");
    return [{ question, body: (end < 0 ? rest : rest.slice(0, end)).trim() }];
  });

  if (!blocks.length) return <Card>{text || "No narrative."}</Card>;

  return (
    <Card className="stagger grid gap-3.5">
      {blocks.map(({ question, body }) => (
        <div key={question}>
          <div className="mb-0.5 text-emph font-semibold text-navy">
            {DISPLAY_HEADINGS[question] ?? question}
          </div>
          <p className="m-0 text-body text-ink-2">{body}</p>
        </div>
      ))}
    </Card>
  );
}

function Step({ step, role }: { step: CausalLink["cause"]; role: string }) {
  const when =
    step.occurred.precision === "exact"
      ? `exactly ${step.occurred.lower.replace("T", " ").slice(0, 16)}`
      : `between ${step.occurred.lower.slice(0, 10)} and ${step.occurred.upper.slice(0, 10)}`;

  return (
    <div className="flex-1 basis-56 rounded-lg border border-rule bg-bg px-3 py-2.5">
      <div className="font-bold text-navy">{step.entity_label}</div>
      <div className="mt-0.5 text-body text-ink-2">
        {role} · {step.field}
      </div>
      <div className="mt-1 text-body">
        {dash(step.old_value)} → {dash(step.new_value)}
      </div>
      {/* An interval shown as an interval. A midpoint would invent precision
          the snapshot data never had. */}
      <div className="mt-1 text-body text-ink-3">
        {when} · {step.occurred.precision}
      </div>
    </div>
  );
}

function Chain({ link }: { link: CausalLink }) {
  const lag =
    link.lag_days_min === link.lag_days_max
      ? `${link.lag_days_min} days later`
      : `${link.lag_days_min}–${link.lag_days_max} days later`;

  return (
    <div>
      <BlockLabel>Why — {link.template_name}</BlockLabel>
      <div className="flex flex-wrap items-stretch gap-2.5">
        <Step step={link.cause} role="cause" />
        <div className="flex flex-col items-center self-center text-ink-3">
          <div className="text-title">→</div>
          <div className="text-label whitespace-nowrap">{lag}</div>
        </div>
        <Step step={link.effect} role="effect" />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-body text-ink-3">
        <Basis basis={link.evidence_basis} />
        <span>
          ordering:{" "}
          {link.ordering_basis === "exact"
            ? "both timestamps exact"
            : "intervals do not overlap"}
        </span>
      </div>
    </div>
  );
}

function Trace({ trace }: { trace: RuleTrace }) {
  return (
    <div>
      <BlockLabel>Rule — {trace.rule_id}</BlockLabel>
      <div className="rounded-lg bg-bg px-3 py-2.5">
        {trace.conditions.map((condition) => (
          <code key={condition} className="block py-px font-mono text-label text-navy">
            {condition}
          </code>
        ))}
        {trace.rationale && (
          <div className="mt-1.5 text-body italic text-ink-2">{trace.rationale}</div>
        )}
      </div>
    </div>
  );
}

function Evidence({ refs }: { refs: EvidenceRef[] }) {
  return (
    <div>
      <BlockLabel>Evidence — {refs.length} source row(s)</BlockLabel>
      {refs.map((ref) => (
        /* The breadcrumb is the label, not the URL. A row's `url` is an
           absolute `file:///C:/Users/.../hrms_schedule.xlsx#Activities!row5`,
           which wrapped over two lines and buried the one part a PM can act
           on. The full path is still there - it is the link and the tooltip. */
        <a
          key={`${ref.raw_data_id}-${ref.url}`}
          href={ref.url ?? "#"}
          title={[ref.raw_table, ref.url].filter(Boolean).join(" — ")}
          className="block truncate py-0.5 text-body text-blue no-underline hover:underline"
        >
          <span className="font-mono text-ink-3">#{ref.raw_data_id} </span>
          {ref.remark ?? ref.url ?? ref.label ?? "source record"}
        </a>
      ))}
    </div>
  );
}

/*
  The model's proposals, at the altitude the Risk tab reads at.

  There used to be a second, longer rendering of these on an Evidence tab,
  with the cited task text under each. That tab is gone and the longer
  version went with it: the Risk *page* is where a draft is accepted or
  dismissed, so the full text a reader needs in order to judge one belongs
  there, beside the act of judging it - not on a third screen that only
  displays it.
*/
function ModelReadBrief({ bundle }: { bundle: RiskDraftBundle }) {
  if (bundle.drafts.length === 0) {
    return <Card>{bundle.reason ?? "Nothing proposed."}</Card>;
  }
  return (
    <>
      <Card className="mb-2.5 border-orange/40 bg-orange/5 text-body leading-relaxed">
        <strong>Read by a model from task text, not computed.</strong> These
        are not findings and none is in the risk register. The Risk page shows
        the task text each was read from, and is where they are accepted or
        dismissed.
      </Card>
      {bundle.drafts.map((draft) => {
        const cites = (draft.cited_task_ids ?? "").split(",").filter((s) => s.trim());
        return (
          <Card key={draft.id} className="mb-2 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-body font-semibold">{draft.title}</div>
              <div className="mt-0.5 text-body text-ink-3">
                {draft.category ?? "no category"} · read from {cites.length} task(s)
              </div>
            </div>
            {draft.pre_rating && (
              <span className="whitespace-nowrap rounded-full border border-rule px-2 py-0.5 text-label text-ink-2">
                {draft.pre_rating}
              </span>
            )}
          </Card>
        );
      })}
    </>
  );
}

/* The project in one row, before anything interprets it.

   Overview had no counts at all - it opened on a severity bar and a narrative,
   so "how big is this and how much of it is done" took a trip to another tab.
   Every figure here is already in `context`; none is computed in this file. */
function AtAGlance({ bundle }: { bundle: InsightBundle }) {
  const c = bundle.context;
  const n = (key: string) => Number(c[key] ?? 0);
  const total = n("task_count");
  const done = n("tasks_done");
  const inFlight = n("tasks_in_progress");
  /* Work the tracker calls finished that a traceability run contradicts.
     Deducted from `done` rather than drawn on top of it: this figure is the
     reason the panel exists in this shape. A ticket marked Done that the
     code does not support was counted here as delivered, in the same green
     as work that genuinely is, and a completion percentage built from it
     overstates the project by exactly this many items.

     Read from the context rather than from `code_check`, and that is not
     interchangeable: `trace_done_contradicted` is joined to the same task
     rows `tasks_done` counts, by tracker key, while `code_check` counts
     rows in the trace export - a different population. Subtracting one
     from the other is how a bar stops summing to its own total. */
  const disputed = Math.min(done, n("trace_done_contradicted"));
  const confirmedDone = Math.max(0, done - disputed);
  const overdue = n("tasks_overdue");
  const owners = n("distinct_owners");

  return (
    <>
      {/* The whole backlog in one bar.

          "129 / 190" made a reader do the division, and the three counts
          that follow it - done, running, not started - were three separate
          tiles they then had to add back together to check the first one.
          One bar is the division and the addition at once, and the reader
          can see at a glance that the untouched segment is the big one.

          Not coloured by status beyond the one status that is a status:
          finished is green, running is the neutral "in flight" blue, and
          not-started is the same unsaturated fill this app uses everywhere
          for "nothing has happened here". Past due is deliberately *not* a
          segment - an overdue item is also in one of these three, and
          double-counting it in a bar whose whole point is that it sums
          would be the one thing this panel must not do. It is a tile.

          The amber "reported done, not in the code" segment is the one
          addition, and it obeys that same rule by being *carved out of*
          finished rather than laid over it: finished + disputed is the old
          `done`, so the bar still sums to `total`. It earns a segment where
          overdue does not because it is not a second fact about an item
          already counted - it is the claim that the item is finished at
          all, failing. Absent entirely when no traceability run has looked,
          because then the honest answer is that nobody has checked. */}
      <Card className="mb-3 flex flex-wrap items-center gap-x-8 gap-y-4">
        <div className="min-w-[150px]">
          <div className={MICRO_LABEL}>Complete</div>
          <b className="mt-1 block text-kpi font-bold text-ink">
            <CountUp
              value={total > 0 ? Math.round((confirmedDone / total) * 100) : 0}
              suffix="%"
            />
          </b>
        </div>
        <div className="min-w-[260px] flex-1">
          <Split
            total={total}
            ariaLabel={
              `Of ${total} work items: ${confirmedDone} finished` +
              (disputed ? `, ${disputed} reported done but not found in the code` : "") +
              `, ${inFlight} in progress, ` +
              `${Math.max(0, total - done - inFlight)} not started`
            }
            parts={[
              { key: "done", label: "finished", value: confirmedDone, tone: "good" },
              ...(disputed
                ? [{
                    key: "disputed",
                    label: "reported done, not in the code",
                    value: disputed,
                    tone: "warn" as const,
                  }]
                : []),
              { key: "wip", label: "in progress", value: inFlight, tone: "info" },
              {
                key: "todo",
                label: "not started",
                value: Math.max(0, total - done - inFlight),
                tone: "unknown",
              },
            ]}
          />
        </div>
      </Card>

      <Stats>
        <Stat value={inFlight} label="in progress" />
        <Stat
          value={overdue}
          label="past due, still open"
          tone={overdue > 0 ? "bad" : "good"}
        />
        <Stat value={n("tasks_due_soon")} label="due within a fortnight" />
        <Stat
          value={owners}
          label={owners === 1 ? "owner - no distribution" : "distinct owners"}
          tone={owners === 1 && total > 5 ? "bad" : "plain"}
        />
        {n("tasks_stale") > 0 && (
          <Stat
            value={n("tasks_stale")}
            label="untouched a week or more"
            foot={`worst ${n("stalest_task_days")}d`}
          />
        )}
      </Stats>
    </>
  );
}

/* The tasks behind the count.

   A finding says "6 task(s) are past their own due date". The next question a
   reader has is which six, and until now the answer was on the Schedule page -
   so the page that tells you something is wrong could not tell you what to open.
   Read from the same snapshot the Schedule draws, so the two lists cannot
   disagree. */
function PastDue({ tasks, asOf }: { tasks: GanttBundle; asOf: string }) {
  const scan = String(asOf).slice(0, 10);
  const late = tasks.rows
    .filter(
      (r) =>
        r.planned_end &&
        r.planned_end < scan &&
        r.status !== "DONE" &&
        r.status !== "DROPPED",
    )
    .map((r) => ({
      ...r,
      days: Math.round(
        (Date.parse(scan) - Date.parse(r.planned_end as string)) / 86400000,
      ),
    }))
    .sort((a, b) => b.days - a.days);

  if (!late.length) return null;

  /* Capped, like the cluster panel beside it and for the same reason. On
     this backlog the list ran to thirty-four rows, which made the first
     panel on Overview four times the height of every other one and pushed
     Delivery Verification below two screenfuls of ticket titles. Twelve is
     enough to see the shape of it; the rest are one click away on the page
     that exists to list them. */
  const SHOWN = 12;
  const rest = late.length - SHOWN;

  return (
    <Panel
      caption={`Past due — ${late.length} open`}
      span={7}
      className="content-start min-w-0"
      action={
        rest > 0 ? (
          <a
            className="text-label text-navy no-underline hover:underline"
            href={withProject("/gantt")}
          >
            All {late.length} on the schedule &rarr;
          </a>
        ) : undefined
      }
    >
      <div className="stagger grid min-w-0 gap-1.5">
        {late.slice(0, SHOWN).map((row) => (
          <div
            key={row.entity_id}
            className="flex min-w-0 items-baseline gap-3 text-body"
          >
            <span className="w-[112px] shrink-0 truncate font-mono text-label text-ink-3">
              {row.label}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink" title={row.title ?? ""}>
              {row.title ?? ""}
            </span>
            <span className="shrink-0 text-label text-ink-3">{row.planned_end}</span>
            <span className="w-[62px] shrink-0 text-right font-bold text-red tabular-nums">
              {row.days}d late
            </span>
          </div>
        ))}
        {rest > 0 && (
          <div className="pt-1 text-label text-ink-3">
            and {rest} more, none worse than {late[SHOWN]?.days}d late
          </div>
        )}
      </div>
    </Panel>
  );
}

/* Where the dates pile up. A cluster is only actionable if you know what is in
   it - "8 tasks share 2026-10-02" is a warning, the eight names are a to-do
   list. Capped, because a band of thirty would take the page over. */
function Cluster({ tasks, bundle }: { tasks: GanttBundle; bundle: InsightBundle }) {
  const date = String(bundle.context.busiest_due_date ?? "");
  if (!date) return null;
  const rows = tasks.rows.filter(
    (r) => r.planned_end === date && r.status !== "DONE" && r.status !== "DROPPED",
  );
  if (rows.length < 2) return null;

  return (
    <Panel caption={`All due ${date} — ${rows.length}`} span={5} className="content-start min-w-0">
      <div className="grid min-w-0 gap-1.5">
        {rows.slice(0, 10).map((row) => (
          <div
            key={row.entity_id}
            className="flex min-w-0 items-baseline gap-2.5 text-body"
          >
            <span className="w-[112px] shrink-0 truncate font-mono text-body text-ink-3">
              {row.label}
            </span>
            <span className="min-w-0 flex-1 truncate text-ink">{row.title ?? ""}</span>
          </div>
        ))}
        {rows.length > 10 && (
          <div className="text-body text-ink-3">and {rows.length - 10} more</div>
        )}
      </div>
    </Panel>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const detail =
    finding.causal_link || finding.rule_trace || finding.evidence.length > 0;

  return (
    <details className="mb-2.5 rounded-lg border border-rule bg-surface">
      <summary className="flex cursor-pointer list-none items-start gap-3 p-3.5 hover:bg-rule-2/60">
        <span
          className={`mt-px min-w-[62px] rounded px-2 py-0.5 text-center text-label font-extrabold tracking-[0.05em] text-white uppercase ${
            SEVERITY_STYLE[finding.severity] ?? "bg-ink-3"
          }`}
        >
          {finding.severity}
        </span>
        <span className="flex-1">
          <span className="block font-semibold">{finding.headline}</span>
          {finding.recommendation && (
            <span className="mt-0.5 block text-body text-ink-2">
              {finding.recommendation}
            </span>
          )}
        </span>
        <span className="text-ink-3">▾</span>
      </summary>
      <div className="grid gap-3.5 border-t border-rule p-3.5 sm:pl-[90px]">
        {finding.causal_link && <Chain link={finding.causal_link} />}
        {finding.rule_trace && <Trace trace={finding.rule_trace} />}
        {finding.evidence.length > 0 && <Evidence refs={finding.evidence} />}
        {!detail && <p className="m-0 text-ink-3">No further detail recorded.</p>}
      </div>
    </details>
  );
}

/* One bar per severity actually present, ranked worst first, colored by the
   same status tokens `FindingCard`'s pill already uses - so a PM sees the
   shape of the findings list before reading a word of it, and the color a
   bar carries here is the same color that finding wears when they scroll to
   it. Count is a direct label rather than an axis, because five bars do not
   need one. */
function SeverityBreakdown({ findings }: { findings: Finding[] }) {
  const counts = SEVERITY_ORDER.map((severity) => ({
    severity,
    count: findings.filter((f) => f.severity === severity).length,
  })).filter((row) => row.count > 0);

  if (counts.length === 0) return null;

  return (
    <Panel caption="Findings by severity" span={4} className="content-start">
      <BarRows
        ariaLabel={`Findings by severity: ${counts
          .map((row) => `${row.count} ${row.severity}`)
          .join(", ")}`}
        labelWidth="w-[72px]"
        rows={counts.map((row) => ({
          key: row.severity,
          label: row.severity,
          value: row.count,
        }))}
        /* The one place in this app a magnitude series is coloured, and it
           is legitimate: the "measure" here *is* severity, and the colour a
           bar carries is the same colour that finding wears on its own card
           further down the page. The word is on the bar either way. */
        tone={(row) => SEVERITY_TONE[row.key]}
      />
    </Panel>
  );
}

/* The severity palette, as chart tones. Kept beside `SEVERITY_STYLE` above,
   which is the same decision expressed as a pill background. */
const SEVERITY_TONE: Record<string, "bad" | "warn" | "info" | "unknown"> = {
  critical: "bad",
  high: "bad",
  medium: "warn",
  low: "info",
  info: "unknown",
};

function Quality({ bundle }: { bundle: InsightBundle }) {
  const q = bundle.data_quality;
  const tasks = Number(bundle.context?.task_count ?? 0) || 1;
  const tiles: [React.ReactNode, string][] = [
    [q.rows_rejected, "rows quarantined"],
    [q.edges_stated, "dependencies stated"],
    [q.edges_inferred, "dependencies inferred"],
  ];

  return (
    <Card>
      {/* The two figures that are ratios are drawn as ratios. Both are
          coverage questions - how much of the data supports the analysis -
          and "0%" written as a word next to "190" made a reader work out
          which of the two numbers was the denominator. */}
      <div className="mb-4 grid gap-x-8 gap-y-4 sm:grid-cols-2">
        <Meter
          /* Against the task count, not the change count. Those are two
             different denominators - 190 work items and 351 recorded
             changes - and the first draft of this meter divided coverage
             by the wrong one, which read as "0 of 351" beside a finding
             saying "0 of 190". On a page whose whole argument is that
             every figure comes from the data it describes, a denominator
             nobody supplied is the one mistake it cannot make. */
          value={Math.round(q.baseline_coverage * tasks)}
          of={tasks}
          label="Work items with a dated baseline"
          tone={q.baseline_coverage < 0.5 ? "warn" : "good"}
        />
        <Meter
          value={q.changes_total - q.changes_low_confidence}
          of={q.changes_total || 1}
          label="Changes with a confident timestamp"
          tone={q.changes_low_confidence > 0 ? "warn" : "good"}
        />
      </div>
      <div className="stagger flex flex-wrap gap-6">
        {tiles.map(([value, label]) => (
          <div key={label}>
            <div className="text-title font-bold text-navy tabular-nums">{value}</div>
            <div className="text-body text-ink-2">{label}</div>
          </div>
        ))}
      </div>
      {q.depends_on_inferred_edges && (
        <div className="mt-3 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-body">
          The schedule conclusion changes if inferred dependencies are removed. It is a
          derived claim, not a stated one — confirm before quoting it externally.
        </div>
      )}
    </Card>
  );
}

/* The figure a PM leans forward for, and the reason this screen leads with it:
   the sheet states one finish date and its own dependencies imply another. It is
   arithmetic over dates a person typed, so it is checkable on the Calculation
   tab - which is why the panel says so rather than asking to be believed. */
function Outlook({
  explain,
  confidence,
}: {
  explain: ExplainBundle;
  confidence?: DeliveryConfidence | null;
}) {
  const planned = explain.project_end_planned;
  const projected = explain.project_end_projected;
  const slip = explain.project_slip_days ?? 0;

  // Nothing to lead with when the plan is consistent with itself. Rendering a
  // hero that says "+0 days" would make a healthy project look alarming.
  if (!planned || !projected || slip <= 0) return null;

  return (
    <Panel caption="Delivery outlook" span={7} className="grid gap-4 content-start">
      <div className="flex flex-wrap items-end gap-x-7 gap-y-4">
        <div>
          <div className={MICRO_LABEL}>The plan says</div>
          <div className="text-heading leading-tight font-light text-ink-2 line-through decoration-1">
            {planned}
          </div>
        </div>
        <div aria-hidden="true" className="mb-1.5 text-emph text-ink-3">
          →
        </div>
        <div>
          <div className={MICRO_LABEL}>Its dependencies imply</div>
          <div className="text-heading leading-tight font-semibold text-orange">
            {projected}
          </div>
        </div>
        <div className="flex-1" />
        <div className="text-right">
          <div className="text-kpi font-bold text-orange">
            <CountUp value={slip} prefix="+" />
          </div>
          <div className="mt-1 text-body text-ink-3">days already in the plan</div>
        </div>
      </div>

      {/* The slip, drawn. Two dates and a number left a reader doing the
          subtraction that is the whole point of the panel; the overrun bar
          is that subtraction, at the scale of the plan it eats into. The
          bar grows from the committed date, which is the direction the
          slip actually travels. */}
      <div aria-hidden="true" className="flex items-center gap-2">
        <span className="h-1.5 flex-1 rounded-full bg-blue/35" />
        <span
          className="grow-x h-1.5 rounded-full bg-orange"
          style={{ width: `${Math.min(40, Math.max(6, slip))}%` }}
        />
      </div>
      <div className="border-t border-rule pt-3 text-body text-ink-2">
        Nobody recorded this slip. It is arithmetic over the dates in the sheet and the
        dependencies between them &mdash; every step is on the{" "}
        <a href="/explain">Calculation</a> tab.
      </div>
      {confidence && (
        <div className="flex items-center gap-2">
          <ConfidenceChip confidence={confidence} />
          <span className="text-body text-ink-3">
            in this figure &mdash; coverage of dated tasks × freshness of the last sync
          </span>
        </div>
      )}
    </Panel>
  );
}

/* The chain that produces the date above. Ordered by the forward pass rather
   than by size, so it reads as a chain and not a ranking. */
function DrivingPath({ explain }: { explain: ExplainBundle }) {
  const onPath = new Set(explain.driving_path);
  const steps = explain.steps.filter((step) => onPath.has(step.entity_id));
  /* Two steps, or it is not a chain. With no dependency edges the driving path
     is whichever single task finishes last, and rendering that under "the chain
     that moves the date" - one row, labelled "origin", pointing at nothing -
     claims a sequence the data does not contain. It was the first panel on the
     page and it was furniture. */
  if (steps.length < 2) return null;

  return (
    <Panel caption="The chain that moves the date" span={5} className="content-start">
      <div className="grid gap-2">
        {steps.map((step) => (
          <div key={step.entity_id} className="flex items-center gap-3">
            <span className="w-[68px] font-mono text-body text-navy">{step.label}</span>
            <span className="flex-1 text-body text-ink-2">{step.title ?? ""}</span>
            <span
              className={
                "text-body font-semibold " +
                (step.propagated_days ? "text-orange" : "text-ink-3")
              }
            >
              {step.propagated_days ? `+${step.propagated_days}d` : "origin"}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

/* The AI-analysis panel from the design: detected, then the reason, then the
   evidence, then the rule. Composed from the components the finding cards
   already use rather than new ones - there is one definition of a chain, one of
   an evidence list and one of a rule trace, and this is a different arrangement
   of them, not a second implementation.

   It leads with the best-evidenced causal finding rather than the most severe:
   the panel's whole claim is "here is why", so a weakly-linked coincidence at
   the top would undercut everything below it. */
const BASIS_STRENGTH = [
  "same_entity",
  "dependency_edge",
  "dependency_path",
  "same_project",
] as const;

function strongestCause(findings: Finding[]): Finding | null {
  const ranked = findings
    .filter((f) => f.causal_link && f.severity !== "info")
    .sort((a, b) => {
      const rank = (f: Finding) => {
        const at = BASIS_STRENGTH.indexOf(
          f.evidence_basis as (typeof BASIS_STRENGTH)[number],
        );
        return at < 0 ? BASIS_STRENGTH.length : at;
      };
      return rank(a) - rank(b);
    });
  return ranked[0] ?? null;
}

function AiAnalysis({ finding }: { finding: Finding }) {
  const link = finding.causal_link;
  if (!link) return null;

  return (
    <Panel caption="AI analysis" span={12} className="grid gap-4">
      <div>
        <BlockLabel>Detected</BlockLabel>
        <div className="text-body font-semibold">{finding.headline}</div>
        {finding.recommendation && (
          <div className="mt-1 text-body text-ink-2">{finding.recommendation}</div>
        )}
      </div>

      <Chain link={link} />

      <div className="grid gap-4 md:grid-cols-2">
        {finding.evidence.length > 0 && <Evidence refs={finding.evidence} />}
        {finding.rule_trace && <Trace trace={finding.rule_trace} />}
      </div>

      <div className="border-t border-rule pt-3 text-body text-ink-3">
        The panel leads with the best-evidenced cause, not the loudest. Orderings that
        cannot be proved are dropped rather than hedged, so a pair missing from here is a
        pair we refused to claim.
      </div>
    </Panel>
  );
}

/* The delivery forecast: a range, from this project's own drift.

   Deliberately shown *beside* the forward pass and never instead of it. They
   answer different questions - the forward pass is "where does the chain land
   if nothing else moves", which is arithmetic and certain; this is "how has
   this plan actually behaved", which is a range and rests on a sample. A page
   that showed only the range would be quoting a probability where it has a
   proof.

   Three things must never leave this panel: the sample size, the sample
   itself, and the assumption. They are served on the bundle rather than
   written here so that the page, the CLI and the .docx cannot drift apart -
   and so a well-meaning edit cannot quietly drop the caveat. */
function Forecast({ bundle }: { bundle: ForecastBundle }) {
  if (!bundle.available) {
    return (
      <Panel caption="Delivery forecast" span={12}>
        <div className="text-body leading-[1.65] text-ink-2">{bundle.reason}</div>
        <div className="mt-2 text-body text-ink-3 italic">
          No range is shown because the data does not support one. That is the
          honest answer, not a missing feature.
        </div>
      </Panel>
    );
  }

  return (
    <Panel caption="Delivery forecast" span={12}>
      <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
        {bundle.points.map((point) => (
          <div key={point.percentile}>
            <div className="text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
              P{point.percentile}
            </div>
            <div className="mt-0.5 text-title leading-none font-bold tabular-nums">
              {point.finish}
            </div>
            <div className="mt-1 text-body text-ink-3 tabular-nums">
              {point.days_late > 0
                ? `+${point.days_late}d vs the commitment`
                : "meets the commitment"}
            </div>
          </div>
        ))}

        <div className="ml-auto max-w-[300px] text-body leading-[1.5] text-ink-3">
          In {bundle.trials.toLocaleString()} trials, resampling{" "}
          <b className="font-semibold text-ink-2">
            {bundle.observations} observed drift(s)
          </b>{" "}
          onto {bundle.open_tasks} open task(s). The forward pass alone says{" "}
          <b className="font-semibold text-ink-2">{bundle.projected_end}</b>.
        </div>
      </div>

      {/* The sample, so a reader can check the basis instead of trusting it.
          Six observations is thin, and showing them is what lets a judge see
          that it is thin rather than take the percentiles on faith. */}
      <div className="mt-4 border-t border-rule pt-3">
        <div className="mb-2 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
          What the range is built from
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {bundle.sample.map((observation) => (
            <span key={observation.entity_id} className="text-body text-ink-3">
              {observation.label}{" "}
              <b className="font-semibold text-ink-2 tabular-nums">
                {observation.days > 0 ? `+${observation.days}d` : `${observation.days}d`}
              </b>
            </span>
          ))}
        </div>
        <p className="mt-2.5 text-body leading-[1.55] text-ink-3 italic">
          {bundle.method} {bundle.assumption}
        </p>
      </div>
    </Panel>
  );
}

/* Recovery scenarios: the design's "what if" screen.

   Every figure is the forward pass re-run over modified rows, so a scenario is
   priced in dates and days rather than in a confidence percentage. That matters
   more than it looks: the design mocked "delivery confidence 61% -> 91%", which
   is a number nothing could defend, and the same panel showed an ETA moving -
   which is the half that is arithmetic. This keeps that half. */
function ScenarioRow({ scenario, best }: { scenario: Scenario; best: boolean }) {
  const meets = scenario.days_late <= 0;

  return (
    <div
      className={
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border px-3.5 py-3 " +
        (best ? "border-green bg-green/8" : "border-rule bg-bg")
      }
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-semibold">{scenario.summary}</span>
          {best && (
            <span className="rounded bg-green px-1.5 py-px text-label font-extrabold tracking-[0.05em] text-surface uppercase">
              best available
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-body text-ink-3">
          {scenario.moves.map((move) => (
            <span key={`${move.kind}-${move.entity_id}`}>
              <span className="font-mono text-blue">{move.label}</span>{" "}
              {move.kind === "compress"
                ? `${move.days}d shorter`
                : `starts ${move.days}d before ${move.against_label}`}
            </span>
          ))}
        </div>
      </div>
      <div className="text-right">
        <div className="text-emph font-semibold text-green">{scenario.projected_end}</div>
        <div className="mt-0.5 text-body text-ink-3">
          {scenario.days_earlier}d earlier ·{" "}
          {meets
            ? "meets the commitment"
            : `still ${scenario.days_late}d past it`}
        </div>
      </div>
    </div>
  );
}

function Scenarios({ bundle }: { bundle: ScenarioBundle }) {
  if (bundle.scenarios.length === 0) return null;

  return (
    <Panel caption="Recovery scenarios" span={12} className="grid gap-3">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-rule bg-bg px-3.5 py-3">
        <div>
          <div className="text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
            Doing nothing
          </div>
          <div className="mt-1 text-emph font-semibold text-orange">
            {bundle.projected_end}
          </div>
        </div>
        <div className="min-w-0 flex-1 text-body text-ink-2">
          Each option below is the same forward pass re-run over a changed plan &mdash;
          the date is computed, not estimated. Nothing is written to your spreadsheet.
        </div>
      </div>

      {bundle.scenarios.map((scenario, index) => (
        <ScenarioRow key={scenario.id} scenario={scenario} best={index === 0} />
      ))}

      {/* The panel that keeps this honest. Without it a reader takes a
          computed date for an achievable one. */}
      <div className="rounded-r-lg border-l-2 border-amber bg-bg px-3.5 py-3">
        <div className="text-label font-bold tracking-[0.07em] text-amber uppercase">
          What this cannot tell you
        </div>
        <p className="mt-1.5 mb-0 text-body text-ink-2">
          These are schedule arithmetic only. Whether the team can absorb a compression is
          a resource question and no allocation data has been ingested, so no option here
          claims to be achievable &mdash; only to be what the dependency graph would do.
          {bundle.depends_on_inferred_edges && (
            <>
              {" "}
              The baseline these are measured against also rests on a dependency inferred
              from the sheet&rsquo;s own dates rather than stated by a person.
            </>
          )}
        </p>
      </div>
    </Panel>
  );
}

/** The page as a pure function of its data, so it can be rendered in a test. */
/* The design's within-project views. Only those with something behind them: a
   tab that opens on "nothing here yet" teaches a reader the product is empty.
   Schedule and Quality have screens of their own, so they are not repeated
   here as half-versions. */
/* What a traceability run found, on the page people actually open.

   This is the second reading of the same project and it disagrees with the
   first on the most basic question there is: how much work exists. The tracker
   exports keyed rows and this screen counts those; the run reads the unkeyed
   sub-rows out of the same export and there are an order of magnitude more of
   them. Both numbers are correct about what they measured, and a reader shown
   only the smaller one has no way to learn the larger exists.

   Kept visually apart from `FindingCard` on purpose. Those are rules this
   product evaluated against rows it imported and can defend line by line;
   these were decided by a model reading a document and by citations checked
   against files. Same page, different standing, and the panel says which. */
/* Which Traceability view and filter each number opens.

   The link is the feature. Before this the section ended in one "open the
   full trace" link, so a PM who read "95 could not be verified" and wanted
   to know which 95 arrived on an unfiltered list of a hundred and seventy
   three rows and had to rebuild the filter by hand. Every count here is now
   a door into exactly the rows it counts.

   The values are the Traceability page's own filter values, not new ones -
   `fVerdict` on that page reads `corroborated`, `unverified`,
   `contradicted`, and `conflict` for the status-conflict set. */
function traceLink(projectId: string, params: Record<string, string>): string {
  const query = new URLSearchParams({ project: projectId, ...params });
  return `/traceability?${query.toString()}`;
}

/* Field names as a person says them. The export wrote these inside
   description prose as `key: value`, so they arrive spelled however the team
   spelled them - `PO`, `Developer` - and printing that verbatim in a sentence
   reads as a column name rather than as a role. */
const ROLE_WORDS: Record<string, string> = {
  developer: "developer",
  dev: "developer",
  po: "product owner",
  pm: "project manager",
  ba: "business analyst",
  qa: "tester",
  tester: "tester",
  reviewer: "reviewer",
};

function roleWord(field: string): string {
  return ROLE_WORDS[field.trim().toLowerCase()] ?? field.toLowerCase();
}

/*
  What the code says about work the tracker calls done.

  This is the second reading of the same project and it disagrees with the
  first on the most basic question there is: how much work exists. The
  tracker exports keyed rows and this screen counts those; the run reads the
  unkeyed sub-rows out of the same export and there are an order of magnitude
  more of them. Both numbers are correct about what they measured, and a
  reader shown only the smaller one has no way to learn the larger exists.

  Kept visually apart from `FindingCard` on purpose. Those are rules this
  product evaluated against rows it imported and can defend line by line;
  these were decided by a model reading a document and by citations checked
  against files. Same page, different standing, and the panel says which.

  The wording rule the reviewer set, applied here: say what it means for
  delivery, not how it was computed. "A separate pass read this project's own
  repository and documents and checked 173 of these 190 rows" is method. "173
  of 190 work items were eligible for code verification" is the fact, and the
  method survives in the tooltip for whoever wants it.
*/
function CodeCheck({
  check,
  tracked,
  projectId,
}: {
  check: NonNullable<InsightBundle["code_check"]>;
  /* How many work items *this* screen counted, passed in rather than written
     down. The first draft of this sentence had the number typed into it,
     which on a page built to prove that every figure comes from the data it
     describes is the one mistake it cannot afford. */
  tracked: number;
  projectId: string;
}) {
  const rows = Number(check.rows ?? 0);
  if (!rows) return null;

  const corroborated = Number(check.corroborated ?? 0);
  const contradicted = Number(check.contradicted ?? 0);
  const unverified = Number(check.unverified ?? 0);
  const conflicts = Number(check.conflicts ?? 0);
  const gaps = (check.ownership_gaps ?? []) as {
    field: string;
    named: number;
    missing: number;
    by_status: { status: string; n: number }[];
  }[];

  /* One shared vocabulary with the Traceability page and the Word export.
     `corroborated` stays the wire value everywhere; `Confirmed` is the only
     thing anybody reads. */
  const parts: SplitPart[] = [
    {
      key: "corroborated",
      label: "Confirmed",
      value: corroborated,
      tone: "good",
      href: traceLink(projectId, { view: "tickets", verdict: "corroborated" }),
      title: "Open these work items on the Traceability page",
    },
    {
      key: "unverified",
      label: "Need Review",
      value: unverified,
      /* Not amber. These are not at risk, they are unmeasured, and colouring
         absence of evidence as warning tells a PM ninety-five items are in
         trouble when what is true is that nobody has looked. */
      tone: "unknown",
      href: traceLink(projectId, { view: "tickets", verdict: "unverified" }),
      title: "Open these work items on the Traceability page",
    },
    {
      key: "contradicted",
      label: "Conflicts",
      value: contradicted,
      tone: "bad",
      href: traceLink(projectId, { view: "tickets", verdict: "contradicted" }),
      title: "Open these work items on the Traceability page",
    },
  ];
  if (conflicts > 0) {
    parts.push({
      key: "conflict",
      label: "Status Concerns",
      value: conflicts,
      tone: "warn",
      href: traceLink(projectId, { view: "tickets", verdict: "conflict" }),
      title: "Work items the code says are built while the tracker has them open",
    });
  }

  return (
    <Section
      title="Delivery Verification"
      action={
        <a
          className="text-body text-navy no-underline hover:underline"
          href={traceLink(projectId, { view: "findings" })}
        >
          View Evidence Analysis &rarr;
        </a>
      }
    >
      <Card>
        <p
          className="m-0 mb-3 text-body text-ink-2"
          title={
            `A separate pass read this project's repository and documents and checked ` +
            `${rows} of these ${tracked} work items against the code. The rest are ` +
            `planning tasks, which name no feature to look for.`
          }
        >
          <b className="text-ink tabular-nums">{rows}</b> of{" "}
          <b className="text-ink tabular-nums">{tracked}</b> work items were
          eligible for code verification.
        </p>

        {/* The mix, as one bar rather than four numbers in a row. Same
            total, same counts, one comparison instead of four - and every
            segment's count is the link into its own rows. */}
        <Split
          parts={parts}
          total={rows}
          ariaLabel={
            `Of ${rows} work items checked against the code: ${corroborated} confirmed, ` +
            `${unverified} need review, ${contradicted} conflict with the code` +
            (conflicts ? `, ${conflicts} status concerns` : "")
          }
        />

        {/* The answer to "does anyone own this". The export has no assignee
            column at all - these fields were written as `key: value` inside
            the description prose and recovered from there. */}
        {gaps.length > 0 && (
          <div className="mt-4 border-t border-rule pt-3">
            <div className={`mb-2 ${MICRO_LABEL}`}>Ownership Gaps</div>
            <div className="stagger grid gap-1">
              {gaps.map((gap) => (
                <p key={gap.field} className="m-0 text-body text-ink-2">
                  <a
                    /* No filter on the Traceability ticket list isolates a
                       missing role - the roles are prose inside a
                       description, not a column anything can filter on - so
                       this opens the finding that reports the same thing:
                       One Person Holds Every Role, on Priority Issues. */
                    href={traceLink(projectId, {
                      view: "findings",
                      focus: "roles-concentrated",
                    })}
                    title={
                      gap.by_status.length > 0
                        ? gap.by_status.map((e) => `${e.n} ${e.status}`).join(", ")
                        : undefined
                    }
                    className="font-bold text-navy tabular-nums no-underline hover:underline"
                  >
                    {gap.missing}
                  </a>{" "}
                  work items have no {roleWord(gap.field)} assigned.
                </p>
              ))}
            </div>
          </div>
        )}
      </Card>
    </Section>
  );
}


/* Two views, not three.

   `Evidence` was the third, and it was deleted rather than filled. What it
   held was a data-quality panel, the model's readings of task text, and a
   list of "what stands behind each finding" - and that last one was the
   tell: every finding card on the two remaining tabs already opens onto its
   own rule trace and source rows, so the section was a second rendering of
   something a reader reaches by clicking the finding itself.

   The other two moved rather than vanished. `Quality` is the caveat on
   everything above it, so it sits at the foot of Overview where the reader
   has just finished reading what it qualifies. The model's proposals were
   already on Risk in the shape that tab reads at, and the longer rendering
   here duplicated them with the task text attached - the Risk page is where
   a draft is accepted, so that is where its full text belongs.

   Code verification stays on Traceability, which is its own screen in the
   rail. Overview links into it by the number. A tab that was a thin copy of
   another page is worse than a link to that page. */
const VIEWS = ["Overview", "Risk"] as const;

export function InsightView({
  bundle,
  explain = null,
  scenarios = null,
  forecast = null,
  drafts = null,
  tasks = null,
  view: initialView = "Overview",
}: {
  bundle: InsightBundle;
  /* Optional: the schedule projection lives in a second bundle. A failure to
     load it drops the outlook panel rather than the page, the same way the
     Calculation tab drops its chart. */
  explain?: ExplainBundle | null;
  /* Optional for the same reason as `explain`: a failure to load the scenarios
     drops one panel, never the findings. */
  scenarios?: ScenarioBundle | null;
  /* Optional, and from a different endpoint on purpose. `app/intelligence/`
     must not read the risk register - a rule conditioning on an opinion is not
     deterministic - so the model's readings cannot ride along on
     `/api/insight`. Two fetches keeps that boundary where it is. */
  drafts?: RiskDraftBundle | null;
  /* Optional, and from the schedule endpoint: `InsightBundle` carries counts,
     not rows. Fetched separately so a failure costs the two task lists rather
     than the findings. */
  tasks?: GanttBundle | null;
  /* Optional for the same reason again. A forecast that fails to load must
     never take the proven figures down with it. */
  forecast?: ForecastBundle | null;
  /* Overridable so the smoke render can assert every view without driving a
     click, and so a caller can deep-link a reader to the findings. */
  view?: (typeof VIEWS)[number];
}) {
  const order = new Map(SEVERITY_ORDER.map((s, i) => [s, i]));
  const findings = [...bundle.findings].sort(
    (a, b) => (order.get(a.severity) ?? 9) - (order.get(b.severity) ?? 9),
  );
  const cause = strongestCause(findings);
  const [view, setView] = useState<string>(initialView);

  return (
    <Page
      current="/insight"
      title="Insight"
      scope={`${bundle.project_id} · ${findings.length} finding(s)`}
      asof={
        <>
          reflects the sheet as at{" "}
          <b className="font-semibold text-ink-2">
            {String(bundle.as_of).replace("T", " ").slice(0, 10)}
          </b>
        </>
      }
      /* The design's action slot, with something real behind it: the .docx the
         report exporter already produces. An action button that does nothing
         is worse than an empty slot.

         Scoped to the project on screen. Without `withProject` this hit the
         route's own default - `excel:Project:1:HRMS` - so the button beside a
         project's findings quietly downloaded a different project's report,
         with nothing on the page or in the file to say so. The Reports page has
         always built its links this way. */
      action={
        <a className="action" href={withProject("/api/report.docx")}>
          <svg viewBox="0 0 24 24">
            <path d="M14 2v6h6" />
            <path d="M4 22V4a2 2 0 0 1 2-2h8l6 6v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2Z" />
          </svg>
          Download report
        </a>
      }
    >
      <SubTabs views={VIEWS} current={view} onSelect={setView} />

      {view === "Overview" && (
        <>
          <AtAGlance bundle={bundle} />

          <Board className="mb-6">
            {tasks && <PastDue tasks={tasks} asOf={String(bundle.as_of)} />}
            {tasks && <Cluster tasks={tasks} bundle={bundle} />}
            {explain && (
              <Outlook explain={explain} confidence={bundle.delivery_confidence} />
            )}
            {explain && <DrivingPath explain={explain} />}
            {cause && <AiAnalysis finding={cause} />}
            <SeverityBreakdown findings={findings} />
            {scenarios && <Scenarios bundle={scenarios} />}
            {/* Only when there is a range to draw. The unavailable state is a
                paragraph explaining what the data lacks, which belongs on the
                Evidence tab with the rest of that - leading the page a reader
                opens for "what should I do" with a panel about what cannot be
                done is how Overview came to say nothing actionable. */}
            {forecast?.available && <Forecast bundle={forecast} />}
          </Board>

          {/* The findings, on the tab people land on.
              They were only on the Risk tab, so Overview showed analysis *about*
              findings - a severity bar, a driving path, a narrative - without
              ever showing one. The prose below is a summary of this list; a
              summary printed above the thing it summarises, on a page whose
              first question is "what do I do", is backwards. */}
          <Section title="Findings">
            {findings.length === 0 ? (
              <AllClear>
                Every delivery threshold was evaluated and none of them
                breached.
              </AllClear>
            ) : (
              findings.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))
            )}
          </Section>

          {bundle.code_check && (
            <CodeCheck
              check={bundle.code_check}
              tracked={Number(bundle.context?.task_count ?? 0)}
              projectId={bundle.project_id}
            />
          )}

          {/* Folded. The narrative is complete prose and worth having - it is
              what gets read aloud - but five paragraphs restating the cards
              above them pushed everything else off the screen. Open by choice,
              not by default. */}
          <Section title={`Summary — ${bundle.narration_source}`}>
            <details className="rounded-lg border border-rule bg-surface px-3.5 py-2.5">
              <summary className="cursor-pointer text-body text-ink-2">
                Read the written summary
              </summary>
              <div className="mt-3">
                <Narrative bundle={bundle} />
              </div>
            </details>
          </Section>

          {/* Last, because it qualifies everything above it. This is the
              section that says which rows were refused, how much of the
              plan carries a baseline, and how many dependencies were
              inferred rather than stated - and a page that reports figures
              without it is a page asking to be trusted. */}
          <Section title="Data Limitations">
            <Quality bundle={bundle} />
          </Section>
        </>
      )}

      {view === "Risk" && (
        <>
          {/* The same correction the Schedule tiles needed, for the same reason.
              `milestones_at_risk` and `tasks_inconsistent` are forward-pass
              results over a dependency graph and `qa_blocked` needs a worklog:
              on a source carrying none of the three, all three read 0 and a
              reader is told there is nothing to worry about. Nothing was
              assessed.

              So each is shown only when the data behind it exists, and what
              takes their place is what this data *can* say - past due, not yet
              started, no date at all. A panel called "where the pressure is"
              has to name pressure, not decline to. */}
          <Section title="Where The Pressure Is">
            <Stats>
              <Stat
                value={String(bundle.context.tasks_overdue ?? 0)}
                label="past due, still open"
                bad={Number(bundle.context.tasks_overdue ?? 0) > 0}
              />
              <Stat
                value={String(bundle.context.tasks_due_soon ?? 0)}
                label="due within a fortnight"
              />
              {Number(bundle.context.edges_total ?? 0) > 0 && (
                <Stat
                  value={String(bundle.context.milestones_at_risk ?? 0)}
                  label="milestones at risk"
                  bad={Number(bundle.context.milestones_at_risk ?? 0) > 0}
                />
              )}
              {Number(bundle.context.edges_total ?? 0) > 0 && (
                <Stat
                  value={String(bundle.context.tasks_inconsistent ?? 0)}
                  label="tasks whose dates cannot hold"
                  bad={Number(bundle.context.tasks_inconsistent ?? 0) > 0}
                />
              )}
              {Number(bundle.context.qa_count ?? 0) > 0 && (
                <Stat
                  value={`${bundle.context.qa_blocked ?? 0} / ${bundle.context.qa_count ?? 0}`}
                  label="QA items blocked"
                  bad={Number(bundle.context.qa_blocked ?? 0) > 0}
                />
              )}
              <Stat
                value={`${bundle.context.tasks_done ?? 0} / ${bundle.context.task_count ?? 0}`}
                label="complete"
              />
            </Stats>
          </Section>

          <Section title="Findings">
            {findings.length === 0 ? (
              <AllClear>
                Every delivery threshold was evaluated and none of them
                breached.
              </AllClear>
            ) : (
              <>
                <Board className="mb-3.5">
                  <SeverityBreakdown findings={findings} />
                </Board>
                {findings.map((finding) => (
                  <FindingCard key={finding.id} finding={finding} />
                ))}
              </>
            )}
          </Section>

          {/* Below the findings, never mixed into them. A finding is a rule that
              fired on a computed number and carries its own trace; these are a
              model's reading of prose. Interleaving them by severity would put
              the two on one footing, and the severity on these is the model's
              own suggestion. */}
          <Section title="AI-Proposed Risks">
            {drafts ? <ModelReadBrief bundle={drafts} /> : <Card>Not loaded.</Card>}
          </Section>
        </>
      )}

    </Page>
  );
}

export function Insight() {
  const [bundle, setBundle] = useState<InsightBundle | null>(null);
  const [explain, setExplain] = useState<ExplainBundle | null>(null);
  const [scenarios, setScenarios] = useState<ScenarioBundle | null>(null);
  const [forecast, setForecast] = useState<ForecastBundle | null>(null);
  const [drafts, setDrafts] = useState<RiskDraftBundle | null>(null);
  /* The task rows, for naming the tasks a finding counts. A finding says "6
     task(s) are past their own due date"; the one question a reader has next is
     "which six", and the answer was on another page. Same snapshot the Schedule
     draws, so the two cannot disagree about which tasks those are. */
  const [tasks, setTasks] = useState<GanttBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<InsightBundle>(withProject("/api/insight")).then(setBundle, setProblem);
    // Fetched separately and allowed to fail: the outlook panel is the best
    // thing on the page, and still not worth the findings for.
    load<ExplainBundle>(withProject("/api/explain")).then(setExplain, () => setExplain(null));
    load<ScenarioBundle>(withProject("/api/scenarios")).then(setScenarios, () => setScenarios(null));
    load<ForecastBundle>(withProject("/api/forecast")).then(setForecast, () => setForecast(null));
    // A plain read - it never asks a model, so opening Insight costs nothing.
    load<RiskDraftBundle>(withProject("/api/risks/drafts")).then(setDrafts, () =>
      setDrafts(null),
    );
    load<GanttBundle>(withProject("/api/gantt")).then(setTasks, () => setTasks(null));
  }, []);

  if (problem) {
    return (
      <Page current="/insight" title="Insight" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    // The shape of the page that is coming, not the word "Loading". Rule 7:
    // the placeholder is the same size as the content so nothing jumps when
    // it lands, and it is visibly *waiting* rather than visibly empty.
    return (
      <Page current="/insight" title="Insight" subtitle="Reading this project's findings…">
        <PageSkeleton />
      </Page>
    );
  }
  return (
    <InsightView
      bundle={bundle}
      explain={explain}
      scenarios={scenarios}
      forecast={forecast}
      drafts={drafts}
      tasks={tasks}
    />
  );
}
