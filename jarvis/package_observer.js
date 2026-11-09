/* Shared package observer. Package-controlled DOM is evidence, never authority. */
(function () {
  'use strict';
  if (window.jarvisPackageObserver) return;
  var nonce = null, sequence = 0, locator = {}, timer = null, last = 0;
  var frozen = new Map(), observations = [], ids = new WeakMap(), nextId = 0;
  var MAX_BYTES = 65536, MAX_BLOCKS = 200, MAX_IMAGES = 64;
  function post(message) { parent.postMessage(JSON.parse(JSON.stringify(Object.assign({jarvis: 'package', protocol: 1}, message))), '*'); }
  function id(el) { if (el.id) return el.id.slice(0, 256); if (!ids.has(el)) ids.set(el, 'dom-' + (++nextId)); return ids.get(el); }
  function rect(el) { var r = el.getBoundingClientRect(); return {x: r.x, y: r.y, width: r.width, height: r.height}; }
  function visible(el) {
    if (!el || el.closest('[hidden],[inert],script,style,template,noscript,#jarvis-story-layer,[aria-hidden="true"]')) return false;
    var r = el.getBoundingClientRect();
    if (!r.width || !r.height || r.bottom <= 0 || r.right <= 0 || r.top >= innerHeight || r.left >= innerWidth) return false;
    for (var p = el; p && p.nodeType === 1; p = p.parentElement) {
      var s = getComputedStyle(p);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity) === 0) return false;
      if (p !== el && /(hidden|clip|scroll|auto)/.test(s.overflow + s.overflowX + s.overflowY)) {
        var a = p.getBoundingClientRect();
        if (r.bottom <= a.top || r.top >= a.bottom || r.right <= a.left || r.left >= a.right) return false;
      }
    }
    return true;
  }
  function where() {
    var tab = Array.from(document.querySelectorAll('[role="tab"][aria-selected="true"]')).find(visible);
    var active = document.querySelector('.slide.current,.slide[aria-current="true"]');
    return Object.assign({}, active ? {slideId: id(active)} : {}, locator, {openDetails: Array.from(document.querySelectorAll('details[open]')).filter(visible).map(id).slice(0, 200),
      fragment: location.hash.slice(0, 1024), ...(tab ? {tabId: id(tab)} : {})});
  }
  function changed() {
    if (!nonce) return;
    var delay = Math.max(150, 250 - (performance.now() - last));
    if (timer !== null) return;
    timer = setTimeout(function () { timer = null; last = performance.now(); sequence++;
      post({type: 'observe.changed', mountNonce: nonce, sequence: sequence, locator: where()}); }, delay);
  }
  function selection() {
    var active = document.activeElement;
    if (active && /^(INPUT|TEXTAREA)$/.test(active.tagName)) {
      if (active.type === 'password' || !visible(active)) return undefined;
      var start = active.selectionStart, end = active.selectionEnd;
      if (typeof start === 'number' && typeof end === 'number' && end > start)
        return {text: active.value.slice(start, Math.min(end, start + 4000)), anchorBlock: id(active), startOffset: start, endOffset: end};
      return undefined;
    }
    var s = getSelection();
    if (!s || s.isCollapsed || !visible(s.anchorNode && (s.anchorNode.nodeType === 1 ? s.anchorNode : s.anchorNode.parentElement))) return undefined;
    return {text: s.toString().slice(0, 4000), anchorBlock: id(s.anchorNode.parentElement), focusBlock: id(s.focusNode.parentElement), startOffset: s.anchorOffset, endOffset: s.focusOffset};
  }
  function collect() {
    var limited = false, collectedChars = 0;
    var blocks = [], images = [], limits = ['reported_dom_package_controlled', 'occlusion_canvas_closed_shadow_unverified', 'unvisited_dynamic_states'];
    var candidates = document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,li,td,th,pre,blockquote,figcaption,summary,button,[role="tab"],label,div,span,a,strong,em,b,i,small,code');
    candidates.forEach(function (el) {
      if (blocks.length >= 10000 || collectedChars >= 2000000) {limited = true; return;}
      if (!visible(el) || el.closest('input,textarea,select')) return;
      // Only direct text avoids repeated ancestor copies and hidden descendants.
      var text = Array.from(el.childNodes).filter(function (n) {return n.nodeType === 3;}).map(function (n) {return n.textContent;}).join(' ').trim();
      if (!text) return;
      if (text.length + collectedChars > 2000000) {text = text.slice(0, 2000000 - collectedChars); limited = true;}
      collectedChars += text.length;
      var role = /^H[1-6]$/.test(el.tagName) ? 'heading' : /^(TD|TH)$/.test(el.tagName) ? 'table' : el.tagName === 'LI' ? 'list' : /^(BUTTON|SUMMARY|LABEL)$/.test(el.tagName) ? 'control' : 'paragraph';
      for (var start = 0; start < text.length; start += 8000) blocks.push({id: id(el) + ':' + start, role: role, text: text.slice(start, start + 8000), rect: rect(el), sourceElementId: el.id || undefined});
    });
    document.querySelectorAll('img').forEach(function (el) { if (images.length >= 4096 || collectedChars + el.alt.length + (el.currentSrc || el.src).length > 2000000) {limited = true; return;} if (visible(el)) {collectedChars += el.alt.length + (el.currentSrc || el.src).length; images.push({source: (el.currentSrc || el.src).slice(0, 2048), alt: el.alt.slice(0, 4000), rect: rect(el)}); } });
    var resources = performance.getEntriesByType('resource');
    if (resources.some(function (r) { var url; try {url = new URL(r.name);} catch (_) {return false;} return url.pathname.indexOf('/api/packages/') === 0 && url.pathname.indexOf('/files/') > 0 && url.pathname.indexOf('/files/@snapshots/') < 0; })) limits.push('mixed_source_revision');
    if (resources.some(function (r) {try {return new URL(r.name).origin !== location.origin;} catch (_) {return false;}})) limits.push('external_resources_observed');
    var truncated = limited || blocks.length > 10000 || blocks.reduce(function (sum, b) {return sum + b.text.length;}, 0) > 2000000;
    if (truncated) {blocks = blocks.slice(0, 10000); var chars = 0; blocks = blocks.filter(function (b) {chars += b.text.length; return chars <= 2000000;}); limits.push('observation_budget');}
    return {truncated: truncated, sequence: sequence, observedAt: new Date().toISOString(), locator: where(),
      viewport: {width: innerWidth, height: innerHeight, scrollX: scrollX, scrollY: scrollY}, blocks: blocks, images: images, selection: selection(), limits: limits};
  }
  function answer(request) {
    var source, offset = 0, imageOffset = 0;
    if (request.continuation) {
      var cursor = frozen.get(request.continuation);
      if (!cursor || cursor.expires < Date.now()) return;
      source = cursor.source; offset = cursor.offset; imageOffset = cursor.imageOffset;
    } else {
      clearTimeout(timer); timer = null; sequence++; source = collect();
      observations.push(source);
      while (observations.length > 2) {var old = observations.shift(); frozen.forEach(function (value, key) {if (value.source === old) frozen.delete(key);});}
    }
    var response = Object.assign({}, source, {type: 'observe.response', mountNonce: nonce, requestId: request.requestId,
      jarvis: 'package', protocol: 1, blocks: [], images: [], complete: false, continuation: null});
    delete response.truncated;
    function fits() { return new TextEncoder().encode(JSON.stringify(response)).length < MAX_BYTES - 256; }
    while (offset < source.blocks.length && response.blocks.length < MAX_BLOCKS) {
      response.blocks.push(source.blocks[offset]);
      if (!fits()) {response.blocks.pop(); break;} offset++;
    }
    while (imageOffset < source.images.length && response.images.length < MAX_IMAGES) {
      response.images.push(source.images[imageOffset]);
      if (!fits()) {response.images.pop(); break;} imageOffset++;
    }
    var consumed = offset === source.blocks.length && imageOffset === source.images.length;
    response.complete = consumed && !source.truncated;
    if (!consumed) {
      var token = crypto.randomUUID();
      frozen.set(token, {source: source, offset: offset, imageOffset: imageOffset, expires: Date.now() + 300000});
      response.continuation = token;
    }
    while (frozen.size > 20) frozen.delete(frozen.keys().next().value);
    post(response);
  }
  window.jarvisPackageObserver = {location: function (value) {locator = Object.assign({}, value); changed();}};
  addEventListener('message', function (event) {
    if (event.source !== parent) return;
    var m = event.data;
    if (!m || m.jarvis !== 'package') return;
    if (m.type === 'observe.init' && m.protocol === 1 && typeof m.mountNonce === 'string') {if (nonce !== m.mountNonce) {nonce = m.mountNonce; sequence = 0; frozen.clear(); observations = [];} changed();}
    if (m.type === 'observe.request' && m.protocol === 1 && m.mountNonce === nonce && typeof m.requestId === 'string') answer(m);
  });
  new MutationObserver(changed).observe(document.documentElement, {subtree: true, childList: true, attributes: true, characterData: true});
  ['scroll', 'resize', 'hashchange', 'popstate', 'selectionchange', 'visibilitychange', 'toggle'].forEach(function (name) {addEventListener(name, changed, true);});
  ['pushState', 'replaceState'].forEach(function (name) {var original = history[name]; history[name] = function () {var result = original.apply(this, arguments); changed(); return result;};});
  post({type: 'observe.ready', capabilities: ['dom', 'selection', 'deck-location']});
})();
