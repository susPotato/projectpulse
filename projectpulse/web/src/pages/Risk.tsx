import { useEffect, useMemo, useState } from "react";
import {
  currentProject,
  dash,
  load,
  send,
  type ApiProblem,
  type ProjectOption,
  type RiskBundle,
  type CitedTask,
  type RiskDraftBundle,
  type RiskIn,
  type RiskOut,
} from "../api";
import { Board, Card, Note, Page, Panel, Problem, Section } from "../components/Shell";

/*
  The risk register: a PM's own judgement, entered and edited by hand - the one
  screen in this app where a person's assessment is the data, not the engine's
  arithmetic over dates in a sheet. `pre_rating` / `post_rating` are the one
  exception: read off the likelihood x impact a PM chose
  (`app/risks/matrix.py`), never typed, so this page never renders a badge that
  disagrees with the pair behind it.
*/

const RATING_STYLE: Record<string, string> = {
  "Very Low": "border-green/45 bg-green/10 text-green",
  Low: "border-green/45 bg-green/10 text-green",
  Medium: "border-amber/45 bg-amber/10 text-amber",
  High: "border-orange/45 bg-orange/10 text-orange",
  "Very High": "border-red/45 bg-red/10 text-red",
};

const CELL_STYLE: Record<string, string> = {
  "Very Low": "bg-green/10",
  Low: "bg-green/20",
  Medium: "bg-amber/20",
  High: "bg-orange/20",
  "Very High": "bg-red/20",
};

function RatingBadge({ rating }: { rating: string | null | undefined }) {
  if (!rating) {
    return <span className="text-body text-ink-3">not assessed</span>;
  }
  return (
    <span
      className={`inline-block rounded-full border px-2 py-0.5 text-label font-bold ${
        RATING_STYLE[rating] ?? "border-rule bg-bg text-ink-3"
      }`}
    >
      {rating}
    </span>
  );
}

