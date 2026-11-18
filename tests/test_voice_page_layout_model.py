"""JARVIS proves a deck's pictures without a browser
(jarvis/presentation_proof.py): the size every picture is drawn at is
computed from its own bytes (picture_proof) and the layout spec's slot
boxes. This test holds that model to the browser: a hard fixture deck (long
words, a table, two pictures, a comparison of four, a picture beside words,
a decision with picture options, in Spanish) is built as make_package builds
it and drawn in headless Chromium at frames across the whole range, and the
size the browser draws each picture is the size the proof computed, within
MODEL_PX. A change to the CSS or the renderer that the model does not follow
fails here, in CI. A slide's words are drawn in the system font stack and
are not modelled.

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from jarvis import layout_spec, make_package as mp, picture_proof
from jarvis.presentation_schema import ContentSlide, ImageObject
from jarvis.presentation_source import read_source

pytestmark = pytest.mark.page

# How far the browser's drawn size may be from the model's, in px.
MODEL_PX = 0.5

# The frames drawn: the range's corners, its middle, and where the deck unit
# switches from the frame's width to its height.
FRAMES = [*layout_spec.corners(), (1024, 700), (880, 640), (1131, 823)]

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
            _slide("imagen", "Una imagen y palabras", "picture-and-words",
                   _text("imagen-texto", "words", long, ("bullet", "Una viñeta junto a la imagen.")),
                   _image("imagen-dibujo", "picture", "ancho")),
            {"id": "decidir", "type": "decision", "decision": "elegir-ruta"},
        ],
        "decisions": [{
            "id": "elegir-ruta", "question": "¿Qué ruta probamos?",
            "options": [{"id": "plantada", "name": "La plantada", "says": "Plantas en la calle.",
                         "picture": "opcion-a"},
                        {"id": "pavimentada", "name": "La pavimentada", "says": "Como ahora.",
                         "picture": "opcion-b"}],
            "recommended": "plantada", "reason": "Retiene más lluvia."}],
    }
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(deck, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return source


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
