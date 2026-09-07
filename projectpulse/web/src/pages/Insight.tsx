import { useEffect, useState } from "react";
import {
  dash,
  load,
  type ApiProblem,
  type CausalLink,
  type EvidenceRef,
  type Finding,
  type InsightBundle,
  type RuleTrace,
} from "../api";
import { Card, Page, Problem, Section } from "../components/Shell";

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
        <a
          key={`${ref.raw_data_id}-${ref.url}`}
          href={ref.url ?? "#"}
          title={ref.raw_table ?? ""}
          className="block break-all py-0.5 text-[12.5px] text-blue no-underline hover:underline"
        >
          <span className="font-mono text-ink-3">#{ref.raw_data_id} </span>
          {ref.url ?? ref.remark}
        </a>
      ))}
    </div>
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

/** The page as a pure function of its data, so it can be rendered in a test. */
export function InsightView({ bundle }: { bundle: InsightBundle }) {
  const order = new Map(SEVERITY_ORDER.map((s, i) => [s, i]));
  const findings = [...bundle.findings].sort(
    (a, b) => (order.get(a.severity) ?? 9) - (order.get(b.severity) ?? 9),
  );

  return (
    <Page
      current="/insight"
      title="Insight"
      subtitle={`${bundle.project_id} · as of ${String(bundle.as_of)
        .replace("T", " ")
        .slice(0, 16)} · ${findings.length} finding(s)`}
    >
      <Section title={`Summary — ${bundle.narration_source}`}>
        <Narrative bundle={bundle} />
      </Section>

      <Section title="Findings">
        {findings.length === 0 ? (
          <Card>Nothing breaches a delivery threshold.</Card>
        ) : (
          findings.map((finding) => <FindingCard key={finding.id} finding={finding} />)
        )}
      </Section>

      <Section title="What this analysis could not use">
        <Quality bundle={bundle} />
      </Section>
    </Page>
  );
}

export function Insight() {
  const [bundle, setBundle] = useState<InsightBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<InsightBundle>("/api/insight").then(setBundle, setProblem);
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
  return <InsightView bundle={bundle} />;
}
