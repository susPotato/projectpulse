import { useEffect, useState } from "react";
import {
  dash,
  load,
  type ApiProblem,
  type Calc,
  type ExplainBundle,
  type ForwardStep,
  type GanttBundle,
  type Operand,
} from "../api";
import { GanttChart } from "../components/GanttChart";
import { Card, Note, Page, Problem, Section, Stat, Stats } from "../components/Shell";

/*
  Every number, derived as input -> algorithm -> output.

  A ten-column table came first and was unreadable: it showed *what* each figure
  was and never *how*. The three-panel shape exists so a reader can follow one
  line at a time, and the step-through exists so they can watch it happen.

  Renders and never computes.
*/

function Panel({ caption, children }: { caption: string; children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-rule bg-bg px-3 py-2.5">
      <div className="mb-2 text-[10.5px] font-extrabold tracking-[0.09em] text-ink-3 uppercase">
        {caption}
      </div>
      {children}
    </div>
  );
}

function OperandRow({ operand, headline = false }: { operand: Operand; headline?: boolean }) {
  return (
    <div className="flex justify-between gap-2.5 border-t border-dotted border-rule py-1 first:border-t-0">
      <div>
        <div className="text-[12.5px] text-ink-2">{operand.label}</div>
        {operand.note && <div className="text-[11px] italic text-ink-3">{operand.note}</div>}
      </div>
      <div
        className={`text-right font-semibold tabular-nums ${
          headline ? "text-base text-red" : ""
        }`}
      >
        {operand.value}
      </div>
    </div>
  );
}

/* Four fields per step, not one sentence: a formula alone is abstract and
   numbers alone are unverifiable. Together a reader can redo the line on paper. */
function CalcStep({ step, pending }: { step: Calc; pending: boolean }) {
  const rows: [string, string, boolean][] = [
    ["formula", step.formula, false],
    ["values", step.substituted, true],
    ["result", step.result, true],
  ];

  return (
    <div
      className={`border-t border-rule py-2 transition-opacity duration-300 first:border-t-0 ${
        pending ? "opacity-25" : "opacity-100"
      }`}
    >
      <div className="mb-1 font-semibold">
        <b className="mr-1.5 text-blue">{step.number}.</b>
        {step.question}
      </div>
      {rows.map(([label, value, mono]) => (
        <div key={label} className="grid grid-cols-[62px_1fr] gap-2 py-px text-[12.5px]">
          <span className="pt-0.5 text-[10.5px] tracking-[0.05em] text-ink-3 uppercase">
            {label}
          </span>
          <span
            className={
              (mono ? "font-mono " : "") +
              (label === "result" ? "font-extrabold text-green tabular-nums" : "")
            }
          >
            {value}
          </span>
        </div>
      ))}
      {step.note && <div className="mt-1 text-[11.5px] italic text-ink-3">{step.note}</div>}
    </div>
  );
}

function Derivation({ step }: { step: ForwardStep }) {
  // The arithmetic is identical either way; revealing it in order is what makes
  // a four-step derivation followable rather than a wall to skim past.
  const [shown, setShown] = useState(step.calc.length);
  const total = step.calc.length;
  const done = shown >= total;

  return (
    <details className="mb-2 rounded-lg border border-rule bg-surface" open={step.is_inconsistent}>
      <summary className="flex cursor-pointer list-none items-baseline gap-2.5 p-3.5">
        <span
          className={`rounded px-1.5 py-0.5 text-[10.5px] font-extrabold tracking-[0.05em] text-white uppercase ${
            step.is_inconsistent ? "bg-red" : "bg-green"
          }`}
        >
          {step.is_inconsistent ? `${step.propagated_days}d hidden` : "consistent"}
        </span>
        <span>
          {step.label}
          {step.title ? ` — ${step.title}` : ""}
        </span>
      </summary>

      <div className="grid gap-3 p-3.5 lg:grid-cols-[260px_1fr_260px]">
        <Panel caption="Input — what the sheet says">
          {step.inputs.map((operand, i) => (
            <OperandRow key={`${operand.label}-${i}`} operand={operand} />
          ))}
        </Panel>

        <Panel caption="Algorithm">
          {total > 0 ? (
            <>
              <div className="mb-2 flex items-center gap-2">
                {/* Everything is visible by default so the page can be read
                    straight through; stepping is opt-in. From the fully-shown
                    state the button restarts at step one rather than blanking
                    the panel, because an empty panel looks like a fault. */}
                <button
                  type="button"
                  onClick={() => setShown(done ? 1 : shown + 1)}
                  className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12.5px] hover:border-blue"
                >
                  {done ? "Step through" : "Next step"}
                </button>
                <button
                  type="button"
                  disabled={done}
                  onClick={() => setShown(total)}
                  className="cursor-pointer rounded-md border border-rule bg-surface px-2.5 py-1 text-[12.5px] hover:border-blue disabled:cursor-default disabled:opacity-45"
                >
                  Show all
                </button>
                <span className="text-[11.5px] text-ink-3">
                  {shown} of {total} steps
                </span>
              </div>
              {step.calc.map((calc, i) => (
                <CalcStep key={calc.number} step={calc} pending={i >= shown} />
              ))}
            </>
          ) : (
            <div className="text-[11.5px] italic text-ink-3">
              nothing to compute — the sheet supplied no dates
            </div>
          )}
        </Panel>

        <Panel caption="Output">
          {step.outputs.map((operand, i) => (
            <OperandRow key={`${operand.label}-${i}`} operand={operand} headline={i === 1} />
          ))}
        </Panel>
      </div>
    </details>
  );
}

