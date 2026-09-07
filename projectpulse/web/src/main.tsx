import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { Insight } from "./pages/Insight";
import { Calculation } from "./pages/Calculation";

/*
  Two routes, so no router dependency.

  FastAPI serves this bundle at both /insight and /explain, and the page picks
  itself from the path. A router would add a dependency and a second source of
  truth about which URLs exist - the tab bar in Shell.tsx already declares them.
*/
const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

const path = window.location.pathname.replace(/\/+$/, "");
const page = path === "/explain" ? <Calculation /> : <Insight />;

createRoot(root).render(<StrictMode>{page}</StrictMode>);
