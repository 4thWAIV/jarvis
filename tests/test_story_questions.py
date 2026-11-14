"""A story's own questions, answered in the package view's head, and an
architecture author's story (package_story: `questions`, `authored`,
`compose`; package_inbox: `_story_questions`, `page_vote`,
`record_story_answer`).

A declared question is an ordinary question: it is answered through the
inbox's one answer route by his click or his voice, asked only once it
applies (`asks_when`), never the standard vote and never a Work item's
review. An author's story is read strictly and composed once with the
standard vote as its one last slide; with no story the composition is
exactly the standard vote alone, as every such package before had."""
from __future__ import annotations

import json

import pytest

from jarvis import package_inbox, package_story, work_packages
from jarvis.package_story import StoryError
from tests.test_brain_page_parity import _call, wired  # noqa: F401  (the fixture)
from tests.test_package_inbox import (BROWSER, INBOX, chitauri, claims_proven, send,  # noqa: F401,E501
                                      shown, story_zip)
from tests.test_work_packages import marks  # noqa: F401  (the fixture)


def options(*ids: str) -> list[dict]:
    return [{"id": i, "name": i.replace("-", " ").title(), "says": f"Choose {i}."} for i in ids]


SURFACE = {"id": "sign-surface", "question": "Do you sign this interface surface?",
           "options": options("sign", "change-a-signature"), "recommended": "sign",
           "reason": "It matches the decision you made."}
FOLLOW = {"id": "which-signature", "question": "Which signature changes?",
          "options": options("grant-read", "warning-sink")}
AUTHORED = {
    "title": "The grant store",
    "questions": [SURFACE, FOLLOW],
    "slides": [
        {"title": "Reading grants", "beats": [{"say": "A read answers with Loaded."}]},
        {"title": "Sign it?", "waits": "sign-surface",
         "beats": [{"say": "Do you sign this surface?"}]},
        {"title": "Which one?", "waits": "which-signature",
         "asks_when": {"question": "sign-surface", "answers": ["change-a-signature"]},
         "beats": [{"say": "Which signature changes?"}]},
    ],
}


def raw(value) -> bytes:
    return json.dumps(value).encode()


# -- an author's story, read strictly ----------------------------------------------------

def test_an_authored_story_composes_once_with_the_standard_vote():
    told = package_story.parse(package_story.compose(package_story.authored(raw(AUTHORED)), "en"))
    assert [s.waits for s in told.slides] == [None, "sign-surface", "which-signature",
                                              package_inbox.UNDERSTOOD_ID]
    assert told.answers == package_inbox.STORY_ANSWERS and told.language == "en"
    assert [q.id for q in told.questions] == ["sign-surface", "which-signature"]
    again = package_story.compose(package_story.authored(raw(AUTHORED)), "en")
    assert again == package_story.compose(package_story.authored(raw(AUTHORED)), "en"), \
        "the same story composes to the same bytes"


@pytest.mark.parametrize("language", ["en", "es"])
def test_with_no_story_the_composition_is_the_standard_vote_alone(language):
    assert package_story.compose(None, language) == package_story.encode(
        package_inbox.with_understood_slide(None, language))


@pytest.mark.parametrize("language", ["en", "es"])
def test_approval_is_composed_once_just_before_the_vote(language):
    told = package_story.parse(package_story.compose(
        package_story.authored(raw({**AUTHORED, "language": language})), language, True))
    assert [s.waits for s in told.slides][-2:] == [package_inbox.APPROVAL_ID,
                                                   package_inbox.UNDERSTOOD_ID]
    assert [s.waits for s in told.slides].count(package_inbox.APPROVAL_ID) == 1


def test_an_author_never_asks_approval_itself():
    for story in ({**AUTHORED, "questions": [{**SURFACE, "id": "approve-architecture"}, FOLLOW]},
                  {**AUTHORED, "slides": [{"title": "Approve?", "waits": "approve-architecture",
                                           "beats": [{"say": "Approve?"}]}]}):
        with pytest.raises(StoryError, match="reserved"):
            package_story.authored(raw(story))


