/* The Project canvas - resolves which project the same way every other
   project-scoped page does (`api.ts#currentProject`, set by the rail's
   `ProjectSwitcher`), then hands off to the shared canvas. */
import { currentProject } from "../api";
import { DashboardCanvas } from "../components/DashboardCanvas";
import { Page, Problem } from "../components/Shell";

export function ProjectDashboardPage() {
  const selection = currentProject();

  if (!selection) {
    return (
      <Page current="/portfolio" title="Project Dashboard" subtitle="No project selected">
        <Problem
          title="No project selected"
          detail="Pick a project from the switcher in the top bar, or open one from the Portfolio."
        />
      </Page>
    );
  }

  return (
    <DashboardCanvas
      scopeType="project"
      scopeId={selection.id}
      title="Project Dashboard"
    />
  );
}
