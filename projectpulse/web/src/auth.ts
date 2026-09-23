/*
  Who is signed in, for the demo.

  ⚠️ This is a **doorway, not a lock.** The credential is checked in the
  browser and the session is a `localStorage` entry, so anyone who opens
  DevTools - or curls `/api/portfolio` - is past it in one move. Every API
  route is exactly as open as it was before this file existed. It buys one
  thing and it is worth being clear about which: the product now has a person
  attached to it, so the app bar can say who is looking and a screenshot has
  an identity in it. Real auth is a server-side session and a dependency on
  every route; when that lands, this module is the thing it replaces.

  Kept apart from `api.ts` on purpose. That module is about the server's
  bundles; nothing here reaches the server at all, and merging them would
  suggest it does.
*/

const KEY = "pulse.session";

/* The only account, spelled out rather than hidden, because hiding it in a
   hash would imply a secret this cannot keep - the check runs on the reader's
   own machine. The static pages deliberately do NOT carry this: they only
   read a session, so the password exists in exactly one place. */
const ACCOUNTS: Record<string, { password: string; name: string; role: string }> = {
  admin: { password: "1234", name: "Admin", role: "Administrator" },
};

export interface Session {
  id: string;
  name: string;
  role: string;
  /* ISO, so "signed in at" is formattable rather than a raw epoch. */
  since: string;
}

export function session(): Session | null {
  try {
    const raw = window.localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Session>;
    if (!parsed || typeof parsed.id !== "string" || !parsed.id) return null;
    return {
      id: parsed.id,
      name: parsed.name || parsed.id,
      role: parsed.role || "",
      since: parsed.since || "",
    };
  } catch {
    // Private window, storage blocked, or a malformed entry. Treated as
    // signed out, which fails towards the login screen rather than towards
    // a half-rendered app.
    return null;
  }
}

/* Returns the session on success and `null` on a bad credential. Not a thrown
   error: a wrong password is an ordinary outcome of a login form, and making
   the caller catch it would put the common path in a `catch` block. */
export function signIn(id: string, password: string): Session | null {
  const account = ACCOUNTS[id.trim().toLowerCase()];
  if (!account || account.password !== password) return null;
  const next: Session = {
    id: id.trim().toLowerCase(),
    name: account.name,
    role: account.role,
    since: new Date().toISOString(),
  };
  try {
    window.localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage refused. The sign-in still stands for this page view; it just
    // will not survive a navigation, which is better than refusing to let
    // somebody in because their browser will not remember them.
  }
  return next;
}

export function signOut(): void {
  try {
    window.localStorage.removeItem(KEY);
  } catch {
    // Nothing to do: there was no stored session to remove.
  }
}

/* One or two letters for the avatar. Initials from the words in the name, and
   a single name gives its first letter only - "AD" from "Admin" would read as
   two words that are not there. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0]!.charAt(0).toUpperCase();
  return (words[0]!.charAt(0) + words[words.length - 1]!.charAt(0)).toUpperCase();
}

/* A credential carried in the URL: `?signin=admin:1234`.

   Not a bypass - it is the same check `signIn` runs, with the same password,
   so a wrong one signs nobody in. It exists because headless Chrome cannot be
   handed a `localStorage` entry: `scripts.shots` launches the browser with a
   throwaway profile per page, so without this every screenshot in `shots/`
   would be a picture of the login form. It doubles as a demo link somebody can
   send.

   The parameter is stripped from the address bar either way, so a password
   does not sit in the URL, in the history, or in a screenshot of the URL bar.
   `replaceState`, not `pushState` - a cleaned URL should not become a Back
   step to the one with the password in it. */
export function signInFromUrl(): Session | null {
  let raw: string | null = null;
  try {
    const params = new URLSearchParams(window.location.search);
    raw = params.get("signin");
    if (raw !== null) {
      params.delete("signin");
      const query = params.toString();
      window.history.replaceState(
        null,
        "",
        window.location.pathname + (query ? `?${query}` : "") + window.location.hash,
      );
    }
  } catch {
    return null;
  }
  if (!raw) return null;
  const at = raw.indexOf(":");
  if (at < 0) return null;
  return signIn(raw.slice(0, at), raw.slice(at + 1));
}

/* Where to go after signing in.

   The hand-written pages cannot show the login form - they are not this
   bundle - so `auth.js` sends a signed-out visitor to `/?next=/gantt` and
   this reads the parameter back. Same-origin paths only: an open redirect is
   a real bug even on a demo, and `next=https://elsewhere` would be one. */
export function nextPath(): string | null {
  try {
    const raw = new URLSearchParams(window.location.search).get("next");
    if (!raw || !raw.startsWith("/") || raw.startsWith("//")) return null;
    return raw;
  } catch {
    return null;
  }
}
