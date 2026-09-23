/*
  The chart vocabulary, in one file.

  Still hand-written SVG and CSS - no chart library, per the house rules - but
  no longer one sparkline reimplemented in five places. `tileRegistry.tsx` had
  a line chart, `Insight.tsx` had a severity bar, `Team.tsx` had another bar,
  and each had drifted: different baselines, different rounding, one of them
  with no zero line at all.

  Three form rules decide which of these you reach for, and they are the part
  worth arguing about rather than the pixels:

  1. **A proportion of a known whole is one bar, not a pie.** `<Split>` is the
     answer to "how do 173 checked items divide up" - a single stacked bar,
     the whole width is the whole, and the legend under it carries the counts.
     A reader compares lengths along one axis instead of angles around a
     circle, and the total is legible because it is the bar.
  2. **Identity lives in labels, magnitude lives in one hue.** `<BarRows>`
     labels every bar with its own name, so telling two rows apart never
     depends on telling two colours apart. Colour is reserved for status.
  3. **Circles are for four things or fewer, and then only as a donut with
     the total in the middle.** `<Donut>` degrades to `<BarRows>` past that
     automatically rather than inventing a fifth, sixth and seventh colour
     for a reader to decode. This is the rule that replaced `MiniChart`'s old
     seven-slice pie.

  Motion here obeys the same rule as everywhere else: it explains, it does
  not decorate. A line draws along its own direction of travel, a bar grows
  from its own baseline, a stacked bar wipes from the origin of its axis.
  Nothing loops. Every chart is complete in the DOM before it animates, and
  every one carries `role="img"` with an `aria-label` that states the
  takeaway - so the chart says the same thing to a reader who never sees it
  move and to one who never sees it at all.
*/
import { useId, type ReactNode } from "react";
import { useReducedMotion } from "./motion";

/* The fixed categorical order. Assigned by an entity's stable position, never
   by rank, and never cycled past the end - the seventh distinct thing in a
   chart is the signal to change chart, not to reuse navy. */
export const CATEGORICAL_ORDER = [
  "navy",
  "blue",
  "orange",
  "green",
  "red",
  "amber",
  "purple",
] as const;

export const MAX_CATEGORIES = CATEGORICAL_ORDER.length;

/* Past this many slices a circle stops being readable and `<Donut>` hands the
   data to `<BarRows>`. Four is not a round number picked for tidiness: it is
   where angle comparison stops beating length comparison for most readers. */
export const MAX_DONUT_SLICES = 4;

export function categoricalColor(index: number): string {
  const name = CATEGORICAL_ORDER[index % CATEGORICAL_ORDER.length];
  return `var(--color-${name})`;
}

const NUMBER = new Intl.NumberFormat();

export function formatCount(value: number): string {
  return NUMBER.format(value);
}

/* ---- the states every chart owes a reader ------------------------------

   Rule 7 again, at chart scale. "The stage has not run" and "the stage ran
   and found nothing" are different facts and must not share a rendering -
   an empty panel that means "all clear" and an empty panel that means "we
   never looked" is the failure this product is built against. */
export function ChartEmpty({
  reason,
  kind = "empty",
}: {
  reason: ReactNode;
  /* `empty` = measured, and the answer is none. `absent` = never measured. */
  kind?: "empty" | "absent";
}) {
  if (kind === "absent") {
    return (
      <div className="rounded border border-dashed border-rule px-3 py-4 text-label text-ink-3">
        {reason}
      </div>
    );
  }
  return <p className="m-0 text-body text-ink-3">{reason}</p>;
}

/* ---- Split: a proportion of a known whole ------------------------------

   One bar, the whole width is the whole, segments in the order given. The
   legend is the chart's axis - direct labels under a stacked bar, not a key
   floating beside it - and each entry can be a link, which is what makes a
   count on a summary page a way *into* the rows behind it.

   `tone` is deliberately not an arbitrary colour. `good` / `warn` / `bad`
   are the three status meanings the house rules reserve those hues for, and
   `unknown` is the fourth thing this product keeps needing and kept
   miscolouring: absence of evidence. Painting 95 unverified items amber
   would tell a PM they are at risk. They are not at risk, they are
   unmeasured, and that is a neutral fact with a neutral fill. */
export type SplitTone = "good" | "warn" | "bad" | "unknown" | "info";

