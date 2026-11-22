"""Whether every slide of a built deck fits its frame, measured by the
program (Tim 2026-11-22, construction-drafts.md "Found on the first full
run": the program measures every slide's fit at every frame size, gives it
to the reviewers as fact, and fixes the layout itself, never the sender).

How much room words take in the system font stack is only known where they
are drawn, so the program draws the built deck as JARVIS's package frame
does (inside a frame, `html.hosted`, the shared deck's own layout and its
own fit, deck.js fitSlide) in headless Chromium, at every frame the layout
proof covers (layout_spec.frames(), 662 x 489 to 1600 x 900), and reads,
for every slide, whether anything on it is clipped or needs scrolling: its
words, its pictures, and a decision's controls (the options and their
Choose bars, Decide later, None of these, his words field and the line
saying what is recorded). Nothing is looked at by a model and no
screenshot is taken: it is a measurement, kept in the built folder as
`.jarvis/fit.json` and given to the deck review beside each slide's layout
data (package_deck.review_turn).

`measure` raises Unavailable when it cannot draw the deck (Playwright's
Chromium missing): the deck step cannot know its fit and stops in Error
saying so.
"""
from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path
from typing import Optional

from jarvis import layout_spec

FILE = ".jarvis/fit.json"
VERSION = "deck-fit/1"
CLIPPED, SCROLLS = "clipped", "needs scrolling"

# For each slide of the deck in the frame: what on it is clipped or needs
# scrolling, by its own words or its control's name. A part one pixel or
# less across is hidden on purpose (a heading for screen readers) and is
# not a part of the slide.
_MEASURE = r"""() => {
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
    out.push({n: i + 1, id: s.dataset.sourceSlide || s.id, fit: s.getAttribute("data-fit") || "", cut});
  });
  return out;
}"""

_SETTLED = "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"


class Unavailable(Exception):
    """The deck could not be drawn to measure it."""


def frames() -> list[tuple[int, int]]:
    """Every frame the layout proof covers, in whole pixels."""
    return sorted({(round(w), round(h)) for w, h in layout_spec.frames()})


def _measure_now(folder: Path, sizes: list[tuple[int, int]]) -> list[dict]:
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
                        seen.append({"frame": [w, h], "slides": deck.evaluate(_MEASURE)})
                    return seen
                finally:
                    browser.close()
        except (PlaywrightError, StopIteration) as e:
            raise Unavailable(f"Chromium could not draw the deck: {e}") from None


def measure(folder: Path, sizes: Optional[list[tuple[int, int]]] = None) -> dict:
    """Every slide of the built deck in `folder`, drawn at every frame:
    {version, frames, fits, slides: [{slide, id, fits, cut: [{frame, parts:
    [{part, how, by_px}]}]}]}. Run in a thread of its own (the deck step's
    loop is running). Raises Unavailable."""
    sizes = list(sizes or frames())
    got: dict = {}

    def go():
        try:
            got["seen"] = _measure_now(Path(folder), sizes)
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
                                              "cut": []})
            if s["cut"]:
                one["fits"] = False
                one["cut"].append({"frame": frame["frame"], "fit": s["fit"],
                                   "parts": [{k: c[k] for k in ("part", "how", "by_px")}
                                             for c in s["cut"]]})
    ordered = sorted(slides.values(), key=lambda s: s["slide"])
    return {"version": VERSION, "frames": [list(f) for f in sizes],
            "fits": all(s["fits"] for s in ordered), "slides": ordered}


def said(slide: dict) -> str:
    """One slide's fit in words, for the reviewers and the author."""
    first, last = layout_spec.spec()["frame"]["min"], layout_spec.spec()["frame"]["max"]
    if slide["fits"]:
        return (f"Fits at every frame from {first[0]} x {first[1]} to {last[0]} x {last[1]}: "
                f"nothing clipped, nothing to scroll (measured).")
    worst = max(slide["cut"], key=lambda c: max(p["by_px"] for p in c["parts"]))
    parts = "; ".join(f"{p['part']!r} {p['how']} by {p['by_px']} px" for p in worst["parts"][:6])
    return (f"Does not fit at {len(slide['cut'])} frame(s), worst at {worst['frame'][0]} x "
            f"{worst['frame'][1]}: {parts}.")


def problems(fit: dict) -> list[str]:
    """Each slide that does not fit, in the author's terms."""
    return [f"Slide {s['slide']} ({s['id']}) does not fit even after JARVIS's own fitting: "
            + said(s) + " Say it in fewer words." for s in fit["slides"] if not s["fits"]]


def of_slide(fit: dict, slide_id: str) -> Optional[dict]:
    return next((s for s in fit.get("slides") or [] if s["id"] == slide_id), None)


def write(folder: Path, fit: dict) -> None:
    path = Path(folder) / FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fit, indent=2) + "\n", encoding="utf-8")
