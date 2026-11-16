// JARVIS's story helper for a page package (docs/work-packages.md, "Telling
// a page's story"). A story page loads it with
//
//     <script src="/api/packages/story.js"></script>
//
// It runs inside the package's sandboxed frame, which has no origin, and
// only listens: JARVIS's page posts it one beat at a time as that beat's
// words START playing, and it points at the beat's targets until the next
// beat or `story.clear`.
//
//   from JARVIS   {jarvis: "package", type: "story.cue", beat, targets, actions}
//                 {jarvis: "package", type: "story.clear"}
//                 {jarvis: "package", type: "focus.show", key, targets} the
//                 part a question is about: brought into view and reported
//                 (story.focus) until focus.clear; never ringed here, the
//                 page draws its light and ring around it
//                 {jarvis: "package", type: "focus.clear"}
//   to JARVIS     {jarvis: "package", type: "story.ready"} once loaded, so a
//                 beat already playing is posted again
//                 {jarvis: "package", type: "story.missing", beat, targets}
//                 the selectors (targets or actions) that found nothing visible
//                 {jarvis: "package", type: "story.ambiguous", beat, targets}
//                 precise targets (a declaration's or a member's) that matched
//                 several elements: none of them is ringed
//                 {jarvis: "package", type: "story.focus", key, rect} where the
//                 part being focused is, in this frame's own coordinates
//                 ({left, top, width, height}, the box around every element
//                 its targets match), or rect null when none is visible: for
//                 a beat (key "beat:<beat>") while it is shown, and for a
//                 focus JARVIS asked for (its own key) until focus.clear;
//                 posted again whenever it moves (a scroll, a resize), so
//                 the page's focus layer (PackageFocus.tsx) follows it
//                 {jarvis: "package", type: "layout.restore"} on Option+Shift+T
//                 pressed inside the frame: keys pressed here never reach
//                 JARVIS's page, so the restore key is passed on. Restore
//                 only: a frame can never take over the page this way.
//
// An action is {show: selector} (scrolled into view) or {click: selector}
// (clicked: a detail view's opener or its close button), run in order
// before the targets are found. A target is one selector; every element it
// matches is ringed as one, except a precise target (one naming
// data-declaration or data-member), which must match exactly one. Everything here is built with createElement and
// textContent: nothing from the story is ever parsed as HTML.
(function () {
  "use strict";
  if (window.__jarvisStory) return;
  window.__jarvisStory = true;

  var LAYER_ID = "jarvis-story-layer";
  var STYLE =
    "#" + LAYER_ID + "{position:fixed;inset:0;pointer-events:none;z-index:2147483647}" +
    ".jarvis-story-ring{position:fixed;box-sizing:border-box;border:3px solid #f5a623;" +
    "border-radius:12px;box-shadow:0 0 0 4px rgba(245,166,35,.25),0 0 22px rgba(245,166,35,.55);" +
    "transition:left .3s ease,top .3s ease,width .3s ease,height .3s ease;" +
    "animation:jarvis-story-pulse 1.6s ease-in-out infinite}" +
    ".jarvis-story-pointer{position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;" +
    "transition:left .45s ease,top .45s ease,transform .45s ease;" +
    "filter:drop-shadow(0 2px 4px rgba(0,0,0,.35))}" +
    ".jarvis-story-pointer i{position:absolute;inset:8px;border-radius:50%;background:#f5a623;" +
    "border:3px solid #fff}" +
    ".jarvis-story-pointer b{position:absolute;right:-12px;top:12px;width:0;height:0;" +
    "border-top:10px solid transparent;border-bottom:10px solid transparent;" +
    "border-left:14px solid #f5a623}" +
    "@keyframes jarvis-story-pulse{0%,100%{box-shadow:0 0 0 4px rgba(245,166,35,.25)," +
    "0 0 22px rgba(245,166,35,.55)}50%{box-shadow:0 0 0 9px rgba(245,166,35,.12)," +
    "0 0 30px rgba(245,166,35,.75)}}" +
    "@media (prefers-reduced-motion:reduce){.jarvis-story-ring,.jarvis-story-pointer" +
    "{animation:none;transition:none}}";
  var PAD = 6;

  var layer = null;
  var beat = null;       // {id, targets} while a beat is shown
  var rings = [];        // [{selector, el}]
  var pointer = null;
  var frame = 0;
  var asked = null;      // {key, targets} while JARVIS asks for a focus
  var reported = {};     // key -> the rect last told, as text

  function tell(message) {
    message.jarvis = "package";
    parent.postMessage(message, "*");
  }

  function ensureLayer() {
    if (layer && layer.isConnected) return layer;
    var style = document.createElement("style");
    style.textContent = STYLE;
    document.head.appendChild(style);
    layer = document.createElement("div");
    layer.id = LAYER_ID;
    layer.setAttribute("aria-hidden", "true");
    document.body.appendChild(layer);
    return layer;
  }

  function matches(selector) {
    try {
      return [].slice.call(document.querySelectorAll(selector));
    } catch (e) {
      return [];                                  // not a selector: reported as missing
    }
  }

  // A precise target names one declaration or one member of a rendered
  // document ([data-declaration="..."], [data-member="..."]): it is ringed
  // only when it matches exactly one element, and reported, never widened,
  // when it matches none or several.
  var PRECISE = /\[\s*data-(declaration|member)\s*[~|^$*]?=/;
  // A member's name carried once is the name itself; a name several members
  // of one declaration share is name#1, name#2, ... (Tim's answer of
  // 2026-11-13). The bare shared name matches no row, and is ambiguous, not
  // missing: it names every row that shares it.
  var BARE_MEMBER = /\[\s*data-member\s*=\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')\s*\]/;
  function shared(selector) {
    var m = BARE_MEMBER.exec(selector);
    if (!m || m[1].indexOf("#") >= 0) return false;
    var prefix = m[1].slice(0, -1) + "#" + m[1].slice(-1);
    return matches(selector.replace(m[0], "[data-member^=" + prefix + "]")).length > 1;
  }
  function ambiguous(selector) {
    if (!PRECISE.test(selector)) return false;
    var n = matches(selector).length;
    return n > 1 || (n === 0 && shared(selector));
  }

  // Inline content can wrap onto several lines. Keep each live rectangle;
  // a union is still useful for visibility and pointer placement.
  function boxes(selector) {
    if (ambiguous(selector)) return [];
    var out = [];
    matches(selector).forEach(function (el) {
      if (getComputedStyle(el).visibility !== "visible") return;
      var rects;
      if (window.JarvisPresentation && el.dataset.member) {
        rects = window.JarvisPresentation.rectangles(el.dataset.member);
      } else if (el.matches("span, strong, em") && el.dataset.member) {
        var range = document.createRange();
        range.selectNodeContents(el);
        rects = [].slice.call(range.getClientRects());
      } else rects = [el.getBoundingClientRect()];
      rects.forEach(function (r) { if (r.width > 0 && r.height > 0) out.push(r); });
    });
    return out;
  }

  function box(selector) {
    return boxes(selector).reduce(function (r, b) {
      return r ? {left:Math.min(r.left,b.left), top:Math.min(r.top,b.top),
                  right:Math.max(r.right,b.right), bottom:Math.max(r.bottom,b.bottom)} : b;
    }, null);
  }

  function place() {
    var first = null;
    rings.forEach(function (ring) {
      var rects = boxes(ring.selector);
      while (ring.parts.length < rects.length) {
        var extra = ring.el.cloneNode(false);
        layer.appendChild(extra);
        ring.parts.push(extra);
      }
      while (ring.parts.length > Math.max(1, rects.length)) ring.parts.pop().remove();
      ring.parts.forEach(function (el, index) {
        var r = rects[index];
        el.style.display = r ? "block" : "none";
        if (!r) return;
        el.style.left = (r.left - PAD) + "px";
        el.style.top = (r.top - PAD) + "px";
        el.style.width = (r.right - r.left + 2 * PAD) + "px";
        el.style.height = (r.bottom - r.top + 2 * PAD) + "px";
        if (!first) first = r;
      });
    });
    if (!pointer) return;
    pointer.style.display = first ? "block" : "none";
    if (!first) return;
    // To the ring's left, pointing right; above it, pointing down, when
    // there is no room on the left.
    var middle = (first.top + first.bottom) / 2;
    if (first.left - PAD > 60) {
      pointer.style.left = (first.left - PAD - 30) + "px";
      pointer.style.top = middle + "px";
      pointer.style.transform = "rotate(0deg)";
    } else {
      pointer.style.left = ((first.left + first.right) / 2) + "px";
      pointer.style.top = Math.max(26, first.top - PAD - 30) + "px";
      pointer.style.transform = "rotate(90deg)";
    }
  }

  // The box around every target, unpadded, or null when none is visible.
  function around(targets) {
    var r = null;
    targets.forEach(function (selector) {
      var b = box(selector);
      if (!b) return;
      r = r ? { left: Math.min(r.left, b.left), top: Math.min(r.top, b.top),
                right: Math.max(r.right, b.right), bottom: Math.max(r.bottom, b.bottom) } : b;
    });
    return r && { left: Math.round(r.left), top: Math.round(r.top),
                  width: Math.round(r.right - r.left), height: Math.round(r.bottom - r.top) };
  }

  // Tell JARVIS where a focus is, only when that has changed.
  function report(key, targets) {
    var rect = targets ? around(targets) : null;
    var now = JSON.stringify(rect);
    if (reported[key] === now) return;
    reported[key] = now;
    tell({ type: "story.focus", key: key, rect: rect });
  }

  function follow() {
    place();
    if (beat) report("beat:" + beat.id, beat.targets);
    if (asked) report(asked.key, asked.targets);
    frame = beat || asked ? requestAnimationFrame(follow) : 0;
  }

  function clear() {
    if (beat) report("beat:" + beat.id, null);
    beat = null;
    rings.forEach(function (ring) { ring.parts.forEach(function (part) { part.remove(); }); });
    rings = [];
    if (pointer) { pointer.remove(); pointer = null; }
    if (frame && !asked) { cancelAnimationFrame(frame); frame = 0; }
  }

  // JARVIS asks to focus the part a question is about: brought into view
  // and reported until focus.clear. Nothing is clicked and nothing ringed.
  function focusOn(message) {
    var key = String(message.key || "");
    var targets = (Array.isArray(message.targets) ? message.targets : []).map(String);
    asked = { key: key, targets: targets };
    delete reported[key];
    var first = null;
    targets.some(function (selector) {
      first = ambiguous(selector) ? null : matches(selector)[0] || null;
      return !!first;
    });
    if (first) first.scrollIntoView({ block: "center", inline: "nearest", behavior: "auto" });
    report(key, targets);
    if (!frame) frame = requestAnimationFrame(follow);
  }

  function unfocus() {
    if (asked) delete reported[asked.key];
    asked = null;
    if (frame && !beat) { cancelAnimationFrame(frame); frame = 0; }
  }

  function act(action, missing) {
    var kind = action.show ? "show" : action.click ? "click" : null;
    var selector = kind && String(action[kind]);
    var el = selector && !ambiguous(selector) ? matches(selector)[0] : null;
    if (!el) {
      if (selector) missing.push(selector);
      return;
    }
    if (kind === "show") el.scrollIntoView({ block: "center", behavior: "smooth" });
    else el.click();
  }

  function cue(message) {
    var id = String(message.beat || "");
    if (beat && beat.id === id) return;           // posted again: already shown
    clear();
    var missing = [];
    (Array.isArray(message.actions) ? message.actions : []).forEach(function (a) {
      if (a && typeof a === "object") act(a, missing);
    });
    var targets = (Array.isArray(message.targets) ? message.targets : []).map(String);
    beat = { id: id, targets: targets };
    var root = ensureLayer();
    targets.forEach(function (selector) {
      var el = document.createElement("div");
      el.className = "jarvis-story-ring";
      el.dataset.beat = id;
      el.dataset.target = selector;
      root.appendChild(el);
      rings.push({ selector: selector, el: el, parts: [el] });
    });
    if (targets.length) {
      pointer = document.createElement("div");
      pointer.className = "jarvis-story-pointer";
      pointer.dataset.beat = id;
      pointer.appendChild(document.createElement("i"));
      pointer.appendChild(document.createElement("b"));
      root.appendChild(pointer);
    }
    place();
    report("beat:" + id, targets);
    targets.forEach(function (selector) { if (!box(selector)) missing.push(selector); });
    if (!frame) frame = requestAnimationFrame(follow);
    // A target can come into view as a `show` scrolls: look again once it
    // has settled before calling it missing.
    if (missing.length) {
      setTimeout(function () {
        if (!beat || beat.id !== id) return;
        var unseen = missing.filter(function (s) { return !box(s); });
        var several = unseen.filter(ambiguous);
        var none = unseen.filter(function (s) { return !ambiguous(s); });
        if (none.length) tell({ type: "story.missing", beat: id, targets: none });
        if (several.length) tell({ type: "story.ambiguous", beat: id, targets: several });
      }, 700);
    }
  }

  window.addEventListener("message", function (event) {
    if (event.source !== parent) return;
    var m = event.data;
    if (!m || typeof m !== "object" || m.jarvis !== "package") return;
    if (m.type === "story.cue") cue(m);
    else if (m.type === "story.clear") clear();
    else if (m.type === "focus.show") focusOn(m);
    else if (m.type === "focus.clear") unfocus();
  });
  window.addEventListener("resize", place);
  window.addEventListener("keydown", function (event) {
    if (event.altKey && event.shiftKey && !event.ctrlKey && !event.metaKey && event.code === "KeyT") {
      event.preventDefault();
      tell({ type: "layout.restore" });
    }
  });
  tell({ type: "story.ready" });
})();
