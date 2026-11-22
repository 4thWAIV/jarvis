"""The CI page tests' measure of a fixture deck: whether every slide fits
its frame and the size its words are drawn at, read in headless Chromium.
It is CI's, never a package's: no package is drawn in a browser on its way
to Tim (his decision of 18 November, construction-drafts.md "Visuals, the
salvage, and sizing": sizing comes from the language, each slot's capacity
worked out by program, jarvis/deck_capacity.py). These tests hold the
shared deck's own fit (deck.js fitSlide) and the capacity model to the
browser on fixture decks.

The built deck is drawn as JARVIS's package frame does (inside a frame,
`html.hosted`, the shared deck's own layout and its own fit: each slide's
words at the largest size at which it fits, between the 13 px floor and
the ceiling) at the frames given (layout_spec.frames() by default), and,
for every slide, what is clipped or needs scrolling is read (its words,
its pictures, and a decision's controls) with the size its words are drawn
at (`drawn`: the least, the most and the one most of its characters are
drawn at, the scale its fit chose and how far down the slide its words and
pictures reach). `measure` raises Unavailable when it cannot draw the deck.
"""
from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from typing import Optional

from jarvis import layout_spec

VERSION = "deck-fit/1"
CLIPPED, SCROLLS = "clipped", "needs scrolling"

# For each slide of the deck in the frame: what on it is clipped or needs
# scrolling, by its own words or its control's name. A part one pixel or
# less across is hidden on purpose (a heading for screen readers) and is
# not a part of the slide.
_MEASURE = r"""(only) => {
  const tiny = r => r.width <= 1 || r.height <= 1;
  const scrolls = st => /(auto|scroll)/.test(st.overflowY + " " + st.overflowX);
  const words = e => {
    const said = e.getAttribute("aria-label") || e.getAttribute("placeholder") ||
      e.getAttribute("alt") || e.textContent || e.tagName.toLowerCase();
    const t = said.replace(/\s+/g, " ").trim();
    return t.length > 80 ? t.slice(0, 77) + "..." : t;
  };
  const out = [];
  document.querySelectorAll(".slide").forEach((s, i) => {
    if (only && !only.includes(s.dataset.sourceSlide || s.id)) return;
    const box = s.getBoundingClientRect();
    const own = getComputedStyle(s);
    const cut = [];
    const seen = new Set();
    const add = (e, how, by) => {
      const key = words(e) + how;
      if (seen.has(key)) return;
      seen.add(key);
      cut.push({part: words(e), tag: e.tagName.toLowerCase(), how, by_px: Math.round(by)});
    };
    if (s.scrollHeight > s.clientHeight + 1 || s.scrollWidth > s.clientWidth + 1)
      cut.push({part: "the slide", tag: "section", how: scrolls(own) ? "needs scrolling" : "clipped",
                by_px: Math.max(s.scrollHeight - s.clientHeight, s.scrollWidth - s.clientWidth)});
    s.querySelectorAll("*").forEach(e => {
      if (!e.getClientRects().length) return;
      const r = e.getBoundingClientRect();
      for (let a = e; a && a !== s; a = a.parentElement) if (tiny(a.getBoundingClientRect())) return;
      const st = getComputedStyle(e);
      if ((st.overflowX !== "visible" || st.overflowY !== "visible") &&
          (e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1))
        add(e, scrolls(st) ? "needs scrolling" : "clipped",
            Math.max(e.scrollHeight - e.clientHeight, e.scrollWidth - e.clientWidth));
      const text = [...e.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
      if (text || /^(BUTTON|INPUT|SELECT|TEXTAREA|IMG|SVG)$/i.test(e.tagName)) {
        const by = Math.max(r.bottom - box.bottom, r.right - box.right, box.top - r.top, box.left - r.left);
        if (by > 1) add(e, scrolls(own) ? "needs scrolling" : "clipped", by);
      }
    });
    // The size every word is drawn at: the smallest, the largest, and the
    // one most of the slide's characters are drawn at; and how much of the
    // slide's height its words and pictures reach down to.
    const sizes = new Map();
    let bottom = box.top;
    s.querySelectorAll("*").forEach(e => {
      if (!e.getClientRects().length) return;
      for (let a = e; a && a !== s; a = a.parentElement) if (tiny(a.getBoundingClientRect())) return;
      const chars = [...e.childNodes].filter(n => n.nodeType === 3)
        .reduce((k, n) => k + n.textContent.replace(/\s+/g, "").length, 0);
      const r = e.getBoundingClientRect();
      if (chars || /^(IMG|SVG)$/i.test(e.tagName)) bottom = Math.max(bottom, Math.min(r.bottom, box.bottom));
      if (!chars) return;
      const px = Math.round(parseFloat(getComputedStyle(e).fontSize) * 10) / 10;
      sizes.set(px, (sizes.get(px) || 0) + chars);
    });
    const drawn = [...sizes.keys()];
    const most = drawn.reduce((a, b) => sizes.get(b) > sizes.get(a) ? b : a, drawn[0] || 0);
    out.push({n: i + 1, id: s.dataset.sourceSlide || s.id, fit: s.getAttribute("data-fit") || "",
              scale: Math.round(parseFloat(s.style.getPropertyValue("--fit")) * 1000) / 1000 || null,
              text_px: drawn.length ? {least: Math.min(...drawn), most: Math.max(...drawn), typical: most} : null,
              reaches: box.height ? Math.round((bottom - box.top) / box.height * 100) / 100 : 0,
              cut});
  });
  return out;
}"""

