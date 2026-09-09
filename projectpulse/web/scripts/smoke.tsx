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
import type {
  ExplainBundle,
  GanttBundle,
  InsightBundle,
  PortfolioBundle,
  ReportOptions,
  ReportPreview,
  ScenarioBundle,
  TeamBundle,
} from "../src/api";
import { CalculationView } from "../src/pages/Calculation";
import { InsightView } from "../src/pages/Insight";
import { PortfolioView } from "../src/pages/Portfolio";
import { TeamView } from "../src/pages/Team";
import { ReportsView } from "../src/pages/Reports";

function read<T>(name: string): T {
  const path = new URL(`./${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

const insight = read<InsightBundle>("insight");
const explain = read<ExplainBundle>("explain");
const gantt = read<GanttBundle>("gantt");
const scenarios = read<ScenarioBundle>("scenarios");
const portfolio = read<PortfolioBundle>("portfolio");
const team = read<TeamBundle>("team");
const reportOptions = read<ReportOptions>("report_options");
const reportPreview = read<ReportPreview>("report_preview");

const cases: Array<[string, string, string[]]> = [
  [
    "Insight",
    renderToString(
      <InsightView bundle={insight} explain={explain} scenarios={scenarios} />,
    ),
    [
      // The rail must agree with the static pages - `tests/test_api.py`
      // compares the links; this checks the labels render at all.
      "Console",
      "Schedule",
      "Insight",
      "Calc",
      "Settings",
      // The outlook panel: the figure the page now leads with. Both dates and
      // the slip, so a hero that silently lost its numbers fails here.
      "The sheet says",
      "Its dependencies imply",
      "days already in the plan",
      // How much to trust the figure above - coverage x freshness, as a band.
      // A missing chip here means the wiring from InsightBundle silently broke.
      "confidence",
      // The chain that produces that date, and the pressure tiles.
      "The chain that moves the date",
      // The AI-analysis panel: detected, the reason, the evidence, the rule.
      "AI analysis",
      "Detected",
      "best-evidenced cause",
      // Recovery scenarios: priced in dates and days, never in confidence.
      "Recovery scenarios",
      "Doing nothing",
      "best available",
      "What this cannot tell you",
      // The within-project views exist and the default one is Overview.
      "Overview",
      "Risk",
      "Evidence",
      // The AI-analysis panel and how well evidenced its claim is.
      "The plan cannot hold",
      "stated dependency",
      // The four questions, split on the server's own headings.
      "What is at risk",
      "Why it is happening",
      // The pressure tiles, the rule traces and the data-quality panel moved to
      // the Risk and Evidence views - asserted in their own cases below, so
      // that a section quietly vanishing still fails something.
    ],
  ],
  [
    "Insight - Risk view",
    renderToString(
      <InsightView
        bundle={insight}
        explain={explain}
        scenarios={scenarios}
        view="Risk"
      />,
    ),
    ["Where the pressure is", "Findings", "The plan cannot hold", "milestones at risk"],
  ],
  [
    "Insight - Evidence view",
    renderToString(<InsightView bundle={insight} view="Evidence" />),
    ["What this analysis could not use", "source rows", "dependencies inferred"],
  ],
  [
    "Portfolio",
    renderToString(<PortfolioView bundle={portfolio} />),
    [
      "Program status",
      "Largest unrecorded slip",
      "Needing attention today",
      "Project portfolio",
      "no sheet ingested",
      // The band, never a score.
      "worst of its projects",
    ],
  ],
  [
    "Team",
    renderToString(<TeamView bundle={team} />),
    [
      "Workload on one window",
      "Effort logged against plan",
      "Logged against planned, by owner",
      "What moved, by week",
      // Two series, so a legend is always present. Once per chart.
      "plan - what the sheet says",
      "beyond the plan - what the chain implies",
      "logged - cumulative, from observed changes",
      // The figure and its unit are interpolated, so server-side rendering
      // splits this text node with a comment marker. Assert the contiguous
      // halves rather than the sentence a reader sees.
      "planned - ",
      "h of estimates",
      "bounded - seen by comparing two snapshots",
      // The burn's headline, and the stall it exists to make visible.
      "effort logged of planned",
      "nothing logged since ",
      // The panel that keeps it honest. Both of its original entries came
      // true, so the assertions moved with them rather than being deleted -
      // an empty "cannot show" panel would mean the discipline had lapsed.
      "What these sheets cannot show",
      "A planned line that moves",
      "estimate_hours",
      "Productivity, as output per unit of effort",
      // Someone on the worklog but not the schedule is said, not hidden.
      "appears only on the worklog",
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
  [
    "Reports",
    renderToString(
      <ReportsView
        options={reportOptions}
        preview={reportPreview}
        chosen={reportPreview.resolved_sections}
        presetId={reportOptions.default_preset}
      />,
    ),
    [
      // The rail, as on every other page.
      "Console",
      "Insight",
      "Reports",
      // The three audiences, served from `exports/document.py` rather than
      // hardcoded here - so this fails if the catalogue stops being served.
      "Weekly status",
      "Steering committee",
      "Executive brief",
      // All three formats are offered. Word may be greyed out where the extra
      // is missing, but it must still be listed with its reason.
      "Markdown",
      "Excel",
      "Word",
      // The blank input templates, which had routes and no link for a long time.
      "Blank input templates",
      "Schedule",
      "Worklog",
      // The preview is the document: its own title, the provenance line that no
      // preset can switch off, and real finding text from the bundle.
      "Delivery status",
      "Reflects the project as at",
      "The plan cannot hold",
      // A section the preset includes, proving the block walker ran.
      "What this analysis could not use",
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
