import { useEffect, useState } from "react";
import {
  dash,
  load,
  type ApiProblem,
  type CausalLink,
  type CitedTask,
  type DeliveryConfidence,
  type EvidenceRef,
  type ExplainBundle,
  type Finding,
  type ForecastBundle,
  type InsightBundle,
  type RiskDraftBundle,
  type Scenario,
  type ScenarioBundle,
  type RuleTrace,
  withProject,
} from "../api";
import {
  Board,
  Card,
  Page,
  Panel,
  Problem,
  Section,
  Stat,
  Stats,
  SubTabs,
} from "../components/Shell";

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

const QUESTIONS = [
  "What is at risk",
  "Why it is happening",
  "What it will impact",
  "What to do next",
  "What this analysis could not use",
] as const;

function Basis({ basis }: { basis: string }) {
  const spec = BASIS[basis] ?? BASIS.none!;
  return (
    <span
      title={spec.title}
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-bold ${spec.className}`}
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
      className={`inline-block rounded-full border px-2 py-0.5 text-[11px] font-bold ${spec.className}`}
    >
      {spec.label}
    </span>
  );
}

function BlockLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-1.5 text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
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
    <Card className="grid gap-3.5">
      {blocks.map(({ question, body }) => (
        <div key={question}>
          <div className="mb-0.5 font-semibold text-navy">{question}</div>
          <p className="m-0 text-ink-2">{body}</p>
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
      <div className="mt-0.5 text-[12.5px] text-ink-2">
        {role} · {step.field}
      </div>
      <div className="mt-1 text-[13px]">
        {dash(step.old_value)} → {dash(step.new_value)}
      </div>
      {/* An interval shown as an interval. A midpoint would invent precision
          the snapshot data never had. */}
      <div className="mt-1 text-[11.5px] text-ink-3">
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
          <div className="text-xl">→</div>
          <div className="text-[10.5px] whitespace-nowrap">{lag}</div>
        </div>
        <Step step={link.effect} role="effect" />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2 text-[11.5px] text-ink-3">
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
          <code key={condition} className="block py-px font-mono text-xs text-navy">
            {condition}
          </code>
        ))}
        {trace.rationale && (
          <div className="mt-1.5 text-[12.5px] italic text-ink-2">{trace.rationale}</div>
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
          className="block truncate py-0.5 text-[12.5px] text-blue no-underline hover:underline"
        >
          <span className="font-mono text-ink-3">#{ref.raw_data_id} </span>
          {ref.remark ?? ref.url ?? ref.label ?? "source record"}
        </a>
      ))}
    </div>
  );
}

/*
  What a model read out of the task text, and the rows it read it from.

  On the Evidence tab rather than beside the findings, and that placement is the
  argument. A finding is a rule that fired on a number this app computed, and
  its evidence is the source row the number came from. This is neither: nobody
  computed it, and the only thing under it is prose somebody typed into Jira.
  Putting it here says what it is - something to go and check - and keeps it out
  of the list a reader is entitled to trust without checking.

  Nothing here can become a risk by being looked at. Accepting one is on the
  Risk page, because that is where the register lives and accepting is the act
  that makes a suggestion into a record.
*/
function ModelRead({ bundle }: { bundle: RiskDraftBundle }) {
  const byId = new Map<string, CitedTask>(bundle.cited_tasks.map((t) => [t.task_id, t]));

  if (bundle.drafts.length === 0) {
    return <Card>{bundle.reason ?? "Nothing proposed."}</Card>;
  }

  return (
    <>
      <Card className="mb-2.5 border-orange/40 bg-orange/5 text-[12.5px] leading-relaxed">
        <strong>Read by a model from task text. Not computed, and not evidence.</strong>{" "}
        Each item below is a suggestion drawn from what somebody wrote in the
        tracker, shown with the rows it was drawn from so you can judge both.
        None of it is in the risk register until you accept it there.
      </Card>
      {bundle.drafts.map((draft) => {
        const cites = (draft.cited_task_ids ?? "")
          .split(",")
          .map((id) => id.trim())
          .filter(Boolean);
        return (
          <Card key={draft.id} className="mb-2.5">
            <div className="mb-1 text-[13px] font-semibold">{draft.title}</div>
            {draft.description && (
              <div className="mb-2 text-[12.5px] leading-relaxed text-ink-2">
                {draft.description}
              </div>
            )}
            <div className="mb-2 flex flex-wrap gap-1.5 text-[11.5px] text-ink-3">
              {draft.category && <span>{draft.category}</span>}
              {draft.pre_rating && <span>· suggested {draft.pre_rating}</span>}
            </div>
            <BlockLabel>Read from — {cites.length} task(s)</BlockLabel>
            {cites.map((id) => {
              const task = byId.get(id);
              return (
                <div key={id} className="border-t border-rule py-1.5 text-[12.5px]">
                  {/* The label, never the id. A citation is matched on the full
                      domain id and *read* as the key the sheet carried - showing
                      `excel:Task:1:excel%3AProject...:NOKEY-179826e5` puts 60
                      characters of internal shape in front of a sentence a
                      person is meant to judge. */}
                  <span className="font-mono text-ink-3">{task?.label ?? id} </span>
                  {task?.title ?? "(task not found)"}
                  {task?.status && <span className="text-ink-3"> · {task.status}</span>}
                  {task?.text && (
                    <div className="mt-1 leading-relaxed text-ink-2">{task.text}</div>
                  )}
                </div>
              );
            })}
          </Card>
        );
      })}
    </>
  );
}

/*
  The same proposals as `ModelRead`, at the altitude the Risk tab reads at.

  Two renderings rather than one shared component, because the tabs ask
  different questions. Risk asks "what might bite" - the claim, how it was
  graded, how much it rests on. Evidence asks "should I believe it" - and that
  needs the task text, which is exactly what makes it too long to sit in a list
  of risks. Linking one to the other beats showing half of each.
*/
function ModelReadBrief({ bundle }: { bundle: RiskDraftBundle }) {
  if (bundle.drafts.length === 0) {
    return <Card>{bundle.reason ?? "Nothing proposed."}</Card>;
  }
  return (
    <>
      <Card className="mb-2.5 border-orange/40 bg-orange/5 text-[12.5px] leading-relaxed">
        <strong>Read by a model from task text, not computed.</strong> These are
        not findings and none is in the risk register. The Evidence tab shows
        the task text each was read from; the Risk page is where they are
        accepted or dismissed.
      </Card>
      {bundle.drafts.map((draft) => {
        const cites = (draft.cited_task_ids ?? "").split(",").filter((s) => s.trim());
        return (
          <Card key={draft.id} className="mb-2 flex items-start gap-3">
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold">{draft.title}</div>
              <div className="mt-0.5 text-[11.5px] text-ink-3">
                {draft.category ?? "no category"} · read from {cites.length} task(s)
              </div>
            </div>
            {draft.pre_rating && (
              <span className="whitespace-nowrap rounded-full border border-rule px-2 py-0.5 text-[11px] text-ink-2">
                {draft.pre_rating}
              </span>
            )}
          </Card>
        );
      })}
    </>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const detail =
    finding.causal_link || finding.rule_trace || finding.evidence.length > 0;

  return (
    <details className="mb-2.5 rounded-lg border border-rule bg-surface">
      <summary className="flex cursor-pointer list-none items-start gap-3 p-3.5 hover:bg-rule-2/60">
        <span
          className={`mt-px min-w-[62px] rounded px-2 py-0.5 text-center text-[10.5px] font-extrabold tracking-[0.05em] text-white uppercase ${
            SEVERITY_STYLE[finding.severity] ?? "bg-ink-3"
          }`}
        >
          {finding.severity}
        </span>
        <span className="flex-1">
          <span className="block font-semibold">{finding.headline}</span>
          {finding.recommendation && (
            <span className="mt-0.5 block text-[13px] text-ink-2">
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
  const max = Math.max(...counts.map((row) => row.count));

  return (
    <Panel caption="Findings by severity" span={4} className="content-start">
      <div className="grid gap-2.5">
        {counts.map((row) => (
          <div key={row.severity} className="flex items-center gap-2.5">
            <span className="w-14 shrink-0 text-[11px] font-semibold text-ink-2 uppercase">
              {row.severity}
            </span>
            <div className="h-3.5 min-w-0 flex-1 rounded bg-rule-2">
              <div
                className={`h-full rounded ${SEVERITY_STYLE[row.severity] ?? "bg-ink-3"}`}
                style={{ width: `${Math.max((row.count / max) * 100, 8)}%` }}
              />
            </div>
            <span className="w-5 shrink-0 text-right text-[12.5px] font-semibold tabular-nums text-ink">
              {row.count}
            </span>
          </div>
        ))}
      </div>
    </Panel>
  );
}

function Quality({ bundle }: { bundle: InsightBundle }) {
  const q = bundle.data_quality;
  const tiles: [React.ReactNode, string][] = [
    [q.rows_rejected, "rows quarantined"],
    [`${q.changes_low_confidence} / ${q.changes_total}`, "changes low-confidence"],
    [`${Math.round(q.baseline_coverage * 100)}%`, "tasks with a baseline"],
    [q.edges_stated, "dependencies stated"],
    [q.edges_inferred, "dependencies inferred"],
  ];

  return (
    <Card>
      <div className="flex flex-wrap gap-6">
        {tiles.map(([value, label]) => (
          <div key={label}>
            <div className="text-xl font-bold text-navy">{value}</div>
            <div className="text-[11.5px] text-ink-2">{label}</div>
          </div>
        ))}
      </div>
      {q.depends_on_inferred_edges && (
        <div className="mt-3 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-[12.5px]">
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
          <div className="text-[11.5px] text-ink-3">The sheet says</div>
          <div className="text-[25px] leading-tight font-light text-ink-2 line-through decoration-1">
            {planned}
          </div>
        </div>
        <div aria-hidden="true" className="mb-1.5 text-lg text-ink-3">
          →
        </div>
        <div>
          <div className="text-[11.5px] text-ink-3">Its dependencies imply</div>
          <div className="text-[25px] leading-tight font-semibold text-orange">
            {projected}
          </div>
        </div>
        <div className="flex-1" />
        <div className="text-right">
          <div className="text-[34px] leading-none font-bold text-orange">+{slip}</div>
          <div className="mt-1 text-[11.5px] text-ink-3">days already in the plan</div>
        </div>
      </div>
      <div className="border-t border-rule pt-3 text-[12.5px] text-ink-2">
        Nobody recorded this slip. It is arithmetic over the dates in the sheet and the
        dependencies between them &mdash; every step is on the{" "}
        <a href="/explain">Calculation</a> tab.
      </div>
      {confidence && (
        <div className="flex items-center gap-2">
          <ConfidenceChip confidence={confidence} />
          <span className="text-[11.5px] text-ink-3">
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
  if (steps.length === 0) return null;

  return (
    <Panel caption="The chain that moves the date" span={5} className="content-start">
      <div className="grid gap-2">
        {steps.map((step) => (
          <div key={step.entity_id} className="flex items-center gap-3">
            <span className="w-[68px] font-mono text-[12px] text-navy">{step.label}</span>
            <span className="flex-1 text-[12.5px] text-ink-2">{step.title ?? ""}</span>
            <span
              className={
                "text-[12px] font-semibold " +
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
        <div className="text-[13.5px] font-semibold">{finding.headline}</div>
        {finding.recommendation && (
          <div className="mt-1 text-[13px] text-ink-2">{finding.recommendation}</div>
        )}
      </div>

      <Chain link={link} />

      <div className="grid gap-4 md:grid-cols-2">
        {finding.evidence.length > 0 && <Evidence refs={finding.evidence} />}
        {finding.rule_trace && <Trace trace={finding.rule_trace} />}
      </div>

      <div className="border-t border-rule pt-3 text-[11.5px] text-ink-3">
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
        <div className="text-[12.5px] leading-[1.65] text-ink-2">{bundle.reason}</div>
        <div className="mt-2 text-[11.5px] text-ink-3 italic">
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
            <div className="text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
              P{point.percentile}
            </div>
            <div className="mt-0.5 text-[19px] leading-none font-bold tabular-nums">
              {point.finish}
            </div>
            <div className="mt-1 text-[11.5px] text-ink-3 tabular-nums">
              {point.days_late > 0
                ? `+${point.days_late}d vs the commitment`
                : "meets the commitment"}
            </div>
          </div>
        ))}

        <div className="ml-auto max-w-[300px] text-[11.5px] leading-[1.5] text-ink-3">
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
        <div className="mb-2 text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
          What the range is built from
        </div>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {bundle.sample.map((observation) => (
            <span key={observation.entity_id} className="text-[11.5px] text-ink-3">
              {observation.label}{" "}
              <b className="font-semibold text-ink-2 tabular-nums">
                {observation.days > 0 ? `+${observation.days}d` : `${observation.days}d`}
              </b>
            </span>
          ))}
        </div>
        <p className="mt-2.5 text-[11.5px] leading-[1.55] text-ink-3 italic">
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
          <span className="text-[13.5px] font-semibold">{scenario.summary}</span>
          {best && (
            <span className="rounded bg-green px-1.5 py-px text-[10px] font-extrabold tracking-[0.05em] text-surface uppercase">
              best available
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-ink-3">
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
        <div className="text-[15px] font-semibold text-green">{scenario.projected_end}</div>
        <div className="mt-0.5 text-[11.5px] text-ink-3">
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
          <div className="text-[11px] font-bold tracking-[0.07em] text-ink-3 uppercase">
            Doing nothing
          </div>
          <div className="mt-1 text-[17px] font-semibold text-orange">
            {bundle.projected_end}
          </div>
        </div>
        <div className="min-w-0 flex-1 text-[12.5px] text-ink-2">
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
        <div className="text-[11px] font-bold tracking-[0.07em] text-amber uppercase">
          What this cannot tell you
        </div>
        <p className="mt-1.5 mb-0 text-[12.5px] text-ink-2">
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
const VIEWS = ["Overview", "Risk", "Evidence"] as const;

export function InsightView({
  bundle,
  explain = null,
  scenarios = null,
  forecast = null,
  drafts = null,
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
         is worse than an empty slot. */
      action={
        <a className="action" href="/api/report.docx">
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
          <Board className="mb-6">
            {explain && (
              <Outlook explain={explain} confidence={bundle.delivery_confidence} />
            )}
            {explain && <DrivingPath explain={explain} />}
            {cause && <AiAnalysis finding={cause} />}
            <SeverityBreakdown findings={findings} />
            {scenarios && <Scenarios bundle={scenarios} />}
            {forecast && <Forecast bundle={forecast} />}
          </Board>

          <Section title={`Summary — ${bundle.narration_source}`}>
            <Narrative bundle={bundle} />
          </Section>
        </>
      )}

      {view === "Risk" && (
        <>
          <Section title="Where the pressure is">
            <Stats>
              <Stat
                value={String(bundle.context.milestones_at_risk ?? 0)}
                label="milestones at risk"
                bad={Number(bundle.context.milestones_at_risk ?? 0) > 0}
              />
              <Stat
                value={`${bundle.context.qa_blocked ?? 0} / ${bundle.context.qa_count ?? 0}`}
                label="QA items blocked"
                bad={Number(bundle.context.qa_blocked ?? 0) > 0}
              />
              <Stat
                value={String(bundle.context.tasks_inconsistent ?? 0)}
                label="tasks whose dates cannot hold"
                bad={Number(bundle.context.tasks_inconsistent ?? 0) > 0}
              />
              <Stat
                value={String(bundle.context.changes_total ?? 0)}
                label="state changes observed"
              />
              <Stat value={String(bundle.context.task_count ?? 0)} label="tasks in the graph" />
            </Stats>
          </Section>

          <Section title="Findings">
            {findings.length === 0 ? (
              <Card>Nothing breaches a delivery threshold.</Card>
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
          <Section title="Proposed from task text">
            {drafts ? <ModelReadBrief bundle={drafts} /> : <Card>Not loaded.</Card>}
          </Section>
        </>
      )}

      {view === "Evidence" && (
        <>
          <Section title="What this analysis could not use">
            <Quality bundle={bundle} />
          </Section>

          <Section title="Read from task text by a model">
            {drafts ? (
              <ModelRead bundle={drafts} />
            ) : (
              <Card>Not loaded.</Card>
            )}
          </Section>

          <Section title="Every finding's source rows">
            {findings.filter((f) => f.evidence.length > 0).length === 0 ? (
              <Card>No finding carries a source row.</Card>
            ) : (
              findings
                .filter((f) => f.evidence.length > 0)
                .map((finding) => (
                  <Card key={finding.id} className="mb-2.5">
                    <div className="mb-2 text-[13px] font-semibold">{finding.headline}</div>
                    <Evidence refs={finding.evidence} />
                  </Card>
                ))
            )}
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
  }, []);

  if (problem) {
    return (
      <Page current="/insight" title="Insight" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return <Page current="/insight" title="Insight" subtitle="Loading..." children={null} />;
  }
  return (
    <InsightView
      bundle={bundle}
      explain={explain}
      scenarios={scenarios}
      forecast={forecast}
      drafts={drafts}
    />
  );
}