def test_a_target_is_written_as_css_strings():
    assert package_story.target('Odd"Name', member='Get\\It\'s') == \
        '[data-declaration="Odd\\"Name"] [data-member="Get\\\\It\'s"]'
    assert package_story.target("IGrantStore", part="members") == \
        '[data-declaration="IGrantStore"] [data-part="members"]'
    with pytest.raises(StoryError):
        package_story.target("two\nlines")


@pytest.mark.parametrize("bad, said", [
    (b'{"title": "x", "title": "y", "slides": []}', "twice in one object"),
    (raw({**AUTHORED, "theme": "dark"}), "The authored story has a field the story format does not have"),
    (raw({**AUTHORED, "answers": "results/other.json"}), "the factory sets it"),
    (raw({**AUTHORED, "slides": [{**AUTHORED["slides"][0], "colour": "red"}]}),
     "Slide 1 has a field"),
    (raw({**AUTHORED, "slides": [{"title": "t", "beats": [{"say": "s", "speed": 2}]}]}),
     "Slide 1, beat 1 has a field"),
    (raw({**AUTHORED, "questions": [{**SURFACE, "weight": 1}, FOLLOW]}),
     "Question 1 has a field"),
    (raw({**AUTHORED, "questions": [{**SURFACE, "options": [
        {"id": "sign", "name": "Sign", "says": "s", "colour": "x"}]}, FOLLOW]}),
     "option 1 has a field"),
    (raw({**AUTHORED, "slides": [{"title": "Vote", "waits": "understood",
                                  "beats": [{"say": "Got it?"}]}]}), "reserved question"),
    (raw({**AUTHORED, "slides": [{"title": "Page's own", "waits": "elsewhere",
                                  "beats": [{"say": "?"}]}]}), "does not declare"),
    (b"\xff\xfe", "not valid UTF-8 JSON"),
    (b"[]", "not an object"),
    (b" " * (package_story.AUTHORED_MAX_BYTES + 1), "over 128 KB"),
])
def test_an_authored_story_is_read_strictly(bad, said):
    with pytest.raises(StoryError, match=said):
        package_story.authored(bad)


@pytest.mark.parametrize("questions, said", [
    ([{**SURFACE, "id": "understood"}, FOLLOW], "reserved"),
    ([{**SURFACE, "id": "approve-item"}, FOLLOW], "reserved"),
    ([SURFACE, FOLLOW, FOLLOW], "same id"),
    ([{**SURFACE, "options": options("sign")}, FOLLOW], "at least two options"),
    ([{**SURFACE, "options": options("sign", "sign")}, FOLLOW], "two options with one id"),
    ([{**SURFACE, "options": options("sign", "later")}, FOLLOW], "may not be later"),
    ([{**SURFACE, "recommended": "maybe"}, FOLLOW], "recommended is not one of its options"),
    ([{**SURFACE, "question": "x" * 121}, FOLLOW], "longer than 120"),
    ([SURFACE, FOLLOW, {**FOLLOW, "id": "nobody-asks"}], "no slide waits for it"),
])
def test_a_malformed_question_is_refused_once_composed(questions, said):
    with pytest.raises(StoryError, match=said):
        package_story.compose(package_story.authored(raw({**AUTHORED, "questions": questions})),
                              "en")


def test_the_count_is_checked_after_composition():
    full = {"title": "Long", "slides": [{"title": f"S{n}", "beats": [{"say": "s"}]}
                                         for n in range(package_story.MAX_SLIDES)]}
    package_story.parse(raw({**full, "language": "en"}))
    with pytest.raises(StoryError, match="more than 60"):
        package_story.compose(package_story.authored(raw(full)), "en")


def test_a_story_in_another_language_is_not_composed_in_this_one():
    with pytest.raises(StoryError, match="not in en"):
        package_story.compose(package_story.authored(raw({**AUTHORED, "language": "es"})), "en")


# -- the questions a story asks in the view's head -------------------------------------

def composed() -> dict:
    return json.loads(package_story.compose(package_story.authored(raw(AUTHORED)), "en"))


def answer(w, how: str, inbox_id: str, decision: str, option: str, words: str = ""):
    if how == "page":
        return w.client.post(f"{INBOX}/{inbox_id}/answer", headers=BROWSER,
                             json={"decision": decision, "option": option, "words": words})
    return _call(w, "answer_package_decision", package=inbox_id, decision=decision,
                 option=option, words=words)["text"]


