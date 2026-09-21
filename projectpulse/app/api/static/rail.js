/*
  Keep the chosen project in the URL when you use the rail.

  The built pages' rail is rendered by `Shell.tsx`, which passes every href
  through `withProject`. These six are hand-written and their rail is literal
  markup, so their links were bare - and the difference was not cosmetic. The
  selection lives in `localStorage` under `pulse.project` and both halves of
  the app read it, so the *data* stayed right; what went wrong is that the
  address bar stopped describing the page. Click Insight from here and the URL
  is `/insight` with no project in it. Send that link to somebody and they open
  it with no stored choice of their own, so the server answers with its own
  default - which is how a colleague ends up looking at a different project
  than the person who sent them the link, with nothing on screen saying so.

  Rewriting hrefs rather than intercepting clicks, so the status bar, "copy
  link address" and middle-click all show the same URL the click would follow.
  A page with no stored selection leaves every link exactly as written.
*/
(function () {
  var STORAGE_KEY = "pulse.project";

  function stored() {
    try {
      var params = new URLSearchParams(window.location.search);
      var fromUrl = params.get("project");
      if (fromUrl) return { id: fromUrl, also: params.getAll("also") };
      var raw = window.localStorage.getItem(STORAGE_KEY);
      if (!raw) return null;
      var parsed = JSON.parse(raw);
      return parsed && parsed.id ? { id: parsed.id, also: parsed.also || [] } : null;
    } catch (e) {
      // Private window, storage blocked, or a malformed entry. Leaving the
      // links alone is the correct fallback - they still work.
      return null;
    }
  }

  function apply() {
    var sel = stored();
    if (!sel) return;
    var links = document.querySelectorAll(".rail a[href]");
    Array.prototype.forEach.call(links, function (a) {
      var href = a.getAttribute("href");
      // Only our own root-relative pages, and never one that already names a
      // project - a link written with an explicit project meant that project.
      if (!href || href.charAt(0) !== "/" || href.indexOf("project=") !== -1) return;
      var params = new URLSearchParams();
      params.set("project", sel.id);
      (sel.also || []).forEach(function (id) { params.append("also", id); });
      a.setAttribute("href",
        href + (href.indexOf("?") === -1 ? "?" : "&") + params.toString());
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", apply);
  } else {
    apply();
  }
})();