const TONE_FILL: Record<SplitTone, string> = {
  good: "bg-green",
  warn: "bg-amber",
  bad: "bg-red",
  info: "bg-blue",
  unknown: "bg-ink-3/45",
};

const TONE_TEXT: Record<SplitTone, string> = {
  good: "text-green",
  warn: "text-amber",
  bad: "text-red",
  info: "text-blue",
  unknown: "text-ink-2",
};

export type SplitPart = {
  key: string;
  label: string;
  value: number;
  tone: SplitTone;
  /* Where this count goes when clicked. A number a reader cannot open is a
     number they have to go and find by hand. */
  href?: string;
  title?: string;
};

export function Split({
  parts,
  total,
  ariaLabel,
  height = "h-2.5",
}: {
  parts: SplitPart[];
  /* Passed rather than summed, so a bar whose parts do not account for the
     whole shows the gap instead of silently rescaling to hide it. */
  total: number;
  ariaLabel: string;
  height?: string;
}) {
  const reduced = useReducedMotion();
  const sum = parts.reduce((a, p) => a + Math.max(0, p.value), 0);
  const whole = Math.max(total, sum, 1);
  const unaccounted = Math.max(0, total - sum);

  return (
    <div className="grid gap-2.5">
      <div
        role="img"
        aria-label={ariaLabel}
        className={`flex w-full overflow-hidden rounded-full bg-rule-2 ${height} ${
          reduced ? "" : "grow-x"
        }`}
      >
        {parts.map((part) => {
          const pct = (Math.max(0, part.value) / whole) * 100;
          if (pct <= 0) return null;
          return (
            <span
              key={part.key}
              className={`${TONE_FILL[part.tone]} h-full`}
              style={{ width: `${pct}%` }}
              title={`${part.label}: ${formatCount(part.value)} of ${formatCount(total)}`}
            />
          );
        })}
        {unaccounted > 0 && (
          <span
            className="h-full bg-rule-2"
            style={{ width: `${(unaccounted / whole) * 100}%` }}
            title={`${formatCount(unaccounted)} not in any of these categories`}
          />
        )}
      </div>

      <div className="stagger flex flex-wrap gap-x-5 gap-y-1.5">
        {parts.map((part) => {
          const body = (
            <>
              <span
                aria-hidden="true"
                className={`inline-block h-2 w-2 shrink-0 rounded-full ${TONE_FILL[part.tone]}`}
              />
              <b className={`text-emph font-semibold tabular-nums ${TONE_TEXT[part.tone]}`}>
                {formatCount(part.value)}
              </b>
              <span className="text-body text-ink-2">{part.label}</span>
            </>
          );
          const shared = "inline-flex items-baseline gap-1.5";
          return part.href ? (
            <a
              key={part.key}
              href={part.href}
              title={part.title}
              className={`${shared} rounded text-ink-2 no-underline transition-colors duration-100 hover:text-ink [&>b]:underline [&>b]:decoration-rule [&>b]:underline-offset-2 hover:[&>b]:decoration-current`}
            >
              {body}
            </a>
          ) : (
            <span key={part.key} title={part.title} className={shared}>
              {body}
            </span>
          );
        })}
      </div>
    </div>
  );
}

/* ---- BarRows: one measure across named things --------------------------

   Horizontal, because the labels are words and words are horizontal. One
   hue, because this is a magnitude series and a second hue would claim a
   second meaning. The value sits at the end of its own bar, right-aligned
   and tabular, so the column of numbers reads as a column.

   `reference` draws the dashed tick this product keeps needing - planned
   against logged, budget against spend - as a mark rather than as a second
   bar, because one comparison per row is one bar and a line, not two bars
   and a legend. */
