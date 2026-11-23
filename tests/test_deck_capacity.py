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


# ── a decision's slide and the answers slide ─────────────────────────────

def _with_decisions(decisions):
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["decisions"] = decisions
    deck["slides"] = deck["slides"][:2] + [{"id": f"ask-{d['id']}", "type": "decision",
                                            "decision": d["id"]} for d in decisions]
    return deck


def _decision(ident="where-to-eat", **fields):
    return {**copy.deepcopy(mp.EXAMPLE["decisions"][0]), "id": ident, **fields}


def test_a_decision_s_slide_is_held_to_what_the_shared_deck_draws_at_the_floor(tmp_path):
    # Worked out from the shared deck's own layout (deck.css) at 662 x 489:
    # its question, context, options and recommendation, every word at its
    # floor, in the spacing the deck falls back to there.
    assert deck_capacity.css_units(".slide.tight", "padding") == [8, 20, 6]
    read_source(_write(tmp_path / "a", _with_decisions([_decision(context=SENTENCE * 3)])))
    with pytest.raises(DeckCompileError) as refused:
        read_source(_write(tmp_path / "b", _with_decisions([_decision(context=SENTENCE * 14)])))
    said = str(refused.value)
    assert said.startswith("capacity: ") and "[decisions.0]" in said
    assert "The decision where-to-eat (slide ask-where-to-eat) is over what its slide holds at " \
           "662 x 489, every word at its floor" in said
    assert "lines of body text where it holds 27: " in said and "%) over" in said
    assert "the context (`context`) " in said and "characters less" in said


def test_each_field_of_a_decision_is_named_where_it_takes_the_room(tmp_path):
    options = [{"id": f"o{n}", "name": f"Choice {n}", "says": SENTENCE * 3} for n in range(1, 5)]
    with pytest.raises(DeckCompileError) as refused:
        read_source(_write(tmp_path, _with_decisions([_decision(
            question="Which of these four do we take? " * 4, options=options,
            recommended="o1", reason=SENTENCE * 2)])))
    said = str(refused.value)
    assert "option o1's text (`options[0].says`)" in said
    assert "Say less in option o" in said


def test_the_answers_slide_is_held_to_what_it_holds(tmp_path):
    many = [_decision(f"choice-{n}", short=f"The choice about part {n} of the plan",
                      options=[{"id": f"yes-{n}", "name": "Yes", "says": "Go ahead."},
                               {"id": f"no-{n}", "name": "No", "says": "Wait."}],
                      recommended=f"yes-{n}") for n in range(1, 31)]
    with pytest.raises(DeckCompileError, match="The answers slide is over what it holds at 662 x "
                                               "489.* its 30 decisions, a row each"):
        read_source(_write(tmp_path / "many", _with_decisions(many)))
    long = [_decision(options=[{"id": "a", "name": "A very long option name " * 4, "says": "x"},
                               {"id": "b", "name": "B", "says": "y"}], recommended="a")]
    with pytest.raises(DeckCompileError, match="the answer .* of where-to-eat takes .* px where "
                                               "the list holds"):
        read_source(_write(tmp_path / "long", _with_decisions(long)))


def test_a_decision_s_own_picture_beside_pictured_options_yields_to_them():
    # The shared deck draws a decision's own picture beside pictured
    # options, or moves it above them with its words beside it, whichever
    # leaves every option and its Choose bar whole (deck.js arrange); the
    # slide holds what either holds, and refuses what neither does.
    from jarvis.presentation_schema import Decision

    def asked(context, says, own=True):
        return Decision(**{**_decision(), "text_only": None, "context": context,
                           **({"picture": "plate"} if own else {}),
                           "options": [{"id": f"o{n}", "name": f"Choice {n}", "says": says,
                                        "picture": "plate"} for n in range(1, 4)],
                           "recommended": "o1"})

    def needs(d):
        return deck_capacity._decision_parts(deck_capacity._Deck(), d, 0, [d], {})[0]

    beside_held = asked(SENTENCE * 2, SENTENCE)
    assert deck_capacity.decision_problems(beside_held, 0, [beside_held], "ask") == []
    # Its words take the room above the options or beside them, never less
    # than without the picture's column.
    assert needs(beside_held) >= needs(asked(None, SENTENCE, own=False))
    for over in (asked(SENTENCE * 12, SENTENCE), asked(SENTENCE, SENTENCE * 5)):
        [(member, said)] = deck_capacity.decision_problems(over, 0, [over], "ask")
        assert member == "where-to-eat" and "is over what its slide holds" in said
    # A context only the column above the options holds is held to that.
    long = asked(SENTENCE * 3, SENTENCE)
    deck = deck_capacity._Deck()
    side = deck.at(deck_capacity.css_units(".decide.with-context",
                                           "grid-template-columns")[0], True)
    assert deck.lines(long.context, side) * deck.body_line > deck.height
    assert deck_capacity.decision_problems(long, 0, [long], "ask") == []


def test_a_decision_s_objects_are_held_with_its_slide_in_the_band_above_its_options(tmp_path):
    # What a decision slide shows beside its words is drawn on it, in its
    # context area: each object in its column of the band above the options,
    # sized with the slide; a diagram or chart laid out in what the rest
    # leaves.
    def steps(n):
        return {"id": "where-steps", "type": "steps", "slot": "shown",
                "source": {"reference": "lunch-plan"},
                "items": [{"id": f"step-{k}", "title": f"Step {k}", "says": SENTENCE}
                          for k in range(1, n + 1)]}
    source = read_source(_write(tmp_path / "a", _with_decisions([_decision(objects=[steps(2)])])))
    decision = source.document.decisions[0]
    box = deck_capacity.shown_box(decision, 0, source.document.decisions)
    assert box[2] > 800 and deck_capacity.holds(box)[1] >= \
        deck_capacity.BAND_MIN_LINES * deck_capacity.line_px("body") - 0.5
    with pytest.raises(DeckCompileError) as refused:
        read_source(_write(tmp_path / "b", _with_decisions([_decision(objects=[steps(8)])])))
    assert "the steps where-steps (`objects[0]`)" in str(refused.value)
    # A decision that shows objects has no picture of its own beside them.
    with pytest.raises(DeckCompileError, match="has no picture of its own"):
        read_source(_write(tmp_path / "c", _with_decisions([_decision(
            objects=[steps(1)], picture="nope")])))
    # A slot other than its context area is refused.
    with pytest.raises(DeckCompileError, match="each one's slot is 'shown'"):
        read_source(_write(tmp_path / "d", _with_decisions([_decision(
            objects=[{**steps(1), "slot": "body"}])])))