def package_of(inbox_id: str) -> work_packages.Package:
    return work_packages.find(package_inbox.get(inbox_id).package_id)


def test_the_head_shows_each_declared_question_with_whether_it_applies(
        wired, shown, marks, tmp_path):
    inbox_id = send(wired, story_zip(composed())).json()["inbox"]["id"]
    vote = package_inbox.page_vote(package_of(inbox_id))
    assert (vote["question"], vote["answer"]) == ("understood", None)
    assert [(q["id"], q["applies"], q["answer"]) for q in vote["asks"]] == [
        ("sign-surface", True, None), ("which-signature", False, None)]
    assert vote["asks"][0]["options"][1] == {"id": "change-a-signature",
                                             "name": "Change A Signature",
                                             "says": "Choose change-a-signature."}
    assert package_inbox.get(inbox_id).decisions_total == 2, \
        "the follow-up is not counted until it applies"


@pytest.mark.parametrize("how", ["page", "voice"])
def test_click_and_voice_record_a_declared_answer_where_the_story_waits(
        wired, shown, marks, tmp_path, how):
    inbox_id = send(wired, story_zip(composed())).json()["inbox"]["id"]
    refused = answer(wired, how, inbox_id, "which-signature", "grant-read")
    if how == "page":
        assert refused.status_code == 409 and "not asked yet" in refused.json()["error"]
    else:
        assert "not asked yet" in refused
    said = answer(wired, how, inbox_id, "sign-surface", "change-a-signature", "Rename one.")
    assert how == "voice" and said.startswith("Recorded.") or said.status_code == 200
    package = package_of(inbox_id)
    record = json.loads(work_packages.read_result(package, "decisions.json"))
    assert record["sign-surface"]["answer"] == "change-a-signature"
    assert record["sign-surface"]["note"] == "Rename one."
    assert record["sign-surface"]["recommended"] is False
    asks = {q["id"]: q for q in package_inbox.page_vote(package)["asks"]}
    assert asks["which-signature"]["applies"], "his answer opens the follow-up"
    assert package_inbox.get(inbox_id).decisions_total == 3
    story = package.story()
    assert [story.slides[n - 1].waits for n in story.open_questions(record)] == [
        "which-signature", "understood"], "the story resumes at the first applicable unanswered"


def test_an_unanswered_question_stays_unanswered_when_he_signs_off_early(
        wired, shown, marks, tmp_path, chitauri):
    inbox_id = send(wired, story_zip(composed()), session="sess-1").json()["inbox"]["id"]
    assert answer(wired, "page", inbox_id, "sign-surface", "sign").status_code == 200
    _call(wired, "sign_off_package", package=inbox_id)
    capture = next(c for c in wired.server._captures.values() if c.get("to_session") == "sess-1")
    assert "sign-surface: sign" in capture["text"]
    assert "understood: not answered" in capture["text"], capture["text"]
    assert "which-signature" not in capture["text"] or \
        "- which-signature: not answered" in capture["text"]


def test_a_question_the_story_does_not_declare_stays_the_page_s_own(
        wired, shown, marks, tmp_path):
    page_owned = {"title": "Notes", "answers": "results/decisions.json",
                  "slides": [{"title": "Go?", "waits": "go-ahead", "beats": [{"say": "Go?"}]}]}
    inbox_id = send(wired, story_zip(page_owned)).json()["inbox"]["id"]
    package = package_of(inbox_id)
    assert package_inbox.page_vote(package) is None
    r = answer(wired, "page", inbox_id, "go-ahead", "yes")
    assert r.status_code == 409


def test_a_declared_question_is_never_his_review(wired, shown, marks, tmp_path):
    review = {**AUTHORED, "answers": "results/decisions.json",
              "questions": [{**SURFACE, "id": "approve-item",
                             "options": options("approve", "not-done")}],
              "slides": [{"title": "Review", "waits": "approve-item", "beats": [{"say": "?"}]}]}
    r = send(wired, story_zip(review))
    assert r.status_code == 409 and "reserved" in r.json()["error"]
