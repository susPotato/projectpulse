/*
  Render both views against a real API payload and assert what came out.

    npm run smoke

  The React equivalent of the node harness that checked the hand-written pages.
  `renderToString` needs no DOM, and the views are pure functions of their
  bundles precisely so this is possible: a build proves the types line up, this
  proves the components actually produce the page.

  The payloads are captured from the running API by `capture-payloads`, so this
  fails when the server's shape changes rather than passing against a fixture
  that has quietly gone stale.
*/
import { readFileSync } from "node:fs";
import { renderToString } from "react-dom/server";
import type { ExplainBundle, GanttBundle, InsightBundle } from "../src/api";
import { CalculationView } from "../src/pages/Calculation";
import { InsightView } from "../src/pages/Insight";

function read<T>(name: string): T {
  const path = new URL(`./${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

const insight = read<InsightBundle>("insight");
const explain = read<ExplainBundle>("explain");
const gantt = read<GanttBundle>("gantt");

const cases: Array<[string, string, string[]]> = [
  [
    "Insight",
    renderToString(<InsightView bundle={insight} />),
    [
      // The tab bar must agree with the static pages.
      "Retriever console",
      "Schedule",
      "Calculation",
      // A finding, its rule trace, and how well evidenced it is.
      "The plan cannot hold",
      "stated dependency",
      "rows_rejected",
      // The four questions, split on the server's own headings.
      "What is at risk",
      "Why it is happening",
      // Data quality is surfaced, never buried.
      "dependencies inferred",
    ],
  ],
  [
    "Calculation",
    renderToString(<CalculationView bundle={explain} schedule={gantt} />),
    [
      "sheet says finish",
      "chain implies finish",
      "worst hidden slip",
      // The three panels, and a step a reader can redo on paper.
      "Input",
      "Algorithm",
      "Output",
      "planned finish - start date",
      "27 days",
      "hidden slip",
      "Step through",
      "Overview",
      "Rule inputs",
      "max_propagated_days",
    ],
  ],
];

let failed = 0;

for (const [name, html, needles] of cases) {
  console.log(`--- ${name}: ${html.length} chars ---`);
  for (const needle of needles) {
    const found = html.includes(needle);
    if (!found) failed += 1;
    console.log(`  ${found ? "FOUND   " : "MISSING "}${needle}`);
  }
  // A view that renders an empty shell is a silent failure, not a pass.
  if (html.length < 2000) {
    failed += 1;
    console.log("  TOO SHORT - the view rendered almost nothing");
  }
}

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
