"""The script describes each slide's visual (construction-drafts.md, Tim
2026-11-22): the outline names each slide's visual form and why; the
script's pieces are typed visuals with their content, each number found by
the program at its source and position, every slide laid out within its
slots' capacity as the script is written; the deck puts the script's
objects on its slides by program; the owners see the visual plan."""
from __future__ import annotations

import copy
import json

import yaml

from jarvis import make_package as mp, package_evidence as pe, package_script as script_
from jarvis import package_swarm as ps, role_prompts, script_visuals as sv
from jarvis.presentation_source import read_source

MEASURED = ("# Lunch survey\n"
            "Office: 9 euros a person, 7 votes\n"
            "Cafe: 14 euros a person, 4 votes\n"
            "Bistro: 22 euros a person, 2 votes\n")


def value(text, line, column="cost"):
    return {"column": column, "value": text, "source": "files/survey.md",
            "position": f"line {line}"}


def label(text):
    return {"column": "place", "value": text, "source": "", "position": ""}


def chart_piece():
    return {"kind": "chart", "chart": "bar", "text": "What a lunch costs at each place",
            "columns": [{"id": "place", "label": "Place", "type": "text", "units": ""},
                        {"id": "cost", "label": "Cost a person", "type": "number",
                         "units": "€"}],
            "x": "place", "y": ["cost"],
            "rows": [{"id": "office", "values": [label("Office"), value("9", 2)]},
                     {"id": "cafe", "values": [label("Café"), value("14", 3)]},
                     {"id": "bistro", "values": [label("Bistro"), value("22", 4)]}]}


def a_script() -> dict:
    return {"version": "script/1", "slides": [
        {"id": "costs", "title": "The office lunch costs least", "on_screen": [
            {"kind": "figures", "figures": [
                {"value": "9 €", "label": "a person, office", "source": "files/survey.md",
                 "position": "line 2"}]},
            chart_piece()],
         "narration": "The office is nine euros a person; the bistro twenty-two.",
         "facts": [{"fact": "The office costs 9 euros.", "source": "files/survey.md",
                    "position": "line 2"}],
         "answers": ["lunch"], "decision": None},
        {"id": "how", "title": "How Friday's lunch works", "on_screen": [
            {"kind": "steps", "steps": [{"title": "Order by Thursday noon", "says": ""},
                                        {"title": "Food arrives at 12:00",
                                         "says": "The team budget pays."}]},
            {"kind": "callout", "label": "Bottom line", "says": "Nobody pays at the table."}],
         "narration": "Order by Thursday noon; it arrives at twelve, paid for.",
         "facts": [], "answers": ["lunch"], "decision": None}]}


def sources(tmp_path) -> sv.Sources:
    (tmp_path / "files").mkdir(exist_ok=True)
    (tmp_path / "files" / "survey.md").write_text(MEASURED, encoding="utf-8")
    return sv.Sources(folders=(tmp_path,))


# ── the outline names each slide's visual ────────────────────────────────

def test_an_outline_without_visual_forms_is_refused():
    outline = {"version": "outline/1", "slides": [
        {"id": "costs", "title": "What lunch costs", "point": "The office costs least.",
         "answers": "lunch", "background": False, "evidence": ["files/survey.md"]}]}
    found = script_.outline_problems(outline, ["lunch"])
    assert "the outline.slides[0] has no visual." in found
    assert "the outline.slides[0] has no why." in found
    outline["slides"][0].update(visual="pie_chart", why="It compares the places.")
    assert any("visual is 'pie_chart', not one of" in p
               for p in script_.outline_problems(outline, ["lunch"]))
    outline["slides"][0]["visual"] = "bar_chart"
    assert script_.outline_problems(outline, ["lunch"]) == []
    form = script_.outline_schema(["lunch"])["properties"]["slides"]["items"]
    assert form["properties"]["visual"]["enum"] == list(sv.FORMS)
    assert {"visual", "why"} <= set(form["required"])
    # An owner may change the form by an edit; the outline must still check.
    assert [(u.field, u.text) for u in script_.outline_units(outline)][-2:] == [
        ("visual", "bar_chart"), ("why", "It compares the places.")]


