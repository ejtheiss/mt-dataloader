/**
 * MT Interactions — Toast, Filter Bar, Export helpers
 * ============================================================
 * Canonical sources:
 *   - Toast.tsx          (lines 128-142)  — Toast container
 *   - Toast.tsx          (lines 59-112)   — ToastPanel (icon + message)
 *   - Toast.tsx          (lines 20-43)    — ToastButton (action button)
 *   - useToast.ts        (lines 26-28)    — defaults: status "info", duration 4000ms
 *   - useToast.ts        (lines 42-58)    — toast render via Chakra
 *   - FilterArea.tsx     (lines 58-185)   — filter management
 *   - FilterPill.tsx     (lines 711-735)  — applied filter → Chip
 *   - Chip.tsx           (lines 118-136)  — chip content layout
 *   - ExportDataButton.tsx (lines 180-195) — toast after export
 *   - ExportDataButton.tsx (lines 155-175) — CSV/JSON export logic
 *
 * This file replaces the app-specific showToast(), and provides
 * MT-pattern equivalents for filter pill management and export
 * with toast feedback.
 * ============================================================
 */

/* ============================================================
   1. TOAST SYSTEM
   Source: Toast.tsx, useToast.ts
   ============================================================ */

/**
 * Toast icons by status
 * Source: Toast.tsx lines 62-91
 *   success → check_circle (text-green-300)
 *   error   → cancel (text-red-300)
 *   warning → warning (text-yellow-200)
 *   info    → info (text-blue-300)
 */
const TOAST_ICONS = {
  success: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-toast-icon-success"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
  error:   '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-toast-icon-error"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
  warning: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-toast-icon-warning"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
  info:    '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="mt-toast-icon-info"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
};

/**
 * Ensure toast container exists
 * Source: useToast.ts line 53 → Chakra position: "bottom"
 */
