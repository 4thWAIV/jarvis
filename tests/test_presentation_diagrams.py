"""Diagrams (construction-drafts.md "deck/0.1 families: diagrams, shapes,
groups, connectors"): the author names a kind (flow, cycle, hierarchy,
timeline) and fills its nodes, links and groups with words and choices;
JARVIS lays every one out by program (diagram_layout), holds it to its
slot's capacity as the source is read (deck_capacity), proves every word in
its place at every frame (presentation_proof) and makes every node,
connector and group a target narration can light. No browser."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
import yaml

from jarvis import deck_capacity, diagram_layout, layout_spec, presentation_proof
from jarvis.presentation import compile_deck
from jarvis.presentation_schema import DIAGRAM_KINDS, DiagramObject, NODE_SHAPES, TONES
from jarvis.presentation_source import DeckCompileError, read_source
from tests import deck_fixtures

BODY = layout_spec.spec()["layouts"]["title-and-body"]["slots"]["body"]
PICTURE = layout_spec.spec()["layouts"]["picture-and-words"]["slots"]["picture"]
FIRST = layout_spec.spec()["layouts"]["two-pictures"]["slots"]["first"]


def _diagram(kind, nodes, links=(), groups=(), slot="body"):
    return {"id": "d", "type": "diagram", "slot": slot, "source": {"reference": "evidence"},
            "kind": kind, "alt": "A diagram", "nodes": list(nodes), "links": list(links),
            "groups": list(groups)}


def _n(ident, label=None, **more):
    return {"id": ident, "label": label or ident.capitalize(), **more}


def _l(ident, start, end, **more):
    return {"id": ident, "from": start, "to": end, **more}


def _object(data) -> DiagramObject:
    return DiagramObject.model_validate(data)


def _deck(tmp_path, diagram, layout="title-and-body", words=None):
    objects = ([words] if words else []) + [diagram]
    deck = {"version": "presentation/1", "id": "diagram-test", "title": "Diagrams",
            "purpose": "Test diagrams.", "conclusion": "They are drawn.", "language": "en",
            "requires": ["presentation-core/1"], "assets": [],
            "references": [{"id": "evidence", "kind": "authored-example", "locator": "A test",
                            "revision": "1", "description": "Test evidence."}],
            "slides": [{"id": "s", "type": "content", "title": "A diagram", "layout": layout,
                        "objects": objects, "readingOrder": [o["id"] for o in objects],
                        "narration": [{"id": "s-cue", "say": "Look.", "focus": [diagram["nodes"][0]["id"]]}],
                        "notes": ""}],
            "decisions": []}
    folder = tmp_path / "source"
    folder.mkdir(exist_ok=True)
    path = folder / "presentation.yaml"
    path.write_text(yaml.safe_dump(json.loads(json.dumps(deck)), sort_keys=False, allow_unicode=True))
    return path


def _refused(tmp_path, diagram, **more):
    with pytest.raises(DeckCompileError) as caught:
        read_source(_deck(tmp_path, diagram, **more))
    return caught.value.diagnostics


FLOW = _diagram("flow", [_n("ask"), _n("check", shape="diamond", tone="warn"), _n("do"), _n("stop", tone="bad")],
                [_l("l1", "ask", "check"), _l("l2", "check", "do", label="yes"),
                 _l("l3", "check", "stop", label="no", style="dashed"), _l("l4", "stop", "ask")])


def test_the_kinds_shapes_and_tones_are_the_schema_s_and_the_spec_s():
    d = diagram_layout.spec()
    assert DIAGRAM_KINDS == ("flow", "cycle", "hierarchy", "timeline")
    assert set(NODE_SHAPES) == set(d["shapes"]) and set(TONES) == set(d["tones"])
    for slot in ("body", "picture", "first", "second", "third", "fourth"):
        assert "diagram" in layout_spec.spec()["accepts"][slot]
    assert "diagram" not in layout_spec.spec()["accepts"]["words"]
    css = layout_spec.generated_css()
    assert ".presentation-diagram {" in css and '[data-tone="bad"]' in css and '[data-shape="diamond"]' in css


@pytest.mark.parametrize("diagram,member,words", [
    (_diagram("flow", [_n("a"), _n("b")], [_l("l1", "a", "c")]), "l1", "not a node"),
    (_diagram("flow", [_n("a"), _n("b")], [_l("l1", "a", "a")]), "l1", "to itself"),
    (_diagram("flow", [_n("a"), _n("b"), _n("c")], [_l("l1", "a", "b")],
              [{"id": "lane", "label": "Lane", "nodes": ["a", "b"]}]), "d", "every node is in one"),
    (_diagram("flow", [_n("a"), _n("b")], groups=[{"id": "g1", "label": "One", "nodes": ["a"]},
                                                  {"id": "g2", "label": "Two", "nodes": ["a", "b"]}]),
     "g2", "in one group"),
    (_diagram("flow", [_n("a", when="May"), _n("b")]), "a", "Only a timeline"),
    (_diagram("hierarchy", [_n("a"), _n("b"), _n("c")], [_l("l1", "a", "b")]), "d", "one root"),
    (_diagram("hierarchy", [_n("a"), _n("b"), _n("c")],
              [_l("l1", "a", "c"), _l("l2", "b", "c"), _l("l3", "a", "b")]), "c", "parents"),
    (_diagram("hierarchy", [_n("a"), _n("b")], [_l("l1", "a", "b")],
              [{"id": "g", "label": "G", "nodes": ["a"]}]), "g", "has no groups"),
    (_diagram("cycle", [_n("a"), _n("b"), _n("c")], [_l("l1", "a", "b"), _l("l2", "b", "c")]),
     "d", "its ring"),
    (_diagram("cycle", [_n("a"), _n("b"), _n("c")],
              [_l("l1", "a", "c"), _l("l2", "c", "b"), _l("l3", "b", "a")]), "d", "its ring"),
    (_diagram("timeline", [_n("a", when="May"), _n("b")]), "b", "says `when`"),
    (_diagram("timeline", [_n("a", when="May"), _n("b", when="June")], [_l("l1", "a", "b")]),
     "l1", "takes no links"),
    (_diagram("timeline", [_n("a", when="1"), _n("b", when="2"), _n("c", when="3")],
              groups=[{"id": "p", "label": "P", "nodes": ["a", "c"]}]), "p", "next to each other"),
])
def test_each_kind_s_rules_are_refused_naming_the_member(tmp_path, diagram, member, words):
    [problem] = _refused(tmp_path, diagram)
    assert problem.code == "diagram" and problem.member == member and words in problem.message


@pytest.mark.parametrize("where,key", [((), "width"), (("nodes", 0), "x"), (("nodes", 1), "fill"),
                                       (("links", 0), "points"), (("nodes", 0), "font")])
def test_an_authored_position_size_colour_or_font_is_refused(tmp_path, where, key):
    diagram = copy.deepcopy(FLOW)
    target = diagram
    for part in where:
        target = target[part]
    target[key] = 10
    [problem] = _refused(tmp_path, diagram)
    assert problem.code == "layout" and f"never gives `{key}`" in problem.message
    assert "box, rounded, pill" in problem.message and "neutral, accent" in problem.message


def test_a_duplicate_id_and_an_unknown_shape_are_refused(tmp_path):
    diagram = copy.deepcopy(FLOW)
    diagram["links"][0]["id"] = "ask"
    [problem] = _refused(tmp_path, diagram)
    assert problem.code == "reference" and "Duplicate ID" in problem.message
    diagram = copy.deepcopy(FLOW)
    diagram["nodes"][0]["shape"] = "star"
    assert _refused(tmp_path, diagram)[0].code == "schema"


KINDS = {
    "flow": (FLOW, BODY),
    "flow-down": (FLOW, PICTURE),
    "lanes": (deck_fixtures.DIAGRAM_FLOW, BODY),
    "hierarchy": (_diagram("hierarchy", [_n("root"), _n("a"), _n("b"), _n("a1"), _n("a2")],
                           [_l("h1", "root", "a"), _l("h2", "root", "b"), _l("h3", "a", "a1"),
                            _l("h4", "a", "a2", label="runs")]), BODY),
    "cycle": (_diagram("cycle", [_n("plan"), _n("do"), _n("check"), _n("act")],
                       [_l("c1", "plan", "do"), _l("c2", "do", "check", label="a month"),
                        _l("c3", "check", "act"), _l("c4", "act", "plan")]), FIRST),
    "timeline": (_diagram("timeline", [_n(f"t{k}", f"Step {k}", when=f"Day {k}") for k in range(5)],
                          groups=[{"id": "ph", "label": "First", "nodes": ["t0", "t1"]}]), BODY),
    "timeline-down": (_diagram("timeline", [_n(f"t{k}", f"Step {k}", when=f"Day {k}") for k in range(4)],
                               groups=[{"id": "ph", "label": "First", "nodes": ["t1", "t2"]}]), PICTURE),
}


@pytest.mark.parametrize("name", sorted(KINDS))
def test_the_layout_is_the_same_every_time_and_nothing_overlaps(name):
    data, box = KINDS[name]
    first = diagram_layout.layout(_object(data), box)
    assert first == diagram_layout.layout(_object(data), box)
    assert first["problems"] == [], first["problems"]
    every = [n["box"] for n in first["nodes"]] + [l["label_box"] for l in first["links"] if l.get("label_box")] + \
        [n["when_box"] for n in first["nodes"] if n.get("when_box")]
    for i, a in enumerate(every):
        assert 0 <= a[0] and 0 <= a[1] and a[0] + a[2] <= 1 + 1e-6 and a[1] + a[3] <= 1 + 1e-6
        for b in every[i + 1:]:
            assert not diagram_layout._overlap(a, b), (name, a, b)
    if first["kind"] != "timeline":
        for g in first["groups"]:
            for n in first["nodes"]:
                inside = n["id"] in g["nodes"]
                gx, gy, gw, gh = g["box"]
                x, y, w, h = n["box"]
                assert inside == (gx <= x and gy <= y and x + w <= gx + gw + 1e-6 and y + h <= gy + gh + 1e-6)
        for a, b in zip(first["groups"], first["groups"][1:]):
            assert not diagram_layout._overlap(a["box"], b["box"])
    assert len({tuple(n["box"][2:]) for n in first["nodes"]}) == 1, "every node of one diagram is one size"
    for frame in layout_spec.frames():
        assert diagram_layout.frame_problems(first, box, frame) == [], (name, frame)


def test_a_flow_is_across_in_a_wide_slot_and_down_in_a_tall_one():
    assert diagram_layout.layout(_object(FLOW), BODY)["orientation"] == "across"
    assert diagram_layout.layout(_object(FLOW), PICTURE)["orientation"] == "down"
    drawn = diagram_layout.layout(_object(FLOW), BODY)
    x = {n["id"]: n["box"][0] for n in drawn["nodes"]}
    assert x["ask"] < x["check"] < x["do"], "ranks run along the flow"
    back = next(l for l in drawn["links"] if l["id"] == "l4")
    assert len(back["points"]) == 6, "a link back is routed round the outside"


def test_a_hierarchy_puts_each_parent_over_its_children():
    drawn = diagram_layout.layout(_object(KINDS["hierarchy"][0]), BODY)
    box = {n["id"]: n["box"] for n in drawn["nodes"]}
    centre = {i: b[0] + b[2] / 2 for i, b in box.items()}
    assert abs(centre["a"] - (centre["a1"] + centre["a2"]) / 2) < 1e-6
    assert box["root"][1] < box["a"][1] < box["a1"][1]


def _short(n, chars=5):
    return [_n(f"n{k}", "x" * chars) for k in range(n)]


def _holds(kind, box):
    return diagram_layout.holds(kind, diagram_layout.room(box))


@pytest.mark.parametrize("kind,build,count_of,box", [
    ("flow", lambda n: _diagram("flow", _short(n), [_l(f"l{k}", f"n{k}", f"n{k + 1}") for k in range(n - 1)]),
     lambda held: held["ranks"], BODY),
    ("hierarchy", lambda n: _diagram("hierarchy", _short(n + 1),
                                     [_l(f"l{k}", "n0", f"n{k}") for k in range(1, n + 1)]),
     lambda held: held["leaves"], PICTURE),
    ("cycle", lambda n: _diagram("cycle", _short(n), [_l(f"l{k}", f"n{k}", f"n{(k + 1) % n}") for k in range(n)]),
     lambda held: held["nodes"], FIRST),
    ("timeline", lambda n: _diagram("timeline", [_n(f"n{k}", "x" * 6, when="x" * 6) for k in range(n)]),
     lambda held: held["nodes"], FIRST),
])
def test_what_a_slot_holds_fits_and_one_more_is_refused(tmp_path, kind, build, count_of, box):
    most = count_of(_holds(kind, box))
    assert 2 <= most <= 24
    fits = diagram_layout.layout(_object(build(most)), box)
    assert fits["problems"] == [], fits["problems"]
    over = diagram_layout.layout(_object(build(most + 1)), box)
    assert over["problems"] and f"holds {most}" in over["problems"][0]


def test_over_capacity_is_refused_as_the_source_is_read_naming_the_slot(tmp_path):
    most = _holds("cycle", BODY)["nodes"]
    n = most + 1
    diagram = _diagram("cycle", _short(n), [_l(f"l{k}", f"n{k}", f"n{(k + 1) % n}") for k in range(n)])
    [problem] = _refused(tmp_path, diagram)
    assert problem.code == "capacity" and problem.member == "d"
    assert problem.message.startswith("The diagram d on slide s does not fit its body slot: the cycle "
                                      f"has {n} nodes, its slot holds {most} round the ring")


def test_words_over_a_node_s_lines_are_refused_naming_the_node(tmp_path):
    long = "Every word here is long enough to need more lines than any node gives it in the end"
    diagram = _diagram("flow", [_n("a", long), _n("b")], [_l("l1", "a", "b")], slot="picture")
    [problem] = _refused(tmp_path, diagram, layout="picture-and-words",
                         words={"id": "w", "type": "text", "slot": "words", "source": {"reference": "evidence"},
                                "paragraphs": [{"id": "w1", "role": "paragraph",
                                                "runs": [{"id": "w1r", "text": "Words.", "emphasis": "normal"}]}]})
    assert problem.code == "capacity", problem.message
    assert "the words of node a" in problem.message and "at most 3" in problem.message


def test_the_spanish_diagram_deck_has_words_at_the_most_lines_a_node_allows(tmp_path):
    source = read_source(deck_fixtures.diagram_deck(tmp_path / "deck"), language="es")
    most = diagram_layout.spec()["label_lines_max"]
    lines = [n["lines"] for slide in source.document.slides for o in getattr(slide, "objects", [])
             if isinstance(o, DiagramObject) for n in o._drawn["nodes"]]
    assert most in lines
    shapes = {n.shape for slide in source.document.slides for o in getattr(slide, "objects", [])
              if isinstance(o, DiagramObject) for n in o.nodes}
    tones = {n.tone for slide in source.document.slides for o in getattr(slide, "objects", [])
             if isinstance(o, DiagramObject) for n in o.nodes}
    kinds = {o.kind for slide in source.document.slides for o in getattr(slide, "objects", [])
             if isinstance(o, DiagramObject)}
    assert shapes == set(NODE_SHAPES) and tones == set(TONES) and kinds == set(DIAGRAM_KINDS)


def test_the_diagram_deck_compiles_proves_and_lights_every_node_connector_and_group(tmp_path):
    made = compile_deck(deck_fixtures.diagram_deck(tmp_path / "deck"), tmp_path / "out")
    rows = {t["member"]: t for t in json.loads(made.target_index.read_text())}
    assert rows["stock"]["kind"] == "node" and rows["stock"]["accessible_name"] == "¿Hay?"
    assert rows["l-stock-almacen"]["kind"] == "connector"
    assert rows["l-stock-almacen"]["accessible_name"] == "¿Hay? to Almacén del puerto: sí"
    assert rows["carril-tienda"]["kind"] == "group" and rows["fase-plan"]["kind"] == "group"
    assert rows["f2"]["accessible_name"] == "12 feb: Diseño aprobado por el ayuntamiento"
    proof = json.loads((made.package_source / presentation_proof.PROOF_FILE).read_text())
    assert proof["proven"]
    flow = next(o for s in proof["slides"] if s["id"] == "flujo" for o in s["objects"])
    assert flow["kind"] == "flow" and flow["orientation"] == "across"
    assert {n["id"] for n in flow["nodes"]} >= {"pedido", "stock"}
    assert flow["connectors"][0]["points"] and flow["groups"][0]["box_px"]["smallest"]
    assert flow["capacity"]["fits"] and flow["capacity"]["holds"]["ranks"] >= 4
    assert flow["text_px"]["node"]["smallest"] >= layout_spec.spec()["text"]["min_px"]
    data = (made.package_source / "presentation-data.js").read_text()
    model = json.loads(data[len("window.PRESENTATION = "):-2])
    assert model["diagrams"]["flujo-pedido"]["nodes"][0]["box"]
    link = model["document"]["slides"][1]["objects"][0]["links"][0]
    assert link["from"] == "pedido", "a link keeps its author's `from`"
    page = (made.package_source / "presentation.js").read_text()
    assert "function diagram(" in page
    deck = (made.package_source / "deck-data.js").read_text()
    assert "Almacén del puerto" in deck, "the shared deck's own text carries a diagram's words"
    assert presentation_proof.reprove(made.package_source) == []


def test_a_word_out_of_its_place_at_a_frame_fails_the_proof():
    drawn = diagram_layout.layout(_object(FLOW), BODY)
    crushed = copy.deepcopy(drawn)
    crushed["nodes"][0]["box"][3] /= 4
    frame = layout_spec.corners()[0]
    assert any("node ask" in p for p in diagram_layout.frame_problems(crushed, BODY, frame))
    source = SimpleNamespace(document=SimpleNamespace(slides=[]))
    assert presentation_proof.prove_diagrams(source) == []


def test_the_capacity_report_says_what_its_slot_holds():
    room = deck_capacity.object_capacity(_object(FLOW), BODY)
    assert room["fits"] and room["kind"] == "flow" and room["nodes"] == 4
    assert room["holds"] == _holds("flow", BODY)
