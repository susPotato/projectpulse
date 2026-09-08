import { useRef, useState } from "react";
import { send, type ApiProblem, type ChatMessage, type ChatResponse } from "../api";
import { Card, Note, Page } from "../components/Shell";

/*
  Free-form chat - the one page in this app with no deterministic engine
  behind it and no evidence panel. Every other tab renders numbers a rule or
  a graph traversal produced; this one renders whatever the model says, which
  is why the banner above the conversation says so before the first message.

  Stateless server-side (`app/agent/chat.py`): the whole transcript lives in
  this component's state and is resent every turn. Lost on reload, on
  purpose - this is the fast first cut, not the final shape of the feature.
*/

function Bubble({ message }: { message: ChatMessage }) {
  const mine = message.role === "user";
  return (
    <div className={`flex ${mine ? "justify-end" : "justify-start"}`}>
      <div
        className={
          "max-w-[75%] rounded-lg px-3.5 py-2.5 text-[13.5px] whitespace-pre-wrap " +
          (mine ? "bg-blue text-white" : "border border-rule bg-surface text-ink")
        }
      >
        {message.content}
      </div>
    </div>
  );
}

export function Agent() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

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
      });
      if (result.ok) {
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

  return (
    <Page current="/agent" title="Agent" scope="free-form chat, not verified against your data">
      <Note>
        Unlike Insight or Risk, nothing here is checked against a rule or a
        graph. The model can be wrong, and it has not been given this
        project&rsquo;s live data - ask it about the schedule or the risk
        register and it will say so rather than guess.
      </Note>

      <Card className="mt-4 flex h-[60vh] flex-col gap-3 overflow-y-auto">
        {messages.length === 0 && (
          <p className="m-0 text-[13px] text-ink-3">
            Ask anything - this conversation is not saved when you leave the page.
          </p>
        )}
        {messages.map((m, i) => (
          <Bubble key={i} message={m} />
        ))}
        {sending && (
          <div className="flex justify-start">
            <div className="rounded-lg border border-rule bg-surface px-3.5 py-2.5 text-[13.5px] text-ink-3">
              Thinking&hellip;
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </Card>

      {error && (
        <div className="mt-3 rounded-md border border-red/40 bg-red/10 px-3 py-2 text-[12.5px] text-red">
          {error}
        </div>
      )}

      <div className="mt-3 flex gap-2">
        <textarea
          className="min-h-[44px] flex-1 resize-none rounded-md border border-rule bg-bg px-3 py-2.5 text-[13.5px] text-ink outline-none focus:border-blue"
          placeholder="Message the agent... (Enter to send, Shift+Enter for a new line)"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <button
          type="button"
          disabled={sending || !draft.trim()}
          onClick={submit}
          className="cursor-pointer rounded-md border border-blue bg-blue px-4 text-[13px] font-semibold text-white disabled:cursor-default disabled:opacity-50"
        >
          Send
        </button>
      </div>
    </Page>
  );
}