function getToastContainer() {
  var container = document.getElementById('mt-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'mt-toast-container';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }
  return container;
}

/**
 * Show a toast notification — MT pattern replacement for showToast()
 *
 * Source: useToast.ts lines 26-58
 *   - Default status: "info"
 *   - Default duration: 4000ms
 *   - Default isClosable: true
 *
 * Source: Toast.tsx lines 128-142 (outer container)
 * Source: Toast.tsx lines 59-112 (ToastPanel: icon + message)
 * Source: Toast.tsx lines 20-43  (ToastButton: action/close)
 *
 * @param {Object} opts
 * @param {string} opts.message       — toast message text
 * @param {string} [opts.status]      — "success" | "error" | "warning" | "info" (default: "info")
 * @param {number} [opts.duration]    — auto-dismiss ms (default: 4000, 0 = sticky)
 * @param {boolean} [opts.closable]   — show close button (default: true)
 * @param {Object} [opts.action]      — { label: string, onclick: function|string }
 */
function showToast(opts) {
  if (typeof opts === 'string') {
    opts = { message: opts };
  }

  var status = opts.status || 'info';
  var duration = opts.duration !== undefined ? opts.duration : 4000;
  var closable = opts.closable !== undefined ? opts.closable : true;

  var container = getToastContainer();

  // Build toast HTML
  // Source: Toast.tsx lines 131-141
  var toast = document.createElement('div');
  toast.className = 'mt-toast';

  var panelHtml = '<div class="mt-toast-panel">';
  // Source: Toast.tsx lines 62-91 — icon per status
  panelHtml += '<span class="mt-toast-icon">' + (TOAST_ICONS[status] || TOAST_ICONS.info) + '</span>';
  panelHtml += '<span class="mt-toast-message">' + escapeHtml(opts.message) + '</span>';
  panelHtml += '</div>';

  // Source: Toast.tsx lines 20-43 — action button
  var actionsHtml = '';
  if (opts.action) {
    actionsHtml += '<div class="mt-toast-action">';
    actionsHtml += '<button class="mt-toast-action-btn" data-toast-action="true">' + escapeHtml(opts.action.label) + '</button>';
    actionsHtml += '</div>';
  }

  // Source: Toast.tsx lines 28-29 — close button
  if (closable) {
    actionsHtml += '<div class="mt-toast-close">';
    actionsHtml += '<button class="mt-toast-close-btn" data-toast-close="true">&times;</button>';
    actionsHtml += '</div>';
  }

  toast.innerHTML = panelHtml + actionsHtml;

  // Wire action button
  if (opts.action) {
    var actionBtn = toast.querySelector('[data-toast-action]');
    if (actionBtn) {
      actionBtn.addEventListener('click', function() {
        if (typeof opts.action.onclick === 'function') {
          opts.action.onclick();
        } else if (typeof opts.action.onclick === 'string') {
          new Function(opts.action.onclick)();
        }
        dismissToast(toast);
      });
    }
  }

  // Wire close button
  var closeBtn = toast.querySelector('[data-toast-close]');
  if (closeBtn) {
    closeBtn.addEventListener('click', function() {
      dismissToast(toast);
    });
  }

  container.appendChild(toast);

  // Source: useToast.ts line 28 — default duration 4000ms
  if (duration > 0) {
    setTimeout(function() { dismissToast(toast); }, duration);
  }

  return toast;
}

/**
 * Dismiss a toast with exit animation
 */
function dismissToast(toast) {
  if (!toast || toast.classList.contains('mt-toast-out')) return;
  toast.classList.add('mt-toast-out');
  setTimeout(function() {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
  }, 150);
}

/* ============================================================
   2. FILTER BAR MANAGEMENT
   Source: FilterArea.tsx, FilterPill.tsx, Chip.tsx
   ============================================================ */

/**
 * Active filters state
 * Source: FilterArea.tsx lines 72-95 — appliedFilters state management
 */
var _activeFilters = {};

/**
 * Add a filter pill to the filter bar
 * Source: FilterArea.tsx lines 113-141 — handleFilterChange
 * Source: FilterPill.tsx lines 711-735 — renders Chip
 * Source: Chip.tsx lines 118-136 — chip content
 *
 * @param {string} key    — filter key (e.g. "status", "type")
 * @param {string} label  — display label (e.g. "Status")
 * @param {string} [value] — filter value, prompts if not provided
 * @param {string} [icon]  — optional icon HTML
 */
function addFilter(key, label, value, icon) {
  if (!value) {
    value = prompt('Enter value for ' + label + ':');
    if (!value) return;
  }

  _activeFilters[key] = { key: key, label: label, value: value, icon: icon || '' };
  renderFilterPills();
  applyFilters();
}

/**
 * Remove a filter pill
 * Source: FilterPill.tsx line 728 — onRemove callback
 * Source: Chip.tsx RemoveIcon lines 11-35
 *
 * @param {string} key — filter key to remove
 */
function removeFilter(key) {
  delete _activeFilters[key];
  renderFilterPills();
  applyFilters();
}

/**
 * Render filter pills into the container
 * Source: FilterArea.tsx lines 153-185 — maps appliedFilters to FilterPill components
 * Source: FilterPill.tsx lines 711-735 — Chip with icon, label, value, remove button
 */
function renderFilterPills() {
  var container = document.getElementById('active-filters');
  if (!container) return;

  var html = '';
  for (var key in _activeFilters) {
    var f = _activeFilters[key];
    // Source: Chip.tsx lines 118-136
    html += '<div class="mt-chip" data-filter-key="' + escapeAttr(key) + '">';
    html += '  <div class="mt-chip-content">';
    if (f.icon) html += '<span class="mt-chip-icon">' + f.icon + '</span>';
    html += '    <span class="mt-chip-label">' + escapeHtml(f.label) + ':</span>';
    html += '    <span class="mt-chip-value">' + escapeHtml(f.value) + '</span>';
    html += '  </div>';
    // Source: Chip.tsx RemoveIcon lines 11-35
    html += '  <button type="button" class="mt-chip-remove" onclick="removeFilter(\'' + escapeAttr(key) + '\')" aria-label="Remove ' + escapeAttr(f.label) + ' filter">';
    html += '    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
    html += '  </button>';
    html += '</div>';
  }
  container.innerHTML = html;
}

/**
 * Apply active filters — updates URL params and triggers HTMX or JS filtering
 * Source: FilterArea.tsx lines 113-141 — handleFilterChange triggers query refetch
 */
function applyFilters() {
  var params = new URLSearchParams(window.location.search);

  // Clear old filter params
  var toDelete = [];
  params.forEach(function(v, k) {
    if (k.startsWith('filter_')) toDelete.push(k);
  });
  toDelete.forEach(function(k) { params.delete(k); });

  // Set new filter params
  for (var key in _activeFilters) {
    params.set('filter_' + key, _activeFilters[key].value);
  }

  // Update URL
  var newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
  history.replaceState(null, '', newUrl);

  // Trigger HTMX refresh if a target table exists
  var table = document.querySelector('[hx-get]');
  if (table && typeof htmx !== 'undefined') {
    htmx.trigger(table, 'filterChanged');
  }

  // Dispatch custom event for JS-only filtering (e.g. filterEdgeCases)
  document.dispatchEvent(new CustomEvent('mt:filtersChanged', {
    detail: { filters: Object.assign({}, _activeFilters) }
  }));
}

/**
 * Get current active filters
 * @returns {Object} key→{key, label, value, icon} map
 */
function getActiveFilters() {
  return Object.assign({}, _activeFilters);
}


/* ============================================================
   3. EXPORT WITH TOAST FEEDBACK
   Source: ExportDataButton.tsx
   ============================================================ */

/**
 * Export data as JSON with MT toast feedback
 * Source: ExportDataButton.tsx lines 155-175 — initiateExport triggers mutation
 * Source: ExportDataButton.tsx lines 180-195 — success toast
 *
 * Wraps the app's exportPreviewJSON() with MT toast pattern.
 *
 * @param {Function} exportFn — the actual export function (e.g. exportPreviewJSON)
 * @param {string} [filename] — filename for toast message
 */
function exportWithToast(exportFn, filename) {
  try {
    exportFn();
    // Source: ExportDataButton.tsx lines 180-195
    // Original toast: status "success", title "Your data export is ready."
    showToast({
      status: 'success',
      message: 'Your data export is ready.' + (filename ? ' (' + filename + ')' : ''),
      duration: 4000
    });
  } catch (e) {
    showToast({
      status: 'error',
      message: 'Export failed: ' + e.message,
      duration: 6000
    });
  }
}

/**
 * Convenience: Export JSON with toast
 * Wraps the existing exportPreviewJSON function
 */
function exportJSON() {
  if (typeof exportPreviewJSON === 'function') {
    exportWithToast(exportPreviewJSON, 'preview.json');
  }
}

/**
 * Convenience: Export CSV with toast
 * Wraps the existing exportPreviewCSV function
 */
function exportCSV() {
  if (typeof exportPreviewCSV === 'function') {
    exportWithToast(exportPreviewCSV, 'preview.csv');
  }
}


/* ============================================================
   4. SORT TABLE (MT IndexTable pattern)
   Source: IndexTable.tsx
   ============================================================ */

/**
 * Sort state management following MT's IndexTable pattern
 * Source: IndexTable.tsx lines 168-192 — getNewSortOrder function
 *   ASC → DESC → (remove) → ASC
 *
 * Source: IndexTable.tsx lines 1595-1640 — sort button in header
 *   <button onClick={() => onChangeOrderBy(getNewSortOrder(...))} ...>
 *     <span>{header}</span>
 *     <Icon iconName={sortIconName(sortOrder)} />
 *   </button>
 */

/**
 * Get next sort order — matches MT's getNewSortOrder exactly
 * Source: IndexTable.tsx lines 168-192
 * @param {string|null} currentOrder — "ASC", "DESC", or null
 * @returns {string|null}
 */
function getNewSortOrder(currentOrder) {
  if (currentOrder === 'ASC') return 'DESC';
  if (currentOrder === 'DESC') return null;
  return 'ASC';
}

/**
 * Sort icon name — matches MT's sortIconName
 * Source: IndexTable.tsx lines 155-166
 * @param {string|null} order
 * @returns {string}
 */
function getSortIconName(order) {
  if (order === 'ASC') return 'arrow_upward';
  if (order === 'DESC') return 'arrow_downward';
  return 'swap_vert';
}

/**
 * Handle sort click on a table header — MT IndexTable pattern
 * For HTMX tables: updates URL and triggers hx-get refresh
 * For client-side tables: dispatches event for sortTable() to consume
 *
 * Source: IndexTable.tsx lines 1595-1640
 *
 * @param {HTMLElement} headerEl — the clicked header element
 * @param {string} column — column key
 */
function handleSortClick(headerEl, column) {
  var currentOrder = headerEl.getAttribute('data-sort-order') || null;
  var newOrder = getNewSortOrder(currentOrder);

  // Clear all sort indicators
  var allHeaders = headerEl.closest('.mt-index-table, table')
    ?.querySelectorAll('[data-sort-column]') || [];
  allHeaders.forEach(function(h) {
    h.removeAttribute('data-sort-order');
    h.classList.remove('sorted-asc', 'sorted-desc');
  });

  // Set new sort
  if (newOrder) {
    headerEl.setAttribute('data-sort-order', newOrder);
    headerEl.classList.add('sorted-' + newOrder.toLowerCase());
  }

  // Dispatch for HTMX or client-side handler
  document.dispatchEvent(new CustomEvent('mt:sortChanged', {
    detail: { column: column, order: newOrder }
  }));
}


/* ============================================================
   5. JSON ACCORDION VIEW
   Source: WebhookDeliveryAttemptsView.tsx lines 44-73
   Source: JsonView.tsx (ReactJson wrapper)
   ============================================================ */

/**
 * Toggle JSON accordion section
 * Source: WebhookDeliveryAttemptsView.tsx lines 48-64
 * MT uses JsonView (ReactJson) for collapsible JSON trees.
 * In HTMX, we use <details> with pre-formatted JSON.
 *
 * @param {HTMLElement} el — the accordion header element
 */
function toggleJsonAccordion(el) {
  var panel = el.nextElementSibling;
  if (!panel) return;
  var isOpen = panel.style.display !== 'none';
  panel.style.display = isOpen ? 'none' : 'block';
  el.setAttribute('aria-expanded', !isOpen);
  el.classList.toggle('open', !isOpen);
}


/* ============================================================
   6. UTILITIES
   ============================================================ */

/**
 * Toggle JSON collapse in a json_view container.
 * Used by json_view.html's Collapse All / Expand All button.
 */
function toggleJsonCollapse(containerId) {
  var container = document.getElementById(containerId);
  if (!container) return;
  var pre = container.querySelector('.mt-json-pre');
  var btn = container.querySelector('.mt-json-toggle-label');
  if (!pre || !btn) return;
  var isCollapsed = pre.getAttribute('data-collapsed') === 'true';
  if (isCollapsed) {
    pre.removeAttribute('data-collapsed');
    pre.style.maxHeight = '';
    btn.textContent = 'Collapse All';
  } else {
    pre.setAttribute('data-collapsed', 'true');
    pre.style.maxHeight = '6em';
    pre.style.overflow = 'hidden';
    btn.textContent = 'Expand All';
  }
}

/* ============================================================
   7. TABLE FILTERING (client-side)
   Used by event_list.html and webhook_list.html filter bars.
   ============================================================ */

/**
 * Filter table rows by text search across all cells.
 * @param {string} query — search text
 * @param {string} [tableId='runs-table'] — table element ID
 */
function filterRunRows(query, tableId) {
  var table = document.getElementById(tableId || 'runs-table');
  if (!table) return;
  var rows = table.querySelectorAll('.mt-index-table-row[role="row"]');
  var q = (query || '').toLowerCase();
  rows.forEach(function(row) {
    if (!q) { row.style.display = ''; return; }
    var text = row.textContent.toLowerCase();
    row.style.display = text.indexOf(q) !== -1 ? '' : 'none';
  });
}

/**
 * Filter webhook/event table rows by a specific attribute value.
 * @param {string} value — filter value (empty string clears the filter)
 * @param {string} [attr='data-run-id'] — attribute to match on
 * @param {string} [tableId] — table element ID
 */
function filterByRun(value, attr, tableId) {
  var table = document.getElementById(tableId || 'runs-table');
  if (!table) return;
  var rows = table.querySelectorAll('.mt-index-table-row[role="row"]');
  rows.forEach(function(row) {
    if (!value) { row.style.display = ''; return; }
    var rowVal = row.getAttribute(attr || 'data-run-id') || '';
    row.style.display = rowVal === value ? '' : 'none';
  });
}


function escapeHtml(str) {
  if (!str) return '';
  var div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
