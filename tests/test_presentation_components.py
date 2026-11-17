"""JARVIS's own components (callout, figures, steps) and its narrow column:
the author gives the words, JARVIS draws them in the layout spec's sizes and
proves, without a browser, that they fit at every wide frame and that every
word fits its width in a narrow one (presentation_proof)."""
from __future__ import annotations

import json

import pytest

from jarvis import layout_spec, presentation_proof
from jarvis.presentation import compile_deck
from jarvis.presentation_source import DeckCompileError, read_source
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
    assert proof["proven"] and "plan-callout" in proof["tightest"]
    deck = (made.package_source / "deck-data.js").read_text()
    assert "Measure the runoff" in deck, "the shared deck's own text carries a component's words"


def test_a_component_too_long_for_its_slot_is_refused(source, tmp_path):
    def crowd(data):
        _with_components(data)
        slide = data["slides"][2]
        slide["objects"][2]["items"] = [
            {"id": f"step-{n}" if n else "plan-measure", "title": "Measure the runoff again"}
            for n in range(8)]
        slide["objects"][3].update(slot="fourth", type="callout", says="Short.")
        del slide["objects"][3]["paragraphs"]
    change(source, crowd)
    with pytest.raises(DeckCompileError, match="The third slot needs .* px of height"):
        compile_deck(source, tmp_path / "compiled")


def test_a_word_too_wide_for_a_narrow_frame_is_refused(source, tmp_path):
    word = "a" * 44     # fits the wide body slot at 662 px, not a narrow column 360 px wide
    change(source, lambda d: d["slides"][1]["objects"][0]["paragraphs"][0]["runs"][0].update(
        text=f"Rain {word} stays"))
    with pytest.raises(DeckCompileError, match="narrow frame 360 px wide: the word") as caught:
        compile_deck(source, tmp_path / "compiled")
    assert any(d.profile == "narrow 360" for d in caught.value.diagnostics)


def test_a_narrow_table_is_measured_as_cards(source):
    from tests.test_presentation import _with_table_and_follow_up
    change(source, _with_table_and_follow_up)
    table = next(o for s in read_source(source).document.slides for o in getattr(s, "objects", [])
                 if o.type == "table")
    width = layout_spec.narrow_slot_width(400)
    u = layout_spec.narrow_unit(400)
    cards = presentation_proof.table_cards_height(table, width, u)
    rows = presentation_proof.table_height(table, width, u)
    # A card puts each cell on its own lines under the row: taller than a row of cells.
    assert cards > rows


def test_narrow_widths_run_from_the_least_to_the_widest_frame():
    widths = layout_spec.narrow_widths()
    assert widths[0] == layout_spec.spec()["narrow"]["min_width"]
    assert widths[-1] == layout_spec.spec()["frame"]["max"][0]
    assert layout_spec.spec()["narrow"]["below"][0] - 1 in widths
    assert layout_spec.is_narrow((661, 700)) and layout_spec.is_narrow((1200, 400))
    assert not layout_spec.is_narrow((662, 489))
