/**
 * MT Drawer & Row Interaction JS
 * ================================
 * Handles drawer open/close, row click modifiers, and drawer action buttons.
 *
 * Plan 10d: WCAG dialog semantics, inert on <main>, focus restore, HTMX teardown,
 * drawer title sync, beforeSwap ordering when <main> is swapped.
 *
 * CANONICAL SOURCES:
 *
 * IndexTable.tsx — platform/app/frontend/dashboard/components/ui-components/IndexTable/IndexTable.tsx
 *   Row click behavior (TableRow, line 388-453):
 *     - metaKey || ctrlKey -> window.open(path, "_blank")                    (line 408-410)
 *     - shiftKey -> window.open(path, "_blank")                              (line 411-415)
 *     - shouldRenderInDrawer -> handleTriggerClick (opens Radix dialog)      (line 444-445)
 *     - else -> onClick(path)                                                (line 446-452)
 *
 * DrawerActions.tsx — platform/app/frontend/dashboard/components/ui-components/Drawer/DrawerActions.tsx
 *   Close: toggleIsDrawerOpen()                                              (line 66-69)
 *   Open as page: handleLinkClick(path) -> router.push(path)                (line 86-89)
 *   Open in new tab: handleLinkClick(path, {shiftKey:true}) -> window.open  (line 103-108)
 *   Copy link: copy(window.location.origin + path)                           (line 123-126)
 *
 * RadixDrawer.tsx — platform/app/frontend/dashboard/components/ui-components/Drawer/RadixDrawer.tsx
 *   Open animation: duration 500ms (data-[state=open]:duration-500)          (line 31)
 *   Close animation: duration 300ms (data-[state=closed]:duration-300)       (line 31)
 *   Overlay: fade in/out                                                     (line 19)
 *   Panel: slide in/out from right                                           (line 39)
 */