def test_each_script_slide_shows_the_visual_its_outline_slide_names(tmp_path):
    outline = {"slides": [{"id": "costs", "visual": "line_chart"}, {"id": "how",
                                                                   "visual": "steps"}]}
    found = script_.problems(a_script(), ["lunch"], sources(tmp_path), outline)
    assert found == [
        "slide costs: the outline shows its point as line_chart; its on_screen has no "
        "line_chart. Give it that visual with its content, " + sv.FORM_CHANGE_WORDS]
    outline["slides"][0]["visual"] = "bar_chart"
    assert script_.problems(a_script(), ["lunch"], sources(tmp_path), outline) == []


# ── the script's visuals and their values ────────────────────────────────

def test_a_script_chart_s_values_must_be_found_at_their_sources(tmp_path):
    held = sources(tmp_path)
    assert script_.problems(a_script(), ["lunch"], held) == []
    wrong = a_script()
    chart = wrong["slides"][0]["on_screen"][1]
    chart["rows"][1]["values"][1]["value"] = "15"                 # line 3 says 14
    chart["rows"][2]["values"][1]["position"] = "the third row"   # not one the program reads
    chart["rows"][0]["values"][1]["source"] = "files/missing.md"
    wrong["slides"][0]["on_screen"][0]["figures"][0]["position"] = "line 3"   # 9 is on line 2
    found = script_.problems(wrong, ["lunch"], held)
    assert any("on_screen[0].figures[0] ('9 €'): the number is not in files/survey.md at "
               "line 3" in p for p in found)
    assert any("rows[0].values[1] ('9'): its source 'files/missing.md' cannot be read" in p
               for p in found)
    assert any("rows[1].values[1] ('15'): the number is not in files/survey.md at line 3"
               in p for p in found)
    assert any("rows[2].values[1] ('22'): its position 'the third row' is not one" in p
               for p in found)
    # A number column's value with no source at all is refused by its form.
    bare = a_script()
    bare["slides"][0]["on_screen"][1]["rows"][0]["values"][1].update(source="", position="")
    assert any("has no source path and position" in p
               for p in script_.problems(bare, ["lunch"], held))
    # A path outside the folders it reads is never read.
    assert sv.Sources(folders=(tmp_path / "files",)).text("/etc/hosts") is None


def test_numbers_are_read_as_written():
    assert [sv.number(t) for t in ("12,000", "12 000", "12.5", "12,5", "-3 %", "9 €")] == [
        12000, 12000, 12.5, 12.5, -3, 9]
    assert sv.number("between 3 and 4") is None
    assert 1234 in sv.numbers_in("cost 1.234 euros") and 9 in sv.numbers_in("9 votes")
    assert sv.span("a\nb\nc", "lines 2-3") == "b\nc" and sv.span("abcdef", "chars 1-3") == "bc"
    assert sv.span("a", "line 4") is None


def test_marking_covers_each_visual_s_words_and_data():
    fields = {(u.slide, u.field): u.text for u in script_.units(a_script())}
    assert fields[("costs", "on_screen[0].figures[0].value")] == "9 €"
    assert fields[("costs", "on_screen[1].columns[1].label")] == "Cost a person"
    assert fields[("costs", "on_screen[1].rows[2].values[1].value")] == "22"
    assert fields[("how", "on_screen[0].steps[1].says")] == "The team budget pays."
    assert fields[("how", "on_screen[1].says")] == "Nobody pays at the table."
    assert script_.slide_sources(a_script()["slides"][0]) == ["files/survey.md"] * 5
    assert "Order by Thursday noon" in script_.opening(
        {"slides": [a_script()["slides"][1]]})


def test_a_script_that_cannot_fit_is_refused_before_the_deck():
    script = a_script()
    script["slides"][1]["on_screen"] = [
        {"kind": "text", "text": "Each repair is checked on the live system first. " * 40,
         "source": "", "position": "", "path": "", "rows": []}]
    found = script_.problems(script, ["lunch"])
    assert found and all(p.startswith("slide 2 (how) does not fit a slide:") for p in found)
    assert any("only prose" in p or "over its capacity" in p for p in found)
    too_many = a_script()
    too_many["slides"][1]["on_screen"] *= 3
    assert any("more than any of JARVIS's layouts holds" in p or "does not fit" in p
               for p in script_.problems(too_many, ["lunch"]))


# ── the deck builds the script's visuals ─────────────────────────────────

def _deck_with(built: dict) -> dict:
    deck, built = copy.deepcopy(mp.VISUALS_EXAMPLE), copy.deepcopy(built)
    deck["slides"] = built["slides"]
    deck["datasets"] = built["datasets"]
    deck["assets"] = built["assets"]
    deck["references"] = [{"id": sv.SCRIPT_REFERENCE, "kind": "authored-example",
                           "locator": "The script", "revision": "1",
                           "description": "The passed script."}]
    return deck


