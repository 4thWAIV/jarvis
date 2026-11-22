"""The mark step (package_mark.py, evidence_structured.py, briefing_labels.py;
workflow.yaml mark): a real package's gathered evidence typed, its
structured files read by program, its prose extracted, marked with the 28
labels and scored against the handoff's questions, each fact and part
traceable to its file and position; the marked evidence indexed with the
raw files, and marked once per package."""
from __future__ import annotations

import json
import re

import pytest

from jarvis import (briefing_labels, evidence_structured as es, model_tiers,
                    package_evidence as pe, package_gather, package_jobs as pj, package_mark,
                    records, sessions)
from jarvis.knowledge.uds import Caller, Query, Stream
from tests.test_package_gather import (FakeRetrieval, _evidence, _job, _work, checkout,  # noqa: F401
                                       gh, indexing, transcript)

TIM = Caller("tim")
_ID = re.compile(r'<(part|item|question|field|label) (?:[^>]*?)(?:id|name)="([^"]+)"')


def _ids(text: str, tag: str) -> list[str]:
    return [m.group(2) for m in _ID.finditer(text) if m.group(1) == tag]


class Roles:
    """The role sessions standing in for the brain, each answering from
    what it is given: what each role was given, by role."""

    def __init__(self):
        self.given: dict[str, list[dict]] = {}

    def for_role(self, role, prompt, folder, **kwargs):
        outer = self

        class Session:
            pick = model_tiers.Pick("claude", "sonnet", 3, "test", ["sonnet"])
            last_turn = None

            async def once(self, text, timeout):
                return await self.ask(text, timeout)

            async def open(self):
                pass

            async def fresh(self):
                pass

            async def close(self):
                pass

            async def ask(self, text, timeout, answer_format=None, strict=True):
                outer.given.setdefault(role, []).append({"prompt": prompt, "text": text,
                                                         **kwargs})
                answer = getattr(outer, role)(text)
                return sessions.Reply(json.dumps(answer), answer)
        return Session()

    @staticmethod
    def extraction(text):
        body = text[text.index("<text>") + 6:text.index("</text>")]
        field = _ids(text, "field")[0]
        values = []
        for word in ("Work items", "12 tests"):
            at = body.find(word)
            if at >= 0:
                values.append({"field": field, "value": word, "confidence": 0.9,
                               "start": at, "end": at + len(word)})
        # A value not where it says: the program drops it.
        values.append({"field": field, "value": "invented", "confidence": 0.5, "start": 0,
                       "end": 8})
        return {"values": values}

    @staticmethod
    def marking(text):
        return {"parts": [{"id": i, "labels": [{"label": "evidence", "score": 0.8}]}
                          for i in _ids(text, "part")]}

    @staticmethod
    def classification(text):
        questions = _ids(text, "question") + ["background"]
        return {"items": [{"id": i, "questions": [{"question": q, "score": 0.5}
                                                  for q in questions]}
                          for i in _ids(text, "item")]}


@pytest.fixture
def roles(monkeypatch):
    found = Roles()
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", found.for_role)
    return found


def _gathered(tmp_path, checkout, monkeypatch):  # noqa: F811
    work = _work(tmp_path)
    doc = _job(tmp_path, work, _evidence(tmp_path, checkout),
               purpose="Tim decides whether PR 171 merges.")
    (tmp_path / "gone.md").unlink()
    retrieval = FakeRetrieval({"the CI run that failed yesterday": [
        {"type": "ci_run", "repo": "4thWAIV/jarvis", "id": "99881", "why": "it failed"}]})
    monkeypatch.setattr(package_gather, "RETRIEVAL", lambda d: retrieval)
    package_gather.gather(pj.get(doc["id"]))
    return doc


