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

import hashlib
from pathlib import Path

import pytest
import yaml

from jarvis import layout_spec, make_package as mp, picture_proof, presentation_proof
from jarvis.presentation_schema import ContentSlide
from jarvis.presentation_source import read_source

pytestmark = pytest.mark.page

# How far the browser's height may be from the model's, in px.
MODEL_PX = 0.2

# The frames drawn: the range's corners, its middle, and where the deck unit
# switches from the frame's width to its height.
FRAMES = [*layout_spec.corners(), (1024, 700), (880, 640), (1131, 823),
          # narrow frames, where every slide is one column (layout_spec narrow)
          (360, 640), (480, 800), (661, 900), (1200, 420)]

PICTURE = ('<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}">'
           '<rect width="{w}" height="{h}" fill="{fill}"/></svg>\n')


def _text(ident: str, slot: str, *paragraphs: tuple[str, str] | str) -> dict:
    """A text object: each paragraph plain words, or (role, words), a
    `**word**` run strong."""
    out = []
    for n, item in enumerate(paragraphs, 1):
        role, words = item if isinstance(item, tuple) else ("paragraph", item)
        runs = []
        for m, part in enumerate(words.split("**")):
            if part:
                runs.append({"id": f"{ident}-{n}-{m}", "text": part,
                             "emphasis": "strong" if m % 2 else "normal"})
        out.append({"id": f"{ident}-p{n}", "role": role, "runs": runs})
    return {"id": ident, "type": "text", "slot": slot, "source": {"reference": "evidence"},
            "paragraphs": out}


def _slide(ident: str, title: str, layout: str, *objects: dict) -> dict:
    return {"id": ident, "type": "content", "title": title, "layout": layout,
            "objects": list(objects), "readingOrder": [o["id"] for o in objects],
            "narration": [{"id": f"{ident}-cue", "say": title, "focus": [objects[0]["id"]]}],
            "notes": ""}


def _image(ident: str, slot: str, asset: str) -> dict:
    return {"id": ident, "type": "image", "slot": slot, "source": {"reference": "evidence"},
            "asset": asset, "alt": "Una imagen de prueba", "fit": "contain"}