def test_a_script_piece_maps_to_the_same_deck_object(tmp_path):
    script = a_script()
    built = sv.built_slides(script)
    costs, how = built["slides"]
    assert [o["type"] for o in costs["objects"]] == ["figures", "chart"]
    assert costs["layout"] == "picture-and-words"
    assert [o["type"] for o in how["objects"]] == ["steps", "callout"]
    chart = costs["objects"][1]
    assert chart["kind"] == "bar" and chart["data"] == {"dataset": "costs-d1"}
    rows = built["datasets"][0]["rows"]
    assert [r["values"]["costs-d1-c2"] for r in rows] == [9, 14, 22]
    # It is a deck JARVIS reads and lays out.
    folder = tmp_path / "deck"
    folder.mkdir()
    (folder / "presentation.yaml").write_text(
        yaml.safe_dump(json.loads(json.dumps(_deck_with(built))), sort_keys=False,
                       allow_unicode=True), encoding="utf-8")
    read_source(folder / "presentation.yaml")
    # The author arranges: its layout and slots are kept, its words are not.
    source = _deck_with(built)
    mine = source["slides"][1]
    mine["layout"] = "comparison"
    mine["objects"][0]["slot"], mine["objects"][1]["slot"] = "first", "second"
    mine["objects"][1]["says"] = "Pay at the table."
    mine["narration"] = [{"id": "how-cue", "say": "Here is how.", "focus": ["how-v1"]}]
    assert sv.unmatched(script, source) == [
        "Slide how's callout how-v2 is not the script's: JARVIS builds every visual from the "
        "script, and the deck only arranges its layout and slots."]
    applied = sv.apply(source, script)
    slide = applied["slides"][1]
    assert slide["layout"] == "comparison"
    assert [(o["id"], o["slot"]) for o in slide["objects"]] == [("how-v1", "first"),
                                                                ("how-v2", "second")]
    assert slide["objects"][1]["says"] == "Nobody pays at the table."
    assert slide["narration"][0]["say"] == "Here is how."
    assert sv.unmatched(script, applied) == []
    assert applied["references"][-1]["id"] == sv.SCRIPT_REFERENCE
    # The author's arrangement stands even where it does not hold (the
    # build says so); an object it gave no slot is JARVIS's arrangement.
    mine["objects"][0]["slot"] = "table"
    assert sv.apply(source, script)["slides"][1]["objects"][0]["slot"] == "table"
    del mine["objects"][1]
    assert sv.apply(source, script)["slides"][1]["layout"] == how["layout"]


def test_the_deck_step_checks_the_deck_against_the_script_s_visuals():
    from jarvis import package_deck
    script = a_script()
    built = sv.built_slides(script)
    deck = _deck_with(built)
    assert package_deck.script_problems(script, deck) == []
    deck["slides"][0]["objects"][0]["items"][0]["value"] = "8 €"
    assert any("costs-v1 is not the script's" in p
               for p in package_deck.script_problems(script, deck))
    assert package_deck.SHOW_NOTE in package_deck.BUILT_NOTE + " " + package_deck.SHOW_NOTE


# ── the owners see the visual plan ───────────────────────────────────────

def test_the_owners_see_the_visual_plan_and_the_structure_owner_owns_it(tmp_path):
    concerns = {c.id: c for c in ps.concerns()}
    assert concerns["shows_point"].owner == ps.STRUCTURE
    assert concerns["shows_point"].asks == "Does each slide show its point?"
    assert "shows_point" in ps.OUTLINE_CONCERNS and "shows_point" in ps.SCRIPT_CONCERNS
    for role in ps.OWNERS:
        assert "The outline names each slide's visual" in role_prompts.PROMPTS[role].system
    assert "also name its visual" in role_prompts.PROMPTS["author"].system
    outline = {"version": "outline/1", "slides": [
        {"id": "costs", "title": "What lunch costs", "point": "The office costs least.",
         "answers": "lunch", "background": False, "evidence": ["files/survey.md"],
         "visual": "bar_chart", "why": "Three costs side by side show the gap at once."}]}
    held = script_.OutlineFile(tmp_path / script_.OUTLINE_FILE, ["lunch"])
    assert held.take({"outline": outline}) == []
    run = ps.Run(doc={"folder": str(tmp_path)}, place=pe.Place(None, "p1"), rows=[], marked={})
    given = ps._work_text(ps.STRUCTURE, run, held, [], {})
    assert "visual: bar_chart" in given and "Three costs side by side" in given
    assert role_prompts.AUTHOR_VISUALS in ps.fill_note(run)


