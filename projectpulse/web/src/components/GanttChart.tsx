import { useEffect, useRef } from "react";
import type { GanttBundle } from "../api";

/*
  Mounts the shared vanilla Gantt.

  Deliberately a wrapper rather than a React port. The static Schedule page uses
  `PulseGantt` too, and two implementations of one chart eventually draw two
  different pictures of one projection - which is the same reason the CLI and
  this app both render the server's `Derivation` instead of formatting their own.

  `PulseGantt.render` is a pure function returning a DOM node, so wrapping it is
  a ref and an effect. If it ever needs React state, port it once and delete the
  vanilla copy - do not add a second.
*/

declare global {
  interface Window {
    PulseGantt?: {
      render(bundle: GanttBundle, opts?: { key?: boolean }): HTMLElement;
    };
  }
}

export function GanttChart({
  bundle,
  showKey = true,
}: {
  bundle: GanttBundle | null;
  showKey?: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = host.current;
    if (!node || !bundle) return;

    // The script is a classic `<script>` in index.html, so it has executed by
    // the time an effect runs - no readiness race to lose here, unlike the
    // hand-written pages where a cached fetch could beat a deferred script.
    if (!window.PulseGantt) return;

    const chart = window.PulseGantt.render(bundle, { key: showKey });
    node.replaceChildren(chart);
    return () => node.replaceChildren();
  }, [bundle, showKey]);

  if (!bundle || !bundle.rows.length) return null;

  return <div ref={host} />;
}
