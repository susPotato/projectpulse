/*
  The sign-in screen.

  Deliberately not a `Page`: there is no rail, no project picker and no app bar
  here, because every one of those is a control over data this visitor has not
  been shown yet. A shell drawn around a login form is a shell that flashes the
  product's navigation at somebody who is not in it.

  It is the only place the credential is checked - see `auth.ts`, including the
  note on what this does and does not protect.
*/
import { useState } from "react";
import { signIn, type Session } from "../auth";

const inputClass =
  "w-full rounded-md border border-rule bg-bg px-2.5 py-2 text-body text-ink outline-none focus:border-blue";

export function Login({ onSignedIn }: { onSignedIn: (session: Session) => void }) {
  const [id, setId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    const session = signIn(id, password);
    if (!session) {
      /* One message for both halves, and not because it is tidy: naming
         which half was wrong tells an unknown visitor which ids exist. The
         same reasoning a real login uses, kept here so the demo does not
         teach the opposite habit. */
      setError("That ID and password do not match.");
      setPassword("");
      return;
    }
    onSignedIn(session);
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-bg px-4 py-10">
      <div className="rise w-full max-w-[380px]">
        {/* The product's own mark, at the size the rail draws it. A login
            screen with no identity on it could belong to anything. */}
        <div className="mb-6 flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-navy text-on-accent">
            <svg
              viewBox="0 0 24 24"
              aria-hidden="true"
              className="h-[18px] w-[18px] fill-none stroke-current stroke-2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 19V9M10 19V5M16 19v-7M22 19H2" />
            </svg>
          </span>
          <div className="min-w-0">
            <div className="text-emph font-semibold text-ink">ProjectPulseAI</div>
            <div className="text-label text-ink-3">Delivery intelligence</div>
          </div>
        </div>

        <form
          onSubmit={submit}
          className="rounded-lg border border-rule bg-surface p-5"
          noValidate
        >
          <h1 className="m-0 text-title font-semibold text-ink">Sign in</h1>
          <p className="mt-1 mb-4 text-body text-ink-2">
            Use your ProjectPulse ID to continue.
          </p>

          <label className="block">
            <span className="mb-1 block text-label font-bold tracking-[0.05em] text-ink-3 uppercase">
              ID
            </span>
            <input
              className={inputClass}
              value={id}
              onChange={(e) => {
                setId(e.target.value);
                setError(null);
              }}
              autoComplete="username"
              autoFocus
              /* The browser's own required-field bubble would land before
                 our message and say something different about the same
                 form, so validation stays in one place - `noValidate` on
                 the form, and an empty field simply fails the check. */
            />
          </label>

          <label className="mt-3 block">
            <span className="mb-1 block text-label font-bold tracking-[0.05em] text-ink-3 uppercase">
              Password
            </span>
            <input
              className={inputClass}
              type="password"
              value={password}
              onChange={(e) => {
                setPassword(e.target.value);
                setError(null);
              }}
              autoComplete="current-password"
            />
          </label>

          {/* Rule 4: the colour never carries it alone - the sentence does,
              and the tone only draws the eye to it. Rendered in place rather
              than as an alert box above the form, so the fields do not move
              down the screen on a failed attempt. */}
          <p
            role="alert"
            aria-live="polite"
            className={`mt-3 mb-0 min-h-5 text-body text-red ${error ? "" : "invisible"}`}
          >
            {error ?? "placeholder"}
          </p>

          <button
            type="submit"
            className="lift mt-3 w-full rounded-md border border-blue bg-blue px-3 py-2 text-body font-semibold text-on-accent"
          >
            Sign in
          </button>

          {/* This is a demo build and the account is published in `auth.ts`
              anyway. Printing it is honest about what the screen is; hiding
              it would only cost the next person opening the app five
              minutes. Remove this block the day real accounts exist. */}
          <p className="mt-4 mb-0 border-t border-rule-2 pt-3 text-label text-ink-3">
            Demo build — ID <b className="font-semibold text-ink-2">admin</b>, password{" "}
            <b className="font-semibold text-ink-2">1234</b>. This screen is a
            doorway, not a lock: the API is unauthenticated.
          </p>
        </form>
      </div>
    </div>
  );
}
