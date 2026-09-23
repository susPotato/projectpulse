/*
  The report builder: choose an audience, see the document, download it.

  Two things about this screen are deliberate.

  **The preview is the document.** It renders the same `ReportDoc` blocks the
  `.docx`, `.xlsx` and Markdown writers walk, fetched from `/api/report/preview`.
  It is not a mock-up of the report and not a second rendering of the bundles -
  a preview built either of those ways is one that eventually disagrees with the
  file a PM actually sends.

  **The catalogue comes from the server.** Presets, sections and formats are all
  served by `/api/report/options`, straight out of `exports/document.py`. So a
  section added to the exporter appears here with no change to this file, and
  this screen cannot offer one the exporter does not know how to build.

  The one thing it decides for itself is when a preset stops being an
  instruction: tick anything and the selection is sent explicitly, because from
  that point the preset is only the label of where the PM started.
*/
import { useEffect, useState } from "react";
import {
  load,
  type ApiProblem,
  type ReportBlock,
  type ReportOptions,
  type ReportPreview,
  withProject,
} from "../api";
import { Board, Page, Panel, Problem } from "../components/Shell";

/** The query string every format URL and the preview share. */
function query(sections: string[]): string {
  // Sections are always sent explicitly rather than as a preset name, so the
  // file cannot be built from a different selection than the one on screen.
  return sections.map((id) => `section=${encodeURIComponent(id)}`).join("&");
}

/* ---------------------------------------------------------------- preview */

/* One block, rendered the way its format renders it. Deliberately plain: this
   is a preview of a document, so it should look like a document and not like
   the rest of the app. */
