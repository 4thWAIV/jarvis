"""JARVIS proves a deck fits without a browser (jarvis/presentation_proof.py):
the height every text and table slot needs is computed from the bundled
font's widths and the layout spec. This test holds that model to the
browser: a hard fixture deck (long words, a table, two pictures, a
comparison of four, a decision with picture options, in Spanish) is built
as make_package builds it and drawn in headless Chromium at frames across
the whole range, and the height the browser gives each slot's words is the
height the model computed, within MODEL_PX, once the proof's small width
margin (layout_spec's "width_margin", kept for subpixel rounding) is taken
off; with it on, the proof never needs less than the browser draws. A
change to the CSS, the font or the renderer that the model does not follow
fails here, in CI.

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import pytest

from jarvis import layout_spec, make_package as mp, presentation_proof
from jarvis.presentation_schema import ChartObject, ContentSlide
from jarvis.presentation_source import read_source
from tests.deck_fixtures import hard_deck

pytestmark = pytest.mark.page

# How far the browser's height may be from the model's, in px.
MODEL_PX = 0.2

# The frames drawn: the range's corners, its middle, and where the deck unit
# switches from the frame's width to its height.
FRAMES = [*layout_spec.corners(), (1024, 700), (880, 640), (1131, 823),
          # narrow frames, where every slide is one column (layout_spec narrow)
          (360, 640), (480, 800), (661, 900), (1200, 420)]

# What the browser drew for one slot: the height of its words (each block's
# box and the space after it), or of its table.
DRAWN = """id => {
  const slot = document.getElementById(id);
  const table = slot.querySelector("table");
  if (table) return table.getBoundingClientRect().height;
  let total = 0;
  for (const child of slot.children) {
    total += child.getBoundingClientRect().height + parseFloat(getComputedStyle(child).marginBottom);
  }
  return total;
}"""


def modelled(obj, slot_box, frame, margin: bool = True) -> float | None:
    """The proof's height for `obj` (None for a picture); without its width
    margin when `margin` is False. A narrow frame measures the one column."""
    if not margin:
        spec = layout_spec.spec()
        spec["text"]["width_margin"] = 0
        with pytest.MonkeyPatch.context() as m:
            m.setattr(layout_spec, "spec", lambda: spec)
            return modelled(obj, slot_box, frame)
    narrow = layout_spec.is_narrow(frame)
    if narrow:
        u, width = layout_spec.narrow_unit(frame[0]), layout_spec.narrow_slot_width(frame[0])
    else:
        u, (width, _) = layout_spec.unit(frame), layout_spec.frame_box(slot_box, frame)
    measured = presentation_proof.object_height(obj, width, u, narrow)
    assert not isinstance(measured, str), measured
    return measured


CHART = """id => {
  const chart = document.getElementById(id).querySelector(".presentation-chart");
  return {plot: chart.querySelector(".presentation-chart-plot").getBoundingClientRect().height,
          axis: chart.querySelector(".presentation-chart-y").getBoundingClientRect().width};
}"""


def chart_off(page, obj, slot_box, frame) -> list[str]:
    """A chart's plot height and y-label column width, drawn against the
    model's (presentation_proof.chart_parts, without its width margin)."""
    spec = layout_spec.spec()
    spec["text"]["width_margin"] = 0
    with pytest.MonkeyPatch.context() as m:
        m.setattr(layout_spec, "spec", lambda: spec)
        if layout_spec.is_narrow(frame):
            u, width = layout_spec.narrow_unit(frame[0]), layout_spec.narrow_slot_width(frame[0])
            height = layout_spec.narrow_height("chart", frame[0])
        else:
            u = layout_spec.unit(frame)
            width, height = layout_spec.frame_box(slot_box, frame)
        parts = presentation_proof.chart_parts(obj, width, u)
    assert not isinstance(parts, str), parts
    plot = height - parts["legend"] - parts["x"] - parts["around"]
    drawn = page.evaluate(CHART, "presentation-main-" + obj.id)
    if abs(drawn["plot"] - plot) > MODEL_PX or abs(drawn["axis"] - parts["axis"]) > MODEL_PX:
        return [f"{frame[0]}x{frame[1]} {obj.id}: drawn plot {drawn['plot']:.2f} px and axis "
                f"{drawn['axis']:.2f} px, model {plot:.2f} and {parts['axis']:.2f} px"]
    return []


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("hard")
    made = mp.make_presentation(hard_deck(folder), folder / "out")
    package = folder / "out" / "compiled" / "prueba-dificil"
    return package, read_source(package / "source.yaml", built=True, language="es")


def test_the_browser_draws_every_slot_the_height_the_model_computed(built):
    from playwright.sync_api import sync_playwright
    package, source = built
    checked, off = 0, []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/api/packages/story.js", lambda route: route.fulfill(
                body="", content_type="application/javascript"))
            # The bundled font JARVIS serves to every deck (layout_spec.FONT_PATH).
            page.route("**" + layout_spec.FONT_PATH, lambda route: route.fulfill(
                body=layout_spec.font_css(), content_type="text/css"))
            for frame in FRAMES:
                page.set_viewport_size({"width": int(frame[0]), "height": int(frame[1])})
                page.goto((package / "index.html").as_uri())
                page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
                page.evaluate("async () => { await document.fonts.ready; }")
                assert page.evaluate("document.fonts.check('18px \"JARVIS Deck\"')"), \
                    "the bundled font is the one drawn"
                for slide in source.document.slides:
                    if not isinstance(slide, ContentSlide):
                        continue
                    boxes = layout_spec.boxes(slide.layout, {o.slot for o in slide.objects})
                    for obj in slide.objects:
                        if isinstance(obj, ChartObject):
                            checked += 1
                            off += chart_off(page, obj, boxes[obj.slot], frame)
                            continue
                        exact = modelled(obj, boxes[obj.slot], frame, margin=False)
                        if exact is None:
                            continue
                        dom = "presentation-main-" + obj.id
                        drawn = page.evaluate(DRAWN, dom)
                        proof = modelled(obj, boxes[obj.slot], frame)
                        checked += 1
                        if abs(drawn - exact) > MODEL_PX or proof < drawn - MODEL_PX:
                            off.append(f"{frame[0]}x{frame[1]} {obj.id}: drawn {drawn:.2f} px, "
                                       f"model {exact:.2f} px, proof {proof:.2f} px")
        finally:
            browser.close()
    assert checked >= len(FRAMES) * 10, checked
    assert not off, "\n".join(off)


def test_the_decision_s_pictured_options_are_drawn(built):
    from playwright.sync_api import sync_playwright
    package, _ = built
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 662, "height": 489})
            page.goto((package / "index.html").as_uri())
            page.wait_for_function("document.querySelector('.slide.current')")
            pictures = page.evaluate("""() => [...document.querySelectorAll('.slide.decision img')]
                .map(img => ({src: img.getAttribute('src'), ok: img.complete && img.naturalWidth > 0}))""")
        finally:
            browser.close()
    assert {p["src"] for p in pictures} >= {"opcion-a.svg", "opcion-b.svg"}
    assert all(p["ok"] for p in pictures), pictures
