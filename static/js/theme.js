/**
 * Theme sync: `data-theme` on <html> matches user preference + system dark mode.
 * Boot snippet in base.html sets initial theme before paint; this file wires
 * listeners and `window.applyDataloaderTheme` for a future settings toggle.
 */
(function () {
  "use strict";

  function storedOrNull() {
    try {
      var s = localStorage.getItem("dataloader-theme");
      return s === "light" || s === "dark" ? s : null;
    } catch (e) {
      return null;
    }
  }

  function resolveTheme() {
    var stored = storedOrNull();
    if (stored) return stored;
    return window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    window.dispatchEvent(
      new CustomEvent("dataloader-theme-changed", { detail: { theme: t } })
    );
  }

  /** @param {'light'|'dark'|'system'} mode */
  window.applyDataloaderTheme = function (mode) {
    try {
      if (mode === "system") {
        localStorage.removeItem("dataloader-theme");
      } else {
        localStorage.setItem("dataloader-theme", mode);
      }
    } catch (e) {
      /* ignore quota / private mode */
    }
    applyTheme(resolveTheme());
  };

  applyTheme(resolveTheme());

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", function () {
      if (storedOrNull()) return;
      applyTheme(resolveTheme());
    });
})();