function Block({ block }: { block: ReportBlock }) {
  if (block.kind === "heading") {
    return block.level <= 1 ? (
      <h3 className="mt-5 mb-2 text-emph font-bold">{block.text}</h3>
    ) : (
      <h4 className="mt-4 mb-1.5 text-body font-bold">{block.text}</h4>
    );
  }

  if (block.kind === "paragraph") {
    return (
      <p className="mb-2 text-body leading-[1.65] text-ink-2">
        {block.label && <b className="text-ink">{block.label}</b>}
        {block.text}
      </p>
    );
  }

  if (block.kind === "note") {
    return (
      <p className="mb-2 border-l-2 border-rule pl-3 text-body leading-[1.6] text-ink-3 italic">
        {block.text}
      </p>
    );
  }

  if (block.kind === "bullets") {
    return (
      /* `break-words`, because a bullet in this preview is very often a
         raw Jira REST URL - one unbreakable 200-character token with no
         space in it. A word that cannot wrap makes its list wider than the
         panel, the panel wider than the column, and the whole page scroll
         sideways, which the table below already takes care not to do. */
      <ul className="mb-2 ml-4 list-disc break-words text-body leading-[1.65] text-ink-2">
        {block.items.map((item, index) => (
          <li key={index}>{item}</li>
        ))}
      </ul>
    );
  }

  if (block.kind === "table") {
    return (
      // Wide tables scroll inside the panel. A projection of forty tasks must
      // not make the whole page scroll sideways.
      <div className="mb-3 overflow-x-auto">
        <table className="w-full border-collapse text-body">
          <thead>
            <tr>
              {block.columns.map((column) => (
                <th
                  key={column}
                  className="border-b border-rule px-2 py-1.5 text-left font-bold whitespace-nowrap text-ink-3"
                >
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {block.rows.map((row, index) => (
              <tr key={index}>
                {row.map((cell, column) => (
                  <td
                    key={column}
                    className="border-b border-rule-2 px-2 py-1.5 align-top tabular-nums text-ink-2"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return null;
}

function Preview({ preview }: { preview: ReportPreview }) {
  return (
    <div>
      <h2 className="text-title font-bold">{preview.title}</h2>
      {preview.preamble.map((block, index) => (
        <Block key={index} block={block} />
      ))}

      {preview.sections.map((section) => (
        <section key={section.id}>
          <h3 className="mt-6 mb-2 border-b border-rule pb-1 text-emph font-bold">
            {section.title}
          </h3>
          {section.blocks.map((block, index) => (
            <Block key={index} block={block} />
          ))}
        </section>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- page */

/* The screen as a pure function of what the server said, so `npm run smoke`
   can render it with no DOM and no fetch - the same split every other page
   here uses. All the state lives in `Reports` below. */
export function ReportsView({
  options,
  preview,
  chosen,
  presetId,
  building = false,
  onPreset = () => {},
  onToggle = () => {},
}: {
  options: ReportOptions;
  preview: ReportPreview | null;
  chosen: string[];
  presetId: string;
  building?: boolean;
  onPreset?: (id: string) => void;
  onToggle?: (id: string) => void;
}) {
  const built = new Set(preview?.resolved_sections ?? []);
  const applyPreset = onPreset;
  const toggle = onToggle;

  return (
    <Page
      current="/reports"
      title="Reports"
      scope={options?.project_id}
      subtitle="Choose an audience, check it, hand it over"
      action={
        <div className="flex flex-wrap gap-2">
          {options?.formats.map((format) =>
            format.available ? (
              <a
                key={format.id}
                className="action"
                href={withProject(`/api/report.${format.extension}?${query(chosen)}`)}
                title={format.description}
              >
                {format.label}
              </a>
            ) : (
              <span
                key={format.id}
                className="action pointer-events-none opacity-45"
                title={format.unavailable_reason}
              >
                {format.label}
              </span>
            ),
          )}
        </div>
      }
    >
      <Board>
        <Panel caption="Who is this for" span={4} className="content-start">
          <div className="grid gap-2">
            {options?.presets.map((preset) => (
              <button
                key={preset.id}
                type="button"
                onClick={() => applyPreset(preset.id)}
                aria-pressed={preset.id === presetId}
                className={
                  "rounded-lg border px-3 py-2.5 text-left " +
                  (preset.id === presetId
                    ? "border-blue bg-blue/8"
                    : "border-rule bg-bg hover:border-rule-2")
                }
              >
                <div className="text-body font-semibold">{preset.label}</div>
                <div className="mt-0.5 text-body leading-[1.5] text-ink-3">
                  {preset.description}
                </div>
              </button>
            ))}
          </div>

          <div className="mt-4 mb-2 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
            Sections
          </div>
          <div className="grid gap-1.5">
            {options?.sections.map((section) => {
              const ticked = chosen.includes(section.id);
              /* Ticked, sent, and still not in the document: the project has no
                 data for it. Said on the row rather than left as a silent gap
                 in the preview.

                 `requires` names a parent section rather than a bundle for
                 exactly one id - `evidence`, which switches source rows on
                 under each finding and is deliberately never a section of its
                 own. It is therefore never in `resolved_sections`, and without
                 this it reported "nothing to show" while the rows it had just
                 turned on were visible in the preview beside it. */
              const isSubToggle = section.requires === "findings";
              const empty =
                ticked && section.available && !isSubToggle && !built.has(section.id);

              return (
                <label
                  key={section.id}
                  className={
                    "flex gap-2.5 rounded-md px-2 py-1.5 " +
                    (section.available
                      ? "cursor-pointer hover:bg-bg"
                      : "cursor-not-allowed opacity-45")
                  }
                  title={
                    section.available
                      ? section.description
                      : `Nothing to include: this project has no ${section.title.toLowerCase()}.`
                  }
                >
                  <input
                    type="checkbox"
                    className="mt-0.5"
                    checked={ticked}
                    disabled={!section.available}
                    onChange={() => toggle(section.id)}
                  />
                  <span className="min-w-0">
                    <span className="block text-body font-semibold">
                      {section.title}
                      {section.id === "evidence" && (
                        <span className="ml-1.5 font-normal text-ink-3">
                          (under each finding)
                        </span>
                      )}
                    </span>
                    <span className="block text-body leading-[1.5] text-ink-3">
                      {empty
                        ? "Nothing to show for this project - left out of the file."
                        : section.description}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>

          {/* The blank inputs, on the screen that is already about handing
              files back and forth. The routes existed for a long time with
              nothing in the app pointing at them. */}
          <div className="mt-5 border-t border-rule pt-3.5">
            <div className="mb-2 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
              Blank input templates
            </div>
            <p className="mb-2 text-body leading-[1.5] text-ink-3">
              Generated from the sheet contract itself, so the file we hand out
              is the file we know how to read. No example rows - one would come
              back as a real task.
            </p>
            <div className="flex gap-2">
              <a className="action" href="/api/template/schedule.xlsx">
                Schedule
              </a>
              <a className="action" href="/api/template/worklog.xlsx">
                Worklog
              </a>
            </div>
          </div>
        </Panel>

        <Panel caption="What will be sent" span={8} className="content-start">
          {preview ? (
            <div className={building ? "opacity-55" : undefined}>
              <Preview preview={preview} />
            </div>
          ) : (
            <p className="text-body text-ink-3">Building the preview...</p>
          )}

          {chosen.length === 0 && (
            <p className="mt-3 text-body text-ink-3 italic">
              Nothing selected. The report still carries its own date and where
              the summary came from - those are never optional.
            </p>
          )}
        </Panel>
      </Board>
    </Page>
  );
}

/* The state and the fetching. Split from the view above so the smoke test can
   render the page without a DOM - see `ReportsView`. */
export function Reports() {
  const [options, setOptions] = useState<ReportOptions | null>(null);
  const [preview, setPreview] = useState<ReportPreview | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [presetId, setPresetId] = useState<string>("");
  const [building, setBuilding] = useState(false);

  useEffect(() => {
    load<ReportOptions>(withProject("/api/report/options"))
      .then((next) => {
        setOptions(next);
        const first =
          next.presets.find((p) => p.id === next.default_preset) ?? next.presets[0];
        setPresetId(first?.id ?? "");
        setChosen(first ? [...first.sections] : []);
      })
      .catch((error: ApiProblem) => setProblem(error));
  }, []);

  /* Re-fetched on every change to the selection, because the preview's whole
     claim is that it is the file. A stale preview is worse than none: it is
     the screen a PM checks before sending the document. */
  useEffect(() => {
    if (!options) return;
    setBuilding(true);
    load<ReportPreview>(withProject(`/api/report/preview?${query(chosen)}`))
      .then((next) => {
        setPreview(next);
        setProblem(null);
      })
      .catch((error: ApiProblem) => setProblem(error))
      .finally(() => setBuilding(false));
  }, [options, chosen]);

  function applyPreset(id: string) {
    const found = options?.presets.find((p) => p.id === id);
    if (!found) return;
    setPresetId(id);
    setChosen([...found.sections]);
  }

  function toggle(id: string) {
    // Ticking anything means the preset is no longer an instruction, only the
    // label of where this started. Cleared so the UI stops claiming otherwise.
    setPresetId("");
    setChosen((current) =>
      current.includes(id) ? current.filter((s) => s !== id) : [...current, id],
    );
  }

  if (problem && !options) {
    return (
      <Page current="/reports" title="Reports" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!options) {
    return <Page current="/reports" title="Reports" subtitle="Loading..." children={null} />;
  }

  return (
    <ReportsView
      options={options}
      preview={preview}
      chosen={chosen}
      presetId={presetId}
      building={building}
      onPreset={applyPreset}
      onToggle={toggle}
    />
  );
}