function Overview({ steps }: { steps: ForwardStep[] }) {
  const columns = ["task", "title", "constrained by", "projected", "sheet says", "hidden", "baseline"];

  return (
    <Card className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            {columns.map((name) => (
              <th
                key={name}
                className="border-b border-rule px-2.5 py-1.5 text-left text-[11px] font-bold tracking-[0.05em] whitespace-nowrap text-ink-3 uppercase"
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {steps.map((step) => (
            <tr
              key={step.entity_id}
              className={`hover:bg-rule-2 ${step.is_inconsistent ? "bg-red/5" : ""}`}
            >
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {step.label}
              </td>
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {step.title}
              </td>
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {step.driver_label ? `${step.driver_label} ${step.lag_days}d` : "own plan"}
              </td>
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {dash(step.projected_end)}
              </td>
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {dash(step.planned_end)}
              </td>
              <td
                className={`border-b border-rule-2 px-2.5 py-1.5 text-right tabular-nums ${
                  step.is_inconsistent ? "font-bold text-red" : ""
                }`}
              >
                {dash(step.propagated_days)}
              </td>
              <td className="border-b border-rule-2 px-2.5 py-1.5 whitespace-nowrap">
                {dash(step.baseline_end)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

/** The page as a pure function of its data, so it can be rendered in a test. */
export function CalculationView({
  bundle,
  schedule,
}: {
  bundle: ExplainBundle;
  schedule: GanttBundle | null;
}) {
  const worst = bundle.steps.reduce((max, s) => Math.max(max, s.propagated_days ?? 0), 0);
  const inconsistent = bundle.steps.filter((s) => s.is_inconsistent);
  const ordered = [...bundle.steps].sort(
    (a, b) => Number(b.is_inconsistent) - Number(a.is_inconsistent),
  );

  return (
    <Page
      current="/explain"
      title="Calculation"
      scope={`${bundle.project_id} · ${bundle.steps.length} tasks`}
      asof={
        inconsistent.length > 0 ? (
          <>
            <b className="font-semibold text-ink-2">{inconsistent.length}</b> whose dates
            cannot hold
          </>
        ) : (
          "every task is consistent with its dependencies"
        )
      }
    >
      <Stats>
        <Stat value={dash(bundle.project_end_planned)} label="sheet says finish" />
        <Stat value={dash(bundle.project_end_projected)} label="chain implies finish" bad />
        <Stat value={`${dash(bundle.project_slip_days)}d`} label="project slip" bad />
        <Stat value={`${worst}d`} label="worst hidden slip" bad />
        <Stat value={bundle.steps.length} label="tasks in the graph" />
        <Stat
          value={`${bundle.edges_stated} + ${bundle.edges_inferred}`}
          label="deps stated + inferred"
        />
      </Stats>

      {bundle.depends_on_inferred_edges && (
        <Note>
          Using only dependencies a person actually wrote down, the projection is{" "}
          {dash(bundle.stated_only_end)} instead of {dash(bundle.project_end_projected)}. The
          difference rests on edges inferred from the sheet's own dates — confirm it before
          quoting the date externally.
        </Note>
      )}

      {schedule && (
        <Section title="Schedule — the same projection, drawn">
          <Card>
            <GanttChart bundle={schedule} showKey={false} />
            <p className="mt-3 mb-0 text-[13px] text-ink-2">
              The red segment on each bar is what the derivation below computes: the days
              the dependency chain adds that the spreadsheet does not show.
            </p>
          </Card>
        </Section>
      )}

      <Section title="How each date is derived">
        {ordered.map((step) => (
          <Derivation key={step.entity_id} step={step} />
        ))}
      </Section>

      <Section title="Overview — all tasks">
        <Overview steps={bundle.steps} />
      </Section>

      {bundle.refused_edges.length > 0 && (
        <Section title="Dependencies the graph refused">
          <Card>
            {bundle.refused_edges.map((edge) => (
              <div
                key={`${edge.predecessor}-${edge.successor}`}
                className="py-0.5 font-mono text-[12.5px]"
              >
                {edge.predecessor.split(":").pop()} → {edge.successor.split(":").pop()} :{" "}
                {edge.reason}
              </div>
            ))}
          </Card>
        </Section>
      )}

      <Section title="Rule inputs">
        <Card>
          <div className="grid gap-x-5 gap-y-0.5 [grid-template-columns:repeat(auto-fit,minmax(240px,1fr))]">
            {Object.keys(bundle.scalars)
              .sort()
              .map((key) => (
                <div
                  key={key}
                  className="flex justify-between gap-2.5 border-b border-dotted border-rule-2 py-1 text-[12.5px]"
                >
                  <span className="text-ink-2">{key}</span>
                  <span className="font-mono tabular-nums">
                    {String((bundle.scalars as Record<string, unknown>)[key])}
                  </span>
                </div>
              ))}
          </div>
        </Card>
      </Section>
    </Page>
  );
}

export function Calculation() {
  const [bundle, setBundle] = useState<ExplainBundle | null>(null);
  const [schedule, setSchedule] = useState<GanttBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    load<ExplainBundle>("/api/explain").then(setBundle, setProblem);
    // The chart is orientation on this tab; the derivations are the point. A
    // failure here drops the chart rather than the page.
    load<GanttBundle>("/api/gantt").then(setSchedule, () => setSchedule(null));
  }, []);

  if (problem) {
    return (
      <Page current="/explain" title="Calculation" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return (
      <Page current="/explain" title="Calculation" subtitle="Loading..." children={null} />
    );
  }
  return <CalculationView bundle={bundle} schedule={schedule} />;
}