function money(value: number | null | undefined): string {
  if (value == null) return "-";
  return value.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

/* The 5x5 heat-map. Cells come from the server (`build_matrix`), which is the
   same rating lookup a risk's own badge uses - a cell here can never disagree
   with a badge in the table below it. */
function Matrix({ bundle }: { bundle: RiskBundle }) {
  const cellAt = (likelihood: string, impact: string) =>
    bundle.matrix.find((c) => c.likelihood === likelihood && c.impact === impact);

  return (
    <Panel caption="Risk matrix — pre-treatment" span={12}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <thead>
            <tr>
              <th className="w-[120px]" />
              {bundle.impacts.map((impact) => (
                <th
                  key={impact}
                  className="pb-1.5 text-center text-label font-medium text-ink-3"
                >
                  {impact}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bundle.likelihoods.map((likelihood) => (
              <tr key={likelihood}>
                <td className="pr-2.5 text-right text-body font-medium whitespace-nowrap text-ink-3">
                  {likelihood}
                </td>
                {bundle.impacts.map((impact) => {
                  const cell = cellAt(likelihood, impact);
                  return (
                    <td
                      key={impact}
                      className={`h-12 w-[110px] rounded border-2 border-surface text-center ${
                        cell ? CELL_STYLE[cell.rating] ?? "" : ""
                      }`}
                    >
                      <div className="text-label font-semibold text-ink-2">
                        {cell?.rating}
                      </div>
                      {!!cell?.risk_count && (
                        <div className="text-label font-bold text-ink">
                          {cell.risk_count} risk{cell.risk_count === 1 ? "" : "s"}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/* One field on the form: a label over an input, sized by `span` out of the
   two-column grid the form lays out in. */
function Field({
  label,
  span = 1,
  children,
}: {
  label: string;
  span?: 1 | 2;
  children: React.ReactNode;
}) {
  return (
    <label className={`grid gap-1 ${span === 2 ? "sm:col-span-2" : ""}`}>
      <span className="text-label font-medium tracking-[0.02em] text-ink-3">{label}</span>
      {children}
    </label>
  );
}

const inputClass =
  "w-full rounded-md border border-rule bg-bg px-2.5 py-1.5 text-body text-ink outline-none focus:border-blue";

const LIKELIHOOD_OPTIONS = ["", "Almost Certain", "Likely", "Possible", "Unlikely", "Rare"];
const IMPACT_OPTIONS = ["", "Insignificant", "Minor", "Moderate", "Major", "Severe"];
const STATUS_OPTIONS = ["Active", "Closed", "Retired"];

/* Create or edit. One form for both: editing seeds every field from the row
   being edited, creating starts from a mostly-empty draft. Saving always goes
   through the same validator the server runs - the badges above are never
   trusted from what this form typed, only from what `POST`/`PUT` echoes back. */
function RiskForm({
  initial,
  categories,
  projects,
  onClose,
  onSaved,
}: {
  initial: RiskOut | null;
  categories: string[];
  projects: ProjectOption[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<RiskIn>(() => ({
    /* Which project this risk belongs to, in the order a person means it:
       the one being edited, then the one the app is currently scoped to,
       then the first project that exists.

       This was a hard-coded `"excel:Project:1:HRMS"` in a free-text box, and
       it is the whole reported bug: a PM working on SAIN either left HRMS's
       id in place - filing the risk against the wrong project - or typed
       "SAIN", which the server used to accept and which then belonged to no
       project at all. Neither showed up on SAIN's own tiles. */
    project_id:
      initial?.project_id ??
      currentProject()?.id ??
      projects[0]?.project_id ??
      null,
    title: initial?.title ?? "",
    risk_no: initial?.risk_no ?? null,
    status: initial?.status ?? "Active",
    key_risk: initial?.key_risk ?? false,
    description: initial?.description ?? null,
    category: initial?.category ?? categories[0] ?? null,
    secondary_categories: initial?.secondary_categories ?? null,
    review_date: initial?.review_date ?? null,
    possible_realise_date: initial?.possible_realise_date ?? null,
    retired_date: initial?.retired_date ?? null,
    cause_title: initial?.cause_title ?? null,
    cause_description: initial?.cause_description ?? null,
    pre_likelihood: initial?.pre_likelihood ?? null,
    pre_impact: initial?.pre_impact ?? null,
    pre_cost: initial?.pre_cost ?? null,
    pre_delay_days: initial?.pre_delay_days ?? null,
    post_likelihood: initial?.post_likelihood ?? null,
    post_impact: initial?.post_impact ?? null,
    post_cost: initial?.post_cost ?? null,
    post_delay_days: initial?.post_delay_days ?? null,
    responsible: initial?.responsible ?? null,
  }));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function set<K extends keyof RiskIn>(key: K, value: RiskIn[K]) {
    setDraft((d) => ({ ...d, [key]: value }));
  }

  async function save() {
    if (!draft.title?.trim()) {
      setError("Title is required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      if (initial) {
        await send(`/api/risks/${initial.id}`, "PUT", draft);
      } else {
        await send("/api/risks", "POST", draft);
      }
      onSaved();
    } catch (err) {
      setError((err as ApiProblem).detail ?? "Could not save this risk.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-navy/25" onClick={onClose}>
      <div
        className="flex h-full w-full max-w-[560px] flex-col overflow-y-auto bg-surface p-5 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-start justify-between">
          <h2 className="m-0 text-emph font-semibold">
            {initial ? "Edit risk" : "Add risk"}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded border-0 bg-transparent text-emph text-ink-3 hover:text-ink"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded-md border border-red/40 bg-red/10 px-3 py-2 text-body text-red">
            {error}
          </div>
        )}

        <div className="grid gap-3.5">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Title" span={2}>
              <input
                className={inputClass}
                value={draft.title ?? ""}
                onChange={(e) => set("title", e.target.value)}
              />
            </Field>
            {/* A picker, not a text box. The id is a machine key
                (`excel:Project:1:SAIN`) and nobody should be asked to
                remember it - a risk typed against a project that does not
                exist is invisible on every screen that asks by project. The
                list is served on the bundle, from `app/scope.py`, so it
                cannot drift from what the rest of the app calls a project. */}
            <Field label="Project" span={2}>
              <select
                className={inputClass}
                value={draft.project_id ?? ""}
                onChange={(e) => set("project_id", e.target.value)}
              >
                {projects.map((option) => (
                  <option key={option.project_id} value={option.project_id}>
                    {option.name}
                  </option>
                ))}
                {/* An older risk whose project is no longer registered still
                    has to be editable, and silently re-homing it onto
                    whatever is first in the list would be a worse answer than
                    showing what it actually says. */}
                {draft.project_id &&
                  !projects.some((p) => p.project_id === draft.project_id) && (
                    <option value={draft.project_id}>
                      {draft.project_id} (unknown project)
                    </option>
                  )}
              </select>
            </Field>
            <Field label="Risk No.">
              <input
                className={inputClass}
                value={draft.risk_no ?? ""}
                placeholder="auto"
                onChange={(e) => set("risk_no", e.target.value || null)}
              />
            </Field>
            <Field label="Status">
              <select
                className={inputClass}
                value={draft.status ?? "Active"}
                onChange={(e) => set("status", e.target.value as RiskIn["status"])}
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s}>{s}</option>
                ))}
              </select>
            </Field>
            <Field label="Category">
              <select
                className={inputClass}
                value={draft.category ?? ""}
                onChange={(e) => set("category", e.target.value || null)}
              >
                <option value="">—</option>
                {categories.map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </Field>
            <label className="flex items-center gap-2 pt-5 text-body">
              <input
                type="checkbox"
                checked={draft.key_risk ?? false}
                onChange={(e) => set("key_risk", e.target.checked)}
              />
              Key risk
            </label>
            <Field label="Description" span={2}>
              <textarea
                className={`${inputClass} min-h-[70px] resize-y`}
                value={draft.description ?? ""}
                onChange={(e) => set("description", e.target.value || null)}
              />
            </Field>
            <Field label="Responsible" span={2}>
              <input
                className={inputClass}
                value={draft.responsible ?? ""}
                onChange={(e) => set("responsible", e.target.value || null)}
              />
            </Field>
          </div>

          <div className="border-t border-rule pt-3.5 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
            Pre-treatment
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Likelihood">
              <select
                className={inputClass}
                value={draft.pre_likelihood ?? ""}
                onChange={(e) => set("pre_likelihood", (e.target.value || null) as RiskIn["pre_likelihood"])}
              >
                {LIKELIHOOD_OPTIONS.map((v) => (
                  <option key={v || "none"} value={v}>
                    {v || "—"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Impact">
              <select
                className={inputClass}
                value={draft.pre_impact ?? ""}
                onChange={(e) => set("pre_impact", (e.target.value || null) as RiskIn["pre_impact"])}
              >
                {IMPACT_OPTIONS.map((v) => (
                  <option key={v || "none"} value={v}>
                    {v || "—"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Cost impact (USD)">
              <input
                type="number"
                className={inputClass}
                value={draft.pre_cost ?? ""}
                onChange={(e) => set("pre_cost", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
            <Field label="Delay impact (days)">
              <input
                type="number"
                className={inputClass}
                value={draft.pre_delay_days ?? ""}
                onChange={(e) =>
                  set("pre_delay_days", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </Field>
          </div>

          <div className="border-t border-rule pt-3.5 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
            Post-treatment
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Likelihood">
              <select
                className={inputClass}
                value={draft.post_likelihood ?? ""}
                onChange={(e) =>
                  set("post_likelihood", (e.target.value || null) as RiskIn["post_likelihood"])
                }
              >
                {LIKELIHOOD_OPTIONS.map((v) => (
                  <option key={v || "none"} value={v}>
                    {v || "—"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Impact">
              <select
                className={inputClass}
                value={draft.post_impact ?? ""}
                onChange={(e) => set("post_impact", (e.target.value || null) as RiskIn["post_impact"])}
              >
                {IMPACT_OPTIONS.map((v) => (
                  <option key={v || "none"} value={v}>
                    {v || "—"}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Cost impact (USD)">
              <input
                type="number"
                className={inputClass}
                value={draft.post_cost ?? ""}
                onChange={(e) => set("post_cost", e.target.value === "" ? null : Number(e.target.value))}
              />
            </Field>
            <Field label="Delay impact (days)">
              <input
                type="number"
                className={inputClass}
                value={draft.post_delay_days ?? ""}
                onChange={(e) =>
                  set("post_delay_days", e.target.value === "" ? null : Number(e.target.value))
                }
              />
            </Field>
          </div>

          <div className="border-t border-rule pt-3.5 text-label font-bold tracking-[0.07em] text-ink-3 uppercase">
            Cause &amp; treatment
          </div>
          <div className="grid gap-3">
            <Field label="Cause">
              <input
                className={inputClass}
                value={draft.cause_title ?? ""}
                onChange={(e) => set("cause_title", e.target.value || null)}
              />
            </Field>
            <Field label="Cause / treatment description">
              <textarea
                className={`${inputClass} min-h-[70px] resize-y`}
                value={draft.cause_description ?? ""}
                onChange={(e) => set("cause_description", e.target.value || null)}
              />
            </Field>
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2 border-t border-rule pt-4">
          <button
            type="button"
            onClick={onClose}
            className="cursor-pointer rounded-md border border-rule bg-bg px-4 py-1.5 text-body font-medium"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={saving}
            onClick={save}
            className="cursor-pointer rounded-md border border-blue bg-blue px-4 py-1.5 text-body font-semibold text-white disabled:opacity-60"
          >
            {saving ? "Saving..." : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RiskRow({
  risk,
  onEdit,
  onDeleted,
  projectName,
}: {
  risk: RiskOut;
  onEdit: () => void;
  onDeleted: () => void;
  projectName?: string;
}) {
  const [deleting, setDeleting] = useState(false);

  async function remove() {
    if (!confirm(`Delete "${risk.title}"?`)) return;
    setDeleting(true);
    try {
      await send(`/api/risks/${risk.id}`, "DELETE");
      onDeleted();
    } catch {
      setDeleting(false);
    }
  }

  return (
    <tr className="border-b border-rule/60 hover:bg-bg">
      <td className="py-2 pr-2.5 text-body text-ink-3">{dash(risk.risk_no)}</td>
      <td className="py-2 pr-2.5">
        <div className="font-semibold text-body">{risk.title}</div>
        {/* The project's name, with the id behind it as a tooltip. A register
            that lists several projects has to say which one each row is on in
            terms a reader recognises - a raw source id is why a risk filed
            against the wrong project read as correct. */}
        <div className="text-body text-ink-3" title={risk.project_id}>
          {projectName ?? `${risk.project_id} (unknown project)`}
        </div>
      </td>
      <td className="py-2 pr-2.5 text-body">{dash(risk.category)}</td>
      <td className="py-2 pr-2.5">
        <span
          className={
            "inline-block rounded-full px-2 py-0.5 text-label font-semibold " +
            (risk.status === "Active" ? "bg-green/15 text-green" : "bg-ink-3/15 text-ink-3")
          }
        >
          {risk.status}
        </span>
      </td>
      <td className="py-2 pr-2.5">
        <RatingBadge rating={risk.pre_rating} />
      </td>
      <td className="py-2 pr-2.5 text-right text-body whitespace-nowrap">
        {money(risk.pre_cost)}
      </td>
      <td className="py-2 pr-2.5">
        <RatingBadge rating={risk.post_rating} />
      </td>
      <td className="py-2 pr-2.5 text-right text-body whitespace-nowrap">
        {money(risk.post_cost)}
      </td>
      <td className="py-2 pr-2.5 text-body whitespace-nowrap">{dash(risk.responsible)}</td>
      <td className="py-2 text-right whitespace-nowrap">
        <button
          type="button"
          onClick={onEdit}
          className="cursor-pointer rounded border-0 bg-transparent px-1.5 text-body text-blue hover:underline"
        >
          Edit
        </button>
        <button
          type="button"
          disabled={deleting}
          onClick={remove}
          className="cursor-pointer rounded border-0 bg-transparent px-1.5 text-body text-red hover:underline disabled:opacity-50"
        >
          Delete
        </button>
      </td>
    </tr>
  );
}

function RiskTable({ bundle, risks, nameOf, onEdit, onChanged }: {
  bundle: RiskBundle;
  risks: RiskOut[];
  nameOf: (projectId: string) => string | undefined;
  onEdit: (risk: RiskOut) => void;
  onChanged: () => void;
}) {
  if (bundle.risks.length === 0) {
    return <Card>No risks logged yet. Add the first one.</Card>;
  }
  if (risks.length === 0) {
    return <Card>No risks on this project yet. Add the first one.</Card>;
  }

  return (
    <Panel caption={`Risks — ${risks.length}`} span={12}>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-rule text-label font-bold tracking-[0.05em] text-ink-3 uppercase">
              <th className="pb-2 pr-2.5">No.</th>
              <th className="pb-2 pr-2.5">Title / Project</th>
              <th className="pb-2 pr-2.5">Category</th>
              <th className="pb-2 pr-2.5">Status</th>
              <th className="pb-2 pr-2.5">Pre-rating</th>
              <th className="pb-2 pr-2.5 text-right">Pre-cost</th>
              <th className="pb-2 pr-2.5">Post-rating</th>
              <th className="pb-2 pr-2.5 text-right">Post-cost</th>
              <th className="pb-2 pr-2.5">Responsible</th>
              <th className="pb-2" />
            </tr>
          </thead>
          <tbody>
            {risks.map((risk) => (
              <RiskRow
                key={risk.id}
                risk={risk}
                projectName={nameOf(risk.project_id)}
                onEdit={() => onEdit(risk)}
                onDeleted={onChanged}
              />
            ))}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

/*
  Risks a model proposed from task text, waiting on somebody.

  Kept out of the table above and out of the matrix, in the query rather than
  in this file (`list_risks` filters them) - so a screen that forgot to filter
  cannot put a suggestion into a register that gets exported into a report and
  sent to a customer. Accepting is what moves a row across; until then this is
  a reading list.

  Generating is a button, never automatic. It spends money and it asks a model
  to make a claim, and neither should happen because somebody opened a page.
*/
/* Citations as the keys a person recognises.

   `cited_task_ids` holds full domain ids because that is what a lookup has to
   match on; a reader wants "NOKEY-179826e5, COWORKLOCAL-3". Falls back to the
   id itself for a task the bundle did not carry, which is visible rather than
   blank. */
function labelsFor(draft: RiskOut, cited: CitedTask[]): string {
  const byId = new Map(cited.map((t) => [t.task_id, t.label ?? t.task_id]));
  return (draft.cited_task_ids ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean)
    .map((id) => byId.get(id) ?? id)
    .join(", ");
}

function DraftsPanel({
  projectId,
  onAccepted,
}: {
  projectId: string | null;
  onAccepted: () => void;
}) {
  const [bundle, setBundle] = useState<RiskDraftBundle | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  //: Built from the project this panel was handed, never from `withProject`.
  //: The filter above changes which project is being looked at, and an ambient
  //: helper would have gone on reading whichever one the app was last scoped
  //: to - so the drafts shown would belong to a different project from the
  //: table beside them.
  const url = projectId
    ? `/api/risks/drafts?project=${encodeURIComponent(projectId)}`
    : null;

  function refresh() {
    if (!url) return;
    load<RiskDraftBundle>(url).then(setBundle, () => setBundle(null));
  }

  useEffect(refresh, [projectId]);

  if (!projectId) {
    return (
      <Card>
        Pick one project above to read its task text. Proposals cite the rows
        they came from, and a row belongs to a project.
      </Card>
    );
  }

  async function generate() {
    setBusy(true);
    setFailure(null);
    try {
      setBundle(await send<RiskDraftBundle>(url!, "POST"));
    } catch (error) {
      setFailure((error as ApiProblem).detail ?? "could not reach the model");
    } finally {
      setBusy(false);
    }
  }

  async function accept(id: number) {
    await send(`/api/risks/drafts/${id}/accept`, "POST");
    refresh();
    onAccepted();
  }

  async function dismiss(id: number) {
    await send(`/api/risks/drafts/${id}`, "DELETE");
    refresh();
  }

  return (
    <div>
      <div className="mb-2.5 flex items-center justify-between gap-2">
        <div className="text-body text-ink-2">
          {bundle?.reason ?? `${bundle?.drafts.length ?? 0} proposal(s) waiting`}
        </div>
        <button
          type="button"
          onClick={generate}
          disabled={busy || !(bundle?.readable_tasks ?? 0)}
          className="action"
          style={{ cursor: busy ? "wait" : "pointer", border: "none" }}
        >
          {busy ? "Reading..." : "Read task text"}
        </button>
      </div>

      {failure && <Card className="mb-2.5 text-red">{failure}</Card>}

      {(bundle?.drafts ?? []).map((draft) => (
        <Card key={draft.id} className="mb-2.5">
          <div className="mb-1 text-body font-semibold">{draft.title}</div>
          {draft.description && (
            <div className="mb-2 text-body leading-relaxed text-ink-2">
              {draft.description}
            </div>
          )}
          <div className="mb-2 text-body text-ink-3">
            {draft.category ?? "no category"} ·{" "}
            <RatingBadge rating={draft.pre_rating} /> · read from{" "}
            <span className="font-mono">{labelsFor(draft, bundle?.cited_tasks ?? [])}</span>
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => accept(draft.id)}
              className="action"
              style={{ cursor: "pointer", border: "none" }}
            >
              Accept into register
            </button>
            <button
              type="button"
              onClick={() => dismiss(draft.id)}
              className="cursor-pointer rounded-md border border-rule bg-surface px-2 py-1 text-body text-ink-2"
            >
              Dismiss
            </button>
          </div>
        </Card>
      ))}
    </div>
  );
}

export function Risk() {
  const [bundle, setBundle] = useState<RiskBundle | null>(null);
  const [problem, setProblem] = useState<ApiProblem | null>(null);
  const [editing, setEditing] = useState<RiskOut | "new" | null>(null);
  /* The register stays program-wide by default - `Layout/fpt-pm-risk.html`
     lists several projects in one table, and that is the view a delivery
     manager wants. The filter is how a PM answers "did my risk land on SAIN",
     which is the question this screen could not previously be asked. */
  const [filter, setFilter] = useState<string>("");

  function refresh() {
    load<RiskBundle>("/api/risks").then(setBundle, setProblem);
  }

  useEffect(refresh, []);

  const nameOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const option of bundle?.projects ?? []) map[option.project_id] = option.name;
    return (projectId: string) => map[projectId];
  }, [bundle]);

  const shown = useMemo(
    () => (bundle?.risks ?? []).filter((r) => !filter || r.project_id === filter),
    [bundle, filter],
  );

  if (problem) {
    return (
      <Page current="/risk" title="Risk" subtitle={problem.title}>
        <Problem {...problem} />
      </Page>
    );
  }
  if (!bundle) {
    return <Page current="/risk" title="Risk" subtitle="Loading..." children={null} />;
  }

  return (
    <Page
      current="/risk"
      title="Risk Register"
      scope={
        filter
          ? `${shown.length} of ${bundle.risks.length} risk(s)`
          : `${bundle.risks.length} risk(s)`
      }
      action={
        <button
          type="button"
          onClick={() => setEditing("new")}
          className="action"
          style={{ cursor: "pointer", border: "none" }}
        >
          + Add risk
        </button>
      }
    >
      <Section>
        <Note>
          Entered and edited by a PM - not derived from a sheet. Only the rating
          badges are computed: they are the likelihood x impact pair looked up
          against the org's risk matrix, never typed independently, so a badge
          here can never disagree with the pair behind it.
        </Note>
      </Section>

      <div className="mb-3.5 flex items-center gap-2 text-body text-ink-2">
        <label htmlFor="risk-project-filter">Project</label>
        <select
          id="risk-project-filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="cursor-pointer rounded-md border border-rule bg-surface px-2 py-1 text-body text-ink"
        >
          <option value="">All projects</option>
          {bundle.projects.map((option) => (
            <option key={option.project_id} value={option.project_id}>
              {option.name}
            </option>
          ))}
        </select>
      </div>

      <Section title="Proposed from task text">
        <Note>
          Read by a model out of what people wrote in the tracker, and shown
          with the tasks it was read from. Nothing here is in the register or on
          the matrix above until you accept it - a risk is still something a
          person decided.
        </Note>
        <DraftsPanel
          projectId={filter || currentProject()?.id || null}
          onAccepted={refresh}
        />
      </Section>

      <Board className="mb-6">
        <Matrix bundle={bundle} />
        <RiskTable
          bundle={bundle}
          risks={shown}
          nameOf={nameOf}
          onEdit={setEditing}
          onChanged={refresh}
        />
      </Board>

      {editing && (
        <RiskForm
          initial={editing === "new" ? null : editing}
          categories={bundle.categories}
          projects={bundle.projects}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            refresh();
          }}
        />
      )}
    </Page>
  );
}
