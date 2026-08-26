/* NE LOCALS soft-navigation stylesheet synchronizer.
 *
 * Purpose:
 * - Prevent flash of unstyled content during soft navigation.
 * - Keep page styles cached after first load WITHOUT letting styles from a
 *   previous page remain active on the next page.
 * - Activate exactly the stylesheet set declared by the fetched page before
 *   swapping visible content.
 */
(function(window, document){
  'use strict';
  if (window.NEFreshStyleRuntime) return;

  var START_ID = 'nfPageStylesStart';
  var END_ID = 'nfPageStylesEnd';
  var CACHE_ATTR = 'data-nf-softnav-style-cache';
  var PAGE_STYLE_ATTR = 'data-nf-page-style';
  var STAGE_ATTR = 'data-nf-style-stage';
  var ORIGINAL_MEDIA_ATTR = 'data-nf-original-media';
  var LOAD_TIMEOUT_MS = 8000;

  function nodesBetween(root, startId, endId){
    var start = root.getElementById ? root.getElementById(startId) : null;
    var end = root.getElementById ? root.getElementById(endId) : null;
    var out = [];
    if (!start || !end) return out;
    var node = start.nextSibling;
    while (node && node !== end) {
      if (node.nodeType === 1 && (node.matches('link[rel="stylesheet"]') || node.matches('style'))) out.push(node);
      node = node.nextSibling;
    }
    return out;
  }

  function absoluteHref(node, baseHref){
    var href = node && node.getAttribute ? (node.getAttribute('href') || '') : '';
    if (!href) return '';
    try { return new URL(href, baseHref || window.location.href).href; } catch (err) { return href; }
  }

  function inlineIdentity(node){
    return 'inline:' + (node && node.textContent ? node.textContent : '');
  }

  function styleIdentity(node, baseHref){
    if (!node || !node.matches) return '';
    if (node.matches('link[rel="stylesheet"]')) return 'link:' + absoluteHref(node, baseHref);
    if (node.matches('style')) return inlineIdentity(node);
    return '';
  }

  function getOriginalMedia(node, sourceNode){
    if (!node) return 'all';
    var remembered = node.getAttribute && node.getAttribute(ORIGINAL_MEDIA_ATTR);
    if (remembered !== null && remembered !== '') return remembered;
    var sourceMedia = sourceNode && sourceNode.getAttribute ? sourceNode.getAttribute('media') : null;
    var currentMedia = node.getAttribute && node.getAttribute('media');
    var media = sourceMedia || (currentMedia && currentMedia !== 'not all' ? currentMedia : '') || 'all';
    if (node.setAttribute) node.setAttribute(ORIGINAL_MEDIA_ATTR, media);
    return media;
  }

  function markPageStyle(node, sourceNode){
    if (!node || !node.setAttribute) return;
    node.setAttribute(PAGE_STYLE_ATTR, 'true');
    node.setAttribute(CACHE_ATTR, 'true');
    getOriginalMedia(node, sourceNode);
  }

  function setStyleActive(node, active, sourceNode){
    if (!node || !node.matches) return;
    markPageStyle(node, sourceNode);
    if (active) {
      var media = getOriginalMedia(node, sourceNode);
      if (node.matches('link[rel="stylesheet"]')) node.disabled = false;
      node.setAttribute('media', media || 'all');
      node.removeAttribute('data-nf-page-style-inactive');
    } else {
      node.setAttribute('media', 'not all');
      node.setAttribute('data-nf-page-style-inactive', 'true');
    }
  }

  function allCurrentPageStyleNodes(){
    var seen = [];
    var out = [];
    function push(node){
      if (!node || seen.indexOf(node) !== -1) return;
      seen.push(node);
      out.push(node);
    }
    nodesBetween(document, START_ID, END_ID).forEach(push);
    Array.prototype.slice.call(document.querySelectorAll('[' + PAGE_STYLE_ATTR + '="true"],[' + CACHE_ATTR + '="true"]')).forEach(push);
    return out.filter(function(node){
      return node.matches && (node.matches('link[rel="stylesheet"]') || node.matches('style'));
    });
  }

  function stylesheetAlreadyPresent(href){
    if (!href) return null;
    var links = Array.prototype.slice.call(document.querySelectorAll('link[rel="stylesheet"][href]'));
    for (var i=0; i<links.length; i+=1) {
      if (absoluteHref(links[i], window.location.href) === href) return links[i];
    }
    return null;
  }

  function inlineStyleAlreadyPresent(cssText){
    var styles = allCurrentPageStyleNodes().filter(function(node){ return node.matches('style'); });
    for (var i=0; i<styles.length; i+=1) {
      if ((styles[i].textContent || '') === cssText) return styles[i];
    }
    return null;
  }

  function waitForAppliedStyles(){
    return new Promise(function(resolve){
      window.requestAnimationFrame(function(){
        window.requestAnimationFrame(function(){
          window.requestAnimationFrame(resolve);
        });
      });
    });
  }

  function beginPaintGuard(){
    document.documentElement.classList.add('nf-softnav-transition');
    var content = document.querySelector('.nf-content');
    if (content) content.classList.add('nf-softnav-style-commit');
  }

  function endPaintGuard(){
    var content = document.querySelector('.nf-content');
    if (content) content.classList.remove('nf-softnav-style-commit', 'nf-softnav-swapping');
    document.documentElement.classList.remove('nf-softnav-transition');
  }

  function stageStylesheet(sourceNode, baseHref){
    return new Promise(function(resolve, reject){
      var targetHref = absoluteHref(sourceNode, baseHref);
      var existing = stylesheetAlreadyPresent(targetHref);
      if (existing) {
        markPageStyle(existing, sourceNode);
        resolve({ node: existing, existing: true, source: sourceNode, identity: 'link:' + targetHref });
        return;
      }

      var clone = document.createElement('link');
      Array.prototype.slice.call(sourceNode.attributes || []).forEach(function(attr){
        if (attr.name.toLowerCase() === 'media' || attr.name.toLowerCase() === 'href') return;
        clone.setAttribute(attr.name, attr.value);
      });
      clone.rel = 'stylesheet';
      clone.href = targetHref;
      clone.media = 'not all';
      clone.setAttribute(STAGE_ATTR, 'true');
      markPageStyle(clone, sourceNode);

      var settled = false;
      var timer = null;
      function finish(ok){
        if (settled) return;
        settled = true;
        if (timer) window.clearTimeout(timer);
        if (ok) resolve({ node: clone, existing: false, source: sourceNode, identity: 'link:' + targetHref });
        else {
          if (clone.parentNode) clone.parentNode.removeChild(clone);
          reject(new Error('Stylesheet failed to load: ' + (clone.href || 'unknown')));
        }
      }
      clone.onload = function(){ finish(true); };
      clone.onerror = function(){ finish(false); };

      var end = document.getElementById(END_ID);
      if (end && end.parentNode) end.parentNode.insertBefore(clone, end);
      else document.head.appendChild(clone);

      timer = window.setTimeout(function(){
        try { finish(!!clone.sheet); } catch (err) { finish(false); }
      }, LOAD_TIMEOUT_MS);
    });
  }

  async function sync(parsedDoc, finalUrl){
    if (!parsedDoc || !parsedDoc.head) return true;
    var currentStart = document.getElementById(START_ID);
    var currentEnd = document.getElementById(END_ID);
    var parsedStart = parsedDoc.getElementById(START_ID);
    var parsedEnd = parsedDoc.getElementById(END_ID);
    if (!currentStart || !currentEnd || !parsedStart || !parsedEnd) return true;

    var baseHref = finalUrl || window.location.href;
    var targetNodes = nodesBetween(parsedDoc, START_ID, END_ID);
    var staged = [];

    try {
      for (var i=0; i<targetNodes.length; i+=1) {
        var target = targetNodes[i];
        if (target.matches('link[rel="stylesheet"]')) {
          staged.push(await stageStylesheet(target, baseHref));
        } else {
          var cssText = target.textContent || '';
          var existingStyle = inlineStyleAlreadyPresent(cssText);
          if (existingStyle) {
            markPageStyle(existingStyle, target);
            staged.push({ node: existingStyle, existing: true, source: target, inline: true, identity: inlineIdentity(target) });
          } else {
            var style = document.createElement('style');
            Array.prototype.slice.call(target.attributes || []).forEach(function(attr){
              if (attr.name.toLowerCase() !== 'media') style.setAttribute(attr.name, attr.value);
            });
            style.textContent = cssText;
            style.media = 'not all';
            markPageStyle(style, target);
            staged.push({ node: style, existing: false, source: target, inline: true, identity: inlineIdentity(target) });
          }
        }
      }

      beginPaintGuard();

      staged.forEach(function(entry){
        var node = entry.node;
        if (entry.inline && !node.parentNode) currentEnd.parentNode.insertBefore(node, currentEnd);
      });

      var targetIdentities = {};
      staged.forEach(function(entry){ targetIdentities[entry.identity] = true; });

      /* Critical isolation step: cached CSS may stay downloaded, but only styles
         declared by the destination page are allowed to remain ACTIVE. */
      allCurrentPageStyleNodes().forEach(function(node){
        var identity = styleIdentity(node, window.location.href);
        setStyleActive(node, !!targetIdentities[identity], null);
      });

      staged.forEach(function(entry){
        setStyleActive(entry.node, true, entry.source);
        entry.node.removeAttribute(STAGE_ATTR);
        if (entry.node.matches && entry.node.matches('link[rel="stylesheet"]')) {
          entry.node.onload = null;
          entry.node.onerror = null;
        }
      });

      await waitForAppliedStyles();
      return true;
    } catch (err) {
      staged.forEach(function(entry){
        var node = entry && entry.node;
        if (node && !entry.existing && node.getAttribute && node.getAttribute(STAGE_ATTR) === 'true' && node.parentNode) {
          node.parentNode.removeChild(node);
        }
      });
      endPaintGuard();
      throw err;
    }
  }

  window.NEFreshStyleRuntime = {
    sync: sync,
    beginPaintGuard: beginPaintGuard,
    endPaintGuard: endPaintGuard,
    waitForAppliedStyles: waitForAppliedStyles
  };
})(window, document);
