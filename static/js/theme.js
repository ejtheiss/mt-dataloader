/**
 * Theme runtime — Light / Dark / System toggle with persistence.
 *
 * Storage key: "dl_theme" in localStorage (values: "light" | "dark" | "system")
 * DOM contract: html[data-theme="light"|"dark"], html.style.colorScheme
 * Event: "dataloader-theme-changed" on document (detail: { preference, effective })
 *
 * The inline boot in <head> handles first-paint to avoid FOUC.
 * This module adds the interactive API, system-preference listener, and event bus.
 */

(function () {
  "use strict";

  var STORAGE_KEY = "dl_theme";
  var EVENT_NAME = "dataloader-theme-changed";
  var _systemQuery = window.matchMedia("(prefers-color-scheme: dark)");

  function _resolve(pref) {
    if (pref === "dark") return "dark";
    if (pref === "light") return "light";
    return _systemQuery.matches ? "dark" : "light";
  }

  function _apply(pref) {
    var effective = _resolve(pref);
    document.documentElement.dataset.theme = effective;
    document.documentElement.style.colorScheme = effective;
    return effective;
  }

  function _onSystemChange() {
    var pref = localStorage.getItem(STORAGE_KEY) || "system";
    if (pref !== "system") return;
    var effective = _apply(pref);
    document.dispatchEvent(new CustomEvent(EVENT_NAME, {
      detail: { preference: pref, effective: effective }
    }));
  }

  /**
   * @param {"light"|"dark"|"system"} preference
   */
  window.applyDataloaderTheme = function (preference) {
    localStorage.setItem(STORAGE_KEY, preference);
    var effective = _apply(preference);
    document.dispatchEvent(new CustomEvent(EVENT_NAME, {
      detail: { preference: preference, effective: effective }
    }));

    var select = document.getElementById("theme-select");
    if (select && select.value !== preference) select.value = preference;
  };

  window.getDataloaderTheme = function () {
    return {
      preference: localStorage.getItem(STORAGE_KEY) || "system",
      effective: document.documentElement.dataset.theme || "light"
    };
  };

  _systemQuery.addEventListener("change", _onSystemChange);

  document.addEventListener("DOMContentLoaded", function () {
    var select = document.getElementById("theme-select");
    if (!select) return;
    select.value = localStorage.getItem(STORAGE_KEY) || "system";
    select.addEventListener("change", function () {
      window.applyDataloaderTheme(this.value);
    });
  });
})();