def test_a_real_package_s_evidence_is_typed_read_extracted_marked_and_scored(
        tmp_path, gh, checkout, transcript, monkeypatch, roles):  # noqa: F811
    # One worker: the pages made by the window alone, not shared across workers.
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 1, "codex": 1})
    doc = _gathered(tmp_path, checkout, monkeypatch)
    package_mark.mark(pj.get(doc["id"]))
    place = pe.place_of(doc)

    # type_files: each stored file's type in its manifest row.
    types = {r.stored: r.file_type for r in pe.read_manifest(place) if r.stored}
    assert types["handoff.json"] == es.DATA
    assert types["retrieved/pr-171.md"] == es.DIFF
    assert types["retrieved/ci_run-99881.md"] == es.CI_RUN
    assert types["jarvis/sender-session.md"] == es.TRANSCRIPT
    assert types["jarvis/tim.md"] == types["files/situation.md"] == es.PROSE
    assert set(types.values()) <= set(es.TYPES) | {es.NO_READER}

    shown = package_mark.marked(place)
    assert shown["labels"] == list(briefing_labels.NAMES) and len(shown["labels"]) == 28
    assert [q["id"] for q in shown["questions"]] == ["merge-171", "background"]
    by_file = {f["file"]: f["marks"] for f in shown["files"]}

    def text_of(stored):
        return (place.folder / stored).read_text(encoding="utf-8")

    # read_structured: each fact the file's own characters at its position.
    for stored, marks in by_file.items():
        text = text_of(stored)
        for fact in marks["facts"]:
            p = fact["position"]
            if stored != "handoff.json" or fact["by"] == "program":
                assert text[p["start"]:p["end"]] == fact["value"], (stored, fact)
    ci = {(f["field"], f["value"]) for f in by_file["retrieved/ci_run-99881.md"]["facts"]}
    assert ("job", "test") in ci and ("result", "failure") in ci
    assert ("failed_test", "tests/test_x.py::test_move") in ci
    pr_facts = by_file["retrieved/pr-171.md"]["facts"]
    assert ("file_changed", "x") in {(f["field"], f["value"]) for f in pr_facts}
    session = {(f["field"], f["value"]) for f in by_file["jarvis/sender-session.md"]["facts"]}
    assert ("command", "pytest tests/test_work_folders.py -q") in session

    # extract_prose: the prose parts of logs and transcripts, for that
    # type's facts; kept only where it stands verbatim, the invented one
    # dropped. A prose file is not extracted (the design names no facts for
    # one), and its marks say so.
    extracted = [f for m in by_file.values() for f in m["facts"] if f["by"] == "extraction"]
    assert extracted and all(f["value"] != "invented" for f in extracted)
    for fact in by_file["jarvis/sender-session.md"]["facts"]:
        if fact["by"] == "extraction":
            assert fact["value"] == "12 tests"      # from its final message, its prose part
    asked = [set(_ids(g["text"], "field")) for g in roles.given["extraction"]]
    assert {"command", "outcome"} in asked
    for stored in ("files/situation.md", "jarvis/tim.md", "handoff.json",
                   "retrieved/pr-171.md"):
        assert by_file[stored]["prose_file"]
        assert by_file[stored]["extraction"] == package_mark.NO_PROSE_FACTS
        assert not [f for f in by_file[stored]["facts"] if f["by"] == "extraction"]

    # mark_prose: every prose file in full, packed into pages that fit the
    # model (Mark rebuilt for speed); each whole sentence with at most its
    # top 3 labels at 0.5 or more (smart passes); each part's segment is
    # the file's own characters at its position.
    situation = by_file["files/situation.md"]["parts"]
    assert situation and all(p["kind"] == "sentence" for p in situation)
    assert all(p["scores"] == {"evidence": 0.8} for p in situation)
    for p in situation:
        assert text_of("files/situation.md")[p["position"]["start"]:p["position"]["end"]] \
            == p["segment"]
    # Only prose files are marked: Tim's record, the sender's words, docs,
    # a PR's description; never the prose parts of a log or a transcript.
    marked_files = [g["text"] for g in roles.given["marking"]]
    prose_files = sum(1 for m in by_file.values() if m["parts"])
    assert len(marked_files) < prose_files, "several files to a page"
    assert sum(g.count("<file name=") for g in marked_files) >= prose_files
    # Classified as they come: the program's facts, each marked page's
    # sentences, then extraction's facts, each its own call or more.
    assert len(marked_files) + 2 >= len(roles.given["classification"]) >= 2
    assert {s for s, m in by_file.items() if m["parts"]} == \
        {s for s, m in by_file.items() if m["prose_file"]} >= {
            "files/situation.md", "jarvis/tim.md", "handoff.json", "retrieved/pr-171.md"}
    assert not by_file["jarvis/sender-session.md"]["parts"]
    assert not by_file["retrieved/ci_run-99881.md"]["parts"]
    description = " ".join(p["segment"] for p in by_file["retrieved/pr-171.md"]["parts"])
    assert description and "diff --git" not in description
    # The handoff's prose is the sender's own words, never its questions.
    handoff = " ".join(p["segment"] for p in by_file["handoff.json"]["parts"])
    assert "Tim decides whether PR 171 merges." in handoff
    assert "Merge PR 171?" not in handoff
    # Marking is told what Tim knows; an item he probably does not know is
    # named without its words.
    given = roles.given["marking"][0]["text"]
    assert 'state="knows" source="jarvis/tim.md"' in given
    assert re.search(r'<material state="probably_does_not_know" '
                     r'source="retrieved/ci_run-99881.md"></material>', given)

    # relate_to_questions: every fact and part scored against the questions
    # and background; never told what Tim knows.
    for marks in by_file.values():
        scored = {q["item"] for q in marks["questions"]}
        expected = {f["id"] for f in marks["facts"]} | {p["id"] for p in marks["parts"]}
        assert scored == expected
        assert all(set(q["scores"]) == {"merge-171", "background"} for q in marks["questions"])
    for role in ("classification", "extraction"):
        for call in roles.given[role]:
            assert "state=" not in call["text"]

    # The tiers' roles, and the run's brain for each.
    assert set(roles.given) == {"marking", "extraction", "classification"}
    assert all(g["requester"] == "claude" for calls in roles.given.values() for g in calls)

    # Indexed with the raw files, found by a search under the evidence path.
    uds = records.uds()
    item = uds.get(TIM, place.item("mark/marked.json"), {Stream.DATA, Stream.VECTORS})
    assert item.data["state"] == "indexed" and item.vectors["complete"]
    hits = uds.search(TIM, Query(words="failed_test test_move", under=place.path))
    assert any(h.path.startswith(place.item("mark/")) for h in hits)
    assert records.get(place.record).data["marked_at"]


