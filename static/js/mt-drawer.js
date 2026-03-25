/**
 * MT Drawer & Row Interaction JS
 * ================================
 * Handles drawer open/close, row click modifiers, and drawer action buttons.
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

  // Current drawer state
  var _drawerUrl = null;
  var _isOpen = false;

  var overlay = function () { return document.getElementById('drawer-overlay'); };
  var panel = function () { return document.getElementById('drawer-panel'); };
  var actionOpenPage = function () { return document.getElementById('drawer-action-open-page'); };
  var actionNewTab = function () { return document.getElementById('drawer-action-new-tab'); };
  var actionCopy = function () { return document.getElementById('drawer-action-copy'); };

  /**
   * Open the drawer.
   * Called after HTMX loads content into #drawer-content.
   *
   * Matches RadixDrawer.tsx open animation:
   *   - Overlay: fade-in (opacity 0 -> 1)
   *   - Panel: slide-in-from-right (translateX(100%) -> 0)
   *   - Duration: 500ms (data-[state=open]:duration-500)
   */
  function openDrawer(event, row) {
    if (event) {
      // Prevent double-open from HTMX + onclick
      event.stopPropagation();
    }

    var url = row ? row.getAttribute('data-url') : null;
    _drawerUrl = url;

    var o = overlay();
    var p = panel();
    if (!o || !p) return;

    // Show elements
    o.style.display = '';
    p.style.display = '';

    // Trigger reflow before adding class (for CSS transition)
    void p.offsetHeight;

    // Add open class — triggers CSS transition
    o.classList.add('drawer-open');
    o.classList.remove('drawer-closing');
    p.classList.add('drawer-open');
    p.classList.remove('drawer-closing');

    _isOpen = true;

    // Show/hide action buttons based on whether we have a URL
    // Matches DrawerActions.tsx: open-as-page, new-tab, copy only shown when path exists
    if (actionOpenPage()) actionOpenPage().style.display = url ? '' : 'none';
    if (actionNewTab()) actionNewTab().style.display = url ? '' : 'none';
    if (actionCopy()) actionCopy().style.display = (url && navigator.clipboard) ? '' : 'none';

    // Prevent body scroll while drawer is open
    document.body.style.overflow = 'hidden';
  }

  /**
   * Close the drawer.
   * Matches RadixDrawer.tsx close animation:
   *   - Overlay: fade-out (opacity 1 -> 0)
   *   - Panel: slide-out-to-right (translateX(0) -> 100%)
   *   - Duration: 300ms (data-[state=closed]:duration-300)
   */
  function closeDrawer() {
    var o = overlay();
    var p = panel();
    if (!o || !p) return;

    // Trigger close animation
    o.classList.remove('drawer-open');
    o.classList.add('drawer-closing');
    p.classList.remove('drawer-open');
    p.classList.add('drawer-closing');

    _isOpen = false;

    // After animation completes (300ms), hide elements
    setTimeout(function () {
      if (!_isOpen) {
        o.style.display = 'none';
        p.style.display = 'none';
        o.classList.remove('drawer-closing');
        p.classList.remove('drawer-closing');
      }
    }, 300);

    // Restore body scroll
    document.body.style.overflow = '';
    _drawerUrl = null;
  }

  /**
   * Handle row click with modifier key support.
   * Matches IndexTable.tsx TableRow click handler (line 388-453):
   *   - metaKey || ctrlKey || shiftKey -> window.open(url, "_blank")
   *   - Normal click -> let HTMX handle drawer open
   */
  function handleRowClick(event, row) {
    var url = row.getAttribute('data-url');

    // Cmd/Ctrl+click or Shift+click -> new tab (only if a page URL exists)
    if (url && (event.metaKey || event.ctrlKey || event.shiftKey)) {
      event.preventDefault();
      event.stopPropagation();
      window.open(url, '_blank');
      return;
    }

    // Normal click -> HTMX fires hx-get, then we open the drawer
    openDrawer(event, row);
  }

  /**
   * DrawerActions: Open as page
   * Matches DrawerActions.tsx line 86-89: handleLinkClick(path) -> router.push(path)
   * In HTMX/vanilla: navigate in same tab
   */
  function drawerOpenAsPage() {
    if (_drawerUrl) {
      window.location.href = _drawerUrl;
    }
  }

  /**
   * DrawerActions: Open in new tab
   * Matches DrawerActions.tsx line 103-108:
   *   handleLinkClick(path, {shiftKey: true}) -> window.open(origin + path, "_blank")
   */
  function drawerOpenInNewTab() {
    if (_drawerUrl) {
      window.open(_drawerUrl, '_blank');
    }
  }

  /**
   * DrawerActions: Copy link
   * Matches DrawerActions.tsx line 123-126:
   *   copy(window.location.origin + pathWithQueryParams)
   * After copy, could swap icon from "link" to "done" (not implemented in SVG version)
   */
  function drawerCopyLink() {
    if (_drawerUrl && navigator.clipboard) {
      navigator.clipboard.writeText(window.location.origin + _drawerUrl);
      // Optional: show feedback (MT uses isCopied state to swap icon to "done")
      var btn = actionCopy();
      if (btn) {
        btn.title = 'Copied!';
        setTimeout(function () { btn.title = 'Copy link'; }, 2000);
      }
    }
  }

  /**
   * KV Table expand/collapse toggle.
   * Matches KeyValueTable.tsx expandable logic (line 198-234):
   *   Shows first N rows, "Show X more" / "Show less" toggle
   */
  function toggleKvExpand(btn) {
    var table = btn.closest('.key-value-table');
    if (!table) return;

    var isExpanded = table.classList.contains('kv-expanded');
    table.classList.toggle('kv-expanded');

    if (isExpanded) {
      // Collapsing: "Show X more"
      var hiddenCount = table.querySelectorAll('.kv-expandable-hidden').length;
      btn.textContent = 'Show ' + hiddenCount + ' more';
    } else {
      // Expanding: "Show less"
      btn.textContent = 'Show less';
    }
  }

  /**
   * Tabs: set active tab (visual only).
   * Matches Tabs.tsx line 172: onClick fires tab change, updates selected state.
   * In HTMX, the content swap is handled by hx-get; this just updates the visual state.
   */
  function setActiveTab(btn) {
    var tabRow = btn.closest('.mt-tabs__row');
    if (!tabRow) return;

    // Remove active from all tabs
    var allTabs = tabRow.querySelectorAll('.mt-tab');
    for (var i = 0; i < allTabs.length; i++) {
      allTabs[i].classList.remove('mt-tab--active');
    }

    // Add active to clicked tab's parent
    var tab = btn.closest('.mt-tab');
    if (tab) {
      tab.classList.add('mt-tab--active');
    }
  }

  /**
   * Close drawer on Escape key.
   * Radix Dialog handles this natively; we replicate it here.
   */
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape' && _isOpen) {
      closeDrawer();
    }
  });

  /**
   * HTMX integration: auto-open drawer after content loads.
   * When HTMX swaps content into #drawer-content, open the drawer.
   */
  document.addEventListener('htmx:afterSwap', function (event) {
    if (event.detail.target && event.detail.target.id === 'drawer-content') {
      // Drawer content just loaded — if not already open, open it
      if (!_isOpen) {
        var triggerEl = event.detail.elt;
        openDrawer(null, triggerEl);
      }
    }
  });

  // Expose functions globally
  window.openDrawer = openDrawer;
  window.closeDrawer = closeDrawer;
  window.handleRowClick = handleRowClick;
  window.drawerOpenAsPage = drawerOpenAsPage;
  window.drawerOpenInNewTab = drawerOpenInNewTab;
  window.drawerCopyLink = drawerCopyLink;
  window.toggleKvExpand = toggleKvExpand;
  window.setActiveTab = setActiveTab;

})();