def test_a_script_diagram_is_a_presentation_diagram_laid_out_by_program():
    script = a_script()
    script["slides"][1]["on_screen"] = [{
        "kind": "diagram", "diagram": "flow", "text": "How Friday's lunch is ordered",
        "nodes": [{"id": n, "label": label, "shape": "rounded", "tone": "neutral", "when": ""}
                  for n, label in (("order", "Order by Thursday"), ("cook", "Kitchen cooks"),
                                   ("eat", "Lunch at noon"))],
        "links": [{"from": "order", "to": "cook", "label": "", "style": ""},
                  {"from": "cook", "to": "eat", "label": "", "style": ""}],
        "groups": []}]
    assert script_.problems(script, ["lunch"], outline={"slides": [
        {"id": "how", "visual": "flow"}]}) == []
    built = sv.built_slides(script)["slides"][1]
    [diagram] = built["objects"]
    assert diagram["type"] == "diagram" and diagram["kind"] == "flow"
    assert [n["id"] for n in diagram["nodes"]] == ["how-v1-order", "how-v1-cook", "how-v1-eat"]
    assert diagram["links"][0]["from"] == "how-v1-order"
    assert ("how", "on_screen[0].nodes[1].label") in {(u.slide, u.field)
                                                      for u in script_.units(script)}
    broken = copy.deepcopy(script)
    broken["slides"][1]["on_screen"][0]["links"][0]["to"] = "nowhere"
    assert any("does not fit a slide" in p and "nowhere" in p
               for p in script_.problems(broken, ["lunch"]))


# ── a decision slide's visuals are built and checked too ─────────────────

def a_decision_slide(*pieces) -> dict:
    return {"id": "choose", "title": "Where should Friday's lunch be?",
            "on_screen": list(pieces), "narration": "The office is cheapest; choose.",
            "facts": [], "answers": ["lunch"],
            "decision": {"id": "where", "question": "Where should Friday's lunch be?",
                         "options": [{"id": "office", "name": "Office", "says": "Cheapest."},
                                     {"id": "bistro", "name": "Bistro", "says": "Nicest."}]}}


def _with_decision(built: dict) -> dict:
    """A deck of the built content slides with the decision slide the author
    writes: its recommendation and reason, and JARVIS's words left to it."""
    deck = _deck_with(built)
    deck["slides"].append({"id": "choose", "type": "decision", "decision": "where"})
    deck["decisions"] = [{"id": "where", "question": "?", "recommended": "office",
                          "reason": "It costs least.", "text_only": "Two rooms everyone knows.",
                          "picture": "an-asset",
                          "options": [{"id": "office", "name": "-", "says": "-"},
                                      {"id": "bistro", "name": "-", "says": "-"}]}]
    return deck


