/**
 * Monaco JSON Editor — shared factory for all textarea replacements.
 *
 * Usage:
 *   initMonacoEditor(containerEl, {
 *     value: '{}',           // initial JSON string
 *     hiddenInput: inputEl,  // hidden <input> to sync value into (for HTMX forms)
 *     readOnly: false,
 *     onChange: function(val) { ... },
 *     onValidate: function(markers) { ... },
 *   }).then(function(handle) {
 *     handle.getValue();
 *     handle.setValue('...');
 *     handle.format();
 *   });
 */

(function () {
  "use strict";

  var MONACO_CDN = "https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min";
  var _monacoReady = null; // Promise<monaco>
  var _schemaCache = null; // Promise<object>

  function _loadMonaco() {
    if (_monacoReady) return _monacoReady;
    _monacoReady = new Promise(function (resolve) {
      if (typeof require !== "undefined" && require.config) {
        require.config({ paths: { vs: MONACO_CDN + "/vs" } });
        require(["vs/editor/editor.main"], function () {
          resolve(window.monaco);
        });
      } else {
        // Loader not yet ready — wait for DOMContentLoaded
        var check = setInterval(function () {
          if (typeof require !== "undefined" && require.config) {
            clearInterval(check);
            require.config({ paths: { vs: MONACO_CDN + "/vs" } });
            require(["vs/editor/editor.main"], function () {
              resolve(window.monaco);
            });
          }
        }, 50);
      }
    });
    return _monacoReady;
  }

  function _fetchSchema() {
    if (_schemaCache) return _schemaCache;
    var cached = sessionStorage.getItem("_dl_json_schema");
    if (cached) {
      try {
        _schemaCache = Promise.resolve(JSON.parse(cached));
        return _schemaCache;
      } catch (_) {
        /* fall through */
      }
    }
    _schemaCache = fetch("/api/schema")
      .then(function (r) {
        return r.json();
      })
      .then(function (schema) {
        try {
          sessionStorage.setItem("_dl_json_schema", JSON.stringify(schema));
        } catch (_) {
          /* quota exceeded — ignore */
        }
        return schema;
      })
      .catch(function () {
        return null;
      });
    return _schemaCache;
  }

  function _detectTheme() {
    var bg = getComputedStyle(document.documentElement)
      .getPropertyValue("--bg")
      .trim();
    if (!bg) return "vs";
    // Simple heuristic: dark backgrounds start with #0-#4 or rgb < 80
    if (bg.startsWith("#")) {
      var hex = bg.replace("#", "");
      if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
      var r = parseInt(hex.substring(0, 2), 16);
      return r < 80 ? "vs-dark" : "vs";
    }
    return "vs";
  }

  var _modelCounter = 0;

  /**
   * @param {HTMLElement} container  — the div that replaces the textarea
   * @param {Object} opts
   * @param {boolean} [opts.useConfigSchema] — when true, the DataLoaderConfig
   *        JSON schema is applied to this editor.  Defaults to true for
   *        backward compat.  Set to false for payload / freeform editors.
   * @returns {Promise<{editor, getValue, setValue, format, dispose}>}
   */
  window.initMonacoEditor = function (container, opts) {
    opts = opts || {};
    var initialValue = opts.value || "";
    var wantSchema = opts.useConfigSchema !== false;

    return Promise.all([_loadMonaco(), _fetchSchema()]).then(function (results) {
      var monaco = results[0];
      var schema = results[1];

      if (schema && !window._monacoSchemaSet) {
        window._monacoSchemaSet = true;
        monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
          validate: true,
          allowComments: false,
          trailingCommas: "error",
          schemas: [
            {
              uri: "https://dataloader.local/schema.json",
              fileMatch: ["dataloader-config-*"],
              schema: schema,
            },
          ],
        });
      }

      _modelCounter++;
      var modelUri = wantSchema
        ? monaco.Uri.parse("inmemory://model/dataloader-config-" + _modelCounter)
        : monaco.Uri.parse("inmemory://model/freeform-" + _modelCounter);
      var model = monaco.editor.createModel(initialValue, "json", modelUri);

      var editor = monaco.editor.create(container, {
        model: model,
        theme: _detectTheme(),
        minimap: { enabled: false },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        fontSize: 13,
        lineNumbers: "on",
        tabSize: 2,
        wordWrap: "on",
        readOnly: opts.readOnly || false,
        renderValidationDecorations: "on",
        folding: true,
        bracketPairColorization: { enabled: true },
      });

      // Sync to hidden input on content change
      var debounceTimer;
      editor.onDidChangeModelContent(function () {
        clearTimeout(debounceTimer);
        debounceTimer = setTimeout(function () {
          var val = editor.getValue();
          if (opts.hiddenInput) {
            opts.hiddenInput.value = val;
          }
          if (typeof opts.onChange === "function") {
            opts.onChange(val);
          }
        }, 300);
      });

      // Validation marker callback
      if (typeof opts.onValidate === "function") {
        monaco.editor.onDidChangeMarkers(function (uris) {
          var model = editor.getModel();
          if (!model) return;
          for (var i = 0; i < uris.length; i++) {
            if (uris[i].toString() === model.uri.toString()) {
              var markers = monaco.editor.getModelMarkers({ resource: model.uri });
              opts.onValidate(markers);
              break;
            }
          }
        });
      }

      // Initial sync
      if (opts.hiddenInput) {
        opts.hiddenInput.value = initialValue;
      }

      var _dirty = false;
      editor.onDidChangeModelContent(function () {
        _dirty = true;
      });

      var handle = {
        editor: editor,
        getValue: function () {
          return editor.getValue();
        },
        setValue: function (v) {
          editor.setValue(v);
          if (opts.hiddenInput) opts.hiddenInput.value = v;
          _dirty = false;
        },
        format: function () {
          editor.getAction("editor.action.formatDocument").run();
        },
        isDirty: function () {
          return _dirty;
        },
        save: function (url, sessionToken) {
          var content = editor.getValue();
          return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              session_token: sessionToken,
              config_json: content,
            }),
          })
            .then(function (r) { return r.json(); })
            .then(function (result) {
              if (result.status === "ok") {
                _dirty = false;
              }
              return result;
            });
        },
        dispose: function () {
          var m = editor.getModel();
          editor.dispose();
          if (m) m.dispose();
        },
      };

      return handle;
    });
  };
})();