def test_each_page_is_classified_as_soon_as_it_is_marked(tmp_path, gh, checkout,  # noqa: F811
                                                         transcript, monkeypatch, roles):
    # package-870e45145b2d: classification began only after all marking
    # ended (marking 200 s, then classification 119 s). Now a page's
    # sentences are classified while the other pages are still marked.
    import asyncio
    import time as _time
    monkeypatch.setattr(model_tiers, "PARALLEL", {"claude": 3, "codex": 3})
    calls: list[tuple[str, float, float, str]] = []
    marked = []
    given = roles.for_role

    def timed(role, prompt, folder, **kwargs):
        session = given(role, prompt, folder, **kwargs)
        ask = session.ask

        async def slow(text, timeout, answer_format=None, strict=True):
            began = _time.monotonic()
            if role == "marking":
                marked.append(text)
                await asyncio.sleep(0.02 if len(marked) == 1 else 0.6)
            reply = await ask(text, timeout, answer_format, strict)
            calls.append((role, began, _time.monotonic(), text))
            return reply
        session.ask = slow
        return session
    monkeypatch.setattr(sessions.BrainChatSession, "for_role", timed)
    doc = _gathered(tmp_path, checkout, monkeypatch)
    package_mark.mark(pj.get(doc["id"]))
    marking = [c for c in calls if c[0] == "marking"]
    assert len(marking) >= 2
    first = min(marking, key=lambda c: c[2])
    words = [w for w in re.findall(r'<part id="[^"]+">(.*?)</part>', first[3], re.S)
             if len(w) > 20]
    assert words
    last_marked = max(m[2] for m in marking)
    classified_parts = [c for c in calls if c[0] == "classification" and c[1] < last_marked
                        and any(w in c[3] for w in words)]
    assert classified_parts, "a page classified before the last page was marked"
    shown = package_mark.marked(pe.place_of(doc))
    for entry in shown["files"]:
        marks = entry["marks"]
        assert {q["item"] for q in marks["questions"]} == \
            {f["id"] for f in marks["facts"]} | {p["id"] for p in marks["parts"]}


