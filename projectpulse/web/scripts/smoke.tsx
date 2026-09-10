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
  ForecastBundle,
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
import { CustomTileModal } from "../src/components/CustomTileModal";
import { Agent } from "../src/pages/Agent";

function read<T>(name: string): T {
  const path = new URL(`./${name}.json`, import.meta.url);
  return JSON.parse(readFileSync(path, "utf-8")) as T;
}

const insight = read<InsightBundle>("insight");
const explain = read<ExplainBundle>("explain");
const gantt = read<GanttBundle>("gantt");
const scenarios = read<ScenarioBundle>("scenarios");
const forecast = read<ForecastBundle>("forecast");
const portfolio = read<PortfolioBundle>("portfolio");
const team = read<TeamBundle>("team");
const reportOptions = read<ReportOptions>("report_options");
const reportPreview = read<ReportPreview>("report_preview");

const cases: Array<[string, string, string[]]> = [
  [
    "Insight",
    renderToString(
      <InsightView
        bundle={insight}
        explain={explain}
        scenarios={scenarios}
        forecast={forecast}
      />,
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
      // The forecast panel. The three figures a reader would quote, and - just
      // as load-bearing - the three things that must never leave the panel
      // with them: the sample size, the sample, and the assumption.
      "Delivery forecast",
      // The percentile labels are `P{point.percentile}`, which server-side
      // rendering splits with an HTML comment - see the JSX gotcha in
      // CLAUDE.md section 6. The finish dates are whole interpolations, and
      // the forecast is seeded from its own sample, so they are stable.
      "2026-07-26",
      "2026-08-19",
      "observed drift(s)",
      "What the range is built from",
      "keeps drifting the way it has been drifting",
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
  [
    // The AI tile builder, in the state it opens in. It holds its draft and
    // transcript in component state, so what this can reach is the opening
    // screen - enough to catch the crash-on-mount and missing-copy class of
    // bug, which is what has actually bitten these pages before. The turn
    // logic itself is covered where it lives, in `tests/test_dashboard.py`.
    "CustomTileModal",
    renderToString(
      <CustomTileModal
        scopeType="project"
        scopeId="excel:Project:1:HRMS"
        existingTiles={[]}
        onClose={() => {}}
        onAdded={() => {}}
      />,
    ),
    [
      "Custom Tile",
      "Build with AI",
      "My Custom Tiles",
      "Paste your data",
      // The honesty line: this surface reads and rearranges, it does not
      // author numbers. If it disappears, the exception stops being labelled.
      "The numbers stay yours",
      // The composer, in its pre-draft wording.
      "Draft it",
    ],
  ],
  [
    // The Agent tab, which now has two modes with deliberately different
    // guarantees. It opens on Chat, so what this reaches is that mode plus
    // the switch - enough to catch the crash-on-mount and missing-copy class
    // of bug. The builder's own turn logic is covered in
    // `tests/test_dashboard.py`, and the modal case above renders it.
    "Agent",
    renderToString(<Agent />),
    [
      // Both modes are named and reachable.
      "Chat",
      "Build a tile",
      // The chat-mode banner: the honesty label on the one surface in this
      // app with no engine behind it. If it goes, so does the warning.
      "nothing here is checked against a rule or a graph",
      // A preset, proving the list rendered rather than being empty.
      "Draft a status update",
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
  // The modal is a dialog rather than a page, so it clears a lower bar.
  const floor = name === "CustomTileModal" ? 1200 : 2000;
  if (html.length < floor) {
    failed += 1;
    console.log("  TOO SHORT - the view rendered almost nothing");
  }
}

console.log(failed ? `\n${failed} check(s) failed` : "\nall checks passed");
process.exit(failed ? 1 : 0);
