/**
 * Scenario Builder — DRY, container-scoped module.
 *
 * Zero per-instance globals. All DOM lookups are scoped to the container
 * element. Event handlers use delegation via data-action attributes.
 *
 * Usage (from Jinja template):
 *   initScenarioBuilder(containerEl, {
 *     idx:              0,
 *     flowRef:          "my_flow",
 *     sessionToken:     "abc123",
 *     stagingOptions:   [{value:"happy_path", label:"Happy path"}, ...],
 *     savedRecipe:      {...} | null
 *   });
 */
(function () {
  'use strict';

  function initScenarioBuilder(container, config) {
    if (container.getAttribute('data-scenario-initialized') === '1') {
      return;
    }
    container.setAttribute('data-scenario-initialized', '1');

    var idx = config.idx;
    var flowRef = config.flowRef;
    var sessionToken = config.sessionToken;
    var stagingOptions = config.stagingOptions || [];
    var savedRecipe = config.savedRecipe || null;

    // ----- DOM helpers -----

    function q(sel) { return container.querySelector(sel); }
    function qAll(sel) { return container.querySelectorAll(sel); }
    function field(name) { return q('[data-field="' + name + '"]'); }

    // ----- Formatting helpers -----

    function fmtDetail(d) {
      if (!d) return '';
      if (typeof d === 'string') return d;
      if (Array.isArray(d)) return d.map(function (e) {
        return (e.path || '') + ': ' + (e.message || e.type || JSON.stringify(e));
      }).join('<br>');
      return JSON.stringify(d, null, 2);
    }

    function fmtAmt(cents) {
      return '$' + (cents / 100).toLocaleString('en-US', {
        minimumFractionDigits: 2, maximumFractionDigits: 2
      });
    }

    // ----- Actor dataset cascade -----

    function cascadeDataset(etSelect) {
      var frame = etSelect.getAttribute('data-frame');
      var isIndiv = etSelect.value === 'individual';
      var dsSelect = q('.actor-dataset-select[data-frame="' + frame + '"]');
      if (!dsSelect) return;
      var options = dsSelect.querySelectorAll('option');
      options.forEach(function (opt) {
        if (opt.getAttribute('data-biz-only') === '1') {
          opt.disabled = isIndiv;
          opt.style.display = isIndiv ? 'none' : '';
        }
      });
      if (isIndiv && dsSelect.selectedOptions[0] && dsSelect.selectedOptions[0].disabled) {
        dsSelect.value = 'standard';
      }
    }

    // ----- Staging rules -----

    function addStagingRule(count, selection) {
      var rulesContainer = q('[data-field="staging-rules"]');
      var row = document.createElement('div');
      row.className = 'staging-rule form-row';
      row.style.cssText = 'gap: 8px; align-items: flex-end; margin-top: 4px;';
      var opts = stagingOptions.map(function (o) {
        return '<option value="' + o.value + '"' +
          (o.value === (selection || 'happy_path') ? ' selected' : '') +
          '>' + o.label + '</option>';
      }).join('');
      row.innerHTML =
        '<div class="form-group" style="flex: 0 0 80px;">' +
          '<input type="number" class="staging-count input input-border" value="' + (count || 0) + '" min="0">' +
        '</div>' +
        '<div class="form-group" style="flex: 1;">' +
          '<select class="staging-selection input input-border">' + opts + '</select>' +
        '</div>' +
        '<button type="button" class="btn btn-sm btn-outline" data-action="remove-staging-rule" style="margin-bottom: 2px;">&#x2715;</button>';
      rulesContainer.appendChild(row);
    }

    function resetStagingRules(rules) {
      var rulesContainer = q('[data-field="staging-rules"]');
      var rows = rulesContainer.querySelectorAll('.staging-rule');
      if (rules && rules.length > 0) {
        rows[0].querySelector('.staging-count').value = rules[0].count || 0;
        rows[0].querySelector('.staging-selection').value = rules[0].selection || 'happy_path';
        for (var i = rows.length - 1; i > 0; i--) rows[i].remove();
        for (var j = 1; j < rules.length; j++) {
          addStagingRule(rules[j].count, rules[j].selection);
        }
      } else {
        rows[0].querySelector('.staging-count').value = 0;
        rows[0].querySelector('.staging-selection').value = 'happy_path';
        for (var k = rows.length - 1; k > 0; k--) rows[k].remove();
      }
    }

    // ----- Edge case max sync -----

    function syncEdgeMax() {
      var max = parseInt(field('instances').value) || 1;
      qAll('.edge-case-count-input').forEach(function (input) {
        input.max = max;
        if (parseInt(input.value) > max) input.value = max;
      });
    }

    var instancesInput = field('instances');
    if (instancesInput) instancesInput.addEventListener('input', syncEdgeMax);

    // ----- Amount variance -----

    function getGlobalVariance() {
      var lo = parseFloat((field('variance-min') || { value: '0' }).value) || 0;
      var hi = parseFloat((field('variance-max') || { value: '0' }).value) || 0;
      return { min: lo, max: hi };
    }

    function normalizeVarianceMode(row) {
      var m = row.getAttribute('data-variance-mode');
      if (m === 'global' || m === 'locked' || m === 'custom') return m;
      row.setAttribute('data-variance-mode', 'global');
      return 'global';
    }

    function renderRowAmount(row, gv) {
      var base = parseInt(row.getAttribute('data-base-amount')) || 0;
      var display = row.querySelector('.amount-display');
      if (!display) return;
      var mode = normalizeVarianceMode(row);
      var lo = 0, hi = 0;
      if (mode === 'global') { lo = gv.min; hi = gv.max; }
      else if (mode === 'custom') {
        lo = parseFloat(row.querySelector('.variance-custom-min').value) || 0;
        hi = parseFloat(row.querySelector('.variance-custom-max').value) || 0;
      }
      if (lo >= 0 && hi <= 0) {
        display.textContent = fmtAmt(base);
        display.classList.remove('amount-range');
      } else {
        display.textContent = fmtAmt(Math.round(base * (1 + lo / 100))) + ' \u2013 ' + fmtAmt(Math.round(base * (1 + hi / 100)));
        display.classList.add('amount-range');
      }
    }

    function updateAmountRanges() {
      var gv = getGlobalVariance();
      qAll('.amount-step-item').forEach(function (row) { renderRowAmount(row, gv); });
    }

    // ----- Variance lock cycling -----

    function lockIconSrc(btn, mode) {
      if (mode === 'global') return btn.getAttribute('data-icon-global');
      if (mode === 'locked') return btn.getAttribute('data-icon-locked');
      return btn.getAttribute('data-icon-custom');
    }

    /**
     * One icon per row, three states (cycle on click):
     *   global  — open lock; uses global Min % / Max % like other money-movement rows.
     *   locked  — closed lock; fixed base amount (no variance on this step).
     *   custom  — pencil; per-step min/max % inputs visible.
     */
    function syncVarianceLockIcon(row) {
      var btn = row.querySelector('.variance-lock-btn');
      var img = btn && btn.querySelector('.variance-lock-icon');
      if (!btn || !img) return;
      var mode = normalizeVarianceMode(row);
      var src = lockIconSrc(btn, mode);
      if (src) img.setAttribute('src', src);
      if (mode === 'global') {
        btn.title =
          'Following global variance (same as other steps). Click to lock this step at the base amount (no variance).';
        btn.setAttribute(
          'aria-label',
          'Global variance. Click to lock this step with no variance.'
        );
      } else if (mode === 'locked') {
        btn.title =
          'Locked: no variance on this step. Click to set custom min/max % for this step only.';
        btn.setAttribute(
          'aria-label',
          'Locked without variance. Click for custom variance.'
        );
      } else {
        btn.title =
          'Custom variance for this step. Click to follow global min/max again like other steps.';
        btn.setAttribute(
          'aria-label',
          'Custom variance. Click to use global variance again.'
        );
      }
    }

    function handleVarianceLock(btn) {
      var row = btn.closest('.amount-step-item');
      var mode = normalizeVarianceMode(row);
      var customInputs = row.querySelector('.variance-custom-inputs');
      if (mode === 'global') {
        row.setAttribute('data-variance-mode', 'locked');
        if (customInputs) customInputs.style.display = 'none';
      } else if (mode === 'locked') {
        row.setAttribute('data-variance-mode', 'custom');
        if (customInputs) customInputs.style.display = 'inline-flex';
        var gv = getGlobalVariance();
        var minInp = row.querySelector('.variance-custom-min');
        var maxInp = row.querySelector('.variance-custom-max');
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
        if (customInputs) customInputs.style.display = 'none';
      }
      renderRowAmount(row, getGlobalVariance());
      syncVarianceLockIcon(row);
    }

    // ----- Timing pills -----

    (function initTimingPills() {
      qAll('.timing-date-btn').forEach(function (btn) {
        var dateInput = btn.nextElementSibling;
        btn.addEventListener('click', function () {
          if (dateInput.style.display === 'none') {
            dateInput.style.display = '';
            dateInput.showPicker && dateInput.showPicker();
          } else {
            dateInput.style.display = 'none';
          }
        });
        dateInput.addEventListener('change', function () {
          if (this.value) {
            var pill = btn.previousElementSibling;
            pill.textContent = 'T0';
            pill.title = 'Base: ' + this.value;
          }
        });
      });
      qAll('.timing-pill-offset').forEach(function (pill) {
        var input = pill.nextElementSibling;
        pill.addEventListener('click', function () {
          if (input.style.display === 'none' || input.style.display === '') {
            input.style.display = 'inline-block';
            input.focus();
          }
        });
        input.addEventListener('change', function () {
          pill.textContent = 'T+' + (parseInt(this.value) || 0);
        });
        input.addEventListener('blur', function () {
          this.style.display = '';
        });
      });
    })();

    // ----- Build recipe from form state -----

    function buildRecipe() {
      var instances = parseInt(field('instances').value);
      var recipe = {
        version: 'v1',
        flow_ref: flowRef,
        instances: instances,
        seed: parseInt(field('seed').value),
        seed_dataset: 'standard',
        edge_case_count: 0,
        amount_variance_min_pct: parseFloat((field('variance-min') || { value: '0' }).value) || 0,
        amount_variance_max_pct: parseFloat((field('variance-max') || { value: '0' }).value) || 0,
      };

      var stagingRules = [];
      qAll('[data-field="staging-rules"] .staging-rule').forEach(function (row) {
        var count = parseInt(row.querySelector('.staging-count').value) || 0;
        if (count > 0) {
          stagingRules.push({
            count: count,
            selection: row.querySelector('.staging-selection').value,
          });
        }
      });
      if (stagingRules.length > 0) recipe.staging_rules = stagingRules;

      var stepVariance = {};
      qAll('.amount-step-item').forEach(function (row) {
        var mode = normalizeVarianceMode(row);
        var stepId = row.getAttribute('data-step-id');
        if (mode === 'locked') stepVariance[stepId] = {};
        else if (mode === 'custom') {
          stepVariance[stepId] = {
            min_pct: parseFloat(row.querySelector('.variance-custom-min').value) || 0,
            max_pct: parseFloat(row.querySelector('.variance-custom-max').value) || 0,
          };
        }
      });
      if (Object.keys(stepVariance).length > 0) recipe.step_variance = stepVariance;

      var edgeOverrides = {};
      qAll('.edge-case-count-input').forEach(function (input) {
        var label = input.getAttribute('data-label');
        var count = parseInt(input.value) || 0;
        if (count > instances) count = instances;
        if (count > 0) {
          edgeOverrides[label] = { count: count };
        }
      });
      if (Object.keys(edgeOverrides).length > 0) {
        recipe.edge_case_overrides = edgeOverrides;
      }

      var overrides = {};
      qAll('.field-row[data-frame]').forEach(function (row) {
        var frame = row.getAttribute('data-frame');
        var frameType = row.getAttribute('data-frame-type');
        if (frameType === 'direct') {
          var cnInput = row.querySelector('.actor-customer-name');
          if (cnInput && cnInput.value.trim()) {
            overrides[frame] = { customer_name: cnInput.value.trim() };
          }
        } else {
          var etSelect = row.querySelector('.actor-entity-type');
          var dsSelect = row.querySelector('.actor-dataset-select');
          var ov = {};
          if (etSelect) ov.entity_type = etSelect.value;
          if (dsSelect && dsSelect.value !== 'standard') ov.dataset = dsSelect.value;
          if (Object.keys(ov).length > 0) overrides[frame] = ov;
        }
      });
      if (Object.keys(overrides).length > 0) {
        recipe.actor_overrides = overrides;
      }

      var spreadDays = parseInt(field('timing-spread').value) || 0;
      var t0Input = q('.timing-date-input');
      var startDate = t0Input ? t0Input.value : '';
      var stepOffsets = {};
      qAll('.amount-step-item').forEach(function (row, rowIdx) {
        var stepId = row.getAttribute('data-step-id');
        var offsetInput = row.querySelector('.timing-offset-input');
        if (offsetInput) {
          var val = parseInt(offsetInput.value) || 0;
          var defaultVal = rowIdx;
          if (val !== defaultVal) stepOffsets[stepId] = val;
        }
      });
      if (spreadDays > 0 || startDate || Object.keys(stepOffsets).length > 0) {
        recipe.timing = {};
        if (spreadDays > 0) recipe.timing.instance_spread_days = spreadDays;
        if (startDate) recipe.timing.start_date = startDate;
        if (Object.keys(stepOffsets).length > 0) recipe.timing.step_offsets = stepOffsets;
      }

      return recipe;
    }

    // ----- Apply to config -----

    function showApplyMessage(html, isError) {
      var out = field('result');
      if (out) {
        out.innerHTML = html;
        out.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      }
      if (typeof showToast === 'function') {
        var text = out ? out.textContent.replace(/\s+/g, ' ').trim() : '';
        if (text.length > 180) text = text.slice(0, 177) + '\u2026';
        showToast({
          status: isError ? 'error' : 'success',
          message: text || (isError ? 'Apply failed' : 'Applied'),
          duration: isError ? 8000 : 3000,
        });
      }
    }

    async function genApply() {
      var btn = q('[data-action="apply"]');
      var out = field('result');
      var label = 'Apply to Config';
      if (!btn) return;
      if (out) out.innerHTML = '';
      btn.disabled = true;
      btn.textContent = 'Applying\u2026';
      var recipe;
      try {
        recipe = buildRecipe();
      } catch (buildErr) {
        btn.disabled = false;
        btn.textContent = label;
        showApplyMessage(
          '<div class="alert alert--critical"><p>Invalid scale settings: ' +
            (buildErr && buildErr.message ? String(buildErr.message) : String(buildErr)) +
            '</p></div>',
          true
        );
        return;
      }
      try {
        var resp = await fetch('/api/flows/recipe-to-working-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Session-Token': sessionToken },
          body: JSON.stringify(recipe),
        });
        var data;
        try {
          data = await resp.json();
        } catch (parseErr) {
          showApplyMessage(
            '<div class="alert alert--critical"><p>Invalid response (HTTP ' +
              resp.status +
              '). Re-validate on Setup and try again.</p></div>',
            true
          );
          return;
        }
        if (!resp.ok || data.error) {
          showApplyMessage(
            '<div class="alert alert--critical"><p>' +
              (data.error || 'Request failed (HTTP ' + resp.status + ')') +
              (data.detail ? '<br>' + fmtDetail(data.detail) : '') +
              '</p></div>',
            true
          );
          return;
        }
        if (out) {
          out.innerHTML =
            '<div class="alert alert--success"><p>Applied. Reloading\u2026</p></div>';
        }
        if (typeof showToast === 'function') {
          showToast({ status: 'success', message: 'Scale applied — reloading', duration: 2500 });
        }
        window.location.href =
          '/flows?session_token=' +
          encodeURIComponent(sessionToken) +
          '&open_scale=' +
          idx;
      } catch (netErr) {
        showApplyMessage(
          '<div class="alert alert--critical"><p>Network error: ' +
            (netErr && netErr.message ? netErr.message : String(netErr)) +
            '</p></div>',
          true
        );
      } finally {
        btn.disabled = false;
        btn.textContent = label;
      }
    }

    // ----- Central event delegation -----

    container.addEventListener('click', function (e) {
      var actionEl = e.target.closest('[data-action]');
      if (!actionEl || !container.contains(actionEl)) return;
      var action = actionEl.getAttribute('data-action');
      if (action === 'apply') {
        e.preventDefault();
        e.stopPropagation();
        void genApply().catch(function (err) {
          showApplyMessage(
            '<div class="alert alert--critical"><p>Apply failed: ' +
              (err && err.message ? err.message : String(err)) +
              '</p></div>',
            true
          );
        });
        return;
      }
      switch (action) {
        case 'add-staging-rule': addStagingRule(); break;
        case 'remove-staging-rule': actionEl.closest('.staging-rule').remove(); break;
      }
    });

    container.addEventListener('click', function (e) {
      var btn = e.target.closest('.variance-lock-btn');
      if (!btn || !container.contains(btn)) return;
      e.preventDefault();
      e.stopPropagation();
      handleVarianceLock(btn);
    });

    container.addEventListener('change', function (e) {
      if (e.target.matches('.actor-entity-type')) cascadeDataset(e.target);
    });

    container.addEventListener('input', function (e) {
      if (e.target.matches('[data-field="variance-min"], [data-field="variance-max"]')) {
        updateAmountRanges();
      }
      if (e.target.matches('.variance-custom-min, .variance-custom-max')) {
        var row = e.target.closest('.amount-step-item');
        renderRowAmount(row, getGlobalVariance());
      }
    });

    // ----- Restore saved recipe -----

    if (savedRecipe) {
      field('instances').value = savedRecipe.instances || 10;
      field('seed').value = savedRecipe.seed || 424242;
      if (savedRecipe.amount_variance_min_pct != null && field('variance-min'))
        field('variance-min').value = savedRecipe.amount_variance_min_pct;
      if (savedRecipe.amount_variance_max_pct != null && field('variance-max'))
        field('variance-max').value = savedRecipe.amount_variance_max_pct;

      if (savedRecipe.staging_rules && savedRecipe.staging_rules.length > 0) {
        resetStagingRules(savedRecipe.staging_rules);
      } else if (savedRecipe.staged_count > 0) {
        resetStagingRules([{ count: savedRecipe.staged_count, selection: savedRecipe.staged_selection || 'happy_path' }]);
      }

      if (savedRecipe.edge_case_overrides) {
        for (var label in savedRecipe.edge_case_overrides) {
          var inp = q('.edge-case-count-input[data-label="' + label + '"]');
          if (inp) inp.value = savedRecipe.edge_case_overrides[label].count || 0;
        }
      }

      if (savedRecipe.step_variance) {
        for (var sid in savedRecipe.step_variance) {
          var row = q('.amount-step-item[data-step-id="' + sid + '"]');
          if (!row) continue;
          var sv = savedRecipe.step_variance[sid];
          if (!sv || (Object.keys(sv).length === 0)) {
            row.setAttribute('data-variance-mode', 'locked');
          } else {
            row.setAttribute('data-variance-mode', 'custom');
            var customInputs = row.querySelector('.variance-custom-inputs');
            if (customInputs) customInputs.style.display = 'inline-flex';
            var minInput = row.querySelector('.variance-custom-min');
            var maxInput = row.querySelector('.variance-custom-max');
            if (minInput) minInput.value = sv.min_pct || 0;
            if (maxInput) maxInput.value = sv.max_pct || 0;
          }
        }
      }

      if (savedRecipe.timing) {
        var t = savedRecipe.timing;
        if (field('timing-spread')) field('timing-spread').value = t.instance_spread_days || 0;
        if (t.start_date) {
          var t0Input = q('.timing-date-input');
          if (t0Input) {
            t0Input.value = t.start_date;
            t0Input.style.display = '';
            var pill = t0Input.previousElementSibling && t0Input.previousElementSibling.previousElementSibling;
            if (pill && pill.classList.contains('timing-pill-t0')) pill.title = 'Base: ' + t.start_date;
          }
        }
        if (t.step_offsets) {
          qAll('.amount-step-item').forEach(function (row) {
            var stepId = row.getAttribute('data-step-id');
            if (stepId in t.step_offsets) {
              var offsetInput = row.querySelector('.timing-offset-input');
              if (offsetInput) {
                offsetInput.value = t.step_offsets[stepId];
                var pill = row.querySelector('.timing-pill-offset');
                if (pill) pill.textContent = 'T+' + t.step_offsets[stepId];
              }
            }
          });
        }
      }

      syncEdgeMax();
      updateAmountRanges();
    }

    qAll('.amount-step-item').forEach(syncVarianceLockIcon);
  }

  window.initScenarioBuilder = initScenarioBuilder;
})();
