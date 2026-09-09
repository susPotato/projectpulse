import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Insight } from "./pages/Insight";
import { Calculation } from "./pages/Calculation";
import { Portfolio } from "./pages/Portfolio";
import { Team } from "./pages/Team";
import { Risk } from "./pages/Risk";
import { Agent } from "./pages/Agent";
import { Reports } from "./pages/Reports";

/*
  Four routes, so no router dependency.

  FastAPI serves this bundle at /portfolio, /team, /insight and /explain, and the page
  picks itself from the path. A router would add a dependency and a second
  source of truth about which URLs exist - the rail in Shell.tsx already
  declares them.
*/
const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

const path = window.location.pathname.replace(/\/+$/, "");
const PAGES: Record<string, React.ReactElement> = {
  "/explain": <Calculation />,
  "/portfolio": <Portfolio />,
  "/team": <Team />,
  "/risk": <Risk />,
  "/agent": <Agent />,
  "/reports": <Reports />,
};
const page = PAGES[path] ?? <Insight />;

createRoot(root).render(<StrictMode>{page}</StrictMode>);
