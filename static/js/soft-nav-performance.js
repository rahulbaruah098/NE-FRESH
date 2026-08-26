/* NE LOCALS soft-navigation performance foundation.
   Shared by Admin + Store role shells.

   Goals:
   - Reuse very recent prefetched GET documents for faster role-panel navigation.
   - Keep cache short-lived so operational pages do not become stale.
   - Never cache POST/action responses.
   - Provide a page lifecycle registry so document/window listeners and timers
     created by soft-navigated page scripts can be cleaned before the next page.
   - Keep normal navigation semantics and hard-navigation fallback intact.
*/
(function () {
  'use strict';

  if (window.NEFreshSoftNavPerformance) return;

  var CACHE_TTL_MS = 7000;
  var CACHE_MAX_ENTRIES = 8;
  var PREFETCH_DELAY_MS = 90;

  var documentCache = new Map();
  var pendingLoads = new Map();
  var scheduledPrefetchTimer = null;
  var scheduledPrefetchKey = '';
  var cacheGeneration = 0;

  var lifecycle = {
    windowListeners: [],
    documentListeners: [],
    intervals: new Set(),
    timeouts: new Set(),
    animationFrames: new Set()
  };

  var nativeFetch = window.fetch.bind(window);
  var nativeWindowAdd = window.addEventListener.bind(window);
  var nativeWindowRemove = window.removeEventListener.bind(window);
  var nativeDocumentAdd = document.addEventListener.bind(document);
  var nativeDocumentRemove = document.removeEventListener.bind(document);
  var nativeSetInterval = window.setInterval.bind(window);
  var nativeClearInterval = window.clearInterval.bind(window);
  var nativeSetTimeout = window.setTimeout.bind(window);
  var nativeClearTimeout = window.clearTimeout.bind(window);
  var nativeRequestAnimationFrame = window.requestAnimationFrame
    ? window.requestAnimationFrame.bind(window)
    : function (callback) { return nativeSetTimeout(callback, 16); };
  var nativeCancelAnimationFrame = window.cancelAnimationFrame
    ? window.cancelAnimationFrame.bind(window)
    : nativeClearTimeout;

  function now() {
    return Date.now ? Date.now() : new Date().getTime();
  }

  function normalizedHref(value) {
    try {
      var url = value instanceof URL ? new URL(value.href) : new URL(String(value || ''), window.location.href);
      url.hash = '';
      return url.href;
    } catch (_) {
      return '';
    }
  }

  function canPrefetch() {
    try {
      var connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
      if (!connection) return true;
      if (connection.saveData) return false;
      var type = String(connection.effectiveType || '').toLowerCase();
      return type !== 'slow-2g' && type !== '2g';
    } catch (_) {
      return true;
    }
  }

  function trimCache() {
    if (documentCache.size <= CACHE_MAX_ENTRIES) return;

    var rows = [];
    documentCache.forEach(function (entry, key) {
      rows.push({ key: key, fetchedAt: Number(entry && entry.fetchedAt || 0) });
    });

    rows.sort(function (a, b) { return a.fetchedAt - b.fetchedAt; });

    while (documentCache.size > CACHE_MAX_ENTRIES && rows.length) {
      documentCache.delete(rows.shift().key);
    }
  }

  function getCachedDocument(value) {
    var key = normalizedHref(value);
    if (!key) return null;

    var entry = documentCache.get(key);
    if (!entry) return null;

    if ((now() - Number(entry.fetchedAt || 0)) > CACHE_TTL_MS) {
      documentCache.delete(key);
      return null;
    }

    return entry;
  }

  function invalidate(value) {
    if (!value) {
      cacheGeneration += 1;
      documentCache.clear();
      pendingLoads.clear();
      return;
    }

    var key = normalizedHref(value);
    if (!key) return;
    documentCache.delete(key);
    pendingLoads.delete(key);
  }

  async function loadDocument(value, options) {
    var opts = options || {};
    var key = normalizedHref(value);

    if (!key) {
      return { ok: false, reason: 'invalid-url' };
    }

    if (!opts.forceFresh) {
      var cached = getCachedDocument(key);
      if (cached) {
        return Object.assign({ fromCache: true }, cached);
      }

      if (pendingLoads.has(key)) {
        return pendingLoads.get(key);
      }
    }

    var loadGeneration = cacheGeneration;

    var requestPromise = (async function () {
      try {
        var response = await nativeFetch(key, {
          method: 'GET',
          credentials: 'same-origin',
          headers: {
            'X-Requested-With': 'XMLHttpRequest',
            'Accept': 'text/html,application/xhtml+xml'
          },
          cache: 'default'
        });

        var contentType = response.headers.get('content-type') || '';

        if (!response.ok || contentType.indexOf('text/html') === -1) {
          return {
            ok: false,
            status: response.status,
            contentType: contentType,
            finalUrl: response.url || key
          };
        }

        var html = await response.text();
        var parsedDoc = new DOMParser().parseFromString(html, 'text/html');
        var entry = {
          ok: true,
          html: html,
          parsedDoc: parsedDoc,
          fetchedAt: now(),
          finalUrl: response.url || key,
          contentType: contentType,
          fromCache: false
        };

        if (loadGeneration === cacheGeneration) {
          documentCache.set(key, entry);
          trimCache();
        }
        return entry;
      } catch (error) {
        return {
          ok: false,
          error: error,
          finalUrl: key
        };
      } finally {
        pendingLoads.delete(key);
      }
    })();

    pendingLoads.set(key, requestPromise);
    return requestPromise;
  }

  function prefetch(value) {
    if (!canPrefetch()) return Promise.resolve(null);

    var key = normalizedHref(value);
    if (!key) return Promise.resolve(null);

    if (getCachedDocument(key)) return Promise.resolve(getCachedDocument(key));
    if (pendingLoads.has(key)) return pendingLoads.get(key);

    return loadDocument(key).catch(function () { return null; });
  }

  function cancelScheduledPrefetch() {
    if (scheduledPrefetchTimer) {
      nativeClearTimeout(scheduledPrefetchTimer);
      scheduledPrefetchTimer = null;
    }
    scheduledPrefetchKey = '';
  }

  function schedulePrefetch(value, immediate) {
    if (!canPrefetch()) return;

    var key = normalizedHref(value);
    if (!key || getCachedDocument(key) || pendingLoads.has(key)) return;

    if (immediate) {
      cancelScheduledPrefetch();
      prefetch(key);
      return;
    }

    if (scheduledPrefetchKey === key && scheduledPrefetchTimer) return;

    cancelScheduledPrefetch();
    scheduledPrefetchKey = key;
    scheduledPrefetchTimer = nativeSetTimeout(function () {
      scheduledPrefetchTimer = null;
      scheduledPrefetchKey = '';
      prefetch(key);
    }, PREFETCH_DELAY_MS);
  }

  function cleanupPageLifecycle() {
    try {
      window.dispatchEvent(new CustomEvent('nefresh:page-before-leave'));
    } catch (_) {}

    lifecycle.windowListeners.splice(0).forEach(function (row) {
      try { nativeWindowRemove(row.type, row.callback, row.options); } catch (_) {}
    });

    lifecycle.documentListeners.splice(0).forEach(function (row) {
      try { nativeDocumentRemove(row.type, row.callback, row.options); } catch (_) {}
    });

    lifecycle.intervals.forEach(function (id) {
      try { nativeClearInterval(id); } catch (_) {}
    });
    lifecycle.intervals.clear();

    lifecycle.timeouts.forEach(function (id) {
      try { nativeClearTimeout(id); } catch (_) {}
    });
    lifecycle.timeouts.clear();

    lifecycle.animationFrames.forEach(function (id) {
      try { nativeCancelAnimationFrame(id); } catch (_) {}
    });
    lifecycle.animationFrames.clear();
  }

  function runTracked(callback) {
    if (typeof callback !== 'function') return;

    var previousWindowAdd = window.addEventListener;
    var previousDocumentAdd = document.addEventListener;
    var previousSetInterval = window.setInterval;
    var previousSetTimeout = window.setTimeout;
    var previousRequestAnimationFrame = window.requestAnimationFrame;

    window.addEventListener = function (type, listener, options) {
      if (typeof listener === 'function' || (listener && typeof listener.handleEvent === 'function')) {
        lifecycle.windowListeners.push({ type: type, callback: listener, options: options });
      }
      return nativeWindowAdd(type, listener, options);
    };

    document.addEventListener = function (type, listener, options) {
      if (typeof listener === 'function' || (listener && typeof listener.handleEvent === 'function')) {
        lifecycle.documentListeners.push({ type: type, callback: listener, options: options });
      }
      return nativeDocumentAdd(type, listener, options);
    };

    window.setInterval = function (handler, delay) {
      var args = Array.prototype.slice.call(arguments, 2);
      var id = nativeSetInterval(function () {
        if (typeof handler === 'function') handler.apply(window, args);
        else try { Function(String(handler))(); } catch (_) {}
      }, delay);
      lifecycle.intervals.add(id);
      return id;
    };

    window.setTimeout = function (handler, delay) {
      var args = Array.prototype.slice.call(arguments, 2);
      var id = nativeSetTimeout(function () {
        lifecycle.timeouts.delete(id);
        if (typeof handler === 'function') handler.apply(window, args);
        else try { Function(String(handler))(); } catch (_) {}
      }, delay);
      lifecycle.timeouts.add(id);
      return id;
    };

    window.requestAnimationFrame = function (handler) {
      var id = nativeRequestAnimationFrame(function (timestamp) {
        lifecycle.animationFrames.delete(id);
        if (typeof handler === 'function') handler(timestamp);
      });
      lifecycle.animationFrames.add(id);
      return id;
    };

    try {
      return callback();
    } finally {
      window.addEventListener = previousWindowAdd;
      document.addEventListener = previousDocumentAdd;
      window.setInterval = previousSetInterval;
      window.setTimeout = previousSetTimeout;
      window.requestAnimationFrame = previousRequestAnimationFrame;
    }
  }

  function pageEntered(url) {
    try {
      window.dispatchEvent(new CustomEvent('nefresh:page-after-enter', {
        detail: { url: String(url || window.location.href) }
      }));
    } catch (_) {}
  }

  /*
    Any traditional state-changing form makes prefetched operational pages
    potentially stale. Clear the tiny in-memory cache before that submit leaves.
  */
  nativeDocumentAdd('submit', function (event) {
    var form = event && event.target;
    if (!form || String(form.tagName || '').toLowerCase() !== 'form') return;
    var method = String(form.getAttribute('method') || 'GET').trim().toUpperCase();
    if (method !== 'GET') invalidate();
  }, true);

  /*
    AJAX/fetch mutations are common in the role panels. Keep the navigation
    cache from serving a prefetched pre-action page after a successful write.
    Request semantics are not changed; this only clears the tiny GET cache.
  */
  window.fetch = function (input, init) {
    var method = 'GET';
    var requestUrl = '';

    try {
      if (init && init.method) method = String(init.method).toUpperCase();
      else if (typeof Request !== 'undefined' && input instanceof Request) method = String(input.method || 'GET').toUpperCase();

      requestUrl = typeof input === 'string'
        ? input
        : ((input && input.url) ? input.url : String(input || ''));
    } catch (_) {}

    return nativeFetch(input, init).then(function (response) {
      if (method !== 'GET' && method !== 'HEAD' && response && response.ok) {
        try {
          var url = new URL(requestUrl, window.location.href);
          if (url.origin === window.location.origin) invalidate();
        } catch (_) {}
      }

      return response;
    });
  };

  nativeWindowAdd('pageshow', function (event) {
    if (event && event.persisted) invalidate();
  });

  window.NEFreshSoftNavPerformance = {
    loadDocument: loadDocument,
    prefetch: prefetch,
    schedulePrefetch: schedulePrefetch,
    cancelScheduledPrefetch: cancelScheduledPrefetch,
    getCachedDocument: getCachedDocument,
    invalidate: invalidate,
    cleanupPageLifecycle: cleanupPageLifecycle,
    runTracked: runTracked,
    pageEntered: pageEntered,
    cacheTtlMs: CACHE_TTL_MS
  };
})();
