"""The script as structured YAML (package_script.py; workflow.yaml swarm decided
"the script's format", Tim 2026-11-20): script/1's schema, given to the
author as its answer schema and checked by the program; the words read
slide by slide for marking and editing; Tim's quoted words, the sources and
slide 1 read by program. And the engine's embeddings the questions to the
sender are grouped by (knowledge serve /v1/items/embed)."""
from __future__ import annotations

import pytest

from jarvis import package_script as script_
from jarvis.knowledge import embedder as emb
from jarvis.knowledge.uds import in_process
from tests.settings_fixtures import hold_setting


def piece(kind, text="", source="", position="", path="", rows=None):
    return {"kind": kind, "text": text, "source": source, "position": position, "path": path,
            "rows": rows or []}


def a_script() -> dict:
    return {"version": "script/1", "slides": [
        {"id": "opening", "title": "The store moved", "on_screen": [
            piece("text", "Every Work item now has its own folder."),
            piece("his_words", "Keep each item in one place.", "jarvis/tim.md", "12-41"),
            piece("table", rows=[["Before", "After"], ["/work/x.work", "/work/x/x.work"]])],
         "narration": "Each item moved into its own folder, as you asked.",
         "facts": [{"fact": "Every item moved.", "source": "retrieved/pr-171.md",
                    "position": "diff"},
                   {"fact": "When it ships.", "source": "unanswered", "position": ""}],
         "answers": ["merge-171"], "decision": None},
        {"id": "decide", "title": "Merge it?", "on_screen": [
            piece("picture", "The two folder layouts.", path="pictures/layouts.png")],
         "narration": "Merge it, or hold it.", "facts": [], "answers": ["merge-171"],
         "decision": {"id": "merge-171", "question": "Merge PR 171?", "options": [
             {"id": "merge", "name": "Merge", "says": "It goes in today."},
             {"id": "hold", "name": "Hold", "says": "It waits."}]}}]}


def test_the_schema_is_the_answer_format_of_both_brains():
    schema = script_.schema(["merge-171"])

    def strict(form):
        if form.get("type") == "object":
            assert form["additionalProperties"] is False
            assert form["required"] == list(form["properties"]), "every property required"
            for sub in form["properties"].values():
                strict(sub)
        for sub in form.get("anyOf", []):
            strict(sub)
        if form.get("type") == "array":
            strict(form["items"])
    strict(schema)
    slide = schema["properties"]["slides"]["items"]["properties"]
    assert set(slide) == {"id", "title", "on_screen", "narration", "facts", "answers",
                          "decision"}
    assert slide["answers"]["items"]["enum"] == ["merge-171"]
    kinds = [k for form in slide["on_screen"]["items"]["anyOf"]
             for k in form["properties"]["kind"]["enum"]]
    assert kinds == ["text", "his_words", "picture", "table", "steps", "figures", "callout",
                     "comparison", "chart"]


def test_a_script_checks_and_every_problem_is_named():
    assert script_.problems(a_script(), ["merge-171"]) == []
    bad = a_script()
    del bad["slides"][0]["narration"]
    bad["slides"][0]["answers"] = ["what"]
    bad["slides"][1]["id"] = "Decide Now"
    bad["slides"][1]["colour"] = "red"
    found = script_.problems(bad, ["merge-171"])
    assert "the script.slides[0] has no narration." in found
    assert any("answers[0] is 'what', not one of merge-171" in p for p in found)
    assert any(p.startswith("the script.slides[1].id is 'Decide Now'") for p in found)
    assert "the script.slides[1] has colour, which script/1 does not have." in found
    words = a_script()
    words["slides"][0]["on_screen"][1]["source"] = ""
    words["slides"][1]["id"] = "opening"
    found = script_.problems(words, ["merge-171"])
    assert found == ["slide 1 (opening) on_screen[1], a his_words, has no source.",
                     "slide 2 (opening): its id is another slide's."]
    assert script_.problems("version: script/1", []) == ["the script is not a mapping."]


