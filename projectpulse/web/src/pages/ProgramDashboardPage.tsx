/* The Program canvas - resolves which program from `?program=`/localStorage
   (see `api.ts#currentProgram`), then hands off to the shared canvas. */
import { useEffect, useState } from "react";
import { currentProgram, load, type ProgramListBundle } from "../api";
import { DashboardCanvas } from "../components/DashboardCanvas";
import { Page, Problem } from "../components/Shell";

export function ProgramDashboardPage() {
  const [name, setName] = useState<string | null>(null);
  const programId = currentProgram();

  useEffect(() => {
    if (!programId) return;
    load<ProgramListBundle>("/api/programs").then(
      (bundle) => setName(bundle.programs.find((p) => p.id === programId)?.name ?? programId),
      () => setName(programId),
    );
  }, [programId]);

  if (!programId) {
    return (
      <Page current="/programs" title="Program Dashboard" subtitle="No program selected">
        <Problem
          title="No program selected"
          detail="Open a program from the Programs list first."
        />
        <a href="/programs" className="mt-3 inline-block text-[12.5px] font-semibold text-navy hover:underline">
          Go to Programs &rarr;
        </a>
      </Page>
    );
  }

  return (
    <DashboardCanvas
      scopeType="program"
      scopeId={programId}
      title="Program Dashboard"
      scopeLabel={name ?? undefined}
    />
  );
}
