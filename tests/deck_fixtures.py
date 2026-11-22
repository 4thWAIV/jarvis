"""Decks for tests written the one way an author writes them now,
presentation.yaml: `presentation_of` turns a deck in the shared deck's own
data form (slides of blocks, decisions) into presentation/1, its pictures
filled in from their files, and `write_yaml` writes it with its pictures."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import yaml

from jarvis import picture_proof

# A presentation in the shared deck's data form, for the deck policy tests
# (make_package.deck_of, decision_package.expand).
PRESENTATION = {
    "title": "Team lunch moved",
    "purpose": "Tell the team where Friday's lunch is now.",
    "conclusion": "Friday's lunch is now in the office, at noon.",
    "slides": [
        {"title": "Friday's lunch has moved",
         "blocks": [{"lead": "Friday's lunch is now in the office, at noon."},
                    {"p": "Nobody has to book a table, and nothing is asked of you."}],
         "say": "This tells you where Friday's lunch is now. It is in the office, at noon, "
                "so nobody has to book a table."},
        {"title": "What changed",
         "blocks": [{"points": ["Food is brought in.", "Nobody has to book a table."]}]},
    ],
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "x"


def _paragraphs(ident: str, blocks: list[dict]) -> list[dict]:
    out = []
    for block in blocks:
        texts = [(t, "paragraph") for k in ("lead", "p", "note") if k in block for t in [block[k]]]
        texts += [(t if isinstance(t, str) else t.get("text", ""), "bullet") for t in block.get("points", [])]
        for text, role in texts:
            n = len(out) + 1
            out.append({"id": f"{ident}-{n}", "role": role,
                        "runs": [{"id": f"{ident}-{n}-r", "text": text.replace("**", "").replace("`", ""),
                                  "emphasis": "normal"}]})
    return out


def presentation_of(deck: dict, folder: Path) -> dict:
    assets: dict[str, str] = {}

    def asset(path: str) -> str:
        if path not in assets:
            raw = (folder / path).read_bytes()
            assets[path] = f"pic-{len(assets) + 1}"
            pictures.append({"id": assets[path], "path": path, "sha256": hashlib.sha256(raw).hexdigest(),
                             "source": {"reference": "evidence"},
                             **_size(picture_proof.prove_bytes(raw))})
        return assets[path]

    pictures: list[dict] = []
    slides, placed = [], set()
    for n, s in enumerate(deck.get("slides") or [], 1):
        if "decision" in s:
            placed.add(s["decision"])
            slides.append({"id": f"place-{s['decision']}", "type": "decision", "decision": s["decision"]})
            continue
        sid = s.get("id") or f"slide-{n}"
        words = f"{sid}-words"
        objects = [{"id": words, "type": "text", "slot": "words", "source": {"reference": "evidence"},
                    "paragraphs": _paragraphs(words, s.get("blocks") or []) or _paragraphs(
                        words, [{"p": s["title"]}])}]
        picture = next((b["picture"] for b in s.get("blocks") or [] if b.get("picture")), None)
        if picture:
            layout = "picture-and-words"
            objects.append({"id": f"{sid}-picture", "type": "image", "slot": "picture",
                            "source": {"reference": "evidence"}, "asset": asset(picture),
                            "alt": "A picture", "fit": "contain"})
        else:
            layout = "opening" if n == 1 else "title-and-body"
            if layout == "title-and-body":
                objects[0]["slot"] = "body"
        slide = {"id": sid, "type": "content", "title": s["title"], "layout": layout, "objects": objects,
                 "readingOrder": [o["id"] for o in objects],
                 "narration": [{"id": f"{sid}-cue", "say": s.get("say") or s["title"], "focus": [words]}],
                 "notes": ""}
        if s.get("short"):
            slide["short"] = s["short"]
        slides.append(slide)
    decisions = []
    for d in deck.get("decisions") or []:
        d = dict(d)
        options = []
        for o in d.get("options") or []:
            o = dict(o)
            o.setdefault("id", _slug(o.get("name", "")))
            if o.pop("recommended", False):
                d["recommended"] = o["id"]
            if o.get("picture"):
                o["picture"] = asset(o["picture"])
            options.append(o)
        d["options"] = options
        d.setdefault("id", _slug(d.get("question", "")))
        for field in ("picture", "compare"):
            if d.get(field):
                d[field] = asset(d[field])
        decisions.append(d)
        if d["id"] not in placed:
            slides.append({"id": f"place-{d['id']}", "type": "decision", "decision": d["id"]})
    out = {"version": "presentation/1", "id": deck.get("name") or _slug(deck.get("title", "deck")),
           "title": deck.get("title", ""), "purpose": deck.get("purpose", ""),
           "conclusion": deck.get("conclusion", ""), "language": deck.get("language", "en"),
           "requires": ["presentation-core/1"], "assets": pictures,
           "references": [{"id": "evidence", "kind": "authored-example", "locator": "A test",
                           "revision": "1", "description": "Test evidence."}],
           "slides": slides, "decisions": decisions}
    if "language" not in deck:
        out.pop("language")
    return out


def _size(picture) -> dict:
    return {"mime": picture.mime, "width": picture.width, "height": picture.height}


def write_yaml(folder: Path, deck: dict, svg: str | None = None) -> Path:
    """`deck` as folder/presentation.yaml, with pictures/plate.svg beside it."""
    from tests.test_decision_package import SVG
    (folder / "pictures").mkdir(parents=True, exist_ok=True)
    (folder / "pictures" / "plate.svg").write_text(svg or SVG)
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(presentation_of(deck, folder), sort_keys=False, allow_unicode=True))
    return source


# A content slide of JARVIS's three components, for presentation/1 sources
# that name the reference "sample-evidence" (as the street example does).
_EVIDENCE = {"reference": "sample-evidence"}
COMPONENTS_SLIDE = {
    "id": "plan", "type": "content", "title": "What the trial costs and how it runs",
    "layout": "comparison", "notes": "",
    "objects": [
        {"id": "plan-callout", "type": "callout", "slot": "first", "source": _EVIDENCE,
         "label": "Bottom line", "says": "One planted block for a year costs less than a repaving."},
        {"id": "plan-figures", "type": "figures", "slot": "second", "source": _EVIDENCE,
         "items": [{"id": "cost-figure", "value": "12 000 €", "label": "a year"},
                   {"id": "rain-figure", "value": "40 %", "label": "less runoff"}]},
        {"id": "plan-steps", "type": "steps", "slot": "third", "source": _EVIDENCE,
         "items": [{"id": "plan-measure", "title": "Measure the runoff", "says": "For one month first."},
                   {"id": "plan-plant", "title": "Plant the block"},
                   {"id": "plan-compare", "title": "Compare", "says": "Against the paved block."}]},
        {"id": "plan-words", "type": "text", "slot": "words", "source": _EVIDENCE,
         "paragraphs": [{"id": "plan-words-p", "role": "paragraph", "runs": [
             {"id": "plan-words-r", "text": "Every figure is from the street survey.", "emphasis": "normal"}]}]},
    ],
    "readingOrder": ["plan-callout", "plan-figures", "plan-steps", "plan-words"],
    "narration": [{"id": "plan-cue", "say": "It costs twelve thousand a year, measured over a month first.",
                   "focus": ["cost-figure", "plan-measure"]}],
}


# A dataset and the slides drawn from it: a bar chart of two series, a line,
# a scatter and a table, each a view (filtered, sorted, limited) of the same
# records. For sources naming "sample-evidence", as the street example does.
DATASET = {
    "id": "blocks", "source": _EVIDENCE,
    "columns": [{"id": "block", "label": "Block", "type": "text"},
                {"id": "kind", "label": "Kind", "type": "text"},
                {"id": "cost", "label": "Cost", "type": "number", "units": "k€"},
                {"id": "runoff", "label": "Runoff", "type": "number", "units": "%"},
                {"id": "trees", "label": "Trees", "type": "number"}],
    "rows": [{"id": f"b{n}", "values": {"block": block, "kind": kind, "cost": cost, "runoff": runoff,
                                         "trees": trees}}
             for n, (block, kind, cost, runoff, trees) in enumerate([
                 ("North", "planted", 12, 40, 18), ("South", "paved", 8, 85, 2),
                 ("East", "planted", 15.5, 35, 24), ("West", "mixed", 10.25, 60, 9),
                 ("Centre", "paved", 9, 90, 0)], 1)],
}


def _chart(ident: str, slot: str, kind: str, x: str, y: list[str], **view) -> dict:
    return {"id": ident, "type": "chart", "slot": slot, "source": _EVIDENCE, "kind": kind,
            "data": {"dataset": "blocks", **view}, "x": x, "y": y, "alt": f"A {kind} chart of the blocks"}


def _content(ident: str, title: str, layout: str, objects: list[dict], focus: list[str]) -> dict:
    return {"id": ident, "type": "content", "title": title, "layout": layout, "notes": "",
            "objects": objects, "readingOrder": [o["id"] for o in objects],
            "narration": [{"id": f"{ident}-cue", "say": title, "focus": focus}]}


DATA_SLIDES = [
    _content("costs", "What each block costs and keeps", "title-and-body",
             [_chart("cost-chart", "body", "bar", "block", ["cost", "runoff"])],
             ["cost-chart-b1-cost"]),
    _content("trend", "Runoff against trees", "two-pictures",
             [_chart("runoff-line", "first", "line", "block", ["runoff"], sort={"column": "trees"}),
              _chart("tree-scatter", "second", "scatter", "trees", ["runoff"])],
             ["tree-scatter-b3-runoff"]),
    _content("planted", "The planted and mixed blocks", "table",
             [{"id": "block-table", "type": "table", "slot": "table", "source": _EVIDENCE,
               "alt": "The blocks that are not paved", "rowHeads": True,
               "data": {"dataset": "blocks", "where": [{"column": "kind", "one_of": ["planted", "mixed"]}],
                        "sort": {"column": "cost", "order": "descending"}, "limit": 2},
               "columns": ["block", "cost", "runoff"]}],
             ["block-table-b3-cost"]),
]


def with_data(data: dict) -> None:
    """The street example (or any source naming sample-evidence) with the
    dataset and its slides after its first content slide."""
    data["datasets"] = [json.loads(json.dumps(DATASET))]
    data["slides"][2:2] = json.loads(json.dumps(DATA_SLIDES))


# The hard fixture deck, in Spanish: long words, bullets, a literal table and
# one from data, two pictures, four columns, the components, two charts and a
# decision with pictured options. The CI page test draws it at frames across
# the range and holds the browser to the proof's model; the release check
# builds it with the rest of the corpus.
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
        "datasets": [{"id": "manzanas", "source": {"reference": "evidence"},
                      "columns": [{"id": "manzana", "label": "Manzana", "type": "text"},
                                  {"id": "coste", "label": "Coste", "type": "number", "units": "k€"},
                                  {"id": "escorrentia", "label": "Escorrentía", "type": "number", "units": "%"},
                                  {"id": "arboles", "label": "Árboles", "type": "number"}],
                      "rows": [{"id": f"m{n}", "values": {"manzana": name, "coste": cost,
                                                          "escorrentia": runoff, "arboles": trees}}
                               for n, (name, cost, runoff, trees) in enumerate([
                                   ("Norte", 12000.5, 40, 18), ("Sur", 8, 85, 2), ("Este", 15.5, 35, 24),
                                   ("Oeste", 10.25, 60, 9)], 1)]}],
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
                    "label": "Conclusión", "says": "La ruta plantada cuesta menos que repavimentar."},
                   {"id": "comp-cifras", "type": "figures", "slot": "second", "source": {"reference": "evidence"},
                    "items": [{"id": "cifra-coste", "value": "12 000 €", "label": "al año"},
                              {"id": "cifra-lluvia", "value": "40 %", "label": "menos agua"}]},
                   {"id": "comp-pasos", "type": "steps", "slot": "third", "source": {"reference": "evidence"},
                    "items": [{"id": "paso-medir", "title": "Medir", "says": "Un mes antes."},
                              {"id": "paso-plantar", "title": "Plantar la manzana interdisciplinaria"}]}),
            _slide("datos", "Costes y escorrentía por manzana", "two-pictures",
                   {"id": "datos-barras", "type": "chart", "slot": "first", "kind": "bar",
                    "source": {"reference": "evidence"}, "alt": "Coste y escorrentía",
                    "data": {"dataset": "manzanas"}, "x": "manzana", "y": ["coste", "escorrentia"]},
                   {"id": "datos-puntos", "type": "chart", "slot": "second", "kind": "scatter",
                    "source": {"reference": "evidence"}, "alt": "Escorrentía según árboles",
                    "data": {"dataset": "manzanas"}, "x": "arboles", "y": ["escorrentia"]},
                   _text("datos-texto", "words", "Cifras inventadas para la prueba.")),
            {"id": "datos-tabla", "type": "content", "title": "Las manzanas con árboles",
             "layout": "table", "notes": "",
             "objects": [{"id": "tabla-manzanas", "type": "table", "slot": "table", "rowHeads": True,
                          "source": {"reference": "evidence"}, "alt": "Manzanas",
                          "data": {"dataset": "manzanas", "where": [{"column": "arboles", "at_least": 5}],
                                   "sort": {"column": "coste", "order": "descending"}},
                          "columns": ["manzana", "coste", "escorrentia"]}],
             "readingOrder": ["tabla-manzanas"],
             "narration": [{"id": "datos-tabla-cue", "say": "Una tabla de datos.",
                            "focus": ["tabla-manzanas-m3-coste"]}]},
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
