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
//   to JARVIS     {jarvis: "package", type: "story.ready"} once loaded, so a
//                 beat already playing is posted again
//                 {jarvis: "package", type: "story.missing", beat, targets}
//                 the selectors (targets or actions) that found nothing visible
//
// An action is {show: selector} (scrolled into view) or {click: selector}
// (clicked: a detail view's opener or its close button), run in order
// before the targets are found. A target is one selector; every element it
// matches is ringed as one. Everything here is built with createElement and
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

  // The box around every visible element a selector matches, or null.
  function box(selector) {
    var r = null;
    matches(selector).forEach(function (el) {
      var b = el.getBoundingClientRect();
      if (b.width <= 0 || b.height <= 0) return;
      r = r ? { left: Math.min(r.left, b.left), top: Math.min(r.top, b.top),
                right: Math.max(r.right, b.right), bottom: Math.max(r.bottom, b.bottom) }
            : { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
    });
    return r;
  }

  function place() {
    var first = null;
    rings.forEach(function (ring) {
      var r = box(ring.selector);
      ring.el.style.display = r ? "block" : "none";
      if (!r) return;
      ring.el.style.left = (r.left - PAD) + "px";
      ring.el.style.top = (r.top - PAD) + "px";
      ring.el.style.width = (r.right - r.left + 2 * PAD) + "px";
      ring.el.style.height = (r.bottom - r.top + 2 * PAD) + "px";
      if (!first) first = r;
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

  function follow() {
    place();
    frame = beat ? requestAnimationFrame(follow) : 0;
  }

  function clear() {
    beat = null;
    rings.forEach(function (ring) { ring.el.remove(); });
    rings = [];
    if (pointer) { pointer.remove(); pointer = null; }
    if (frame) { cancelAnimationFrame(frame); frame = 0; }
  }

  function act(action, missing) {
    var kind = action.show ? "show" : action.click ? "click" : null;
    var selector = kind && String(action[kind]);
    var el = selector ? matches(selector)[0] : null;
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
      rings.push({ selector: selector, el: el });
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
    targets.forEach(function (selector) { if (!box(selector)) missing.push(selector); });
    frame = requestAnimationFrame(follow);
    // A target can come into view as a `show` scrolls: look again once it
    // has settled before calling it missing.
    if (missing.length) {
      setTimeout(function () {
        if (!beat || beat.id !== id) return;
        var unseen = missing.filter(function (s) { return !box(s); });
        if (unseen.length) tell({ type: "story.missing", beat: id, targets: unseen });
      }, 700);
    }
  }

  window.addEventListener("message", function (event) {
    if (event.source !== parent) return;
    var m = event.data;
    if (!m || typeof m !== "object" || m.jarvis !== "package") return;
    if (m.type === "story.cue") cue(m);
    else if (m.type === "story.clear") clear();
  });
  window.addEventListener("resize", place);
  tell({ type: "story.ready" });
})();
