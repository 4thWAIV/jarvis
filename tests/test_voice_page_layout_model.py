"""JARVIS proves a deck's pictures without a browser
(jarvis/presentation_proof.py): the size every picture is drawn at is
computed from its own bytes (picture_proof) and the layout spec's slot
boxes. This test holds that model to the browser: a hard fixture deck (long
words, a table, two pictures, a comparison of four, a picture beside words,
a decision with picture options, JARVIS's components, charts and a table
from data, in Spanish) is built as make_package builds it and drawn in
headless Chromium at frames across the whole range, and the size the browser
draws each picture is the size the proof computed, within MODEL_PX; every
object the capacity model (deck_capacity) says fits its slot is drawn
within it, nothing cut off, and every chart keeps its least plot; every
diagram's nodes and groups (the diagram deck's four kinds, and the hard
deck's flow) are drawn where diagram_layout put them, within MODEL_PX, with
no word cut off. A change
to the CSS or the renderer that the models do not follow fails here, in CI,
on this fixture deck: no package is drawn in a browser on its way.

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import pytest

from jarvis import deck_capacity, layout_spec, make_package as mp, picture_proof
from jarvis.presentation_schema import ContentSlide, ImageObject
from jarvis.presentation_source import read_source
from tests.deck_fixtures import hard_deck

pytestmark = pytest.mark.page

# How far the browser's drawn size may be from the model's, in px.
MODEL_PX = 0.5

# The frames drawn: the range's corners, its middle, and where the deck unit
# switches from the frame's width to its height.
FRAMES = [*layout_spec.corners(), (1024, 700), (880, 640), (1131, 823)]

# What the browser drew for one picture: its size inside its slot, contained.
DRAWN = """id => {
  const img = document.getElementById(id).querySelector("img");
  const box = img.getBoundingClientRect();
  const scale = Math.min(box.width / img.naturalWidth, box.height / img.naturalHeight);
  return [img.naturalWidth * scale, img.naturalHeight * scale];
}"""


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    folder = tmp_path_factory.mktemp("hard")
    made = mp.make_presentation(hard_deck(folder), folder / "out")
    package = folder / "out" / "compiled" / "prueba-dificil"
    return package, read_source(package / "source.yaml", built=True, language="es")


def test_the_browser_draws_every_picture_the_size_the_proof_computed(built):
    from playwright.sync_api import sync_playwright
    package, source = built
    checked, off = 0, []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/api/packages/story.js", lambda route: route.fulfill(
                body="", content_type="application/javascript"))
            for frame in FRAMES:
                page.set_viewport_size({"width": int(frame[0]), "height": int(frame[1])})
                page.goto((package / "index.html").as_uri())
                page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
                page.wait_for_function("[...document.images].every(img => img.complete)")
                for slide in source.document.slides:
                    if not isinstance(slide, ContentSlide):
                        continue
                    boxes = layout_spec.boxes(slide.layout, {o.slot for o in slide.objects})
                    for obj in slide.objects:
                        if not isinstance(obj, ImageObject):
                            continue
                        drawn = page.evaluate(DRAWN, "presentation-main-" + obj.id)
                        model = picture_proof.drawn(source.pictures[obj.asset],
                                                    layout_spec.frame_box(boxes[obj.slot], frame))
                        checked += 1
                        if any(abs(d - m) > MODEL_PX for d, m in zip(drawn, model)):
                            off.append(f"{frame[0]}x{frame[1]} {obj.id}: drawn "
                                       f"{drawn[0]:.2f} x {drawn[1]:.2f} px, model "
                                       f"{model[0]:.2f} x {model[1]:.2f} px")
        finally:
            browser.close()
    assert checked == len(FRAMES) * 3, checked
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


# For each slot of the current slide: how far its content runs past it, and
# a chart's plot height.
OVERFLOW = """() => [...document.querySelectorAll('.slide.current .presentation-slot')].map(slot => ({
  id: slot.dataset.object, kind: slot.dataset.kind,
  over: Math.max(slot.scrollHeight - slot.clientHeight, slot.scrollWidth - slot.clientWidth),
  plot: slot.querySelector('.presentation-chart-plot')?.getBoundingClientRect().height ?? null}))"""


def test_every_object_within_capacity_is_drawn_within_its_slot(built):
    """The capacity model is the language's: what it lets through is drawn
    whole by the browser, at every frame, words at whatever size the
    slide's fit chose; and every slot of the deck is within its capacity."""
    from playwright.sync_api import sync_playwright
    package, source = built
    content = [s for s in source.document.slides if isinstance(s, ContentSlide)]
    assert all(deck_capacity.problems(s) == [] for s in content)
    plot_min = layout_spec.spec()["chart"]["plot_min_px"]
    off, kinds = [], set()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/api/packages/story.js", lambda route: route.fulfill(
                body="", content_type="application/javascript"))
            for frame in FRAMES:
                page.set_viewport_size({"width": int(frame[0]), "height": int(frame[1])})
                page.goto((package / "index.html").as_uri())
                page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
                for n, slide in enumerate(source.document.slides):
                    if not isinstance(slide, ContentSlide):
                        continue
                    page.evaluate(f"document.getElementById('go-{n + 1}').click()")
                    page.wait_for_function(f"document.querySelector('.slide.current')?.id === "
                                           f"'presentation-slide-{slide.id}'")
                    page.wait_for_timeout(50)
                    for seen in page.evaluate(OVERFLOW):
                        kinds.add(seen["kind"])
                        if seen["kind"] not in ("image", "vector") and seen["over"] > 1:
                            off.append(f"{frame[0]}x{frame[1]} {slide.id}/{seen['id']}: "
                                       f"{seen['over']} px past its slot")
                        if seen["plot"] is not None and seen["plot"] < plot_min - 1:
                            off.append(f"{frame[0]}x{frame[1]} {seen['id']}: plot "
                                       f"{seen['plot']:.0f} px, under {plot_min}")
        finally:
            browser.close()
    assert {"text", "table", "callout", "figures", "steps", "chart", "diagram"} <= kinds, kinds
    assert not off, "\n".join(off)


