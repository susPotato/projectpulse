/*
  Generate TypeScript types from the API's own OpenAPI schema.

    npm run types

  Hand-written types would be a second declaration of the contract, and the two
  would drift - which is the exact failure mode the page/server coupling tests
  exist to catch at runtime. Generating them moves that check to compile time:
  rename a field in `api/schemas/insight.py` and the build fails instead of a
  panel silently rendering `undefined`.

  The output is committed, so a build never needs a running server.
*/
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = resolve(HERE, "..", "..");
const PYTHON = join(REPO, ".venv", "Scripts", "python.exe");
const SCHEMA = join(HERE, "openapi.json");
const OUT = join(HERE, "..", "src", "api-types.ts");

// Dump the schema without starting a server - `app.openapi()` is pure.
const schema = execFileSync(
  PYTHON,
  ["-c", "import json; from app.api.main import app; print(json.dumps(app.openapi()))"],
  { cwd: REPO, encoding: "utf-8", env: { ...process.env, DATABASE_URL: "sqlite:///pulse.db" } },
);

mkdirSync(dirname(SCHEMA), { recursive: true });
writeFileSync(SCHEMA, schema, "utf-8");

// Invoke the CLI's JS entry point directly. `npx.cmd` needs a shell on Windows
// (spawnSync EINVAL without one), and going through node avoids the question.
const cli = join(HERE, "..", "node_modules", "openapi-typescript", "bin", "cli.js");
execFileSync(process.execPath, [cli, SCHEMA, "-o", OUT], {
  cwd: join(HERE, ".."),
  stdio: "inherit",
});

console.log(`types written to ${OUT}`);
