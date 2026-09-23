/*
  The motion primitives, in one file.

  The CSS half of the system lives in `index.css` (and, for the hand-written
  pages, in `app/api/static/shell.css`): durations, curves, keyframes and the
  `.rise` / `.stagger` / `.draw` utilities. What needs JavaScript is only the
  part CSS cannot see - whether an element has been scrolled to, and counting
  a number up to a value.

  Two rules hold everywhere in here:

  1. **The final state is the rendered state.** Every component below can be
     rendered on the server, in a test, or with animation switched off, and
     produces exactly the markup it settles on. Nothing is revealed *by* an
     animation, so nothing is lost when there is none. That is what keeps
     `renderToString` honest and what keeps the page usable to a reader who
     has asked their OS for less movement.

  2. **`prefers-reduced-motion` is obeyed at the source, not by shortening.**
     A hook reads it once and the components skip the animation entirely.
     The CSS `@media` block is the backstop for the class-driven half.
*/
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ElementType,
  type ReactNode,
} from "react";

/* Reads the OS setting, and keeps reading it: a reader who turns it on while
   the page is open should not have to reload to be listened to.

   Returns `true` on the server and in any environment without `matchMedia`,
   which is the safe direction - a test harness and a print renderer both
   want the settled frame. */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return true;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });

  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(query.matches);
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  return reduced;
}

/* Has this element been scrolled into view yet?

   Latches: once true it stays true, because an element that fades out again
   when you scroll past it is a thing that fights the reader rather than
   telling them anything. `rootMargin` fires slightly before the edge so a
   panel is settled by the time it is actually readable.

   Returns `true` immediately where there is no observer - a server render, an
   old engine - so the content is never gated behind a feature check. */
export function useInView<T extends HTMLElement>(): [
  React.RefObject<T | null>,
  boolean,
] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(
    () => typeof IntersectionObserver === "undefined",
  );

  useEffect(() => {
    if (seen) return;
    const node = ref.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setSeen(true);
          observer.disconnect();
        }
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.01 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [seen]);

  return [ref, seen];
}

/* A block that arrives when you reach it.

   Used for panels below the fold. Above the fold the `.stagger` class on a
   container is cheaper and needs no observer - reach for this only where the
   content is far enough down that a reader would otherwise arrive after the
   animation already played to an empty screen. */
export function Reveal({
  children,
  as: Tag = "div",
  delay = 0,
  className = "",
}: {
  children: ReactNode;
  as?: ElementType;
  delay?: number;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const [ref, inView] = useInView<HTMLDivElement>();

  if (reduced) return <Tag className={className}>{children}</Tag>;

  return (
    <Tag
      ref={ref}
      className={`${inView ? "rise" : "opacity-0"} ${className}`}
      style={inView && delay ? { animationDelay: `${delay}ms` } : undefined}
    >
      {children}
    </Tag>
  );
}

/* A figure that counts up to itself.

   Only for figures a reader is meant to register as a quantity - a KPI, a
   verdict count. Never for an identifier, a date, or anything in a table
   column, where a digit in motion is just a digit you cannot read.

   Eased out, so it decelerates into the answer rather than stopping dead.
   The integer part is snapped at each frame; `decimals` exists for the rare
   rate, and the same count of decimals is shown the whole way so the string
   never changes width mid-flight. */
export function CountUp({
  value,
  decimals = 0,
  prefix = "",
  suffix = "",
  className = "",
}: {
  value: number;
  decimals?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}) {
  const reduced = useReducedMotion();
  const format = useMemo(
    () =>
      new Intl.NumberFormat(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      }),
    [decimals],
  );

  /* Starts at zero when it is going to animate, and at the answer when it
     is not - so a reduced-motion reader, a server render and a print never
     see a figure they would have to wait out. */
  const [shown, setShown] = useState(() => (reduced ? value : 0));
  /* Where the next run counts *from*. Null until the first run, which is
     what makes the first paint count up from zero while a later change
     animates the change itself rather than collapsing to zero and
     re-climbing - that would read as the number having dropped. */
  const target = useRef<number | null>(null);

  useEffect(() => {
    if (reduced) {
      target.current = value;
      setShown(value);
      return;
    }
    const start = target.current ?? 0;
    target.current = value;
    if (start === value) {
      setShown(value);
      return;
    }

    let frame = 0;
    const began = performance.now();
    const step = (now: number) => {
      const t = Math.min(1, (now - began) / 760);
      // The same decelerating curve as `--ease-out`, in one dimension.
      const eased = 1 - Math.pow(1 - t, 3);
      setShown(start + (value - start) * eased);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [value, reduced]);

  return (
    <span className={`tabular-nums ${className}`}>
      {prefix}
      {format.format(decimals ? shown : Math.round(shown))}
      {suffix}
    </span>
  );
}

/* The loading state, at the size of the thing that is coming.

   Rule 7 of the house style wants four states and wants two of them to be
   distinguishable: this is *waiting*, and it is deliberately shaped like the
   content so the layout does not jump when the content lands. "Nothing was
   supplied" and "nothing is wrong" are sentences, not shimmer, and they are
   written where they occur. */
export function Skeleton({
  rows = 3,
  className = "",
}: {
  rows?: number;
  className?: string;
}) {
  return (
    <div
      className={`grid gap-2 ${className}`}
      role="status"
      aria-label="Loading"
    >
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="skeleton h-3.5"
          // Ragged, like text. A stack of identical bars reads as a table
          // that failed to load rather than as content on its way.
          style={{ width: `${[100, 82, 91, 74, 96][i % 5]}%` }}
        />
      ))}
    </div>
  );
}

/* The same, shaped like a row of KPI tiles. */
export function SkeletonStats({ count = 4 }: { count?: number }) {
  return (
    <div
      className="mb-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(164px,1fr))]"
      role="status"
      aria-label="Loading"
    >
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="rounded-lg border border-rule bg-surface px-3.5 py-3">
          <div className="skeleton h-6 w-16" />
          <div className="skeleton mt-2 h-2.5 w-24" />
        </div>
      ))}
    </div>
  );
}
