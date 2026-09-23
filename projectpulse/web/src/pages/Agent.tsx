import { useEffect, useRef, useState } from "react";
import {
  currentProject,
  load,
  send,
  type ApiProblem,
  type ChatMessage,
  type ChatResponse,
  type PortfolioBundle,
} from "../api";
import { Card, Note, Page } from "../components/Shell";
import { SavedTiles, TileBuilder, type TileTarget } from "../components/TileBuilder";

/*
  The Agent tab, in two modes - and the two are not the same kind of thing,
  which is the point of putting them side by side.

  * **Chat** is the one page in this app with no deterministic engine behind
    it and no evidence panel. Every other tab renders numbers a rule or a
    graph traversal produced; this renders whatever the model says.
  * **Build a tile** is the opposite, and shares its implementation with the
    `Custom Tile` dialog on a dashboard (`components/TileBuilder.tsx`): the
    numbers are the person's own, the model only ever returns a validated
    `{title, chart_type, labels, values}`, an exact instruction never reaches
    a model at all, and every change is reported from a server-side diff
    rather than from the model's account of what it did.

  Keeping them one tab with one banner each is deliberate. A PM should be
  able to see, in one place, the difference between asking a model for prose
  and asking it to arrange their own figures - and neither banner should be
  mistaken for the other.

  Chat is stateless server-side (`app/agent/chat.py`): the whole transcript
  lives in this component's state and is resent every turn. Lost on reload,
  on purpose - this is the fast first cut, not the final shape of the feature.
*/

type Mode = "chat" | "tile";

/* One-click starters, so the tab is useful without typing from scratch -
   pattern borrowed from pimsathon-main's skill_library (preset prompts a
   user picks rather than composes), rewritten for a PM's own work rather
   than a developer's. They fill the box rather than send immediately, so a
   PM can edit before committing to a turn. */
const PRESETS = [
  {
    label: "Draft a status update",
    prompt:
      "Help me draft a short status update for stakeholders. I'll describe " +
      "what happened this week, what's at risk, and what's next - turn it " +
      "into a clear, concise message.",
  },
  {
    label: "Explain a risk in plain language",
    prompt:
      "I need to explain a project risk to a non-technical stakeholder. " +
      "Here is the risk: ",
  },
  {
    label: "Brainstorm mitigations",
    prompt:
      "Suggest a few practical mitigation options for this risk, with the " +
      "trade-offs of each: ",
  },
  {
    label: "Prep a steering meeting agenda",
    prompt:
      "Draft a short agenda for a steering committee review covering " +
      "schedule status, key risks, and decisions needed. Context: ",
  },
] as const;

const MODES: ReadonlyArray<readonly [Mode, string, string]> = [
  ["chat", "Chat", "free-form chat, not verified against your data"],
  ["tile", "Build a tile", "your own numbers, arranged - every change reported"],
];

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={
          "max-w-[75%] rounded-lg px-3.5 py-2.5 text-body whitespace-pre-wrap " +
          (mine ? "bg-blue text-white" : "border border-rule bg-surface text-ink")
        }
      >
        {message.content}
      </div>
    </div>
  );
}