_SETTLED = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


class Unavailable(Exception):
    """The deck could not be drawn to measure it."""


def frames() -> list[tuple[int, int]]:
    """Every frame the layout proof covers, in whole pixels."""
    return sorted({(round(w), round(h)) for w, h in layout_spec.frames()})


def _measure_now(folder: Path, sizes: list[tuple[int, int]],
                 only: Optional[list[str]] = None) -> list[dict]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise Unavailable(f"Playwright is not installed: {e}") from None
    index = (folder / "index.html").resolve()
    with tempfile.TemporaryDirectory() as tmp:
        host = Path(tmp) / "frame.html"
        host.write_text("<!doctype html><style>html,body{margin:0;height:100%;overflow:hidden}"
                        "iframe{border:0;display:block;width:100%;height:100%}</style>"
                        f'<iframe src="{index.as_uri()}"></iframe>', encoding="utf-8")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    w, h = sizes[0]
                    page = browser.new_page(viewport={"width": w, "height": h})
                    page.route("**/api/packages/story.js", lambda route: route.fulfill(
                        body="", content_type="application/javascript"))
                    page.goto(host.as_uri())
                    deck = next(f for f in page.frames if f != page.main_frame)
                    deck.wait_for_function("document.querySelector('.slide.current')")
                    deck.wait_for_function("[...document.images].every(i => i.complete)")
                    seen = []
                    for w, h in sizes:
                        page.set_viewport_size({"width": w, "height": h})
                        deck.evaluate(_SETTLED)
                        deck.evaluate(_SETTLED)
                        seen.append({"frame": [w, h], "slides": deck.evaluate(_MEASURE, only)})
                    return seen
                finally:
                    browser.close()
        except (PlaywrightError, StopIteration) as e:
            raise Unavailable(f"Chromium could not draw the deck: {e}") from None


