/* NE FRESH view-state persistence
   Keeps page display position stable when users refresh, reload, go forward/back,
   or return from another internal page. It is intentionally UI-only and does not
   change backend routes, forms, submissions, filters, or business logic. */
(function () {
  'use strict';

  var STORAGE_PREFIX = 'nefresh:view-state:v2:';
  var RESTORE_CLASS = 'nf-restore-pending';
  var SAVE_THROTTLE_MS = 160;
  var MAX_AGE_MS = 1000 * 60 * 60 * 8;
  var saveTimer = null;
  var restoreReleased = false;

  function now() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function pageKey() {
    return STORAGE_PREFIX + window.location.pathname + window.location.search;
  }

  function safeSessionGet(key) {
    try { return window.sessionStorage ? sessionStorage.getItem(key) : null; }
    catch (_) { return null; }
  }

  function safeSessionSet(key, value) {
    try { if (window.sessionStorage) sessionStorage.setItem(key, value); }
    catch (_) {}
  }

  function parseState() {
    var raw = safeSessionGet(pageKey());
    if (!raw) return null;

    try {
      var state = JSON.parse(raw);
      if (!state || !state.t || (now() - state.t) > MAX_AGE_MS) return null;
      return state;
    } catch (_) {
      return null;
    }
  }

  function indexedKey(prefix, el, index) {
    if (el && el.id) return prefix + ':#' + el.id;
    return prefix + ':' + index;
  }

  function scrollContainers() {
    var selectors = [
      '.nf-sidebar-scroll',
      '.nf-content',
      '.nf-main',
      '.table-responsive',
      '.table-wrap',
      '.table-container',
      '.nf-table-wrap',
      '.admin-table-wrap',
      '.store-table-wrap',
      '.orders-table-wrap',
      '.order-table-wrap',
      '.scroll-table',
      '.scroll-area',
      '.scroll-box',
      '.overflow-auto',
      '[data-persist-scroll]',
      '[data-restore-scroll]'
    ];

    var seen = [];
    var out = [];

    selectors.forEach(function (selector) {
      document.querySelectorAll(selector).forEach(function (el) {
        if (!el || seen.indexOf(el) !== -1) return;
        seen.push(el);
        out.push(el);
      });
    });

    return out;
  }

  function openGroups() {
    var groups = [];
    document.querySelectorAll('details[open], .nf-dropdown.open, .dropdown.open').forEach(function (el, index) {
      groups.push(indexedKey('open', el, index));
    });
    return groups;
  }

  function collectState() {
    var containerState = {};

    scrollContainers().forEach(function (el, index) {
      containerState[indexedKey('scroll', el, index)] = {
        top: el.scrollTop || 0,
        left: el.scrollLeft || 0
      };
    });

    return {
      t: now(),
      x: window.scrollX || window.pageXOffset || 0,
      y: window.scrollY || window.pageYOffset || 0,
      hash: window.location.hash || '',
      containers: containerState,
      openGroups: openGroups()
    };
  }

  function saveStateNow() {
    safeSessionSet(pageKey(), JSON.stringify(collectState()));
  }

  function scheduleSave() {
    if (saveTimer) return;
    saveTimer = window.setTimeout(function () {
      saveTimer = null;
      saveStateNow();
    }, SAVE_THROTTLE_MS);
  }

  function releaseRestoreHold() {
    if (restoreReleased) return;
    restoreReleased = true;
    document.documentElement.classList.remove(RESTORE_CLASS);
  }

  function restoreOpenGroups(state) {
    if (!state || !Array.isArray(state.openGroups)) return;

    var saved = state.openGroups;
    document.querySelectorAll('details, .nf-dropdown, .dropdown').forEach(function (el, index) {
      var key = indexedKey('open', el, index);
      if (saved.indexOf(key) === -1) return;

      if (String(el.tagName).toLowerCase() === 'details') {
        el.open = true;
      } else {
        el.classList.add('open');
      }
    });
  }

  function restoreContainers(state) {
    if (!state || !state.containers) return;

    scrollContainers().forEach(function (el, index) {
      var saved = state.containers[indexedKey('scroll', el, index)];
      if (!saved) return;

      if (typeof saved.top === 'number') el.scrollTop = saved.top;
      if (typeof saved.left === 'number') el.scrollLeft = saved.left;
    });
  }

  function restoreWindowPosition(state) {
    var y = Number(state && state.y || 0);
    var x = Number(state && state.x || 0);

    if (y > 0 || x > 0) {
      window.scrollTo(x, y);
    } else if (state && state.hash && document.querySelector(state.hash)) {
      try { document.querySelector(state.hash).scrollIntoView(); }
      catch (_) {}
    }
  }

  function restoreState() {
    restoreReleased = false;

    var state = parseState();
    if (!state) {
      releaseRestoreHold();
      return;
    }

    restoreOpenGroups(state);

    var tries = 0;
    var maxTries = 12;

    function applyRestore() {
      tries += 1;
      restoreContainers(state);
      restoreWindowPosition(state);

      /* Reveal after the first stable restore pass, then keep correcting quietly.
         This prevents the visible top-page flash without holding a blank page too long. */
      if (tries >= 2) {
        releaseRestoreHold();
      }

      if (tries < maxTries) {
        window.setTimeout(applyRestore, tries < 4 ? 40 : 120);
      } else {
        releaseRestoreHold();
      }
    }

    window.requestAnimationFrame(function () {
      applyRestore();
      window.setTimeout(releaseRestoreHold, 650);
    });
  }

  try {
    if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
  } catch (_) {}

  window.addEventListener('scroll', scheduleSave, { passive: true });
  document.addEventListener('scroll', scheduleSave, true);
  document.addEventListener('input', scheduleSave, true);
  document.addEventListener('change', scheduleSave, true);
  document.addEventListener('submit', saveStateNow, true);

  document.addEventListener('click', function (event) {
    var link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
    if (!link) return;
    try {
      var target = new URL(link.getAttribute('href'), window.location.href);
      if (target.origin === window.location.origin) saveStateNow();
    } catch (_) {}
  }, true);

  window.addEventListener('pagehide', saveStateNow);
  window.addEventListener('beforeunload', saveStateNow);
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'hidden') saveStateNow();
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', restoreState, { once: true });
  } else {
    restoreState();
  }

  window.addEventListener('pageshow', function () {
    restoreState();
  });
})();
