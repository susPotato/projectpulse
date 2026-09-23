/*
  The sign-in gate for the hand-written pages, and their copy of the avatar.

  These six pages are not in the React bundle, so they cannot render the login
  form - it lives in `web/src/pages/Login.tsx` and that is the only place the
  credential is checked. This file therefore does exactly two things and
  deliberately knows no password:

  1. No session -> go to `/?next=<this page>`, where the form is. The bundle
     reads `next` back (`web/src/auth.ts#nextPath`) and returns you here, so
     signing in from Schedule lands on Schedule rather than on Programs.
  2. A session -> draw the same avatar `Shell.tsx` draws, from the same
     `shell.css` classes, into this page's `.appbar`.

  Loaded WITHOUT `defer`, unlike `theme.js` and `rail.js` beside it. Those two
  adjust a page that is allowed to be seen; this one decides whether it may be
  seen at all, and a deferred redirect paints the whole page first - the flash
  of a project's tasks at somebody who is not signed in, which is the one thing
  the gate exists to prevent.

  ⚠️ Same caveat as `web/src/auth.ts`: a doorway, not a lock. The JSON API
  behind every one of these pages is unauthenticated, so this stops a visitor,
  not an attacker.
*/
(function () {
  var STORAGE_KEY = "pulse.session";

  function session() {
    try {
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      if (!parsed || typeof parsed.id !== "string" || !parsed.id) return null;
      return {
        id: parsed.id,
        name: parsed.name || parsed.id,
        role: parsed.role || "",
      };
    } catch (e) {
      // Storage blocked or a malformed entry: treated as signed out, which
      // fails towards the login screen rather than into a half-open page.
      return null;
    }
  }

  var user = session();

  if (!user) {
    /* A URL credential (`?signin=admin:1234`) is forwarded UP rather than
       carried inside `next`, because the bundle is the only thing that knows
       the password - see `web/src/auth.ts`. Keeping it out of `next` also
       keeps it out of the URL we come back to, so the password is not left in
       the address bar of the page you land on. */
    var search, credential;
    try {
      search = new URLSearchParams(window.location.search);
      credential = search.get("signin");
      if (credential !== null) search.delete("signin");
    } catch (e) {
      search = null;
      credential = null;
    }
    var rest = search ? search.toString() : "";
    var back = window.location.pathname + (rest ? "?" + rest : "");
    // `replace`, not `assign`: a signed-out bounce should not become a step in
    // the back history, or Back from the login form returns here and bounces
    // again.
    window.location.replace(
      "/?next=" + encodeURIComponent(back) +
      (credential ? "&signin=" + encodeURIComponent(credential) : "")
    );
    return;
  }

  /* Matches `web/src/auth.ts#initials`. Two letters from two words, one from
     one - "AD" out of "Admin" would read as initials that are not there. */
  function initials(name) {
    var words = String(name).trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return "?";
    if (words.length === 1) return words[0].charAt(0).toUpperCase();
    return (words[0].charAt(0) + words[words.length - 1].charAt(0)).toUpperCase();
  }

  function mount() {
    var bar = document.querySelector(".appbar");
    // Traceability has no app bar of its own. Nothing to hang the avatar on
    // is not an error - the gate above has already done the part that matters.
    if (!bar || bar.querySelector(".avatar-wrap")) return;

    var wrap = document.createElement("div");
    wrap.className = "avatar-wrap";

    var button = document.createElement("button");
    button.type = "button";
    button.className = "avatar";
    button.textContent = initials(user.name);
    button.setAttribute("aria-haspopup", "menu");
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-label", "Signed in as " + user.name);
    button.title = "Signed in as " + user.name;

    var menu = document.createElement("div");
    menu.className = "avatar-menu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;

    var who = document.createElement("div");
    who.className = "avatar-who";
    var name = document.createElement("b");
    name.textContent = user.name;
    var sub = document.createElement("span");
    sub.textContent = user.role ? user.role + " · " + user.id : user.id;
    who.appendChild(name);
    who.appendChild(sub);

    var out = document.createElement("button");
    out.type = "button";
    out.setAttribute("role", "menuitem");
    out.textContent = "Sign out";
    out.addEventListener("click", function () {
      try {
        window.localStorage.removeItem(STORAGE_KEY);
      } catch (e) {
        // Nothing stored to remove; the redirect below still signs you out of
        // this page view.
      }
      window.location.replace("/");
    });

    menu.appendChild(who);
    menu.appendChild(out);
    wrap.appendChild(button);
    wrap.appendChild(menu);

    /* The avatar goes last - `Shell.tsx` puts it there too, because identity
       is chrome rather than something this page does - and `.appbar .spacer`
       is what pushes it to the right-hand end. Three of these pages never
       needed one (nothing sat on the right of their bar), so one is added
       here rather than edited into each file: a page that grows a right-hand
       control later then has it already. */
    if (!bar.querySelector(".spacer")) {
      var spacer = document.createElement("span");
      spacer.className = "spacer";
      bar.appendChild(spacer);
    }
    bar.appendChild(wrap);

    function setOpen(open) {
      menu.hidden = !open;
      button.setAttribute("aria-expanded", open ? "true" : "false");
    }

    button.addEventListener("click", function () {
      setOpen(menu.hidden);
    });

    // `pointerdown` rather than `click`, so an outside press does not close the
    // menu and let the button's own handler reopen it in the same gesture.
    document.addEventListener("pointerdown", function (event) {
      if (!menu.hidden && !wrap.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setOpen(false);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
