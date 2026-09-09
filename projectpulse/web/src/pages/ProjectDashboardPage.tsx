/* The Project canvas - resolves which project the same way every other
   project-scoped page does (`api.ts#currentProject`, set by the rail's
   `ProjectSwitcher`), then hands off to the shared canvas.

   `ProjectSwitcher` shows a project as selected even before anyone has
   picked one - it falls back to `bundle.projects[0]`, the worst-ranked
   project - which made this page a dead end: it required an *explicit*
   `currentProject()` and had nothing to offer when there wasn't one, even
   though the switcher sitting right there in the header looked already set.
   Falling back to that same top project here, rather than erroring, is what
   makes "click into a project" work on the very first visit - and it keeps
   this page consistent with every other project-scoped page (Insight, Risk,
   Team, ...), which already default server-side rather than blocking. */
import { useEffect, useState } from "react";
import { currentProject, load, type ApiProblem, type PortfolioBundle } from "../api";
import { DashboardCanvas } from "../components/DashboardCanvas";
import { Page, Problem } from "../components/Shell";

export function ProjectDashboardPage() {
  const explicit = currentProject();
  const [fallbackId, setFallbackId] = useState<string | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);

  useEffect(() => {
    if (explicit) return;
    load<PortfolioBundle>("/api/portfolio").then((bundle) => {
      setFallbackId(bundle.projects[0]?.project_id ?? "");
    }, setProblem);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const projectId = explicit?.id ?? fallbackId;

  if (problem) {
    return (
      <Page current="/programs" title="Project Dashboard" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }

  if (projectId === "") {
    return (
      <Page current="/programs" title="Project Dashboard" subtitle="No projects yet">
        <Problem
          title="No projects yet"
          detail="Nothing has been ingested for any project. Open a Program from the Programs list, or run a sync first."
        />
      </Page>
    );
  }

  if (projectId === null) {
    return (
      <Page current="/programs" title="Project Dashboard" subtitle="Loading..." children={null} />
    );
  }

  return <DashboardCanvas scopeType="project" scopeId={projectId} title="Project Dashboard" />;
}
