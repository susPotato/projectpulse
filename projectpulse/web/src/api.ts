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

export type ExplainBundle = components["schemas"]["ExplainBundle"];
export type ForwardStep = components["schemas"]["ForwardStep"];
export type Calc = components["schemas"]["Calc"];
export type Operand = components["schemas"]["Operand"];

export type GanttBundle = components["schemas"]["GanttBundle"];

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

/** A dash, never a zero: unknown and "none" are different claims. */
export function dash(value: string | number | null | undefined): string {
  return value === null || value === undefined ? "-" : String(value);
}