def measure(folder: Path, sizes: Optional[list[tuple[int, int]]] = None,
            only: Optional[list[str]] = None) -> dict:
    """Every slide of the built deck in `folder` (with `only`, those slide
    ids alone), drawn at every frame:
    {version, frames, fits, slides: [{slide, id, fits, cut: [{frame, parts:
    [{part, how, by_px}]}]}]}. Run in a thread of its own (an event loop may
    be running). Raises Unavailable."""
    sizes = list(sizes or frames())
    got: dict = {}

    def go():
        try:
            got["seen"] = _measure_now(Path(folder), sizes, only)
        except BaseException as e:      # noqa: BLE001 - handed back to the caller
            got["error"] = e
    worker = threading.Thread(target=go, name="deck-fit", daemon=True)
    worker.start()
    worker.join()
    if "error" in got:
        raise got["error"] if isinstance(got["error"], Unavailable) else Unavailable(
            f"{type(got['error']).__name__}: {got['error']}")
    slides: dict[str, dict] = {}
    for frame in got["seen"]:
        for s in frame["slides"]:
            one = slides.setdefault(s["id"], {"slide": s["n"], "id": s["id"], "fits": True,
                                              "cut": [], "drawn": []})
            one["drawn"].append({"frame": frame["frame"], "text_px": s.get("text_px"),
                                 "scale": s.get("scale"), "reaches": s.get("reaches")})
            if s["cut"]:
                one["fits"] = False
                one["cut"].append({"frame": frame["frame"], "fit": s["fit"],
                                   "parts": [{k: c[k] for k in ("part", "how", "by_px")}
                                             for c in s["cut"]]})
    ordered = sorted(slides.values(), key=lambda s: s["slide"])
    return {"version": VERSION, "frames": [list(f) for f in sizes],
            "fits": all(s["fits"] for s in ordered), "slides": ordered}


def drawn_at_ends(slide: dict) -> dict[str, dict]:
    """The size a slide's words are drawn at at the smallest and the largest
    frame, as measured: {"smallest": {least, most, typical}, "largest": ...}."""
    first, last = layout_spec.spec()["frame"]["min"], layout_spec.spec()["frame"]["max"]
    out = {}
    for name, frame in (("smallest", first), ("largest", last)):
        seen = next((d for d in slide.get("drawn") or [] if d["frame"] == list(frame)), None)
        if seen and seen.get("text_px"):
            out[name] = seen["text_px"]
    return out


def _sizes_said(slide: dict) -> str:
    first, last = layout_spec.spec()["frame"]["min"], layout_spec.spec()["frame"]["max"]
    ends = drawn_at_ends(slide)
    said_ = [f"at {f[0]} x {f[1]} most of its words at {t['typical']:g} px (the least "
             f"{t['least']:g} px, the most {t['most']:g} px)"
             for f, t in ((first, ends.get("smallest")), (last, ends.get("largest"))) if t]
    return ("Its words are drawn as large as the slide allows: " + "; ".join(said_) + " "
            "(measured).") if said_ else ""


def said(slide: dict) -> str:
    """One slide's fit and the size its words are drawn at, in words, for
    the reviewers and the author."""
    first, last = layout_spec.spec()["frame"]["min"], layout_spec.spec()["frame"]["max"]
    if slide["fits"]:
        return " ".join(filter(None, [
            f"Fits at every frame from {first[0]} x {first[1]} to {last[0]} x {last[1]}: "
            f"nothing clipped, nothing to scroll (measured).", _sizes_said(slide)]))
    worst = max(slide["cut"], key=lambda c: max(p["by_px"] for p in c["parts"]))
    parts = "; ".join(f"{p['part']!r} {p['how']} by {p['by_px']} px" for p in worst["parts"][:6])
    return (f"Does not fit at {len(slide['cut'])} frame(s), worst at {worst['frame'][0]} x "
            f"{worst['frame'][1]}: {parts}.")


def problems(fit: dict) -> list[str]:
    """Each slide that does not fit, in the author's terms."""
    floor = layout_spec.spec()["text"]["min_px"]
    return [f"Slide {s['slide']} ({s['id']}) does not fit even after JARVIS's own fitting, its "
            f"words at the {floor} px floor: " + said(s) + " Split it into two slides, each "
            f"with its share of the words (a decision's background on a content slide before "
            f"it)." for s in fit["slides"] if not s["fits"]]


def of_slide(fit: dict, slide_id: str) -> Optional[dict]:
    return next((s for s in fit.get("slides") or [] if s["id"] == slide_id), None)