def test_a_decision_slide_s_visual_is_drawn_on_the_decision_slide(tmp_path):
    from jarvis import package_deck
    script = a_script()
    script["slides"].append(a_decision_slide(
        {"kind": "text", "text": "Three places were asked.\n- Office\n- Bistro",
         "source": "", "position": "", "path": "", "rows": []}, chart_piece()))
    held = sources(tmp_path)
    # The outline's visual for it is checked like a content slide's.
    assert any(p.startswith("slide choose: the outline shows its point as line_chart")
               for p in script_.problems(script, ["lunch"], held,
                                         {"slides": [{"id": "choose", "visual": "line_chart"}]}))
    assert script_.problems(script, ["lunch"], held,
                            {"slides": [{"id": "choose", "visual": "bar_chart"}]}) == []
    # Its numbers are found at their sources like a content slide's.
    wrong = copy.deepcopy(script)
    wrong["slides"][2]["on_screen"][1]["rows"][1]["values"][1]["value"] = "15"
    assert any("slide 3 (choose) on_screen[1].rows[1].values[1] ('15')" in p
               for p in script_.problems(wrong, ["lunch"], held))
    # Too much for its decision slide is refused as the script is written.
    crowded = copy.deepcopy(script)
    crowded["slides"][2]["on_screen"].append(
        {"kind": "text", "text": "Each repair is checked on the live system first. " * 40,
         "source": "", "position": "", "path": "", "rows": []})
    assert any("(its decision slide) does not fit" in p
               for p in script_.problems(crowded, ["lunch"]))
    # One script slide is one deck slide: JARVIS builds the decision's words
    # and its visual from the script, for the decision slide itself.
    built = sv.built_slides(script)
    assert [s["id"] for s in built["slides"]] == ["costs", "how"]
    part = built["decisions"][0]
    assert part["slide"] == "choose" and part["question"] == "Where should Friday's lunch be?"
    assert part["context"] == "Three places were asked.\n- Office\n- Bistro"
    assert part["say"] == "The office is cheapest; choose."
    assert [(o["type"], o["slot"]) for o in part["objects"]] == [("chart", "shown")]
    assert [o["name"] for o in part["options"]] == ["Office", "Bistro"]
    deck = _with_decision(built)
    found = package_deck.script_problems(script, deck)
    assert any("Decision slide choose's question is not the script's" in p for p in found)
    applied = sv.apply(deck, script)
    assert [s["id"] for s in applied["slides"]] == ["costs", "how", "choose"]
    decision = applied["decisions"][0]
    assert decision["recommended"] == "office" and decision["reason"] == "It costs least."
    assert decision["text_only"] == "Two rooms everyone knows." and "picture" not in decision
    assert decision["objects"][0]["data"] == {"dataset": "choose-d1"}
    assert {d["id"] for d in applied["datasets"]} == {"costs-d1", "choose-d1"}
    assert package_deck.script_problems(script, applied) == []
    # An extra slide is not the script's: one script slide, one deck slide.
    extra = copy.deepcopy(applied)
    extra["slides"].insert(2, {**copy.deepcopy(applied["slides"][1]), "id": "choose-shown"})
    assert any("the script's are ['costs', 'how', 'choose']" in p
               for p in package_deck.script_problems(script, extra))
    # It is a deck JARVIS reads: the decision's chart laid out in its band.
    folder = tmp_path / "deck"
    folder.mkdir()
    script_.write(tmp_path / "script.yaml", script)
    (folder / "presentation.yaml").write_text(yaml.safe_dump(json.loads(json.dumps(applied)),
                                                             sort_keys=False, allow_unicode=True),
                                              encoding="utf-8")
    source = read_source(folder / "presentation.yaml")
    assert source.document.decisions[0].objects[0]._drawn["series"]
    # A decision slide of plain words is its context alone.
    plain = a_script()
    plain["slides"].append(a_decision_slide({"kind": "text", "text": "The office is cheapest.",
                                             "source": "", "path": "", "position": "",
                                             "rows": []}))
    assert "objects" not in sv.built_slides(plain)["decisions"][0]
    assert script_.problems(plain, ["lunch"], held,
                            {"slides": [{"id": "choose", "visual": "text"}]}) == []


def test_tim_s_words_on_a_decision_slide_are_drawn_on_it():
    from jarvis import package_deck
    script = a_script()
    script["slides"].append(a_decision_slide(
        {"kind": "his_words", "text": "Keep lunch cheap.", "source": "files/survey.md",
         "position": "line 1", "path": "", "rows": []}))
    script["slides"][2]["narration"] = "You said: keep lunch cheap."
    deck = sv.apply(_with_decision(sv.built_slides(script)), script)
    shown = deck["decisions"][0]["objects"][0]
    assert shown["paragraphs"][0]["runs"][0]["emphasis"] == "his-words"
    assert package_deck.script_problems(script, deck) == []
    deck["decisions"][0]["say"] = "Choose."
    assert any("Slide choose's narration does not say Tim's words" in p
               for p in package_deck.script_problems(script, deck))


def test_an_edit_of_a_decision_s_words_is_an_edit_of_its_script_slide():
    script = a_script()
    script["slides"].append(a_decision_slide(
        {"kind": "text", "text": "Three places were asked.", "source": "", "position": "",
         "path": "", "rows": []},
        {"kind": "text", "text": "The office won.", "source": "", "position": "", "path": "",
         "rows": []}))
    deck = sv.apply(_with_decision(sv.built_slides(script)), script)
    assert sv.script_edits(script, deck) == ([], [])
    asked = copy.deepcopy(deck)
    asked["decisions"][0]["question"] = "Where is Friday's lunch?"
    assert sv.script_edits(script, asked)[0][0]["field"] == "decision.question"
    said = copy.deepcopy(deck)
    said["decisions"][0]["context"] = "Three places were asked.\nThe office clearly won."
    assert sv.script_edits(script, said) == ([{
        "slide": "choose", "field": "on_screen[1].text", "start": 11, "end": 11,
        "old": "", "new": "clearly "}], [])
    named = copy.deepcopy(deck)
    named["decisions"][0]["options"][1]["says"] = "The nicest room."
    assert sv.script_edits(script, named)[0][0]["field"] == "decision.options[1].says"
    # The arrangement stays the deck's: its recommendation and reason.
    reasoned = copy.deepcopy(deck)
    reasoned["decisions"][0]["reason"] = "Nine euros a person."
    assert sv.script_edits(script, reasoned) == ([], [])
    # Another option is a change of the script slide itself: never a dead end.
    more = copy.deepcopy(deck)
    more["decisions"][0]["options"].append({"id": "cafe", "name": "Cafe", "says": "Near."})
    assert sv.SCRIPT_ITEM in sv.script_edits(script, more)[1][0]


