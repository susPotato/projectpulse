/*
  Wires up the light/dark switch on the rail.

  The switch itself only ever *reads and writes* `<html data-theme>` - the
  early inline script in each page's <head> is what sets it before first
  paint, so there is no flash of the wrong palette while this file loads.
  This script just paints the icon and handles the click.
*/
(function () {
  "use strict";

  var KEY = "pulse-theme";

  // Sun and moon, on the rail's 24px grid. Each shows the mode a click
  // switches *to*, not the one currently active - the common convention.
  var SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>';
  var MOON = '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/>';

  function current() {
    return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
  }

  function paint(btn, theme) {
    var svg = btn.querySelector("svg");
    if (svg) svg.innerHTML = theme === "dark" ? SUN : MOON;
    var label = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
    btn.setAttribute("aria-label", label);
    btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
    btn.title = label;
  }

  function init() {
    var buttons = document.querySelectorAll("[data-theme-toggle]");
    if (!buttons.length) return;

    var theme = current();
    buttons.forEach(function (btn) { paint(btn, theme); });

    buttons.forEach(function (btn) {
      btn.addEventListener("click", function () {
        theme = theme === "dark" ? "light" : "dark";
        document.documentElement.setAttribute("data-theme", theme);
        try {
          localStorage.setItem(KEY, theme);
        } catch (e) {
          // Private browsing / storage disabled: the toggle still works for
          // this page view, it just will not be remembered.
        }
        buttons.forEach(function (b) { paint(b, theme); });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
