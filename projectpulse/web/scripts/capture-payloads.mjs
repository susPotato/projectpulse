/*
  Re-capture the payloads the smoke test renders against.

    npm run payloads

  `smoke.tsx` renders every view against a real API response rather than a
  hand-written fixture, which is the only version of that test worth having: a
  fixture drifts from the server and then passes forever while the page is
  broken.

  The catch is that a captured payload drifts too, just more loudly - it fails
  the moment the server grows a field the page now reads. `smoke.tsx` has
  claimed since it was written that these files come from a `capture-payloads`
  script; the script did not exist, so the JSON was refreshed by hand and every
  schema change meant a confusing `undefined` in a stack trace before anyone
  remembered why. This is that script.

  Like `gen-types.mjs`, no server: each bundle is built by calling the pipeline
  directly, so this works with nothing listening on :8000 and cannot photograph
  a stale build.
*/
import { execFileSync } from "node:child_process";
import { writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const PYTHON = join(REPO, ".venv", "Scripts", "python.exe");

// Each entry is a `pipeline` function and the file the smoke test reads it as.
// `analyze_project` is the one that takes a narrator; it is left off so the
// captured narrative is always the deterministic template and the fixture
// cannot change because a model was reachable that day.
const BUNDLES = [
  ["insight", "analyze_project"],
  ["explain", "explain_project"],
  ["gantt", "gantt_project"],
  ["team", "team_project"],
  ["scenarios", "scenarios_project"],
  ["portfolio", "portfolio"],
];

// The report builder's two payloads are assembled by the route rather than by
// a pipeline function - the catalogue is the exporter's own, and the preview
// is a `ReportDoc`, not a bundle. Fetched through FastAPI's in-process test
// client rather than by calling the functions: a route's `Query(default=None)`
// is a sentinel object until FastAPI resolves it, so a direct call captures
// something no request would ever produce. Still no server, and still no port.
const ROUTES = [
  ["report_options", "/api/report/options"],
  ["report_preview", "/api/report/preview"],
];

const script = `
import json
from app.db import SessionLocal
from app.intelligence import pipeline
from app.scope import PORTFOLIO, also_for

project = PORTFOLIO[0].canonical_id
out = {}
with SessionLocal() as session:
    for name, fn in ${JSON.stringify(BUNDLES)}:
        call = getattr(pipeline, fn)
        # 'portfolio' spans every project and takes no single id.
        if fn == "portfolio":
            bundle = call(session)
        else:
            bundle = call(session, project_id=project, also=also_for(project))
        out[name] = bundle.model_dump(mode="json")

from fastapi.testclient import TestClient
from app.api.main import app

with TestClient(app) as client:
    for name, url in ${JSON.stringify(ROUTES)}:
        response = client.get(url)
        response.raise_for_status()
        out[name] = response.json()
print(json.dumps(out))
`;

const raw = execFileSync(PYTHON, ["-c", script], {
  cwd: REPO,
  encoding: "utf-8",
  maxBuffer: 64 * 1024 * 1024,
  env: { ...process.env, DATABASE_URL: "sqlite:///pulse.db" },
});

const captured = JSON.parse(raw);
const written = [...BUNDLES, ...ROUTES];
for (const [name] of written) {
  const path = join(HERE, `${name}.json`);
  writeFileSync(path, `${JSON.stringify(captured[name], null, 2)}\n`, "utf-8");
  console.log(`  ${name}.json`);
}
console.log(`\n${written.length} payload(s) captured. Run \`npm run smoke\` next.`);
