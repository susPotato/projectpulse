import type { components } from "./api-types";

/*
  The API surface, typed from the server's own OpenAPI schema.

  These aliases are the only place the generated names are reached into, so a
  schema change surfaces here rather than in a dozen components.
*/
export type InsightBundle = components["schemas"]["InsightBundle"];
export type Finding = components["schemas"]["Finding"];
export type CausalLink = components["schemas"]["CausalLink"];
export type RuleTrace = components["schemas"]["RuleTrace"];
export type EvidenceRef = components["schemas"]["EvidenceRef"];
export type DataQuality = components["schemas"]["DataQuality"];
export type DeliveryConfidence = components["schemas"]["DeliveryConfidence"];

export type ExplainBundle = components["schemas"]["ExplainBundle"];
export type ForwardStep = components["schemas"]["ForwardStep"];
export type ScenarioBundle = components["schemas"]["ScenarioBundle"];
export type ForecastBundle = components["schemas"]["ForecastBundle"];
export type Scenario = components["schemas"]["Scenario"];
export type PortfolioBundle = components["schemas"]["PortfolioBundle"];
export type ProjectRow = components["schemas"]["ProjectRow"];
export type TeamBundle = components["schemas"]["TeamBundle"];
export type Member = components["schemas"]["Member"];
export type BurnSeries = components["schemas"]["BurnSeries"];
export type BurnPoint = components["schemas"]["BurnPoint"];
export type ProgramBundle = components["schemas"]["ProgramBundle"];
export type Calc = components["schemas"]["Calc"];
export type Operand = components["schemas"]["Operand"];

export type GanttBundle = components["schemas"]["GanttBundle"];

export type RiskBundle = components["schemas"]["RiskBundle"];
export type ChatMessage = components["schemas"]["ChatMessage"];
export type ChatResponse = components["schemas"]["ChatResponse"];
export type RiskOut = components["schemas"]["RiskOut"];
export type RiskIn = components["schemas"]["RiskIn"];
export type RiskMatrixCell = components["schemas"]["RiskMatrixCell"];
export type RiskDraftBundle = components["schemas"]["RiskDraftBundle"];
export type CitedTask = components["schemas"]["CitedTask"];
export type ProjectOption = components["schemas"]["ProjectOption"];

export type ReportOptions = components["schemas"]["ReportOptions"];
export type ReportPreview = components["schemas"]["ReportPreview"];
export type ReportBlock = components["schemas"]["ReportBlock"];
export type ReportSection = components["schemas"]["ReportSection"];

export type ProgramSummary = components["schemas"]["ProgramSummary"];
export type ProgramListBundle = components["schemas"]["ProgramListBundle"];
export type ProgramRollupBundle = components["schemas"]["ProgramRollupBundle"];
export type CreatedProgram = components["schemas"]["CreatedProgram"];
export type CreatedProject = components["schemas"]["CreatedProject"];
export type ResourceRow = components["schemas"]["ResourceRow"];
export type ResourceConflict = components["schemas"]["ResourceConflict"];

export type CatalogueBundle = components["schemas"]["CatalogueBundle"];
export type TileSpecOut = components["schemas"]["TileSpecOut"];
export type DashboardOut = components["schemas"]["DashboardOut"];
export type TileOut = components["schemas"]["TileOut"];
export type TileIn = components["schemas"]["TileIn"];
export type DashboardScope = "program" | "project";

export type CustomChartDraft = components["schemas"]["CustomChartDraft"];
export type CustomTileIn = components["schemas"]["CustomTileIn"];
export type CustomTileOut = components["schemas"]["CustomTileOut"];
export type LiveSource = components["schemas"]["LiveSource"];
export type CustomTileListBundle = components["schemas"]["CustomTileListBundle"];
export type DraftChange = components["schemas"]["DraftChange"];
export type TileChatResponse = components["schemas"]["TileChatResponse"];
export type TileChatMessage = components["schemas"]["ChatMessage"];
export type ChartType = "bar" | "line" | "pie";

/** What went wrong, in terms a reader can act on rather than a status code. */
export interface ApiProblem {
  title: string;
  detail: string;
  fix?: string;
}

/**
 * Which project every project-scoped page reads, so switching it in one
 * place (the rail's project picker) carries across a full page navigation -
 * this app has no client router, each page is its own load.
 *
 * `?project=` in the URL wins and is remembered for next time; absent that,
 * the last remembered choice is used; absent that too, every endpoint's own
 * server-side default (today, the one demo project) applies untouched - so a
 * page nobody has pointed a picker at yet behaves exactly as it always did.
 */
/*
  Query params, or none at all when there is no DOM.

  `web/scripts/smoke.tsx` renders every view server-side, where `window` does
  not exist - so reaching straight for `window.location` there throws before a
  page renders a single node, and the check that exists to catch layout and
  data bugs cannot run at all. Absent a DOM there is no URL and no storage, so
  "nothing is selected" is the honest answer and every caller already handles
  it. Same reasoning as the try/catch around localStorage below.
*/
function searchParams(): URLSearchParams {
  if (typeof window === "undefined") return new URLSearchParams();
  return new URLSearchParams(window.location.search);
}

function storage(): Storage | null {
  if (typeof window === "undefined") return null;
  return window.localStorage;
}

const STORAGE_KEY = "pulse.project";

export interface ProjectSelection {
  id: string;
  also: string[];
}

