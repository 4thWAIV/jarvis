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

from jarvis import (briefing_labels, evidence_structured as es, package_evidence as pe,
                    package_gather, package_jobs as pj, package_mark, records, sessions)
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
            async def once(self, text, timeout):
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
        labels = _ids(text, "label")
        return {"scores": [{"id": i, "scores": {n: (0.8 if n == "evidence" else 0.0)
                                                for n in labels}}
                           for i in _ids(text, "part")]}

    @staticmethod
    def classification(text):
        questions = _ids(text, "question") + ["background"]
        return {"scores": [{"item": i, "scores": {q: 0.5 for q in questions}}
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

    # extract_prose: kept only where it stands verbatim; the invented one dropped.
    extracted = [f for m in by_file.values() for f in m["facts"] if f["by"] == "extraction"]
    assert extracted and all(f["value"] != "invented" for f in extracted)
    assert any(f["value"] == "Work items" for f in by_file["files/situation.md"]["facts"])
    for fact in by_file["jarvis/sender-session.md"]["facts"]:
        if fact["by"] == "extraction":
            assert fact["value"] == "12 tests"      # from its final message, its prose part

    # mark_prose: one call a file; every part scored against all 28 labels;
    # each part's segment is the file's own characters at its position.
    situation = by_file["files/situation.md"]["parts"]
    assert situation and all(set(p["scores"]) == set(briefing_labels.NAMES) for p in situation)
    for p in situation:
        assert text_of("files/situation.md")[p["position"]["start"]:p["position"]["end"]] \
            == p["segment"]
    marked_files = [g["text"] for g in roles.given["marking"]]
    assert len(marked_files) == sum(1 for m in by_file.values() if m["parts"])
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
    monkeypatch.setattr(Roles, "marking", staticmethod(lambda text: {"scores": []}))
    with pytest.raises(pj.Blocked) as blocked:
        package_mark.mark(pj.get(doc["id"]))
    assert blocked.value.step == "mark"
    assert blocked.value.reason.startswith("Marking could not answer:")
    assert not records.get(pe.place_of(doc).record).data.get("marked_at")


def test_the_run_goes_through_gather_and_mark_to_research(tmp_path, gh, checkout,  # noqa: F811
                                                          transcript, monkeypatch, roles):
    doc = _gathered(tmp_path, checkout, monkeypatch)
    assert pj.run(doc["id"]) == 1
    after = pj.get(doc["id"])
    assert after["done"] == ["gather", "mark"] and after["error"]["step"] == "research"
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


def test_the_labels_are_the_28_with_their_kinds():
    assert len(briefing_labels.LABELS) == 28
    kinds = [label.kind for label in briefing_labels.LABELS]
    assert (kinds.count("keep"), kinds.count("fix"), kinds.count("remove")) == (14, 6, 8)
    assert set(briefing_labels.DEPEND_ON_HIM) <= set(briefing_labels.NAMES)
