import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwind from "@tailwindcss/vite";

/*
  Builds into `app/api/static/app/`, and **that output is committed**.

  Judges run the code, so `python -m scripts.demo` has to be enough - it cannot
  depend on `npm install` succeeding on their machine. Committing `dist` keeps
  the runtime a single Python command while still giving us a real toolchain,
  which is what fixes the class of bug the hand-written pages kept producing:
  Vite emits content-hashed filenames and writes the references itself, so an
  asset path can never drift from what the page asks for, and a stale cache can
  never pin a fixed file.

  `base` matters: the bundle is served from /static/app/ but the routes are
  /insight and /explain, so relative asset URLs would resolve against the wrong
  directory.
*/
export default defineConfig({
  plugins: [react(), tailwind()],
  base: "/static/app/",
  build: {
    outDir: "../app/api/static/app",
    emptyOutDir: true,
    // One bundle per entry keeps load order a non-question - the `defer` race
    // that produced "PulseGantt is not defined" cannot happen inside a module.
    rollupOptions: {
      output: { manualChunks: undefined },
    },
  },
  server: {
    // `npm run dev` proxies the API so the dev server needs no CORS handling.
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/static/gantt.js": "http://127.0.0.1:8000",
      "/static/gantt.css": "http://127.0.0.1:8000",
    },
  },
});
