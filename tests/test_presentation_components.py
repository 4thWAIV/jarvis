"""JARVIS's own components (callout, figures, steps): the author gives the
words, JARVIS draws them in the layout spec's sizes, each item a target
narration can light, and holds them to their slot's capacity as the source
is read (deck_capacity), with no browser."""
from __future__ import annotations

import json

import pytest

from jarvis import layout_spec, presentation_proof
from jarvis.presentation import compile_deck
from jarvis.presentation_source import DeckCompileError
from tests.deck_fixtures import COMPONENTS_SLIDE
from tests.test_presentation import change, source  # noqa: F401  (the street example, copied)


def _with_components(data, slide=COMPONENTS_SLIDE):
    data["slides"].insert(2, json.loads(json.dumps(slide)))


def test_components_compile_prove_and_light_each_item(source, tmp_path):
    change(source, _with_components)
    made = compile_deck(source, tmp_path / "compiled")
    rows = {t["member"]: t for t in json.loads(made.target_index.read_text())}
    assert rows["cost-figure"]["kind"] == "figure" and rows["cost-figure"]["accessible_name"] == "12 000 € a year"
    assert rows["plan-measure"]["kind"] == "step" and rows["plan-callout"]["kind"] == "callout"
    proof = json.loads((made.package_source / presentation_proof.PROOF_FILE).read_text())
    assert proof["proven"]
    plan = next(s for s in proof["slides"] if s["id"] == "plan")
    objects = {o["id"]: o for o in plan["objects"]}
    assert objects["plan-figures"]["style"] == "figure" and objects["plan-figures"]["items"][0] == \
        {"id": "cost-figure", "value": "12 000 €", "label": "a year"}
    assert objects["plan-steps"]["items"][0]["title"] == "Measure the runoff"
    assert all(o["capacity"]["fits"] for o in objects.values())
    deck = (made.package_source / "deck-data.js").read_text()
    assert "Measure the runoff" in deck, "the shared deck's own text carries a component's words"
    page = (made.package_source / "presentation.js").read_text()
    assert all(f"function {name}(" in page for name in ("callout", "figures", "steps", "chart"))


def test_a_component_too_long_for_its_slot_is_refused_naming_the_slot(source, tmp_path):
    def crowd(data):
        _with_components(data)
        slide = data["slides"][2]
        slide["objects"][2]["items"] = [
            {"id": f"step-{n}" if n else "plan-measure", "title": "Measure the runoff again",
             "says": "Every block, every week, for a whole season."} for n in range(8)]
    change(source, crowd)
    with pytest.raises(DeckCompileError) as caught:
        compile_deck(source, tmp_path / "compiled")
    [problem] = caught.value.diagnostics
    assert problem.code == "capacity" and problem.member == "plan-steps"
    assert problem.message.startswith("The third slot of slide plan (comparison) is over its "
                                      "capacity at 662 x 489, every word at its floor: plan-steps "
                                      "needs ")
    assert "%) over" in problem.message


def test_every_component_style_is_at_least_the_floor_and_its_css_is_the_spec_s():
    spec = layout_spec.spec()
    assert spec["styles"]["figure"]["min_px"] >= spec["text"]["min_px"]
    css = layout_spec.generated_css()
    assert ".presentation-callout {" in css and ".presentation-steps {" in css and \
        "--deck-figure:" in css and ".presentation-chart {" in css
    assert presentation_proof.prove_text() == []
    for slot in ("words", "body", "first", "second", "third", "fourth"):
        assert {"callout", "figures", "steps"} <= set(spec["accepts"][slot])
    assert "chart" in spec["accepts"]["picture"] and "chart" in spec["accepts"]["body"]