export function BarRows({
  rows,
  ariaLabel,
  max,
  labelWidth = "w-[104px]",
  format = formatCount,
  tone,
}: {
  rows: { key: string; label: string; value: number; reference?: number; href?: string; title?: string }[];
  ariaLabel: string;
  max?: number;
  labelWidth?: string;
  format?: (value: number) => string;
  /* Status colouring, per row, when the bar is genuinely about status.
     Omitted for an ordinary magnitude series, which stays one hue. */
  tone?: (row: { key: string; value: number }) => SplitTone | undefined;
}) {
  const reduced = useReducedMotion();
  // Bars start at zero. Always: a bar chart with a floating baseline
  // exaggerates every difference on it.
  const ceiling = Math.max(
    1,
    max ?? 0,
    ...rows.map((r) => Math.max(r.value, r.reference ?? 0)),
  );

  return (
    <div className="grid gap-1.5" role="img" aria-label={ariaLabel}>
      {rows.map((row, i) => {
        const pct = Math.min(100, (Math.max(0, row.value) / ceiling) * 100);
        const refPct =
          row.reference == null
            ? null
            : Math.min(100, (Math.max(0, row.reference) / ceiling) * 100);
        const fill = tone?.({ key: row.key, value: row.value });
        const inner = (
          <>
            <span
              className={`${labelWidth} shrink-0 truncate text-label text-ink-2`}
              title={row.title ?? row.label}
            >
              {row.label}
            </span>
            <span className="relative h-2.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-rule-2">
              <span
                className={`block h-full rounded-sm ${
                  fill ? TONE_FILL[fill] : "bg-navy"
                } ${reduced ? "" : "grow-x"}`}
                style={{
                  width: `${pct}%`,
                  animationDelay: reduced ? undefined : `${Math.min(i, 9) * 45}ms`,
                }}
              />
              {refPct != null && (
                <span
                  aria-hidden="true"
                  className="absolute top-0 bottom-0 w-px bg-ink-2"
                  style={{ left: `${refPct}%` }}
                  title={`planned ${format(row.reference as number)}`}
                />
              )}
            </span>
            <span className="w-[54px] shrink-0 text-right text-label tabular-nums text-ink">
              {format(row.value)}
            </span>
          </>
        );

        return row.href ? (
          <a
            key={row.key}
            href={row.href}
            className="flex items-center gap-2 rounded no-underline transition-colors duration-100 hover:bg-bg"
          >
            {inner}
          </a>
        ) : (
          <div key={row.key} className="flex items-center gap-2">
            {inner}
          </div>
        );
      })}
    </div>
  );
}

/* ---- Sparkline: one series over time -----------------------------------

   An area under the line rather than a bare stroke, at low opacity: the fill
   is what makes the trend readable at 64px tall, where a 2px line against a
   white card is mostly white card. Not a gradient - one flat tint of the same
   hue, so it survives both themes and a greyscale print.

   Zero baseline drawn and labelled, last point marked and its value written
   beside it. A sparkline whose end value you have to hover for is a decoration.

   The line draws itself along its own direction of travel, which is the only
   motion here that carries information: it is the series being read left to
   right. `pathLength` is normalised to 100 so the dash animation needs no
   measurement of the real geometry. */
const SPARK_TONE: Record<string, string> = {
  navy: "text-navy",
  blue: "text-blue",
  green: "text-green",
  orange: "text-orange",
  red: "text-red",
};

export function Sparkline({
  values,
  labels,
  ariaLabel,
  height = 64,
  tone = "navy",
}: {
  values: number[];
  labels?: string[];
  ariaLabel: string;
  height?: number;
  tone?: "navy" | "blue" | "green" | "orange" | "red";
}) {
  const reduced = useReducedMotion();
  const clipId = useId();
  // Spelled out, not interpolated: Tailwind scans source text, so a computed
  // `text-${tone}` produces no CSS at all.
  const stroke = SPARK_TONE[tone];
  const w = 260;
  const h = height;
  const pad = 3;

  if (values.length < 2) {
    return (
      <ChartEmpty reason="Not enough points yet to draw a trend." kind="absent" />
    );
  }

  const top = Math.max(1, ...values.map((v) => Math.abs(v)));
  const x = (i: number) => (i / (values.length - 1)) * w;
  const y = (v: number) => h - pad - (Math.max(0, v) / top) * (h - pad * 2);

  const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line} L${w},${h} L0,${h} Z`;
  const last = values[values.length - 1] as number;

  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      width="100%"
      className={`${stroke} block`}
      role="img"
      aria-label={ariaLabel}
      preserveAspectRatio="none"
    >
      {/* The fill is clipped to the plot so `preserveAspectRatio="none"`
          cannot smear it past the baseline when the tile is short. */}
      <clipPath id={clipId}>
        <rect x="0" y="0" width={w} height={h} />
      </clipPath>
      <g clipPath={`url(#${clipId})`}>
        <path d={area} fill="currentColor" opacity={0.12} className={reduced ? "" : "fade-in"} />
        <path
          d={line}
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
          pathLength={100}
          className={reduced ? "" : "draw"}
          style={reduced ? undefined : ({ "--len": 100 } as React.CSSProperties)}
          vectorEffect="non-scaling-stroke"
        />
      </g>
      {/* Zero, drawn. Every bar and line in this app starts here and the
          line saying so costs one pixel. */}
      <line x1={0} y1={h} x2={w} y2={h} stroke="var(--color-rule)" strokeWidth={1} />
      <circle
        cx={x(values.length - 1)}
        cy={y(last)}
        r={3}
        fill="var(--color-surface)"
        stroke="currentColor"
        strokeWidth={2}
        vectorEffect="non-scaling-stroke"
      />
      {labels && <title>{`${labels[labels.length - 1]}: ${formatCount(last)}`}</title>}
    </svg>
  );
}