def test_marked_once_and_every_run_reuses_it(tmp_path, gh, checkout, transcript,  # noqa: F811
                                             monkeypatch, roles):
    doc = _gathered(tmp_path, checkout, monkeypatch)
    package_mark.mark(pj.get(doc["id"]))
    calls = sum(len(v) for v in roles.given.values())
    second = pj.new_run(doc["id"], "the sender answered")
    package_gather.gather(second)
    package_mark.mark(second)
    assert sum(len(v) for v in roles.given.values()) == calls


def test_a_session_that_cannot_answer_puts_the_run_in_error(tmp_path, gh, checkout,  # noqa: F811
                                                            transcript, monkeypatch, roles):
    doc = _gathered(tmp_path, checkout, monkeypatch)
    monkeypatch.setattr(Roles, "marking", staticmethod(lambda text: {"parts": []}))
    with pytest.raises(pj.Blocked) as blocked:
        package_mark.mark(pj.get(doc["id"]))
    assert blocked.value.step == "mark"
    assert blocked.value.reason.startswith("Marking could not answer:")
    assert not records.get(pe.place_of(doc).record).data.get("marked_at")


def test_the_run_goes_through_gather_and_mark_to_research(tmp_path, gh, checkout,  # noqa: F811
                                                          transcript, monkeypatch, roles):
    from jarvis import model_tiers, package_swarm

    def no_model(*_a, **_kw):
        raise model_tiers.NoModel("none in tier 1")
    monkeypatch.setattr(package_swarm, "PICK", no_model)    # no real model under test
    doc = _gathered(tmp_path, checkout, monkeypatch)
    assert pj.run(doc["id"]) == 1
    after = pj.get(doc["id"])
    assert after["done"] == ["gather", "mark"] and after["error"]["step"] == "research"
    assert after["error"]["reason"].startswith("No model could be found for the author")
    assert package_mark.marked(pe.place_of(doc))["files"]


def test_mark_is_the_step_after_gather():
    assert pj.STEPS[:2] == ("gather", "mark")
    assert pj.PRODUCERS["mark"] == "jarvis.package_mark:mark"


# ── the program's own reading ─────────────────────────────────────────────

def test_an_unknown_file_has_no_reader_never_a_guess():
    assert es.type_of("files/a.py", "file", b"print(1)\n") == es.NO_READER
    assert es.type_of("files/a.bin", "file", b"\xff\xfe\x00") == es.NO_READER
    assert es.type_of("files/run.log", "file", b"nothing recognised\n") == es.NO_READER
    assert es.read_structured("files/a.py", es.NO_READER, "print(1)") == []
    assert es.prose_of("files/a.py", es.NO_READER, "print(1)").text == ""


@pytest.mark.parametrize("name,text,expected", [
    ("files/out.txt", "FAILED tests/t.py::test_a - KeyError\n", es.TEST_RESULTS),
    ("files/report.xml", '<testsuite name="s"></testsuite>', es.TEST_RESULTS),
    ("given/1.md", "npm ERR! code 1\n", es.BUILD_LOG),
    ("files/change.patch", "+x\n", es.DIFF),
    ("files/data.json", '{"a": 1}', es.DATA),
    ("files/data.csv", "a,b\n1,2\n", es.DATA),
    ("files/notes.md", "# Notes\n\nThe move is ready.\n", es.PROSE),
])
def test_each_type_from_name_and_content(name, text, expected):
    assert es.type_of(name, "file", text.encode()) == expected


