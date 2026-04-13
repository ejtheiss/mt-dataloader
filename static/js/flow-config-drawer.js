/**
 * Plan 10e — Flow config drawer: Bands 2–4 (bindings + recipe-patch for bands 3–4).
 * Runs init after each HTMX swap of the drawer partial (see flow_config_drawer.html).
 */
(function () {
  'use strict';

  function fmtMoneyCents(cents) {
    var n = parseInt(cents, 10) || 0;
    return '$' + (n / 100).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  function normalizeVarianceMode(row) {
    var m = row.getAttribute('data-variance-mode');
    if (m === 'global' || m === 'locked' || m === 'custom') return m;
    row.setAttribute('data-variance-mode', 'global');
    return 'global';
  }

  function getGlobalVariance(root) {
    var vmin = root.querySelector('[data-flow-cfg-variance-min]');
    var vmax = root.querySelector('[data-flow-cfg-variance-max]');
    return {
      min: parseFloat((vmin && vmin.value) || '0') || 0,
      max: parseFloat((vmax && vmax.value) || '0') || 0,
    };
  }

  function renderRowAmount(row, gv) {
    var base = parseInt(row.getAttribute('data-base-amount'), 10) || 0;
    var el = row.querySelector('.flow-cfg-amount-display');
    if (!el) return;
    var mode = normalizeVarianceMode(row);
    var lo = 0;
    var hi = 0;
    if (mode === 'global') {
      lo = gv.min;
      hi = gv.max;
    } else if (mode === 'custom') {
      lo = parseFloat(row.querySelector('.flow-cfg-var-custom-min').value) || 0;
      hi = parseFloat(row.querySelector('.flow-cfg-var-custom-max').value) || 0;
    }
    if (lo >= 0 && hi <= 0) {
      el.textContent = fmtMoneyCents(base);
    } else {
      el.textContent =
        fmtMoneyCents(Math.round(base * (1 + lo / 100))) +
        ' – ' +
        fmtMoneyCents(Math.round(base * (1 + hi / 100)));
    }
  }

  function updateAmountRanges(root) {
    var gv = getGlobalVariance(root);
    root.querySelectorAll('[data-flow-cfg-amount-row]').forEach(function (row) {
      renderRowAmount(row, gv);
    });
  }

  function lockIconSrc(btn, mode) {
    if (mode === 'global') return btn.getAttribute('data-icon-global');
    if (mode === 'locked') return btn.getAttribute('data-icon-locked');
    return btn.getAttribute('data-icon-custom');
  }

  function syncVarianceLockIcon(row) {
    var btn = row.querySelector('.flow-cfg-variance-lock-btn');
    var img = btn && btn.querySelector('.flow-cfg-variance-lock-icon');
    if (!btn || !img) return;
    var mode = normalizeVarianceMode(row);
    var src = lockIconSrc(btn, mode);
    if (src) img.setAttribute('src', src);
  }

  function cycleVarianceLock(row, root) {
    var mode = normalizeVarianceMode(row);
    var custom = row.querySelector('.flow-cfg-var-custom');
    if (mode === 'global') {
      row.setAttribute('data-variance-mode', 'locked');
      if (custom) custom.style.display = 'none';
    } else if (mode === 'locked') {
      row.setAttribute('data-variance-mode', 'custom');
      if (custom) custom.style.display = 'inline-flex';
      var gv = getGlobalVariance(root);
      var minInp = row.querySelector('.flow-cfg-var-custom-min');
      var maxInp = row.querySelector('.flow-cfg-var-custom-max');
      if (
        minInp &&
        maxInp &&
        parseFloat(minInp.value) === 0 &&
        parseFloat(maxInp.value) === 0 &&
        (gv.min !== 0 || gv.max !== 0)
      ) {
        minInp.value = gv.min;
        maxInp.value = gv.max;
      }
    } else {
      row.setAttribute('data-variance-mode', 'global');
      if (custom) custom.style.display = 'none';
    }
    renderRowAmount(row, getGlobalVariance(root));
    syncVarianceLockIcon(row);
  }

  function buildRecipeBands34Patch(root) {
    var patternRef = root.getAttribute('data-pattern-ref');
    var patch = {};

    var rules = [];
    root.querySelectorAll('[data-flow-cfg-staging-row]').forEach(function (row) {
      var c = parseInt(row.querySelector('.flow-cfg-staging-count').value, 10) || 0;
      var sel = row.querySelector('.flow-cfg-staging-select').value;
      if (c > 0) rules.push({ count: c, selection: sel });
    });
    patch.staging_rules = rules;

    var spreadEl = root.querySelector('[data-flow-cfg-timing-spread]');
    var spread = spreadEl ? parseInt(spreadEl.value, 10) || 0 : 0;
    var timing = {};
    if (spread > 0) timing.instance_spread_days = spread;
    else timing.instance_spread_days = 0;

    var dateInp = root.querySelector('.flow-cfg-timing-start-date');
    if (dateInp && dateInp.value) timing.start_date = dateInp.value;

    var stepOffsets = {};
    var rows = root.querySelectorAll('[data-flow-cfg-amount-row]');
    rows.forEach(function (row, idx) {
      var sid = row.getAttribute('data-step-id');
      var offInp = row.querySelector('.flow-cfg-timing-offset');
      if (offInp) {
        var val = parseInt(offInp.value, 10) || 0;
        var defaultVal = idx;
        if (val !== defaultVal) stepOffsets[sid] = val;
      }
    });
    if (Object.keys(stepOffsets).length > 0) timing.step_offsets = stepOffsets;

    patch.timing = timing;

    var vmin = root.querySelector('[data-flow-cfg-variance-min]');
    var vmax = root.querySelector('[data-flow-cfg-variance-max]');
    patch.amount_variance_min_pct = parseFloat((vmin && vmin.value) || '0') || 0;
    patch.amount_variance_max_pct = parseFloat((vmax && vmax.value) || '0') || 0;

    var stepVar = {};
    rows.forEach(function (row) {
      var sid = row.getAttribute('data-step-id');
      var mode = normalizeVarianceMode(row);
      if (mode === 'locked') stepVar[sid] = {};
      else if (mode === 'custom') {
        stepVar[sid] = {
          min_pct: parseFloat(row.querySelector('.flow-cfg-var-custom-min').value) || 0,
          max_pct: parseFloat(row.querySelector('.flow-cfg-var-custom-max').value) || 0,
        };
      }
    });
    if (Object.keys(stepVar).length > 0) patch.step_variance = stepVar;

    return { flow_ref: patternRef, patch: patch };
  }

  function flowCfgReloadDrawer(flowIdx, token) {
    var url =
      '/api/flows/' +
      encodeURIComponent(String(flowIdx)) +
      '/config-drawer?session_token=' +
      encodeURIComponent(token);
    if (window.htmx) {
      htmx.ajax('GET', url, { target: '#drawer-content', swap: 'innerHTML' });
    } else {
      fetch(url)
        .then(function (r) {
          return r.text();
        })
        .then(function (html) {
          var t = document.getElementById('drawer-content');
          if (t) t.innerHTML = html;
        });
    }
  }

  function setupFlowCfgRecipeBands(root) {
    if (!root || root.getAttribute('data-flow-cfg-js-init') === '1') return;
    root.setAttribute('data-flow-cfg-js-init', '1');

    root.querySelectorAll('[data-flow-cfg-amount-row]').forEach(function (row) {
      var mode = normalizeVarianceMode(row);
      var custom = row.querySelector('.flow-cfg-var-custom');
      if (custom) custom.style.display = mode === 'custom' ? 'inline-flex' : 'none';
      syncVarianceLockIcon(row);
    });
    updateAmountRanges(root);

    root.addEventListener('input', function (e) {
      if (
        e.target.classList.contains('flow-cfg-var-custom-min') ||
        e.target.classList.contains('flow-cfg-var-custom-max') ||
        e.target.matches('[data-flow-cfg-variance-min]') ||
        e.target.matches('[data-flow-cfg-variance-max]')
      ) {
        updateAmountRanges(root);
      }
    });

    root.addEventListener('click', function (e) {
      var t = e.target;
      var lockBtn = t.closest && t.closest('.flow-cfg-variance-lock-btn');
      if (lockBtn) {
        var row = lockBtn.closest('[data-flow-cfg-amount-row]');
        if (row) cycleVarianceLock(row, root);
        return;
      }
      var dateBtn = t.closest && t.closest('.flow-cfg-date-btn');
      if (dateBtn) {
        var row0 = dateBtn.closest('[data-flow-cfg-amount-row]');
        var inp = row0 && row0.querySelector('.flow-cfg-timing-start-date');
        if (inp) {
          inp.classList.remove('flow-cfg-sr-only');
          if (inp.showPicker) inp.showPicker();
          else inp.focus();
        }
        return;
      }
      if (t.closest && t.closest('[data-flow-cfg-staging-add]')) {
        var wrap = root.querySelector('[data-flow-cfg-staging-rules]');
        if (!wrap) return;
        var proto = wrap.querySelector('[data-flow-cfg-staging-row]');
        if (!proto) return;
        var clone = proto.cloneNode(true);
        clone.querySelector('.flow-cfg-staging-count').value = '0';
        clone.querySelector('.flow-cfg-staging-remove').style.visibility = 'visible';
        wrap.appendChild(clone);
        return;
      }
      if (t.closest && t.closest('.flow-cfg-staging-remove')) {
        var rm = t.closest('.flow-cfg-staging-remove');
        var prow = rm && rm.closest('[data-flow-cfg-staging-row]');
        var allR = root.querySelectorAll('[data-flow-cfg-staging-row]');
        if (prow && allR.length > 1) prow.remove();
        return;
      }
      if (t.closest && t.closest('[data-flow-cfg-apply-recipe]')) {
        var st = root.querySelector('[data-flow-cfg-recipe-status]');
        if (st) {
          st.textContent = 'Applying…';
          st.className = 'metadata-save-status';
        }
        var token = root.getAttribute('data-session-token');
        var flowIdx = root.getAttribute('data-flow-idx');
        var body = buildRecipeBands34Patch(root);
        fetch('/api/flows/recipe-patch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': token },
          body: JSON.stringify(body),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data };
            });
          })
          .then(function (res) {
            if (st) {
              if (res.ok && !res.data.error) {
                st.textContent = 'Applied';
                st.className = 'metadata-save-status saved';
                flowCfgReloadDrawer(flowIdx, token);
              } else {
                st.textContent = res.data.error || 'Error';
                st.className = 'metadata-save-status error';
              }
            }
          })
          .catch(function () {
            if (st) {
              st.textContent = 'Network error';
              st.className = 'metadata-save-status error';
            }
          });
        return;
      }
      if (t.closest && t.closest('[data-flow-cfg-save-bindings]')) {
        var stb = root.querySelector('[data-flow-cfg-bindings-status]');
        if (stb) {
          stb.textContent = 'Saving…';
          stb.className = 'metadata-save-status';
        }
        var tok = root.getAttribute('data-session-token');
        var fidx = root.getAttribute('data-flow-idx');
        var map = {};
        root.querySelectorAll('.flow-cfg-binding-select').forEach(function (sel) {
          var frame = sel.getAttribute('data-frame');
          if (frame) map[frame] = sel.value;
        });
        fetch('/api/flows/' + encodeURIComponent(String(fidx)) + '/actor-bindings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': tok },
          body: JSON.stringify({ frame_to_library_id: map }),
        })
          .then(function (r) {
            return r.json().then(function (data) {
              return { ok: r.ok, data: data };
            });
          })
          .then(function (res) {
            if (stb) {
              if (res.ok && res.data.status === 'ok') {
                stb.textContent = 'Saved';
                stb.className = 'metadata-save-status saved';
                flowCfgReloadDrawer(fidx, tok);
              } else {
                stb.textContent = res.data.error || 'Error';
                stb.className = 'metadata-save-status error';
              }
            }
          })
          .catch(function () {
            if (stb) {
              stb.textContent = 'Network error';
              stb.className = 'metadata-save-status error';
            }
          });
      }
    });

    root.querySelectorAll('.flow-cfg-timing-offset').forEach(function (inp) {
      inp.addEventListener('change', function () {
        var row = inp.closest('[data-flow-cfg-amount-row]');
        var pill = row && row.querySelector('.flow-cfg-timing-pill--off');
        if (pill) pill.textContent = 'T+' + (parseInt(inp.value, 10) || 0);
      });
    });
  }

  window.initFlowCfgRecipeUI = function (root) {
    if (!root) return;
    root.removeAttribute('data-flow-cfg-js-init');
    setupFlowCfgRecipeBands(root);
  };
})();