def test_a_decision_s_short_name_is_its_script_slide_s_title():
    # Its name in the border's Chapters and on the answers slide comes from
    # the script by program, never the author.
    script = a_script()
    script["slides"].append(a_decision_slide(
        {"kind": "text", "text": "The office is cheapest.", "source": "", "position": "",
         "path": "", "rows": []}))
    part = sv.built_slides(script)["decisions"][0]
    assert part["short"] == "Where should Friday's lunch be?"
    theirs = _with_decision(sv.built_slides(script))
    theirs["decisions"][0]["short"] = "Lunch"
    deck = sv.apply(theirs, script)
    assert deck["decisions"][0]["short"] == "Where should Friday's lunch be?"
    from jarvis import package_deck
    assert package_deck.script_problems(script, deck) == []
    renamed = copy.deepcopy(deck)
    renamed["decisions"][0]["short"] = "Lunch"
    assert any("Decision slide choose's short is not the script's" in p
               for p in sv.unmatched(script, renamed))
    # An owner's edit of it is the same edit of the script slide's title.
    titled = copy.deepcopy(deck)
    titled["decisions"][0]["short"] = "Where should lunch be?"
    [edit], why = sv.script_edits(script, titled)
    assert not why and edit["field"] == "title" and edit["new"] == "Where should lunch be?"


def test_the_script_sizes_a_decision_slide_as_the_deck_will_draw_it(tmp_path):
    # Its lead-in is decision k of n among the script's decisions; before
    # the deck its options are sized pictured and not, the worse kept; at
    # the deck with the author's own recommendation, reason and pictures.
    script = a_script()
    first = a_decision_slide({"kind": "text", "text": "Two places were asked.", "source": "",
                              "position": "", "path": "", "rows": []})
    second = copy.deepcopy(first)
    second["id"] = "choose-day"
    second["decision"] = {"id": "day", "question": "Which day?", "options": [
        {"id": "friday", "name": "Friday", "says": "As now."},
        {"id": "thursday", "name": "Thursday", "says": "A day earlier."}]}
    script["slides"] += [first, second]
    sized: list = []
    real = sv.deck_capacity.decision_problems

    def seen(model, index, decisions, slide_id):
        sized.append((slide_id, index, len(decisions), model.reason,
                      any(o.picture for o in model.options)))
        return real(model, index, decisions, slide_id)
    import pytest
    with pytest.MonkeyPatch.context() as mp_:
        mp_.setattr(sv.deck_capacity, "decision_problems", seen)
        assert script_.problems(script, ["lunch"]) == []
        assert ("choose-day", 1, 2, sv.STAND_IN_REASON, True) in sized
        assert ("choose-day", 1, 2, sv.STAND_IN_REASON, False) in sized
        sized.clear()
        deck = {"decisions": [{"id": "where", "recommended": "office",
                               "reason": "It costs least of the two.", "text_only": "Rooms.",
                               "options": [{"id": "office"}, {"id": "bistro"}]}]}
        assert script_.problems(script, ["lunch"], deck=deck) == []
        assert ("choose", 0, 2, "It costs least of the two.", False) in sized
        assert not [x for x in sized if x[0] == "choose" and x[4]]
    # A reason too long for the slide is refused at the deck's script check.
    deck["decisions"][0]["reason"] = "Where we eat on Friday matters to the whole team. " * 40
    assert any("slide 3 (choose) (its decision slide) does not fit" in p and "`reason`" in p
               for p in script_.problems(script, ["lunch"], deck=deck))


# ── a deck owner's edit of a built word is an edit of the script ─────────

