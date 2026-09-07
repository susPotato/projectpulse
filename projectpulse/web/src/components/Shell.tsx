/*
  The chrome every page shares: tab bar, headings, cards, stat tiles.

  Small deliberate components rather than one big layout, because the two pages
  differ in body and agree on everything around it - and the tab bar must agree
  across the *static* pages too, so its links are spelled out here once.
*/
import type { ReactNode } from "react";

const TABS = [
  { href: "/", label: "Retriever console" },
  { href: "/gantt", label: "Schedule" },
  { href: "/insight", label: "Insight" },
  { href: "/explain", label: "Calculation" },
] as const;

export function Tabs({ current }: { current: string }) {
  return (
    <nav className="flex flex-wrap gap-0.5 border-b border-rule">
      {TABS.map((tab) => {
        const active = tab.href === current;
        return (
          <a
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={
              "-mb-px rounded-t-md border border-transparent px-4 py-2 text-[13.5px] no-underline " +
              (active
                ? "border-rule border-b-surface bg-surface font-semibold text-ink"
                : "text-ink-2 hover:bg-rule-2 hover:text-ink")
            }
          >
            {tab.label}
          </a>
        );
      })}
    </nav>
  );
}

export function Page({
  current,
  title,
  subtitle,
  children,
}: {
  current: string;
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto max-w-[1240px] p-5">
      <Tabs current={current} />
      <h1 className="mt-4 mb-0.5 text-xl font-semibold">{title}</h1>
      <p className="mt-0 mb-4 text-[13px] text-ink-2">{subtitle}</p>
      {children}
    </div>
  );
}

export function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="mb-6">
      {title && (
        <h2 className="mt-6 mb-2 text-xs font-bold tracking-[0.08em] text-ink-3 uppercase">
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-rule bg-surface p-4 ${className}`}>
      {children}
    </div>
  );
}

export function Stats({ children }: { children: ReactNode }) {
  return (
    <div className="mb-3 grid gap-2 [grid-template-columns:repeat(auto-fit,minmax(150px,1fr))]">
      {children}
    </div>
  );
}

/* `tabular-nums` is deliberately absent on these: equal-width digits make a
   large standalone figure look mechanical. */
export function Stat({
  value,
  label,
  bad = false,
}: {
  value: ReactNode;
  label: string;
  bad?: boolean;
}) {
  return (
    <div className="rounded-lg border border-rule bg-surface px-3 py-2.5">
      <b className={`block text-[22px] leading-tight ${bad ? "text-red" : ""}`}>{value}</b>
      <span className="text-[11px] tracking-[0.06em] text-ink-3 uppercase">{label}</span>
    </div>
  );
}

export function Note({ children }: { children: ReactNode }) {
  return (
    <div className="mt-3 rounded-md border border-amber/40 bg-amber/10 px-3 py-2 text-[12.5px]">
      {children}
    </div>
  );
}

export function Problem({
  title,
  detail,
  fix,
}: {
  title: string;
  detail: string;
  fix?: string;
}) {
  return (
    <Card>
      <p className="m-0 font-semibold text-red">{title}</p>
      <p className="mt-1 mb-0 text-[13px] text-ink-2">{detail}</p>
      {fix && (
        <pre className="mt-3 mb-0 overflow-x-auto rounded bg-bg p-2.5 font-mono text-xs text-ink">
          {fix}
        </pre>
      )}
    </Card>
  );
}
