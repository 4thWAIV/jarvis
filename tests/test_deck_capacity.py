"""Sizing by the language (Tim 2026-11-22, construction-drafts.md "Visuals,
the salvage, and sizing"): each layout slot's capacity is worked out by
program from the layout spec at the smallest frame (662 x 489) with every
word at its floor, for every object type (deck_capacity); content over a
slot's capacity, and a slide of only prose over one paragraph, are refused
as the source is read, naming the slot and how much over. No browser."""
from __future__ import annotations

import copy
import json

import pytest
import yaml

from jarvis import deck_capacity, layout_spec, make_package as mp, presentation_proof
from jarvis.presentation import compile_deck
from jarvis.presentation_source import DeckCompileError, read_source

SENTENCE = ("Each repair is checked on the live system before the next one starts, and what "
            "it changed is written to the item it belongs to. ")


def _write(folder, deck) -> object:
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(json.loads(json.dumps(deck)), sort_keys=False,
                                     allow_unicode=True), encoding="utf-8")
    return source


def _paragraphs(prefix, texts, role="paragraph"):
    return [{"id": f"{prefix}-{n}", "role": role,
             "runs": [{"id": f"{prefix}-{n}-w", "text": t, "emphasis": "normal"}]}
            for n, t in enumerate(texts, 1)]


def _example_with(objects, title="What changed"):
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["slides"][1].update(title=title, objects=objects,
                             readingOrder=[o["id"] for o in objects])
    deck["slides"][1]["narration"][0]["focus"] = [objects[0]["id"]]
    return deck


def _text(paragraphs, slot="body"):
    return {"id": "changes", "type": "text", "slot": slot, "source": {"reference": "lunch-plan"},
            "paragraphs": paragraphs}


def test_each_slot_s_capacity_is_worked_out_from_the_spec_at_the_smallest_frame():
    spec = layout_spec.spec()
    frame = tuple(spec["frame"]["min"])
    floor = spec["styles"]["body"]["min_px"]
    line = floor * spec["styles"]["body"]["line"]
    slots = deck_capacity.slots()
    assert set(slots) == set(spec["layouts"])
    for layout, named in slots.items():
        boxes = layout_spec.boxes(layout, set(layout_spec.slots_of(layout)))
        for slot, room in named.items():
            w, h = layout_spec.frame_box(boxes[slot], frame)
            assert room["lines"] == int(h / line)
            assert room["chars_per_line"] == int(w // (floor * spec["capacity"]["char_em"]))
    assert slots["title-and-body"]["body"]["lines"] == 22
    assert slots["title-and-body"]["body"]["chars_per_line"] == 78


def test_words_wrap_by_whole_words_and_a_long_word_breaks():
    px = 13
    per = int(100 // (px * layout_spec.spec()["capacity"]["char_em"]))     # 12 characters a line
    assert per == 12
    assert deck_capacity.lines_of("one two three", 100, px) == 2
    assert deck_capacity.lines_of("a" * 30, 100, px) == 3
    assert deck_capacity.lines_of("", 100, px) == 1


def test_text_over_its_slot_is_refused_naming_the_slot_and_how_much_over(tmp_path):
    deck = _example_with([_text(_paragraphs("long", [SENTENCE * 8] * 6))])
    with pytest.raises(DeckCompileError) as caught:
        read_source(_write(tmp_path, deck))
    found = {d.member: d for d in caught.value.diagnostics}
    over = found["changes"]
    assert over.code == "capacity"
    assert over.message.startswith("The body slot of slide what-changed (title-and-body) is over "
                                   "its capacity at 662 x 489, every word at its floor: changes "
                                   "needs ")
    assert "lines of body text where it holds 22: " in over.message and "%) over" in over.message
    assert "Split the slide in two, or show what can be shown" in over.message
    # It is only prose, too: said once for the slide.
    assert "Slide what-changed is only prose" in found["what-changed"].message


def test_a_slide_of_only_prose_over_one_paragraph_goes_back_even_within_its_slot(tmp_path):
    # Four paragraphs of three sentences: within the body slot, past one paragraph.
    deck = _example_with([_text(_paragraphs("dense", [SENTENCE * 3] * 4))])
    slide = read_source(_write(tmp_path / "a", deck), built=True).document.slides[1]
    boxes = layout_spec.boxes(slide.layout, {"body"})
    assert deck_capacity.object_capacity(slide.objects[0], boxes["body"])["fits"]
    with pytest.raises(DeckCompileError) as caught:
        read_source(_write(tmp_path / "b", deck))
    [problem] = caught.value.diagnostics
    assert problem.member == "what-changed" and problem.code == "capacity"
    one = 6 * 78
    assert problem.message.startswith("Slide what-changed is only prose, ")
    assert f"one paragraph here holds about {one}" in problem.message
    assert "Do not put long paragraphs on a slide" in problem.message
    # The same words shown as steps pass; one paragraph of prose passes.
    steps = {"id": "changes", "type": "steps", "slot": "body", "source": {"reference": "lunch-plan"},
             "items": [{"id": f"step-{n}", "title": f"Repair {n}", "says": SENTENCE.strip()}
                       for n in range(1, 5)]}
    read_source(_write(tmp_path / "c", _example_with([steps])))
    read_source(_write(tmp_path / "d", _example_with([_text(_paragraphs("one", [SENTENCE * 2]))])))


def test_a_title_over_its_place_is_refused(tmp_path):
    deck = _example_with([_text(_paragraphs("x", ["Short."]))], title="A title " * 30)
    with pytest.raises(DeckCompileError, match="The title of slide what-changed takes"):
        read_source(_write(tmp_path, deck))


def test_a_table_over_its_slot_is_refused_with_what_to_do(tmp_path):
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["slides"][1] = {
        "id": "what-changed", "type": "content", "title": "Who approves what", "layout": "table",
        "objects": [{"id": "who", "type": "table", "slot": "table", "alt": "Who approves",
                     "source": {"reference": "lunch-plan"}, "head": ["Area", "Needs"],
                     "rows": [{"id": f"who-{n}", "cells": [f"Area {n}", "Your yes, in writing."]}
                              for n in range(1, 30)]}],
        "readingOrder": ["who"],
        "narration": [{"id": "who-cue", "focus": ["who"], "say": "Who approves what."}], "notes": ""}
    with pytest.raises(DeckCompileError, match="The table slot of slide what-changed .* Show fewer "
                                               "rows"):
        read_source(_write(tmp_path, deck))


def test_capacity_is_a_rule_for_writing_a_built_deck_arriving_is_not_held_to_it_again(tmp_path):
    deck = _example_with([_text(_paragraphs("dense", [SENTENCE * 3] * 4))])
    source = _write(tmp_path, deck)
    read_source(source, built=True)
    with pytest.raises(DeckCompileError):
        read_source(source)


def test_the_layout_data_carries_each_object_s_capacity(tmp_path):
    made = compile_deck(_write(tmp_path / "s", mp.VISUALS_EXAMPLE), tmp_path / "out")
    proof = json.loads((made.package_source / presentation_proof.PROOF_FILE).read_text())
    rooms = {o["id"]: o["capacity"] for s in proof["slides"] for o in s.get("objects") or []}
    assert rooms["how-steps"] == {"holds_lines": 22, "needs_lines": rooms["how-steps"]["needs_lines"],
                                  "fits": True}
    assert set(rooms) == {"opening-callout", "how-steps", "cost-figures", "cost-chart", "vote-table",
                          "order-flow", "who-words", "who-tree", "week-cycle", "week-days"}
    assert rooms["order-flow"]["fits"] and rooms["order-flow"]["holds"]["ranks"] >= 4