(function () {
  'use strict';

  var _drawerUrl = null;
  var _isOpen = false;
  /** @type {Element | null} */
  var _previousFocus = null;

  var overlay = function () { return document.getElementById('drawer-overlay'); };
  var panel = function () { return document.getElementById('drawer-panel'); };
  var drawerTitleEl = function () { return document.getElementById('drawer-title'); };
  var mainEl = function () { return document.querySelector('main'); };
  var actionOpenPage = function () { return document.getElementById('drawer-action-open-page'); };
  var actionNewTab = function () { return document.getElementById('drawer-action-new-tab'); };
  var actionCopy = function () { return document.getElementById('drawer-action-copy'); };

  function clearMainInertAndAriaModal() {
    var m = mainEl();
    var p = panel();
    if (m) m.inert = false;
    if (p) p.removeAttribute('aria-modal');
  }

  function restorePreviousFocus() {
    if (_previousFocus && _previousFocus.isConnected) {
      try {
        _previousFocus.focus();
      } catch (e) { /* ignore */ }
    }
    _previousFocus = null;
  }

  /**
   * Copy first heading text from swapped fragment into shell #drawer-title (Plan 10d Preflight §2).
   */
  function syncDrawerWideFromSwap(contentEl, triggerEl) {
    var p = panel();
    if (!p) return;
    var wide =
      (triggerEl && triggerEl.getAttribute('data-drawer-wide') === 'true') ||
      !!(contentEl && contentEl.querySelector('[data-flow-config-drawer="1"]'));
    p.classList.toggle('drawer-panel--wide', wide);
  }

  function syncDrawerTitleFromContent(contentEl) {
    var title = drawerTitleEl();
    if (!title || !contentEl) return;
    var h = contentEl.querySelector('h1, h2');
    var text = h ? String(h.textContent).replace(/\s+/g, ' ').trim() : '';
    title.textContent = text || 'Details';
  }

  /**
   * Teardown listeners / in-flight work inside #drawer-content before HTMX replaces it (Plan 10d §2).
   */
  function teardownDrawerContent(container) {
    if (!container || container.id !== 'drawer-content') return;
    if (typeof window.destroyScenarioBuilder === 'function') {
      window.destroyScenarioBuilder(container);
    }
    container.scrollTop = 0;
  }

  /**
   * Move focus into the panel after open transition (~500ms) or on transitionend.
   */
  function focusPanelAfterOpen(p) {
    if (!p) return;
    var done = false;
    function finish() {
      if (done) return;
      done = true;
      try {
        p.focus();
      } catch (e) { /* ignore */ }
    }
    var onEnd = function (ev) {
      if (ev.target !== p || ev.propertyName !== 'transform') return;
      p.removeEventListener('transitionend', onEnd);
      finish();
    };
    p.addEventListener('transitionend', onEnd);
    window.setTimeout(function () {
      p.removeEventListener('transitionend', onEnd);
      finish();
    }, 520);
  }

  /**
   * Open the drawer.
   * Called after HTMX loads content into #drawer-content, or from row click (overlay first).
   */
  function openDrawer(event, row) {
    if (event) {
      event.stopPropagation();
    }

    var url = row ? row.getAttribute('data-url') : null;
    _drawerUrl = url;

    var o = overlay();
    var p = panel();
    var m = mainEl();
    if (!o || !p) return;

    var wasOpen = _isOpen;
    if (!wasOpen) {
      _previousFocus = document.activeElement;
      if (m) m.inert = true;
      p.setAttribute('aria-modal', 'true');
    }

    o.style.display = '';
    p.style.display = '';

    void p.offsetHeight;

    o.classList.add('drawer-open');
    o.classList.remove('drawer-closing');
    p.classList.add('drawer-open');
    p.classList.remove('drawer-closing');

    _isOpen = true;

    if (actionOpenPage()) actionOpenPage().style.display = url ? '' : 'none';
    if (actionNewTab()) actionNewTab().style.display = url ? '' : 'none';
    if (actionCopy()) actionCopy().style.display = (url && navigator.clipboard) ? '' : 'none';

    document.body.style.overflow = 'hidden';

    if (row && row.getAttribute('data-drawer-wide') === 'true') {
      p.classList.add('drawer-panel--wide');
    }

    if (!wasOpen) {
      focusPanelAfterOpen(p);
    }
  }

  /**
   * Close the drawer. Clears inert / aria-modal and restores focus immediately (Plan 10d Preflight §3).
   */
  function closeDrawer() {
    clearMainInertAndAriaModal();
    restorePreviousFocus();

    var o = overlay();
    var p = panel();
    if (!o || !p) return;

    if (!_isOpen) {
      p.classList.remove('drawer-panel--wide');
      document.body.style.overflow = '';
      _drawerUrl = null;
      return;
    }

    p.classList.remove('drawer-panel--wide');

    o.classList.remove('drawer-open');
    o.classList.add('drawer-closing');
    p.classList.remove('drawer-open');
    p.classList.add('drawer-closing');

    _isOpen = false;

    window.setTimeout(function () {
      if (!_isOpen) {
        o.style.display = 'none';
        p.style.display = 'none';
        o.classList.remove('drawer-closing');
        p.classList.remove('drawer-closing');
      }
    }, 300);

    document.body.style.overflow = '';
    _drawerUrl = null;
  }

  function handleRowClick(event, row) {
    var url = row.getAttribute('data-url');

    if (url && (event.metaKey || event.ctrlKey || event.shiftKey)) {
      event.preventDefault();
      event.stopPropagation();
      window.open(url, '_blank');
      return;
    }

    openDrawer(event, row);
  }

  function drawerOpenAsPage() {
    if (_drawerUrl) {
      window.location.href = _drawerUrl;
    }
  }

  function drawerOpenInNewTab() {
    if (_drawerUrl) {
      window.open(_drawerUrl, '_blank');
    }
  }

  function drawerCopyLink() {
    if (_drawerUrl && navigator.clipboard) {
      navigator.clipboard.writeText(window.location.origin + _drawerUrl);
      var btn = actionCopy();
      if (btn) {
        btn.title = 'Copied!';
        window.setTimeout(function () { btn.title = 'Copy link'; }, 2000);
      }
    }
  }

  function toggleKvExpand(btn) {
    var table = btn.closest('.key-value-table');
    if (!table) return;

    var isExpanded = table.classList.contains('kv-expanded');
    table.classList.toggle('kv-expanded');

    if (isExpanded) {
      var hiddenCount = table.querySelectorAll('.kv-expandable-hidden').length;
      btn.textContent = 'Show ' + hiddenCount + ' more';
    } else {
      btn.textContent = 'Show less';
    }
  }

  function setActiveTab(btn) {
    var tabRow = btn.closest('.mt-tabs__row');
    if (!tabRow) return;

    var allTabs = tabRow.querySelectorAll('.mt-tab');
    for (var i = 0; i < allTabs.length; i++) {
      allTabs[i].classList.remove('mt-tab--active');
    }

    var tab = btn.closest('.mt-tab');
    if (tab) {
      tab.classList.add('mt-tab--active');
    }
  }

  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && _isOpen) {
      closeDrawer();
    }
  });

  /**
   * HTMX: tear down drawer subtree before swap; clear inert before <main> swaps while drawer open.
   *
   * Case A — target is #drawer-content: run teardown so listeners and fetches do not leak.
   * Case B — target is <main> (e.g. id=content): close drawer first so `main.inert` is cleared
   *          before HTMX mutates the tree (Plan 10d Preflight §3).
   */
  document.body.addEventListener('htmx:beforeSwap', function (evt) {
    var t = evt.detail && evt.detail.target;
    if (t && t.id === 'drawer-content') {
      teardownDrawerContent(t);
    }
    if (_isOpen && t && t.tagName === 'MAIN') {
      closeDrawer();
    }
  });

  document.addEventListener('htmx:afterSwap', function (event) {
    if (!event.detail || !event.detail.target || event.detail.target.id !== 'drawer-content') {
      return;
    }
    var content = event.detail.target;
    syncDrawerTitleFromContent(content);
    var triggerEl = event.detail.elt;
    syncDrawerWideFromSwap(content, triggerEl);
    if (!_isOpen) {
      openDrawer(null, triggerEl);
    }
  });

  window.openDrawer = openDrawer;
  window.closeDrawer = closeDrawer;
  window.handleRowClick = handleRowClick;
  window.drawerOpenAsPage = drawerOpenAsPage;
  window.drawerOpenInNewTab = drawerOpenInNewTab;
  window.drawerCopyLink = drawerCopyLink;
  window.toggleKvExpand = toggleKvExpand;
  window.setActiveTab = setActiveTab;
})();