/* ---- Donut: identity, four ways or fewer -------------------------------

   Stroke-based rather than wedge paths, because a stroked arc is one number
   to animate (`stroke-dashoffset`) and because the hole is where the total
   goes - which is the whole reason to prefer a donut to a pie. A pie's centre
   is ink spent saying nothing.

   Past `MAX_DONUT_SLICES` this renders bars instead. That is not a fallback,
   it is the rule: seven angles around a circle, each needing a colour nobody
   can name, is a worse chart than seven labelled bars, and the old
   seven-slice pie with an "Other" wedge was the product admitting it.
*/
export function Donut({
  parts,
  ariaLabel,
  centerLabel,
}: {
  parts: { key: string; label: string; value: number }[];
  ariaLabel: string;
  centerLabel?: string;
}) {
  const reduced = useReducedMotion();
  const total = parts.reduce((a, p) => a + Math.max(0, p.value), 0);

  if (!parts.length || total <= 0) {
    return <ChartEmpty reason="Nothing to break down." />;
  }

  if (parts.length > MAX_DONUT_SLICES) {
    return (
      <BarRows
        rows={parts
          .slice()
          .sort((a, b) => b.value - a.value)
          .map((p) => ({ key: p.key, label: p.label, value: p.value }))}
        ariaLabel={ariaLabel}
      />
    );
  }

  // r chosen so the circumference is almost exactly 100 - the dash maths then
  // reads in percent, which is what the numbers below it are.
  const r = 15.915;
  let offset = 0;

  return (
    <div className="flex items-center gap-4">
      <svg
        viewBox="0 0 42 42"
        width="100%"
        className="h-[84px] w-[84px] shrink-0 -rotate-90"
        role="img"
        aria-label={ariaLabel}
      >
        <circle
          cx="21"
          cy="21"
          r={r}
          fill="none"
          stroke="var(--color-rule-2)"
          strokeWidth={5}
        />
        {parts.map((part, i) => {
          const pct = (Math.max(0, part.value) / total) * 100;
          const dash = `${pct} ${100 - pct}`;
          const segment = (
            <circle
              key={part.key}
              cx="21"
              cy="21"
              r={r}
              fill="none"
              stroke={categoricalColor(i)}
              strokeWidth={5}
              strokeDasharray={dash}
              strokeDashoffset={-offset}
              className={reduced ? "" : "fade-in"}
              style={reduced ? undefined : { animationDelay: `${i * 90}ms` }}
            >
              <title>{`${part.label}: ${formatCount(part.value)}`}</title>
            </circle>
          );
          offset += pct;
          return segment;
        })}
        {centerLabel && (
          <text
            x="21"
            y="21"
            textAnchor="middle"
            dominantBaseline="central"
            className="rotate-90 fill-ink font-bold"
            /* A user-space size, not a type-scale utility: the glyph lives
               inside a 42-unit viewBox, so 11px would fill the hole. */
            fontSize={7}
            style={{ transformOrigin: "21px 21px" }}
          >
            {centerLabel}
          </text>
        )}
      </svg>
      <div className="stagger grid min-w-0 flex-1 gap-1">
        {parts.map((part, i) => (
          <div key={part.key} className="flex items-baseline gap-1.5 text-label">
            <span
              aria-hidden="true"
              className="h-2 w-2 shrink-0 self-center rounded-full"
              style={{ background: categoricalColor(i) }}
            />
            <span className="min-w-0 flex-1 truncate text-ink-2" title={part.label}>
              {part.label}
            </span>
            <span className="shrink-0 tabular-nums text-ink">{formatCount(part.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* ---- SplitRows: the same whole, divided, once per row ------------------

   `<Split>` answers "how does this one set divide up". This answers it for
   several sets at once, on a shared scale, which is a different question and
   was being faked with a column of unrelated bars.

   The shared scale is the whole point. Each row is drawn against the largest
   row, not against itself, so a person holding 151 items and a person holding
   4 do not both render as a full-width bar. Row-relative scaling is the
   single most common way a workload chart lies.

   One legend for the whole table rather than one per row: the segments mean
   the same thing on every row, and repeating that six times is six times the
   ink for none of the information. */
export function SplitRows({
  rows,
  parts,
  ariaLabel,
  labelWidth = "w-[150px]",
  note,
  onPick,
}: {
  rows: { key: string; label: string; values: number[]; title?: string }[];
  /* The segment definitions, in order, shared by every row. `values[i]`
     belongs to `parts[i]`. */
  parts: { label: string; tone: SplitTone }[];
  ariaLabel: string;
  labelWidth?: string;
  /* Rendered under a row, for the one row that needs saying something about. */
  note?: (key: string) => ReactNode;
  /* Makes every segment a door. `part` is the index into `parts`, or -1 for
     the row label and total, meaning "this row, all of it".

     Without this the chart is a dead end: it can tell a reader that one
     person has sixteen items past due and then leave them to find those
     sixteen by scrolling a list of a hundred and fifty-one. A count you
     cannot open is a count you have to take on trust. */
  onPick?: (key: string, part: number) => void;
}) {
  const reduced = useReducedMotion();
  const totals = rows.map((r) => r.values.reduce((a, b) => a + Math.max(0, b), 0));
  const ceiling = Math.max(1, ...totals);

  return (
    <div className="grid gap-2.5">
      <div className="flex flex-wrap gap-x-5 gap-y-1">
        {parts.map((part) => (
          <span key={part.label} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`inline-block h-2 w-2 rounded-full ${TONE_FILL[part.tone]}`}
            />
            <span className="text-label text-ink-2">{part.label}</span>
          </span>
        ))}
      </div>

      <div className="grid gap-1.5" role="img" aria-label={ariaLabel}>
        {rows.map((row, i) => {
          const total = totals[i] as number;
          return (
            <div key={row.key}>
              <div className="flex items-center gap-2.5">
                {onPick ? (
                  <button
                    type="button"
                    onClick={() => onPick(row.key, -1)}
                    title={`Show everything ${row.label} is carrying`}
                    className={`${labelWidth} shrink-0 cursor-pointer truncate border-0 bg-transparent p-0 text-left text-body text-ink underline decoration-rule underline-offset-2 transition-colors duration-100 hover:decoration-current`}
                  >
                    {row.label}
                  </button>
                ) : (
                  <span
                    className={`${labelWidth} shrink-0 truncate text-body text-ink`}
                    title={row.title ?? row.label}
                  >
                    {row.label}
                  </span>
                )}
                <span className="relative h-3 min-w-0 flex-1 overflow-hidden rounded-sm bg-rule-2">
                  <span
                    className={`flex h-full ${reduced ? "" : "grow-x"}`}
                    style={{
                      width: `${(total / ceiling) * 100}%`,
                      animationDelay: reduced ? undefined : `${Math.min(i, 9) * 45}ms`,
                    }}
                  >
                    {row.values.map((value, j) => {
                      if (value <= 0) return null;
                      const part = parts[j];
                      if (!part) return null;
                      const width = `${(value / total) * 100}%`;
                      const label = `${row.label} — ${formatCount(value)} ${part.label}`;
                      return onPick ? (
                        <button
                          key={part.label}
                          type="button"
                          onClick={() => onPick(row.key, j)}
                          title={`${label}. Click to list them.`}
                          aria-label={label}
                          className={`h-full cursor-pointer border-0 p-0 ${TONE_FILL[part.tone]} hover:brightness-110`}
                          style={{ width }}
                        />
                      ) : (
                        <span
                          key={part.label}
                          className={`h-full ${TONE_FILL[part.tone]}`}
                          style={{ width }}
                          title={label}
                        />
                      );
                    })}
                  </span>
                </span>
                <span className="w-[52px] shrink-0 text-right text-body font-semibold tabular-nums text-ink">
                  {formatCount(total)}
                </span>
              </div>
              {note?.(row.key)}
            </div>
          );
        })}
      </div>
    </div>
  );
}

/* ---- HeatStrip: counts per row, per time bucket ------------------------

   A row per entity, a cell per bucket, on one shared time axis - so "when
   did this happen" and "who did it" are answered by the same picture.

   Intensity is square-rooted, not linear. With one cell holding 134 and the
   rest holding 1 to 7, a linear ramp paints every other cell white and the
   chart says only "one big day", which a reader already knew from the total.
   The square root is the standard area-perception correction and it keeps the
   small cells legible without pretending they are close to the large one -
   and the count is printed in the cell regardless, so the encoding never has
   to be trusted on its own.

   Deliberately not a line chart: these are discrete events on sparse days,
   and a line between two of them draws work on days when nothing happened. */
export function HeatStrip({
  rows,
  columns,
  ariaLabel,
  tone = "good",
  labelWidth = "w-[150px]",
}: {
  rows: { key: string; label: string; cells: number[]; title?: string }[];
  /* One label per bucket, same length as every row's `cells`. */
  columns: string[];
  ariaLabel: string;
  tone?: SplitTone;
  labelWidth?: string;
}) {
  const reduced = useReducedMotion();
  const peak = Math.max(1, ...rows.flatMap((r) => r.cells));

  if (!columns.length) {
    return <ChartEmpty kind="absent" reason="No dates were recorded, so there is no timeline to draw." />;
  }

  return (
    <div className="overflow-x-auto">
      <div className="min-w-[520px]">
        <div className="grid gap-1" role="img" aria-label={ariaLabel}>
          {rows.map((row, i) => (
            <div key={row.key} className="flex items-center gap-2.5">
              <span
                className={`${labelWidth} shrink-0 truncate text-body text-ink`}
                title={row.title ?? row.label}
              >
                {row.label}
              </span>
              <span className="flex min-w-0 flex-1 gap-px">
                {row.cells.map((n, j) => (
                  <span
                    key={j}
                    className={`relative h-6 flex-1 rounded-[2px] ${
                      n > 0 ? TONE_FILL[tone] : "bg-rule-2"
                    } ${reduced || n === 0 ? "" : "fade-in"}`}
                    style={{
                      opacity: n > 0 ? 0.25 + 0.75 * Math.sqrt(n / peak) : 1,
                      animationDelay:
                        reduced ? undefined : `${Math.min(i, 9) * 45 + j * 12}ms`,
                    }}
                    title={`${row.label} — ${columns[j]}: ${formatCount(n)}`}
                  >
                    {n > 0 && (
                      <span className="absolute inset-0 flex items-center justify-center text-label font-bold tabular-nums text-surface">
                        {n}
                      </span>
                    )}
                  </span>
                ))}
              </span>
              <span className="w-[52px] shrink-0 text-right text-body font-semibold tabular-nums text-ink">
                {formatCount(row.cells.reduce((a, b) => a + b, 0))}
              </span>
            </div>
          ))}

          {/* The axis. Every label where they fit, otherwise the ends and
              the middle - a tick per cell that overlaps its neighbour is
              worse than no tick at all. */}
          <div className="flex items-center gap-2.5 pt-0.5">
            <span className={`${labelWidth} shrink-0`} />
            <span className="flex min-w-0 flex-1 gap-px">
              {columns.map((label, j) => (
                <span
                  key={j}
                  className="min-w-0 flex-1 truncate text-center text-label text-ink-3"
                  title={label}
                >
                  {columns.length <= 14 || j === 0 || j === columns.length - 1
                    ? label
                    : ""}
                </span>
              ))}
            </span>
            <span className="w-[52px] shrink-0 text-right text-label text-ink-3">
              total
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---- Meter: one figure against its own ceiling -------------------------

   The smallest chart in the set, and the one that replaced the most prose.
   "76 of 173" is a sentence; this is the same sentence with the ratio drawn,
   which is the part a reader was doing in their head. */
export function Meter({
  value,
  of,
  label,
  tone = "info",
}: {
  value: number;
  of: number;
  label: ReactNode;
  tone?: SplitTone;
}) {
  const reduced = useReducedMotion();
  const pct = of > 0 ? Math.min(100, (value / of) * 100) : 0;
  return (
    <div className="grid gap-1">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-label text-ink-2">{label}</span>
        <span className="text-label tabular-nums text-ink-3">
          {formatCount(value)} / {formatCount(of)}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-rule-2"
        role="img"
        aria-label={`${formatCount(value)} of ${formatCount(of)}`}
      >
        <div
          className={`h-full rounded-full ${TONE_FILL[tone]} ${reduced ? "" : "grow-x"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}