export function currentProject(): ProjectSelection | null {
  const params = searchParams();
  const fromUrl = params.get("project");
  if (fromUrl) {
    const selection = { id: fromUrl, also: params.getAll("also") };
    try {
      storage()?.setItem(STORAGE_KEY, JSON.stringify(selection));
    } catch {
      // Private window, cleared storage, or storage blocked - the URL param
      // still won this page load, which is all correctness requires here.
    }
    return selection;
  }

  try {
    const stored = storage()?.getItem(STORAGE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as ProjectSelection;
    return parsed.id ? { id: parsed.id, also: parsed.also ?? [] } : null;
  } catch {
    return null;
  }
}

/**
 * Append the current project selection, if any, to an API path - so the page
 * you're already on asks for the project it is showing.
 */
export function withProject(path: string): string {
  const selection = currentProject();
  if (!selection) return path;
  return projectLink(path, { project_id: selection.id, source_ids: selection.also });
}

/**
 * Which program the Programs list / Program dashboard reads - same
 * `?program=` + localStorage pattern as `currentProject`, kept as a separate
 * key and a separate function rather than folded into it: a program and a
 * project are different axes, and a page can be scoped to either without the
 * other changing underneath it.
 */
const PROGRAM_STORAGE_KEY = "pulse.program";

export function currentProgram(): string | null {
  const params = searchParams();
  const fromUrl = params.get("program");
  if (fromUrl) {
    try {
      storage()?.setItem(PROGRAM_STORAGE_KEY, fromUrl);
    } catch {
      // See currentProject - a private window losing the remembered choice
      // does not change what this page load resolves to.
    }
    return fromUrl;
  }
  try {
    return storage()?.getItem(PROGRAM_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function programLink(href: string, programId: string): string {
  const search = new URLSearchParams();
  search.set("program", programId);
  return `${href}${href.includes("?") ? "&" : "?"}${search.toString()}`;
}

/**
 * A link to a *specific* project's page, from a row that names it - the
 * Program board's per-project links, which pick a project rather than
 * reading the ambient one `withProject` uses.
 */
export function projectLink(
  href: string,
  row: { project_id: string; source_ids: string[] },
): string {
  const search = new URLSearchParams();
  search.set("project", row.project_id);
  for (const id of row.source_ids) {
    if (id !== row.project_id) search.append("also", id);
  }
  return `${href}${href.includes("?") ? "&" : "?"}${search.toString()}`;
}

/**
 * Fetch one endpoint, mapping failures onto something the UI can render.
 *
 * The server distinguishes an unreachable database (503) from an empty one
 * (404), so the page can name which - a bare failure reads as a broken product
 * rather than a container that is not running.
 */
export async function load<T>(path: string): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path);
  } catch (error) {
    const offline = typeof window !== "undefined" && window.location.protocol === "file:";
    throw {
      title: offline ? "Opened as a file, not served" : "Could not reach the server",
      detail: offline
        ? `This page fetches ${path}, which needs the app running.`
        : String(error),
      fix: "python -m scripts.replay\npython -m scripts.demo",
    } satisfies ApiProblem;
  }

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    const detail =
      (body as { detail?: string } | null)?.detail ?? `HTTP ${response.status}`;
    if (response.status === 503) {
      throw { title: "Database unreachable", detail, fix: "docker compose up -d" };
    }
    if (response.status === 404) {
      throw { title: "No data yet", detail, fix: "python -m scripts.replay" };
    }
    throw { title: `Could not load ${path}`, detail };
  }

  return body as T;
}

/**
 * The admin token, if somebody has unlocked this browser tab.
 *
 * `sessionStorage`, not `localStorage`, and the same key `/llm` writes - it is
 * a credential, so it should not outlive the tab, and there should be exactly
 * one place a person pastes it.
 *
 * Absent on an ordinary visit, which is the normal case: only the routes that
 * spend money on the deployment's own API key, or destroy a project, ask for
 * it. Everything else on this app is readable and writable without one.
 */
function adminToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.sessionStorage.getItem("pulse-admin-token") ?? "";
  } catch {
    // Private window or storage blocked - the same as not having one.
    return "";
  }
}

/**
 * The write half of `load` - same error shape, for the screens where a
 * person's own data is being changed rather than the engine's findings being
 * rendered.
 */
export async function send<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH" | "DELETE",
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body) headers["Content-Type"] = "application/json";
  const token = adminToken();
  if (token) headers["X-Pulse-Admin-Token"] = token;

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: Object.keys(headers).length ? headers : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw { title: "Could not reach the server", detail: String(error) } satisfies ApiProblem;
  }

  if (!response.ok) {
    const problem: unknown = await response.json().catch(() => null);
    const detail = (problem as { detail?: string } | null)?.detail ?? `HTTP ${response.status}`;
    // A 403 here is not a bug and not a broken product: it is the deployment
    // declining to spend money, or delete a project, for someone it cannot
    // identify. Say where the key is rather than reporting the status code.
    if (response.status === 403) {
      throw {
        title: "This action needs an admin token",
        detail,
        fix: "Open /llm and paste your admin token to unlock this tab.",
      } satisfies ApiProblem;
    }
    throw { title: `${method} ${path} failed`, detail } satisfies ApiProblem;
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** A dash, never a zero: unknown and "none" are different claims. */
export function dash(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "-" : String(value);
}
