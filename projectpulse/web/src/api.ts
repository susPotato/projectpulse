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

export type ReportOptions = components["schemas"]["ReportOptions"];
export type ReportPreview = components["schemas"]["ReportPreview"];
export type ReportBlock = components["schemas"]["ReportBlock"];
export type ReportSection = components["schemas"]["ReportSection"];

/** What went wrong, in terms a reader can act on rather than a status code. */
export interface ApiProblem {
  title: string;
  detail: string;
  fix?: string;
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
    const offline = window.location.protocol === "file:";
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
 * The write half of `load` - same error shape, for the one screen in this
 * app where a person's own data is being changed rather than the engine's
 * findings being rendered (the risk register).
 */
export async function send<T>(
  path: string,
  method: "POST" | "PUT" | "DELETE",
  body?: unknown,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw { title: "Could not reach the server", detail: String(error) } satisfies ApiProblem;
  }

  if (!response.ok) {
    const problem: unknown = await response.json().catch(() => null);
    const detail = (problem as { detail?: string } | null)?.detail ?? `HTTP ${response.status}`;
    throw { title: `${method} ${path} failed`, detail } satisfies ApiProblem;
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** A dash, never a zero: unknown and "none" are different claims. */
export function dash(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "-" : String(value);
}