def hard_deck(folder: Path) -> Path:
    """The hard fixture set as presentation.yaml, its pictures beside it."""
    assets = []
    for ident, (w, h, fill) in {"ancho": (640, 360, "#38bdf8"), "alto": (300, 420, "#f97316"),
                                "opcion-a": (320, 200, "#22c55e"),
                                "opcion-b": (320, 200, "#a855f7")}.items():
        raw = PICTURE.format(w=w, h=h, fill=fill).encode()
        (folder / f"{ident}.svg").write_bytes(raw)
        picture = picture_proof.prove_bytes(raw)
        assets.append({"id": ident, "path": f"{ident}.svg",
                       "sha256": hashlib.sha256(raw).hexdigest(), "mime": picture.mime,
                       "width": picture.width, "height": picture.height,
                       "source": {"reference": "evidence"}})
    long = ("La internacionalización del electroencefalograma contrarrevolucionario "
            "desproporcionadamente interdisciplinario")
    deck = {
        "version": "presentation/1", "id": "prueba-dificil", "title": "Una prueba difícil",
        "purpose": "Comprobar que el modelo de altura coincide con el navegador.",
        "conclusion": "El modelo y el navegador dan la misma altura en cada marco.",
        "language": "es", "requires": ["presentation-core/1"], "assets": assets,
        "situation": {
            "subject": "Una baraja de prueba con palabras largas, tablas y cuatro columnas.",
            "happened": "Se cambió cómo JARVIS dibuja las barajas; ahora lo calcula sin navegador.",
            "approved_before": "Usted aprobó que JARVIS calcule el diseño sin navegador.",
            "changes": "Si el modelo se aparta del navegador, esta prueba falla en CI."},
        "references": [{"id": "evidence", "kind": "authored-example", "locator": "Una prueba",
                        "revision": "1", "description": "Datos inventados para la prueba."}],
        "slides": [
            _slide("palabras", "Palabras largas y viñetas", "title-and-body",
                   _text("palabras-texto", "body",
                         ("heading", "Electroencefalografía"), long,
                         "Una frase **con negrita** en medio, y acentos: árbol, canción, pingüino.",
                         ("bullet", "Primera viñeta con desproporcionadamente muchas sílabas."),
                         ("bullet", "Segunda viñeta, más corta."),
                         ("numbered", "Un paso numerado."))),
            {"id": "tabla", "type": "content", "title": "Una tabla con cabeceras",
             "layout": "table", "notes": "",
             "objects": [{"id": "tabla-datos", "type": "table", "slot": "table",
                          "source": {"reference": "evidence"}, "alt": "Costes por ruta",
                          "head": ["Ruta", "Coste anual", "Mantenimiento"], "rowHeads": True,
                          "rows": [{"id": f"fila-{n}", "cells": cells} for n, cells in enumerate([
                              ["Plantada", "12 000", "Riego dos veces por semana"],
                              ["Pavimentada", "8 000", "Ninguno"],
                              ["Interdisciplinaria", "15 500", "Contrarrevolucionario"],
                              ["Mixta", "10 250", "Poda anual"]], 1)]},
                         _text("tabla-texto", "words", "Las cifras son inventadas.")],
             "readingOrder": ["tabla-datos", "tabla-texto"],
             "narration": [{"id": "tabla-cue", "say": "Una tabla.", "focus": ["tabla-datos"]}]},
            _slide("dos", "Dos imágenes", "two-pictures", _image("dos-a", "first", "ancho"),
                   _image("dos-b", "second", "alto"),
                   _text("dos-texto", "words", "Una ancha y una alta, " + long + ".")),
            _slide("cuatro", "Cuatro columnas", "comparison",
                   *[_text(f"col-{slot}", slot, ("heading", name), text) for slot, name, text in [
                       ("first", "Primera", "Interdisciplinar y contraproducente."),
                       ("second", "Segunda", "Corta."),
                       ("third", "Tercera", "Una columna con más palabras que las demás juntas."),
                       ("fourth", "Cuarta", "Desproporcionado.")]],
                   _text("cuatro-texto", "words", "Cuatro columnas comparadas, una frase debajo.")),
            _slide("componentes", "Componentes de JARVIS", "comparison",
                   {"id": "comp-aviso", "type": "callout", "slot": "first", "source": {"reference": "evidence"},
                    "label": "Conclusión", "says": "La ruta plantada cuesta menos que repavimentar " + long},
                   {"id": "comp-cifras", "type": "figures", "slot": "second", "source": {"reference": "evidence"},
                    "items": [{"id": "cifra-coste", "value": "12 000 €", "label": "al año"},
                              {"id": "cifra-lluvia", "value": "40 %", "label": "menos agua"}]},
                   {"id": "comp-pasos", "type": "steps", "slot": "third", "source": {"reference": "evidence"},
                    "items": [{"id": "paso-medir", "title": "Medir", "says": "Un mes antes."},
                              {"id": "paso-plantar", "title": "Plantar la manzana interdisciplinaria"}]}),
            _slide("imagen", "Una imagen y palabras", "picture-and-words",
                   _text("imagen-texto", "words", long, ("bullet", "Una viñeta junto a la imagen.")),
                   _image("imagen-dibujo", "picture", "ancho")),
            {"id": "decidir", "type": "decision", "decision": "elegir-ruta"},
        ],
        "decisions": [{
            "id": "elegir-ruta", "question": "¿Qué ruta probamos?",
            "why_you": "Usted decide el presupuesto de la prueba.",
            "evidence": ["Las dos rutas están listas."],
            "options": [{"id": "plantada", "name": "La plantada", "says": "Plantas en la calle.",
                         "picture": "opcion-a"},
                        {"id": "pavimentada", "name": "La pavimentada", "says": "Como ahora.",
                         "picture": "opcion-b"}],
            "recommended": "plantada", "reason": "Retiene más lluvia."}],
    }
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(deck, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return source


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