export function Agent() {
  const [mode, setMode] = useState<Mode>("chat");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Which project the last reply was actually answered from, as the server
      reports it. Read back rather than assumed: "a project is selected" and
      "the model was given its data" are different claims, and showing the
      first while meaning the second is how somebody comes to trust an answer
      that was never grounded. */
  const [grounded, setGrounded] = useState<string>("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  /* Where a tile built here gets added. Resolved from the ambient project
     selection, falling back to the top-ranked project the way every other
     project-scoped page in this app already does - but *named on the button*
     rather than assumed, because adding a tile writes to someone's dashboard
     and a silent target is the wrong default for a write. */
  const [target, setTarget] = useState<TileTarget | null>(null);
  const [targetProblem, setTargetProblem] = useState(false);
  /** "Build a tile" vs "My Custom Tiles" - the same two tabs the dashboard's
      Custom Tile dialog offers, added here because this page previously had
      no second tab at all: a tile saved from here landed correctly (in the
      library, and on a dashboard when a project was ambient) with no way to
      see that from this page - which reads exactly like a failed save even
      though it was not one. */
  const [tileTab, setTileTab] = useState<"build" | "saved">("build");

  useEffect(() => {
    if (mode !== "tile" || target !== null || targetProblem) return;
    load<PortfolioBundle>("/api/portfolio").then(
      (bundle) => {
        const selected = currentProject()?.id;
        const project =
          bundle.projects.find((p) => p.project_id === selected) ?? bundle.projects[0];
        if (project) {
          setTarget({ scopeType: "project", scopeId: project.project_id, label: project.name });
        } else {
          setTargetProblem(true);
        }
      },
      () => setTargetProblem(true),
    );
  }, [mode, target, targetProblem]);

  function usePreset(prompt: string) {
    setDraft(prompt);
    requestAnimationFrame(() => {
      const el = inputRef.current;
      if (!el) return;
      el.focus();
      el.setSelectionRange(el.value.length, el.value.length);
    });
  }

  async function submit() {
    const text = draft.trim();
    if (!text || sending) return;

    const next = [...messages, { role: "user" as const, content: text }];
    setMessages(next);
    setDraft("");
    setError(null);
    setSending(true);

    try {
      const result = await send<ChatResponse>("/api/agent/chat", "POST", {
        messages: next,
        /* Sent every turn, not captured once: nothing is held server-side, and
           somebody who switches project mid-conversation means the next
           question is about the new one. */
        project: currentProject()?.id ?? "",
      });
      if (result.ok) {
        setGrounded(result.grounded_in || "");
        setMessages([...next, { role: "assistant", content: result.reply }]);
      } else {
        setError(result.error || "The model could not be reached.");
      }
    } catch (err) {
      setError((err as ApiProblem).detail ?? "Could not reach the server.");
    } finally {
      setSending(false);
      requestAnimationFrame(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }));
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  const scope = MODES.find(([id]) => id === mode)?.[2] ?? "";

  return (
    <Page current="/agent" title="Agent" scope={scope}>
      {/* The mode switch. Two things the agent can do, named. */}
      <div className="flex flex-wrap gap-1.5">
        {MODES.map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setMode(id)}
            className={`cursor-pointer rounded-full border px-3 py-1 text-body font-semibold ${
              mode === id
                ? "border-blue bg-blue/15 text-blue"
                : "border-rule bg-surface text-ink-2 hover:text-ink"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {mode === "chat" ? (
        <>
          <Note>
            Unlike Insight or Risk, nothing here is checked against a rule or a
            graph - the model can be wrong.{" "}
            {grounded ? (
              <>
                It <strong>has</strong> been given {grounded}&rsquo;s computed
                snapshot: task counts, what is past due, the milestones and what
                the data cannot support. It is told to quote those figures
                rather than work any out, so its answers should agree with the
                Insight and Schedule pages - if one disagrees, those pages are
                the ones that are right.
              </>
            ) : (
              <>
                Pick a project above and its computed snapshot is sent with each
                question. Without one the model has no project data and will say
                so rather than guess.
              </>
            )}
          </Note>

          <Card className="mt-4 flex h-[60vh] flex-col gap-3 overflow-y-auto">
            {messages.length === 0 && (
              <p className="m-0 text-body text-ink-3">
                Ask anything - this conversation is not saved when you leave the page.
              </p>
            )}
            {messages.map((m, i) => (
              <Bubble key={i} message={m} />
            ))}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-lg border border-rule bg-surface px-3.5 py-2.5 text-body text-ink-3">
                  Thinking&hellip;
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </Card>

          {error && (
            <div className="mt-3 rounded-md border border-red/40 bg-red/10 px-3 py-2 text-body text-red">
              {error}
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-1.5">
            {PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => usePreset(preset.prompt)}
                className="cursor-pointer rounded-full border border-rule bg-surface px-3 py-1 text-body text-ink-2 hover:border-blue hover:text-blue"
              >
                {preset.label}
              </button>
            ))}
          </div>

          <div className="mt-2 flex gap-2">
            <textarea
              ref={inputRef}
              className="min-h-[44px] flex-1 resize-none rounded-md border border-rule bg-bg px-3 py-2.5 text-body text-ink outline-none focus:border-blue"
              placeholder="Message the agent, or paste a link to have it read - Enter to send, Shift+Enter for a new line"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={onKeyDown}
            />
            <button
              type="button"
              disabled={sending || !draft.trim()}
              onClick={submit}
              className="cursor-pointer rounded-md border border-blue bg-blue px-4 text-body font-semibold text-white disabled:cursor-default disabled:opacity-50"
            >
              Send
            </button>
          </div>
        </>
      ) : (
        <>
          <Note>
            This mode is not the chat above. The numbers are yours - pasted or
            typed - and the model only rearranges them into a chart it is not
            allowed to author: an exact instruction (&ldquo;make it a line
            chart&rdquo;) never reaches a model at all, and everything each
            turn changed is reported from a comparison of the two charts rather
            than from the model&rsquo;s own account of what it did.
            {target && (
              <>
                {" "}
                Saving adds the tile to <strong>{target.label}</strong>.
              </>
            )}
            {targetProblem && (
              <>
                {" "}
                No project is loaded, so a tile built here is saved to your
                library and can be added to a dashboard from there.
              </>
            )}
          </Note>

          {/* Same two tabs, same labels, same styling, as the dashboard's
              Custom Tile dialog (`CustomTileModal.tsx`) - this page just had
              nowhere to put the second one before. "Build with AI" rather
              than "Build a tile" again: the mode switch above already says
              "Build a tile", and repeating it one row down would read as two
              different things happening to share a name. */}
          <div className="mt-4 flex border-b border-rule">
            {([
              ["build", "Build with AI"],
              ["saved", "My Custom Tiles"],
            ] as const).map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setTileTab(id)}
                className={`-mb-px cursor-pointer border-0 border-b-2 bg-transparent px-3 py-2 text-body ${
                  tileTab === id
                    ? "border-navy font-semibold text-ink"
                    : "border-transparent text-ink-2 hover:text-ink"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Not `Card`: it hardcodes `p-4` and the builder owns its own
              padding, and overriding a Tailwind utility by class order is not
              something Tailwind guarantees. The builder needs a bounded flex
              column to grow into - see its module docstring. */}
          <div className="flex h-[68vh] flex-col overflow-hidden rounded-b-lg border border-t-0 border-rule bg-surface">
            {tileTab === "build" ? (
              <TileBuilder target={target} />
            ) : (
              <SavedTiles target={target} />
            )}
          </div>
        </>
      )}
    </Page>
  );
}