def test_an_edit_of_a_built_word_is_the_same_edit_of_the_script():
    script = a_script()
    deck = _deck_with(sv.built_slides(script))
    assert sv.script_edits(script, deck) == ([], [])
    said = copy.deepcopy(deck)
    said["slides"][1]["objects"][1]["says"] = "Nobody pays at the till."
    assert sv.script_edits(script, said) == ([{
        "slide": "how", "field": "on_screen[1].says", "start": 0, "end": 25,
        "old": "Nobody pays at the table.", "new": "Nobody pays at the till."}], [])
    counted = copy.deepcopy(deck)
    counted["datasets"][0]["rows"][1]["values"]["costs-d1-c2"] = 15.0
    assert sv.script_edits(script, counted)[0] == [{
        "slide": "costs", "field": "on_screen[1].rows[1].values[1].value", "start": 0,
        "end": 2, "old": "14", "new": "15"}]
    titled = copy.deepcopy(deck)
    titled["slides"][0]["title"] = "Lunch at the office costs least"
    assert sv.script_edits(script, titled)[0][0]["field"] == "title"
    # The arrangement stays the deck's.
    arranged = copy.deepcopy(deck)
    arranged["slides"][1]["layout"] = "comparison"
    for obj, slot in zip(arranged["slides"][1]["objects"], ("first", "second")):
        obj["slot"] = slot
    arranged["slides"][1]["narration"][0]["say"] = "Here is how it works."
    assert sv.script_edits(script, arranged) == ([], [])
    # A part added or removed, or what JARVIS sets, is not an edit of it.
    fewer = copy.deepcopy(deck)
    fewer["slides"][1]["objects"][0]["items"].pop()
    assert sv.SCRIPT_ITEM in sv.script_edits(script, fewer)[1][0]
    retyped = copy.deepcopy(deck)
    retyped["slides"][1]["objects"][1]["label"] = "Bottom line, again"
    retyped["slides"][1]["objects"][0]["items"][0]["id"] = "how-v1-first"
    assert any("JARVIS sets it from the script" in w for w in sv.script_edits(script, retyped)[1])


def test_the_deck_source_makes_a_built_word_s_edit_in_the_script(tmp_path):
    from jarvis import package_deck
    held = sources(tmp_path)
    assert held
    run = ps.Run(doc={"folder": str(tmp_path), "request": {"questions": [{"id": "lunch"}]}},
                 place=pe.Place(None, "p1"), rows=[], marked={})
    script_.write(run.script, a_script())
    package_deck.source_of(run).parent.mkdir(parents=True)
    package_deck._write_source(run, json.loads(json.dumps(_deck_with(
        sv.built_slides(a_script())))))
    builds = []

    def build():
        package_deck.from_script(run)
        builds.append(1)
        return object()
    source = package_deck.DeckSource(run, build)
    text = source.text()
    old = "Nobody pays at the table."
    at = text.index(old)
    after, applied, refused = source.edit([{"start": at, "end": at + len(old), "old": old,
                                            "new": "Nobody pays at the till.", "reason": "r",
                                            "source": ""}])
    assert refused == [] and applied[0]["in"] == "script.yaml"
    assert script_.read(run.script)["slides"][1]["on_screen"][1]["says"] == \
        "Nobody pays at the till."
    # The deck step works from the script as it stands: its plan re-read,
    # only the slide whose script changed is written again.
    plan = package_deck.Plan(run, a_script())
    plan.parts = {"costs": {"slide": {}}, "how": {"slide": {}}}
    was = {sid: u.key for sid, u in plan.source_units.items()}
    assert plan.refresh() == ["how"] and list(plan.parts) == ["costs"]
    assert plan.source_units["costs"].key == was["costs"] != plan.source_units["how"].key
    assert "Nobody pays at the till." in after and old not in after
    # A number its source does not hold is not put in the script, so it is
    # not applied.
    text = source.text()
    at = text.index("14.0") if "14.0" in text else text.index(": 14")
    old = text[at:at + 4]
    _, applied, refused = source.edit([{"start": at, "end": at + 4, "old": old,
                                        "new": old.replace("14", "15"), "reason": "r",
                                        "source": ""}])
    assert applied == [] and "not applied" in refused[0]["why"]
    assert script_.read(run.script)["slides"][0]["on_screen"][1]["rows"][1]["values"][1][
        "value"] == "14"
    # An edit of the arrangement stays the deck's.
    text = source.text()
    old = "Order by Thursday noon; it arrives at twelve, paid for."
    at = text.index(old)
    after, applied, refused = source.edit([{"start": at, "end": at + len(old), "old": old,
                                            "new": "Here is how it works.", "reason": "r",
                                            "source": ""}])
    assert refused == [] and "in" not in applied[0]
    assert "Here is how it works." in after
    assert script_.read(run.script)["slides"][1]["narration"] == old


