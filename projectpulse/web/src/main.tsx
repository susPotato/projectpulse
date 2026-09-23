import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { nextPath, session, signInFromUrl, type Session } from "./auth";
import { Login } from "./pages/Login";
import { Insight } from "./pages/Insight";
import { Portfolio } from "./pages/Portfolio";
import { Team } from "./pages/Team";
import { Risk } from "./pages/Risk";
import { Agent } from "./pages/Agent";
import { Reports } from "./pages/Reports";
import { Programs } from "./pages/Programs";
import { Projects } from "./pages/Projects";
import { ProgramDashboardPage } from "./pages/ProgramDashboardPage";
import { ProjectDashboardPage } from "./pages/ProjectDashboardPage";

/*
  So no router dependency.

  FastAPI serves this bundle at /, /portfolio, /team and /insight,
  and the page picks itself from the path. A router would add a dependency
  and a second source of truth about which URLs exist - the rail in
  Shell.tsx already declares them.

  "/" is the Programs list - a PM opening the app should land at the top of
  the Program -> Project hierarchy, not a flat cross-program ranking.
  `/portfolio` still renders the old flat "every project, ranked" view (kept
  for anyone who has it bookmarked, or wants the whole-portfolio cut) - it
  just no longer owns the rail's one "Program" tab, which now opens `/programs`.
*/
const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

const path = window.location.pathname.replace(/\/+$/, "");
const PAGES: Record<string, React.ReactElement> = {
  "": <Programs />,
  "/portfolio": <Portfolio />,
  "/team": <Team />,
  "/risk": <Risk />,
  "/agent": <Agent />,
  "/reports": <Reports />,
  "/programs": <Programs />,
  "/projects": <Projects />,
  "/programs/dashboard": <ProgramDashboardPage />,
  "/project/dashboard": <ProjectDashboardPage />,
};
const page = PAGES[path] ?? <Insight />;

/*
  The sign-in gate.

  Both of these are read once, before anything renders, and in this order:
  `nextPath` first because `signInFromUrl` rewrites the address bar to strip the
  password out of it. `?next=` is how a hand-written page (Schedule, Settings,
  Jira, Trace) sends a signed-out visitor here - those pages are not in this
  bundle, so there is nothing to render in their place and we go back to where
  they were asking to be once somebody is in.
*/
const RETURN_TO = nextPath();
const SIGNED_IN = session() ?? signInFromUrl();

/*
  State rather than a reload, so signing in lands on the page you were already
  asking for with no second round trip.

  `useState` initialised from storage, not an effect: an effect would paint the
  app for one frame before deciding nobody is signed in, which is the flash of
  content this gate exists to avoid.
*/
function Gate() {
  const [user, setUser] = useState<Session | null>(SIGNED_IN);

  /* Already in and asked for somewhere else - a URL credential that came up
     through `auth.js`, or a second tab signed in while this one sat on the
     form. Rendering `null` for the instant before the navigation, because the
     Programs page is not what this visitor asked for and should not flash. */
  if (user && RETURN_TO) {
    window.location.replace(RETURN_TO);
    return null;
  }

  if (!user) {
    return (
      <Login
        onSignedIn={(signedIn) => {
          if (RETURN_TO) {
            window.location.replace(RETURN_TO);
            return;
          }
          setUser(signedIn);
        }}
      />
    );
  }
  return page;
}

createRoot(root).render(
  <StrictMode>
    <Gate />
  </StrictMode>,
);
