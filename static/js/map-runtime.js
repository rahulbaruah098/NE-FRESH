/* NE LOCALS shared map dependency runtime.
   Loads Leaflet on demand with multiple trusted CDN fallbacks so a single CDN
   failure does not leave Admin/Store map pages blank. */
(function(window, document){
  'use strict';
  if (window.NEFreshMapRuntime) return;

  var inflight = null;
  var trackedMaps = [];
  var CSS_ID = 'nfLeafletRuntimeCss';
  var JS_ID = 'nfLeafletRuntimeJs';
  var SOURCES = [
    {
      css: 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css',
      js: 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'
    },
    {
      css: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.css',
      js: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.js'
    },
    {
      css: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
      js: 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js'
    }
  ];

  function removeById(id){
    var node = document.getElementById(id);
    if (node && node.parentNode) node.parentNode.removeChild(node);
  }

  function loadCss(url, timeoutMs){
    return new Promise(function(resolve){
      removeById(CSS_ID);
      var link = document.createElement('link');
      link.id = CSS_ID;
      link.rel = 'stylesheet';
      link.href = url;
      link.setAttribute('data-nf-map-runtime', 'true');
      var settled = false;
      function finish(ok){ if (settled) return; settled = true; resolve(!!ok); }
      link.onload = function(){ finish(true); };
      link.onerror = function(){ finish(false); };
      document.head.appendChild(link);
      window.setTimeout(function(){
        try { finish(!!link.sheet); } catch (err) { finish(false); }
      }, Number(timeoutMs || 3500));
    });
  }

  function loadJs(url, timeoutMs){
    return new Promise(function(resolve){
      if (window.L && typeof window.L.map === 'function') return resolve(true);
      removeById(JS_ID);
      var script = document.createElement('script');
      script.id = JS_ID;
      script.src = url;
      script.async = true;
      script.setAttribute('data-nf-map-runtime', 'true');
      var settled = false;
      function finish(ok){ if (settled) return; settled = true; resolve(!!ok); }
      script.onload = function(){ finish(!!(window.L && typeof window.L.map === 'function')); };
      script.onerror = function(){ finish(false); };
      document.head.appendChild(script);
      window.setTimeout(function(){
        finish(!!(window.L && typeof window.L.map === 'function'));
      }, Number(timeoutMs || 4500));
    });
  }

  async function ensureLeaflet(){
    if (window.L && typeof window.L.map === 'function') return window.L;
    if (inflight) return inflight;

    inflight = (async function(){
      for (var i = 0; i < SOURCES.length; i += 1) {
        var source = SOURCES[i];
        var result = await Promise.all([
          loadCss(source.css, 3500),
          loadJs(source.js, 4500)
        ]);
        if (result[0] && result[1] && window.L && typeof window.L.map === 'function') {
          return window.L;
        }
      }
      throw new Error('Leaflet map library could not be loaded from the configured sources.');
    })();

    try {
      return await inflight;
    } finally {
      inflight = null;
    }
  }

  function invalidate(map, delay){
    if (!map || typeof map.invalidateSize !== 'function') return;
    window.setTimeout(function(){
      try { map.invalidateSize({ pan:false, animate:false }); } catch (err) {}
    }, Number(delay || 80));
  }

  function track(map){
    if (!map || trackedMaps.indexOf(map) !== -1) return map;
    trackedMaps.push(map);
    return map;
  }

  function cleanupAll(){
    var maps = trackedMaps.slice();
    trackedMaps = [];
    maps.forEach(function(map){
      try { if (map && typeof map.remove === 'function') map.remove(); } catch (err) {}
    });
  }

  window.NEFreshMapRuntime = {
    ensureLeaflet: ensureLeaflet,
    invalidate: invalidate,
    track: track,
    cleanupAll: cleanupAll,
    isReady: function(){ return !!(window.L && typeof window.L.map === 'function'); }
  };
})(window, document);
