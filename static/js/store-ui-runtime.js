/* NE LOCALS — Store UI runtime.
 * Presentation-only helpers for resilient Store tables and date/time display.
 * Does not change routes, forms, prices, stock, payments, settlements or order state.
 */
(function () {
  'use strict';

  var WRAPPER_SELECTOR =
    'body[data-portal="store"] .nf-store-page [class*="table-wrap"], ' +
    'body[data-portal="store"] .nf-store-page [class*="table-scroll"], ' +
    'body[data-portal="store"] .nf-store-page .table-responsive, ' +
    'body[data-portal="store"] .nf-store-page .store-table-scroll-shell';

  function ensureTableWrapper(table) {
    if (!table || !table.parentNode) return null;
    var existing = table.closest('[class*="table-wrap"],[class*="table-scroll"],.table-responsive,.store-table-scroll-shell');
    if (existing) return existing;

    var shell = document.createElement('div');
    shell.className = 'store-table-scroll-shell';
    table.parentNode.insertBefore(shell, table);
    shell.appendChild(table);
    return shell;
  }

  function normaliseHeader(text) {
    return String(text || '').replace(/\s+/g, ' ').trim().toLowerCase();
  }

  function semanticColumnClass(header) {
    var h = normaliseHeader(header);
    if (!h) return '';
    if (/^(sl\s*no|s\.?l\.?\s*no|#|no\.?|serial)/.test(h)) return 'store-col-serial';
    if (/(order\s*(#|id|no|number)|order$)/.test(h)) return 'store-col-order';
    if (/(product|bundle|item)/.test(h)) return 'store-col-product';
    if (/(customer|user|store|delivery partner|rider)/.test(h)) return 'store-col-person';
    if (/(phone|mobile|contact)/.test(h)) return 'store-col-phone';
    if (/(amount|total|price|earning|payout|fee|refund|gmv|value|payment due)/.test(h)) return 'store-col-money';
    if (/(status|state|health)/.test(h)) return 'store-col-status';
    if (/(payment|method|mode)/.test(h)) return 'store-col-payment';
    if (/(deliver to|address|location|remark|reason|message|description|note|details|review)/.test(h)) return 'store-col-long';
    if (/(date|created|updated|time|reviewed|delivered at|placed at)/.test(h)) return 'store-col-date';
    if (/(action|manage|controls?)/.test(h)) return 'store-col-action';
    if (/(category|unit|stock|quantity|qty)/.test(h)) return 'store-col-medium';
    return '';
  }

  function classifyTableColumns(table) {
    if (!table) return;
    var headers = Array.prototype.slice.call(table.querySelectorAll('thead th'));
    var count = headers.length;
    table.setAttribute('data-store-column-count', String(count));
    if (count >= 7) table.setAttribute('data-store-dense-table', 'true');
    else table.removeAttribute('data-store-dense-table');

    var rows = Array.prototype.slice.call(table.querySelectorAll('tbody tr'));
    rows.forEach(function (row) {
      Array.prototype.forEach.call(row.children, function (cell, index) {
        if (!cell || cell.tagName !== 'TD') return;
        cell.classList.remove(
          'store-cell-compact','store-cell-wrap','store-cell-wide',
          'store-col-serial','store-col-order','store-col-product','store-col-person',
          'store-col-phone','store-col-money','store-col-status','store-col-payment',
          'store-col-long','store-col-date','store-col-action','store-col-medium'
        );

        var headerText = headers[index] ? headers[index].textContent : (cell.getAttribute('data-label') || '');
        var semantic = semanticColumnClass(headerText);
        if (semantic) cell.classList.add(semantic);

        var text = (cell.textContent || '').replace(/\s+/g, ' ').trim();
        var hasComplexBlocks = !!cell.querySelector('form,textarea,ul,ol,details,[class*="actions"],[class*="flow"],[class*="detail"],[class*="description"]');
        var lineBreaks = (cell.innerText || '').split(/\n+/).filter(Boolean).length;

        if (semantic === 'store-col-long' || hasComplexBlocks || text.length > 82 || lineBreaks >= 4) {
          cell.classList.add('store-cell-wide');
        } else if (text.length > 34 || lineBreaks >= 2) {
          cell.classList.add('store-cell-wrap');
        } else {
          cell.classList.add('store-cell-compact');
        }
      });
    });
  }

  function prepareTables(root) {
    root = root || document;
    var tables = root.querySelectorAll('body[data-portal="store"] .nf-store-page table');

    Array.prototype.forEach.call(tables, function (table) {
      var wrap = ensureTableWrapper(table);
      if (!wrap) return;

      wrap.dataset.storeSwipeReady = '1';
      wrap.setAttribute('data-store-swipe-table', 'true');
      if (!wrap.hasAttribute('tabindex')) wrap.setAttribute('tabindex', '0');
      if (!wrap.hasAttribute('role')) wrap.setAttribute('role', 'region');
      if (!wrap.hasAttribute('aria-label')) {
        wrap.setAttribute('aria-label', 'Scrollable data table. Drag with the mouse or swipe left and right to view more columns.');
      }
      classifyTableColumns(table);
    });
  }

  function parseDateTime(value) {
    if (!value) return null;
    var text = String(value).trim();
    var match = text.match(/^(.*?)(\d{4})-(\d{2})-(\d{2})[T\s]+(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/i);
    if (match) {
      return { prefix: match[1] || '', date: match[4] + '/' + match[3] + '/' + match[2], time: match[5].padStart(2, '0') + ':' + match[6] };
    }
    match = text.match(/^(.*?)(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*([AP]M)$/i);
    if (match) {
      var hour = parseInt(match[5], 10);
      var pm = match[7].toUpperCase() === 'PM';
      if (pm && hour < 12) hour += 12;
      if (!pm && hour === 12) hour = 0;
      return { prefix: match[1] || '', date: match[2].padStart(2, '0') + ' ' + match[3] + ' ' + match[4], time: String(hour).padStart(2, '0') + ':' + match[6] };
    }
    return null;
  }

  function formatLeafTextNode(node) {
    if (!node || node.nodeType !== 3 || !node.parentElement) return;
    var parent = node.parentElement;
    if (parent.closest('script,style,textarea,input,select,option,.store-date-time')) return;
    var raw = node.nodeValue || '';
    var trimmed = raw.trim();
    if (!trimmed) return;
    var parsed = parseDateTime(trimmed);
    if (!parsed) return;

    var fragment = document.createDocumentFragment();
    if (parsed.prefix) fragment.appendChild(document.createTextNode(parsed.prefix));
    var span = document.createElement('span');
    span.className = 'store-date-time';
    span.setAttribute('data-store-date-formatted', '1');
    var date = document.createElement('span');
    date.className = 'store-date-time-date';
    date.textContent = parsed.date;
    var time = document.createElement('span');
    time.className = 'store-date-time-time';
    time.textContent = parsed.time;
    span.appendChild(date);
    span.appendChild(time);
    fragment.appendChild(span);
    node.parentNode.replaceChild(fragment, node);
  }

  function formatTableDates(root) {
    root = root || document;
    var cells = root.querySelectorAll('body[data-portal="store"] .nf-store-page table td');
    Array.prototype.forEach.call(cells, function (cell) {
      if (cell.dataset.storeDateScanned === '1') return;
      cell.dataset.storeDateScanned = '1';
      var walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      var nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(formatLeafTextNode);
    });
  }

  /* Mouse/pen drag-to-scroll for every horizontally overflowing Store table.
   * - left-button horizontal drag scrolls from ordinary table content;
   * - native touch swipe remains unchanged;
   * - normal scrollbar remains unchanged;
   * - form controls/links keep their click behaviour;
   * - holding Shift/Ctrl/Alt/Meta bypasses drag so users can deliberately
   *   select/copy text without the table taking over the gesture.
   */
  function enablePointerDrag(root) {
    root = root || document;

    Array.prototype.forEach.call(root.querySelectorAll(WRAPPER_SELECTOR), function (wrap) {
      if (wrap.dataset.storePointerDragReady === '1') return;
      wrap.dataset.storePointerDragReady = '1';
      wrap.classList.add('store-table-drag-scroll');

      var state = null;
      var DRAG_THRESHOLD = 6;
      var suppressClick = false;

      function hasHorizontalOverflow() {
        return (wrap.scrollWidth - wrap.clientWidth) > 4;
      }

      function isProtectedControl(target) {
        return !!(target && target.closest && target.closest(
          'button,input,select,textarea,label,summary,[contenteditable="true"],a[href]'
        ));
      }

      function modifiersPressed(event) {
        return !!(event.shiftKey || event.altKey || event.ctrlKey || event.metaKey);
      }

      function clearSelection() {
        var selection = window.getSelection ? window.getSelection() : null;
        if (selection && typeof selection.removeAllRanges === 'function') selection.removeAllRanges();
      }

      function endDrag(pointerId) {
        if (!state) return;
        try {
          if (pointerId !== undefined && wrap.hasPointerCapture && wrap.hasPointerCapture(pointerId)) wrap.releasePointerCapture(pointerId);
        } catch (e) {}
        wrap.classList.remove('is-store-table-dragging');
        state = null;
      }

      wrap.addEventListener('pointerdown', function (event) {
        if ((event.pointerType !== 'mouse' && event.pointerType !== 'pen') || event.button !== 0) return;
        if (modifiersPressed(event) || isProtectedControl(event.target) || !hasHorizontalOverflow()) return;

        state = {
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          startScrollLeft: wrap.scrollLeft,
          dragging: false
        };
        try { wrap.setPointerCapture(event.pointerId); } catch (e) {}
      }, { passive: true });

      wrap.addEventListener('pointermove', function (event) {
        if (!state || event.pointerId !== state.pointerId) return;
        var dx = event.clientX - state.startX;
        var dy = event.clientY - state.startY;

        if (!state.dragging) {
          if (Math.abs(dx) < DRAG_THRESHOLD) return;
          if (Math.abs(dx) <= Math.abs(dy)) return;
          state.dragging = true;
          suppressClick = true;
          clearSelection();
          wrap.classList.add('is-store-table-dragging');
        }

        wrap.scrollLeft = state.startScrollLeft - dx;
        if (event.cancelable) event.preventDefault();
      }, { passive: false });

      wrap.addEventListener('pointerup', function (event) {
        if (!state || event.pointerId !== state.pointerId) return;
        var dragged = state.dragging;
        endDrag(event.pointerId);
        if (dragged) window.setTimeout(function () { suppressClick = false; }, 0);
      });
      wrap.addEventListener('pointercancel', function (event) { if (state && event.pointerId === state.pointerId) endDrag(event.pointerId); });
      wrap.addEventListener('lostpointercapture', function () { if (state) endDrag(state.pointerId); });

      wrap.addEventListener('click', function (event) {
        if (!suppressClick) return;
        event.preventDefault();
        event.stopPropagation();
        suppressClick = false;
      }, true);

      /* Backup for browsers that expose mouse events but not useful pointer capture. */
      wrap.addEventListener('mousedown', function (event) {
        if (window.PointerEvent || event.button !== 0 || modifiersPressed(event) || isProtectedControl(event.target) || !hasHorizontalOverflow()) return;
        var startX = event.clientX;
        var startY = event.clientY;
        var startScrollLeft = wrap.scrollLeft;
        var dragging = false;

        function move(moveEvent) {
          var dx = moveEvent.clientX - startX;
          var dy = moveEvent.clientY - startY;
          if (!dragging) {
            if (Math.abs(dx) < DRAG_THRESHOLD || Math.abs(dx) <= Math.abs(dy)) return;
            dragging = true;
            suppressClick = true;
            clearSelection();
            wrap.classList.add('is-store-table-dragging');
          }
          wrap.scrollLeft = startScrollLeft - dx;
          moveEvent.preventDefault();
        }
        function up() {
          document.removeEventListener('mousemove', move, true);
          document.removeEventListener('mouseup', up, true);
          wrap.classList.remove('is-store-table-dragging');
          if (dragging) window.setTimeout(function () { suppressClick = false; }, 0);
        }
        document.addEventListener('mousemove', move, true);
        document.addEventListener('mouseup', up, true);
      });
    });
  }

  function init(root) {
    prepareTables(root || document);
    formatTableDates(root || document);
    enablePointerDrag(document);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { init(document); }, { once: true });
  } else {
    init(document);
  }

  window.addEventListener('nefresh:page-after-enter', function () {
    window.requestAnimationFrame(function () { init(document); });
  });
})();