def test_the_words_are_read_slide_by_slide_and_his_words_are_never_edited(tmp_path):
    script = a_script()
    units = script_.units(script)
    assert [(u.slide, u.field) for u in units][:6] == [
        ("opening", "title"), ("opening", "on_screen[0].text"), ("opening", "on_screen[1].text"),
        ("opening", "on_screen[2].rows[0][0]"), ("opening", "on_screen[2].rows[0][1]"),
        ("opening", "on_screen[2].rows[1][0]")]
    assert [u.editable for u in units if u.field == "on_screen[1].text"] == [False]
    assert ("decide", "decision.options[1].says") in {(u.slide, u.field) for u in units}

    file = script_.ScriptFile(tmp_path / script_.FILE, ["merge-171"])
    assert file.take({"message": "x", "script": script}) == []
    text, applied, refused = file.edit([
        {"slide": "opening", "field": "title", "start": 4, "end": 9, "old": "store",
         "new": "record store"},
        {"slide": "opening", "field": "on_screen[1].text", "start": 0, "end": 4,
         "old": "Keep", "new": "Put"},
        {"slide": "decide", "field": "decision.options[0].says", "start": 0, "end": 2,
         "old": "It", "new": "PR 171"},
        {"slide": "nowhere", "field": "title", "start": 0, "end": 1, "old": "M", "new": "m"}])
    assert [e["new"] for e in applied] == ["record store", "PR 171"]
    assert [e.get("slide") for e in refused] == ["opening", "nowhere"]
    kept = file.read()
    assert kept["slides"][0]["title"] == "The record store moved"
    assert kept["slides"][0]["on_screen"][1]["text"] == "Keep each item in one place."
    assert kept["slides"][1]["decision"]["options"][0]["says"] == "PR 171 goes in today."
    assert "title: The record store moved" in text
    # An edit that would leave a script that does not check is not applied.
    _, applied, refused = file.edit([{"slide": "opening", "field": "title", "start": 0,
                                      "end": 22, "old": "The record store moved", "new": ""}])
    assert applied == [] and len(refused) == 1
    assert file.read()["slides"][0]["title"] == "The record store moved"


def test_a_script_not_in_the_answer_is_not_written(tmp_path):
    file = script_.ScriptFile(tmp_path / script_.FILE, ["merge-171"])
    assert file.take({"message": "only words"}) == ["The answer has no script."]
    assert not file.path.exists() and file.text() == ""


def test_his_words_the_sources_and_slide_one_are_read_by_program():
    script = a_script()
    assert script_.his_words(script) == [{"slide": "opening",
                                          "text": "Keep each item in one place.",
                                          "source": "jarvis/tim.md", "position": "12-41"}]
    assert script_.sources(script) == "retrieved/pr-171.md\njarvis/tim.md"
    assert script_.slide_sources(script["slides"][1]) == []
    assert script_.opening(script) == [
        "The store moved", "Every Work item now has its own folder.",
        "Keep each item in one place.", "Before | After", "/work/x.work | /work/x/x.work"]


def test_the_engine_embeds_texts_with_its_own_model(tmp_path, monkeypatch):
    hold_setting("knowledge_enabled", "1")
    monkeypatch.setattr(emb, "RUNNER", lambda: emb.FakeEmbedder(32))
    with in_process(tmp_path / "engine") as (_, uds):
        vectors = uds.embed(["When did CI pass?", "When did the CI pass?", "Who reviews?"])
        assert len(vectors) == 3 and all(len(v) == 32 for v in vectors)
        assert abs(sum(x * x for x in vectors[0]) - 1) < 1e-5, "normalised: dot is cosine"
        assert uds.embed([]) == []
    monkeypatch.setattr(emb, "RUNNER", lambda: None)
    from jarvis.knowledge_client import EngineUnavailable
    with in_process(tmp_path / "bare") as (_, uds):
        with pytest.raises(EngineUnavailable):
            uds.embed(["anything"])


def test_the_outline_checks_and_its_owners_edits_are_applied_where_their_old_text_is(tmp_path):
    outline = {"version": "outline/1", "slides": [
        {"id": "ask", "title": "The ask", "point": "Approve 0.85.", "answers": "q1",
         "background": False, "evidence": ["files/situation.md"], "visual": "figures",
         "why": "One number is the point."},
        {"id": "ask", "title": "Why", "point": "It was measured.", "answers": "q2",
         "background": True, "evidence": [], "visual": "line_chart",
         "why": "The measurements over time show it."}]}
    assert script_.outline_problems(outline, ["q1"]) == [
        "the outline.slides[1].answers is 'q2', not one of q1."]
    assert script_.outline_problems(outline, ["q1", "q2"]) == [
        "slide 2 (ask): its id is another slide's."]
    outline["slides"][1]["id"] = "why"
    held = script_.OutlineFile(tmp_path / script_.OUTLINE_FILE, ["q1", "q2"])
    assert held.take({"outline": outline}) == []
    assert held.form["outline"]["properties"]["version"]["enum"] == ["outline/1"]
    _, applied, refused = held.edit([
        {"slide": "ask", "field": "evidence[0]", "start": 0, "end": 18,
         "old": "files/situation.md", "new": "files/measured.md", "reason": "r", "source": ""},
        {"slide": "why", "field": "point", "start": 0, "end": 2, "old": "No", "new": "x",
         "reason": "r", "source": ""}])
    assert len(applied) == 1 and len(refused) == 1
    assert held.read()["slides"][0]["evidence"] == ["files/measured.md"]