# ── a slide's visual form revised after the outline is signed off ────────

def test_a_slide_s_form_can_change_after_the_outline_is_signed_off(tmp_path):
    held = sources(tmp_path)
    outline_path = tmp_path / script_.OUTLINE_FILE
    outline = {"version": "outline/1", "slides": [
        {"id": s, "title": "T", "point": "P", "answers": "lunch", "background": False,
         "evidence": ["files/survey.md"], "visual": v, "why": "W"}
        for s, v in (("costs", "line_chart"), ("how", "steps"))]}
    script_.write(outline_path, outline)
    work = script_.ScriptFile(tmp_path / script_.FILE, ["lunch"], held, outline_path)
    assert "forms" in work.form
    found = work.take({"script": a_script(), "forms": []})
    assert found and sv.FORM_CHANGE_WORDS in found[0]
    broken = a_script()
    broken["slides"][1]["on_screen"] = []
    assert work.take({"script": broken, "forms": [
        {"slide": "costs", "visual": "bar_chart", "why": "Three costs side by side."}]})
    assert script_.read(outline_path)["slides"][0]["visual"] == "line_chart"
    assert work.take({"script": a_script(), "forms": [
        {"slide": "costs", "visual": "bar_chart", "why": "Three costs side by side."}]}) == []
    assert script_.read(outline_path)["slides"][0]["visual"] == "bar_chart"
    assert work.form_changes == [{"slide": "costs", "was": "line_chart", "visual": "bar_chart",
                                  "why": "Three costs side by side."}]

    # The structure owner signs off the new form: its concern is open again.
    from jarvis import sessions

    class Swarm:
        concerns = {c.id: c for c in ps.concerns()}
        signoffs: list = []
        said: list = []

        def say(self, who, text):
            self.said.append((who, text))
    swarm = Swarm()
    ps.forms_changed(swarm, work.form_changes, "close-1")
    assert "line_chart is now bar_chart" in swarm.said[0][1]
    assert swarm.signoffs[-1].concern == "shows_point"
    assert swarm.signoffs[-1].state == sessions.NOT_YET
    assert swarm.signoffs[-1].owner == ps.STRUCTURE
    items = [{"id": 1, "what": "x", "raised_by": [ps.CONTEXT], "concerns": ["background"],
              "fixes": [], "evidence_missing": []}]
    ps.form_item(items, work.form_changes)
    assert items[-1]["raised_by"] == [ps.STRUCTURE] and items[-1]["id"] == 2
    assert "forms" in ps.close_note(ps.Run(doc={"folder": str(tmp_path)},
                                           place=pe.Place(None, "p1"), rows=[], marked={}), [])


# ── a picture is a file that is there; a chart's sourced words are found ─

def _png() -> bytes:
    import struct
    import zlib

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff))
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * 4 for _ in range(4))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def test_a_picture_piece_is_a_file_jarvis_reads_and_draws(tmp_path):
    held = sources(tmp_path)
    script = a_script()
    picture = {"kind": "picture", "text": "The office kitchen", "source": "", "position": "",
               "path": "files/kitchen.png", "rows": []}
    script["slides"][1]["on_screen"] = [script["slides"][1]["on_screen"][0], picture]
    assert any("a picture ('files/kitchen.png'): there is no such file" in p
               for p in script_.problems(script, ["lunch"], held))
    (tmp_path / "files" / "kitchen.png").write_bytes(b"not a picture")
    assert any("cannot be drawn: it is not a PNG" in p
               for p in script_.problems(script, ["lunch"], held))
    (tmp_path / "files" / "kitchen.png").write_bytes(_png())
    assert script_.problems(script, ["lunch"], held) == []


def test_a_chart_s_words_with_a_source_are_found_at_their_position(tmp_path):
    held = sources(tmp_path)
    script = a_script()
    rows = script["slides"][0]["on_screen"][1]["rows"]
    rows[0]["values"][0].update(source="files/survey.md", position="line 2")
    assert script_.problems(script, ["lunch"], held) == []
    rows[2]["values"][0].update(source="files/survey.md", position="line 2")
    assert any("rows[2].values[0] ('Bistro'): the words are not in files/survey.md at line 2"
               in p for p in script_.problems(script, ["lunch"], held))