# Each diagram on the current slide: its own box, and each node's and
# group's box inside it, and whether any of its words is cut off.
DIAGRAM_BOXES = """() => [...document.querySelectorAll('.slide.current .presentation-diagram')].map(d => {
  const at = d.getBoundingClientRect();
  const of = e => { const r = e.getBoundingClientRect(); return [r.left - at.left, r.top - at.top, r.width, r.height]; };
  return {id: d.parentElement.dataset.object, size: [at.width, at.height],
    boxes: Object.fromEntries([...d.querySelectorAll('.presentation-diagram-node, .presentation-diagram-group')]
      .map(e => [e.dataset.member, of(e)])),
    cut: [...d.querySelectorAll('.presentation-diagram-text, .presentation-diagram-label, .presentation-diagram-when, .presentation-diagram-group-label')]
      .filter(e => e.scrollHeight > e.clientHeight + 1 || e.scrollWidth > e.clientWidth + 1).map(e => e.textContent)};
})"""


@pytest.fixture(scope="module")
def diagrams(tmp_path_factory):
    from tests.deck_fixtures import diagram_deck
    folder = tmp_path_factory.mktemp("diagrams")
    mp.make_presentation(diagram_deck(folder / "source"), folder / "out")
    package = folder / "out" / "compiled" / "diagramas"
    return package, read_source(package / "source.yaml", built=True, language="es")


def test_the_browser_draws_every_diagram_where_its_layout_put_it(diagrams, built):
    """diagram_layout places every node and group as a share of its slot;
    the browser draws each there within MODEL_PX at every frame, and every
    word of it within its place (the hard deck's flow beside words too)."""
    from playwright.sync_api import sync_playwright
    from jarvis.presentation_schema import DiagramObject
    off, checked = [], 0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/api/packages/story.js", lambda route: route.fulfill(
                body="", content_type="application/javascript"))
            for package, source in (diagrams, built):
                drawn = {o.id: o for s in source.document.slides if isinstance(s, ContentSlide)
                         for o in s.objects if isinstance(o, DiagramObject)}
                for frame in FRAMES:
                    page.set_viewport_size({"width": int(frame[0]), "height": int(frame[1])})
                    page.goto((package / "index.html").as_uri())
                    page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
                    for n, slide in enumerate(source.document.slides):
                        if not isinstance(slide, ContentSlide) or not any(
                                isinstance(o, DiagramObject) for o in slide.objects):
                            continue
                        page.evaluate(f"document.getElementById('go-{n + 1}').click()")
                        page.wait_for_function(f"document.querySelector('.slide.current')?.id === "
                                               f"'presentation-slide-{slide.id}'")
                        page.wait_for_timeout(50)
                        boxes = layout_spec.boxes(slide.layout, {o.slot for o in slide.objects})
                        for seen in page.evaluate(DIAGRAM_BOXES):
                            obj = drawn[seen["id"]]
                            w, h = layout_spec.frame_box(boxes[obj.slot], frame)
                            where = f"{frame[0]}x{frame[1]} {obj.id}"
                            if abs(seen["size"][0] - w) > MODEL_PX or abs(seen["size"][1] - h) > MODEL_PX:
                                off.append(f"{where}: drawn {seen['size']}, slot {w:.2f} x {h:.2f}")
                            for item in [*obj._drawn["nodes"], *obj._drawn["groups"]]:
                                x, y, bw, bh = item["box"]
                                model = [x * w, y * h, bw * w, bh * h]
                                got = seen["boxes"][item["id"]]
                                checked += 1
                                if any(abs(a - b) > MODEL_PX for a, b in zip(got, model)):
                                    off.append(f"{where}/{item['id']}: drawn {[round(v, 2) for v in got]}, "
                                               f"model {[round(v, 2) for v in model]}")
                            if seen["cut"]:
                                off.append(f"{where}: words cut off: {seen['cut']}")
        finally:
            browser.close()
    assert checked > len(FRAMES) * 20, checked
    assert not off, "\n".join(off)