def test_facts_are_the_files_own_characters_at_their_positions():
    log = ("______ test_move ______\n>   assert 1 == 2\ntests/test_x.py:12: AssertionError\n"
           "==== short test summary info ====\n"
           "FAILED tests/test_x.py::test_move - AssertionError\n")
    facts = es.read_structured("files/out.txt", es.TEST_RESULTS, log)
    assert {(f.field, f.value) for f in facts} >= {
        ("failed_test", "tests/test_x.py::test_move"), ("line", "12"),
        ("error", "AssertionError"), ("file", "tests/test_x.py")}
    data = '{"jobs": [{"name": "t\\"q", "steps": [1, true]}]}'
    leaves = es.read_structured("files/d.json", es.DATA, data)
    assert [(f.field, data[f.start:f.end]) for f in leaves] == [
        ("jobs[0].name", 't\\"q'), ("jobs[0].steps[0]", "1"), ("jobs[0].steps[1]", "true")]
    for f in facts + leaves:
        assert f.value == (log if f in facts else data)[f.start:f.end]


def test_the_handoff_s_prose_maps_back_to_its_raw_position():
    raw = json.dumps({"sender_answers": {"plainly": "Line one.\nLine \"two\".", "why_now": "Now."},
                      "questions": [{"id": "q", "text": "Merge?"}]}, indent=2)
    view = es.prose_of("handoff.json", es.DATA, raw)
    assert view.text == 'Line one.\nLine "two".\n\nNow.'
    at = view.to_file(view.text.index("two"), view.text.index("two") + 3)
    assert raw[at[0]:at[1]] == "two"
    start, end = view.to_file(0, len("Line one.\nLine"))
    assert raw[start:end] == "Line one.\\nLine"


def test_a_value_over_two_pieces_keeps_both_places_in_the_file():
    raw = json.dumps({"purpose": "The move is ready.", "recommendation": "Merge it now."})
    view = es.prose_of("handoff.json", es.DATA, raw)
    start = view.text.index("ready.")
    end = view.text.index("Merge") + len("Merge")
    spans = view.spans(start, end)
    assert [raw[a:b] for a, b in spans] == ["ready.", "Merge"]
    assert view.to_file(start, end) == (spans[0][0], spans[-1][1])
    sep = view.text.index(es.SEPARATOR)
    assert view.spans(sep, sep + len(es.SEPARATOR)) == []

    row = pe.Row(id="r1", type="handoff", source="handoff.json", stored="handoff.json",
                 file_type=es.DATA)
    f = package_mark.FileMarks(row, raw, view)
    assert f.position(start, end) == {"start": spans[0][0], "end": spans[1][1],
                                      "spans": [{"start": a, "end": b} for a, b in spans]}
    f.fact("x", view.text[start:end], f.position(start, end), package_mark.BY_EXTRACTION)
    item = package_mark._fact_item(f, f.facts[0])
    assert item.source == f"handoff.json {spans[0][0]}-{spans[0][1]}, " \
                          f"{spans[1][0]}-{spans[1][1]}, x"


def test_extraction_fields_are_the_types_facts_and_none_for_prose():
    def names(file_type):
        found = package_mark.fields_for(file_type)
        return None if found is None else [f.name for f in found]
    assert names(es.TEST_RESULTS) == ["failed_test", "error", "file", "line"]
    assert names(es.BUILD_LOG) == ["build_error", "failed_step"]
    assert names(es.CI_RUN) == ["job", "result", "started", "finished"]
    assert names(es.TRANSCRIPT) == ["command", "outcome"]
    for other in (es.PROSE, es.DIFF, es.DATA):
        assert names(other) is None


def test_which_files_are_prose_files():
    assert es.is_prose_file("files/notes.md", es.PROSE, "file")
    assert es.is_prose_file("handoff.json", es.DATA, "handoff")
    assert es.is_prose_file("retrieved/pr-1.md", es.DIFF, "pr")
    assert not es.is_prose_file("retrieved/commit-a.md", es.DIFF, "commit")
    assert not es.is_prose_file("jarvis/sender-session.md", es.TRANSCRIPT, "transcript")
    assert not es.is_prose_file("files/ci.md", es.CI_RUN, "file")


def test_the_labels_are_the_28_with_their_kinds():
    assert len(briefing_labels.LABELS) == 28
    kinds = [label.kind for label in briefing_labels.LABELS]
    assert (kinds.count("keep"), kinds.count("fix"), kinds.count("remove")) == (14, 6, 8)
    assert set(briefing_labels.DEPEND_ON_HIM) <= set(briefing_labels.NAMES)
