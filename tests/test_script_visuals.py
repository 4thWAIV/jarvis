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
        "line_chart. Give it that visual with its content, or say in the conversation why "
        "another carries the point."]
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
