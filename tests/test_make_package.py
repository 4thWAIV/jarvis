"""The one command that makes a package and sends it to Tim
(jarvis/make_package.py): a presentation deck built (no votes) for the
package workflow, a hand-made package's folder with only its purpose given,
the version worked out, an agent's package sent to Tim's package inbox with
who sent it, and a deck or a page from the fresh author refused as a
briefing. Content checks shared with decision packages are in
tests/test_decision_package.py; the isolated JARVIS here is a stand-in."""
from __future__ import annotations

import base64
import json
import zipfile
from pathlib import Path

import pytest

from jarvis import (data_paths, decision_package as dp, make_package as mp,
                    package_inbox)
from tests import deck_fixtures
from tests.deck_fixtures import write_yaml
from tests.test_decision_package import _Delivery, small_deck, write

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"


pytestmark = pytest.mark.usefixtures("authored_package_source")


def presentation() -> dict:
    return json.loads(json.dumps(deck_fixtures.PRESENTATION))


@pytest.fixture(autouse=True)
def session_key():
    """Session sess-1's own key, which every delivery is signed with."""
    from jarvis import agent_keys
    return agent_keys.make_pair(agent_keys.key_folder("sess-1"), "jarvis-agent sess-1")


def built_deck(out: Path) -> dict:
    """The deck data of the one package built in `out`."""
    [archive] = out.glob("*.zip")
    text = zipfile.ZipFile(archive).read("deck-data.js").decode()
    return json.loads(text.split("=", 1)[1].strip().rstrip(";"))


# -- a presentation ---------------------------------------------------------------------

def test_a_presentation_carries_the_standard_vote_asked_at_done(tmp_path):
    made = mp.make_presentation(write_yaml(tmp_path / "in", presentation()), tmp_path / "out")
    archive = tmp_path / "out" / "team-lunch-moved-1.0.0.zip"
    assert made.archive == archive and made.kind == "presentation"
    z = zipfile.ZipFile(archive)
    assert "scripts/decide.py" not in z.namelist(), "no slide asks the vote: JARVIS records it"
    package = json.loads(z.read("package.json"))
    understood = package.pop("understanding")
    assert {"deck-data.js", "deck.css", "deck.js", "index.html", "story.json",
            "presentation.css", "source.yaml"} <= set(understood)
    assert set(understood.values()) == {"UNDERSTANDING.md"}
    assert package == {"name": "team-lunch-moved", "version": "1.0.0",
                       "purpose": "Tell the team where Friday's lunch is now.", "runs": "page", "entry": "index.html",
                       "story": "story.json", "returns": ["results/decisions.json"],
                       "vote": "approve-package", "brief": "UNDERSTANDING.md"}
    assert not built_deck(tmp_path / "out").get("decisions"), "the vote is no decision of the deck"
    story = json.loads(z.read("story.json"))
    titles = [s["title"] for s in story["slides"]]
    assert titles[:2] == ["Friday's lunch has moved", "What changed"], \
        "its own slides first, its opening first of all"
    assert not any(s.get("waits") for s in story["slides"]), "the vote is asked at Done"


def test_a_deck_or_a_page_from_the_fresh_author_is_refused_as_a_briefing(tmp_path, monkeypatch,
                                                                         capsys):
    """A deck or a page for Tim is a briefing: only the package workflow
    makes one (make_package --start), so the fresh author's is refused and
    nothing is delivered."""
    from jarvis import package_author
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    for source in (write_yaml(tmp_path / "deck", presentation()), page_folder(tmp_path / "page")):
        assert mp.main([str(source), "--work", "work-1", "--out", str(tmp_path / "out"),
                        "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 1
        assert package_author.BRIEFING_BY_REQUEST in capsys.readouterr().out
    assert delivery.calls == [], "nothing delivered, nothing reported"

def test_a_deck_with_neither_slides_nor_decisions_is_refused(tmp_path, capsys):
    assert mp.main([str(write_yaml(tmp_path, {"title": "Nothing"})), "--check-only"]) == 1
    assert "at least 1 item" in capsys.readouterr().out


def test_the_presentation_example_is_valid(tmp_path, capsys):
    assert mp.main(["--example", "presentation"]) == 0
    content = tmp_path / "presentation.yaml"
    content.write_text(capsys.readouterr().out)
    assert mp.main([str(content), "--check-only"]) == 0
    assert "a presentation, carrying the standard vote" in capsys.readouterr().out
    assert mp.main(["--example"]) == 0
    content.write_text(capsys.readouterr().out)
    assert mp.main([str(content), "--check-only"]) == 0
    assert "a decision package, 1 decision(s)" in capsys.readouterr().out


def test_a_content_json_deck_is_refused_naming_presentation_yaml(tmp_path, capsys):
    assert mp.main([str(write(tmp_path, small_deck())), "--check-only"]) == 1
    assert mp.JSON_DECK_RETIRED in capsys.readouterr().out


# -- what is worked out -----------------------------------------------------------------

def test_it_goes_to_his_package_inbox_naming_its_sender(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    assert mp.main([str(agent_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--session", "sess-1", "--workflow", "wf-1",
                    "--work", "work-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, _token, body)] = delivery.calls
    assert url.startswith("http://127.0.0.1:19999/api/messages?"), "a message carrying it"
    assert "project=lunch" in url and "workflow=wf-1" in url and "work=work-1" in url
    assert base64.b64decode(body["package"])[:2] == b"PK", "the zip's own bytes"
    assert body["says"].startswith("Agent package: "), "its kind and title by default"
    out = capsys.readouterr().out
    assert "Sent to Tim as message 41, carrying it; in his package inbox as " \
        "pkg-0123456789ab" in out
    assert "Nothing is shown until he asks for it" in out
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    assert (record["inbox"], record["message"], record["session"]) == (
        "pkg-0123456789ab", 41, "sess-1")

def test_the_version_follows_the_highest_already_delivered(tmp_path, monkeypatch):
    for version in ("1.0.0", "1.0.9", "1.0.10"):
        folder = data_paths.packages_dir() / f"team-lunch-moved-{version}-x"
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps(
            {"name": "team-lunch-moved", "version": version, "purpose": "p", "runs": "page",
             "entry": "index.html", "returns": []}))
    assert mp.next_version("team-lunch-moved") == "1.0.11"
    assert mp.next_version("something-new") == "1.0.0"
    deck = mp.deck_of(write(tmp_path, presentation()))
    assert deck["version"] == "1.0.11"
    given = dict(presentation(), version="2.0.0")
    assert mp.deck_of(write(tmp_path, given))["version"] == "2.0.0"


def test_a_short_name_keeps_whole_words():
    assert dp.short_name("Where do we eat on Friday?") == "Where do we eat"
    assert dp.short_name("Supercalifragilistic") == "Supercalifragili"


def test_a_compare_picture_is_a_known_field(tmp_path):
    deck = small_deck()
    deck["decisions"][0]["compare"] = "pictures/plate.svg"
    assert dp.problems_of(dp.expand(deck), write(tmp_path, deck).parent) == []


def test_no_decision_part_is_refused_for_its_words(tmp_path):
    deck = small_deck()
    for d in deck["decisions"]:
        d["reason"] = " ".join(["word"] * 45)
    assert dp.problems_of(dp.expand(deck), write(tmp_path, deck).parent) == []


# -- a hand-made folder -----------------------------------------------------------------

def page_folder(root: Path, **manifest) -> Path:
    folder = root / "Globe Numbers"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text(
        '<script src="app.js"></script><script src="/api/packages/story.js"></script>')
    (folder / "app.js").write_text("// app\n")
    (folder / "story.json").write_text(json.dumps(json.loads(
        (EXAMPLES / "story-deck" / "story.json").read_text())))
    (folder / "explanation.md").write_text("# Globe numbers\nThe authored explanation of the numbers and story.")
    (folder / "package.json").write_text(json.dumps({"purpose": "Show the numbers.",
        "understanding": {"index.html": "explanation.md", "app.js": "explanation.md", "story.json": "explanation.md"},
        **manifest}))
    (folder / "results").mkdir()
    (folder / "results" / "old.json").write_text("{}")
    return folder


def agent_folder(root: Path) -> Path:
    """An agent package's folder: its brief and only its purpose given."""
    folder = root / "Count Words"
    folder.mkdir(parents=True)
    (folder / "BRIEF.md").write_text("Count the words.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Count the words."}))
    return folder


# -- every package carries his vote -----------------------------------------------------

def built_story(out: Path) -> dict:
    [archive] = out.glob("*.zip")
    return json.loads(zipfile.ZipFile(archive).read("story.json"))


def test_a_page_with_no_story_carries_the_vote_and_gets_no_story(tmp_path):
    folder = tmp_path / "in" / "Plain Page"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>Hello</p>")
    (folder / "about.md").write_text("# Hello\nA page that says hello.")
    (folder / "package.json").write_text(json.dumps({"purpose": "Say hello.", "returns": [],
                                                     "understanding": {"index.html": "about.md"}}))
    out = tmp_path / "out"
    out.mkdir()
    archive = mp.make_folder(folder, out).archive
    manifest = json.loads(zipfile.ZipFile(archive).read("package.json"))
    assert manifest["vote"] == "approve-package" and "story" not in manifest
    assert "results/decisions.json" in manifest["returns"]
    assert "story.json" not in zipfile.ZipFile(archive).namelist()

def test_a_page_s_own_story_is_kept_as_it_is_and_the_vote_asked_at_done(tmp_path):
    folder = page_folder(tmp_path / "in")
    given = json.loads((folder / "story.json").read_text())
    given["answers"] = "results/mine.json"
    given["slides"][-1]["waits"] = "go-ahead"
    (folder / "story.json").write_text(json.dumps(given))
    (tmp_path / "b").mkdir()
    archive = mp.make_folder(folder, tmp_path / "b").archive
    assert built_story(tmp_path / "b") == given
    assert json.loads(zipfile.ZipFile(archive).read("package.json"))["vote"] == "approve-package"

def in_force(language: str) -> None:
    data_paths.language_path().write_text(json.dumps({"language": language}))


@pytest.mark.parametrize("given, saved, words", [
    ("es", "en", ["Aprobar", "Rechazar"]),
    ("en", "es", ["Approve", "Reject"]),
    (None, "es", ["Aprobar", "Rechazar"]),
    (None, "en", ["Approve", "Reject"]),
])
def test_the_vote_at_done_is_in_the_deck_s_language_else_the_one_in_force(
        tmp_path, monkeypatch, given, saved, words):
    from jarvis import spoken_words
    monkeypatch.setattr(spoken_words, "_active", saved)
    deck = mp.deck_of(write(tmp_path, presentation() | ({"language": given} if given else {})))
    assert not deck.get("decisions")
    folder = tmp_path / "page"
    (folder / "web").mkdir(parents=True)
    (folder / "web" / "index.html").write_text("x")
    (folder / "package.json").write_text(json.dumps({
        "name": "p", "version": "1.0.0", "purpose": "p", "runs": "page", "entry": "web/index.html",
        "returns": [], "vote": "approve-package"}))
    if given:
        (folder / "deck-data.js").write_text("window.DECK = " + json.dumps({"language": given}) + ";")
        (folder / "scripts").mkdir()
        (folder / "scripts" / "decide.py").write_text("")
    from jarvis import work_packages
    package = work_packages.Package(folder, work_packages.Manifest.parse(
        (folder / "package.json").read_bytes()))
    [vote] = package_inbox.questions(package)
    assert vote.id == "approve-package" and [o["name"] for o in vote.options] == words


# -- Got it is retired --------------------------------------------------------------------

@pytest.mark.parametrize("decision, said", [
    ("understood", "Got it, which is retired"),
    ("approve-package", "the standard vote, which the command adds itself"),
])
def test_a_deck_that_declares_the_vote_is_refused(tmp_path, capsys, decision, said):
    content = presentation() | {"decisions": [{
        "id": decision, "question": "Is it clear?", "reason": "So he can say.",
        "recommended": "got-it", "text_only": "A reply is not a choice between looks at all.",
        "options": [{"id": "got-it", "name": "Got it", "says": "Clear."},
                    {"id": "questions", "name": "I have questions", "says": "Ask."}]}]}
    assert mp.main([str(write_yaml(tmp_path, content)), "--check-only"]) == 1
    assert said in capsys.readouterr().out


def test_a_page_whose_story_asks_got_it_is_refused(tmp_path):
    folder = page_folder(tmp_path / "in")
    given = json.loads((folder / "story.json").read_text())
    given["answers"] = "results/decisions.json"
    given["slides"].append({"title": "Got it?", "waits": "understood",
                            "beats": [{"say": "Got it, or do you have questions?"}]})
    (folder / "story.json").write_text(json.dumps(given))
    (tmp_path / "a").mkdir()
    with pytest.raises(dp.Refused) as refused:
        mp.make_folder(folder, tmp_path / "a")
    assert "Got it, which is retired" in " ".join(refused.value.problems)

@pytest.mark.parametrize("question", ["approve-item", "merge-pr"])
def test_a_deck_that_asks_approval_still_ends_with_the_package_vote(tmp_path, question):
    """A review package (approve-item) or a merge package (merge-pr) asks
    about the work; the package itself still ends with Approve or Reject."""
    content = presentation() | {"decisions": [{
        "id": question, "question": "Approve it?", "reason": "It is done.",
        "recommended": "approve", "text_only": "A verdict is a reply, not a choice between looks.",
        "options": [{"id": "approve", "name": "Approve", "says": "Done."},
                    {"id": "hold", "name": "Hold", "says": "Not yet."}]}]}
    deck = mp.deck_of(write(tmp_path, content))
    assert [d["id"] for d in deck["decisions"]] == [question], "the vote is asked at Done"
    assert mp.deck_kind(deck) == "decision package"


def test_a_conversation_package_for_a_work_item_goes(tmp_path, monkeypatch):
    """A package for a Work item that asks nothing is no longer refused:
    what follows his sign-off is the workflow's to carry on."""
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = tmp_path / "in" / "Say Notes"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("Say the notes.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Say the notes."}))
    assert mp.main([str(folder), "--out", str(tmp_path / "out"), "--session", "sess-1",
                    "--work", "work-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, _, _)] = delivery.calls
    assert "work=work-1" in url


def test_an_agent_package_for_a_work_item_goes_with_its_work_key(tmp_path, monkeypatch):
    """Its vote is asked on the work item as it arrives (the server's side
    is in test_package_inbox); the package carries the item's key."""
    from jarvis import package_signature
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = tmp_path / "in" / "Count Words"
    folder.mkdir(parents=True)
    (folder / "BRIEF.md").write_text("Count the words.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Count the words."}))
    assert mp.main([str(folder), "--out", str(tmp_path / "out"), "--session", "sess-1",
                    "--work", "work-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(_url, _signer, body)] = delivery.calls
    [archive] = (tmp_path / "out").glob("*.zip")
    assert ".jarvis/work-key" in "\n".join(zipfile.ZipFile(archive).namelist())
    assert package_signature.read(archive).work_key["work"] == "work-1"


def test_a_folder_with_only_a_purpose_is_worked_out(tmp_path):
    manifest = mp.manifest_of(page_folder(tmp_path))
    assert manifest == {"purpose": "Show the numbers.", "runs": "page", "entry": "index.html",
                        "story": "story.json", "name": "globe-numbers",
                        "returns": ["results/**"], "version": "1.0.0", "brief": "explanation.md",
                        "understanding": {"index.html": "explanation.md", "app.js": "explanation.md",
                                          "story.json": "explanation.md"}}


def test_a_folder_is_zipped_read_back_and_delivered(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = agent_folder(tmp_path / "in")
    (folder / "notes.txt").write_text("The words to count.\n")
    (folder / "notes.md").write_text("# Notes\nThe words the brief counts.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Count the words.",
                                                     "understanding": {"notes.txt": "notes.md"}}))
    (folder / "results").mkdir()
    (folder / "results" / "old.json").write_text("{}")
    assert mp.main([str(folder), "--out", str(tmp_path / "out"),
                    "--project", "globe", "--session", "sess-1",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    archive = tmp_path / "out" / "count-words-1.0.0.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert "BRIEF.md" in names and "notes.txt" in names and "results/old.json" not in names
    assert len(delivery.calls) == 1
    out = capsys.readouterr().out
    assert "agent package: " in out and "in his package inbox" in out

@pytest.mark.parametrize("change, said", [
    (lambda f: (f / "package.json").write_text("{}"), "needs a purpose"),
    (lambda f: (f / "BRIEF.md").write_text("Do it."), "could run as page or agent"),
    (lambda f: (f / "index.html").write_text('<script type="module" src="a.js"></script>'
                                             '<script src="/api/packages/story.js"></script>'),
     "loads a script as a module"),
    (lambda f: (f / "index.html").write_text("<p>no helper</p>"), "does not load the story helper"),
    (lambda f: (f / "package.json").write_text('{"purpose": "p", "retuns": []}'),
     "did you mean 'returns'"),
    (lambda f: (f / "package.json").write_text('{"purpose": "p", "entry": "main.html"}'),
     "'main.html', which is not in"),
])
def test_an_unclear_folder_is_refused_with_the_reason(tmp_path, change, said):
    folder = page_folder(tmp_path)
    change(folder)
    with pytest.raises(dp.Refused) as refused:
        mp.manifest_of(folder)
    assert said in " ".join(refused.value.problems)


def test_a_folder_with_no_way_to_run_is_refused(tmp_path):
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "package.json").write_text('{"purpose": "p"}')
    with pytest.raises(dp.Refused) as refused:
        mp.manifest_of(folder)
    assert "how it runs cannot be told" in " ".join(refused.value.problems)


@pytest.mark.parametrize("example", ["text-stats-agent", "spanish-speech-test",
                                     "mic-check-page", "story-deck"])
def test_every_hand_made_example_passes_as_it_is(tmp_path, example):
    made = mp.make_folder(EXAMPLES / example, tmp_path)
    assert made.archive.is_file()


def test_the_old_command_name_is_the_same_command(tmp_path, capsys):
    assert dp.main([str(write_yaml(tmp_path, presentation())), "--check-only"]) == 0
    assert "a presentation" in capsys.readouterr().out


def test_every_deck_carries_its_proof_a_presentation_too(tmp_path):
    """A presentation carries the standard vote (PR 89), so it is proven
    exactly as a decision package is: no browser, and his JARVIS makes the
    proof again as it arrives."""
    from jarvis import presentation_proof
    for name, deck in (("d", small_deck()), ("p", presentation())):
        made = mp.make_presentation(write_yaml(tmp_path / name, deck), tmp_path / name / "out")
        with zipfile.ZipFile(made.archive) as z:
            assert json.loads(z.read(presentation_proof.PROOF_FILE))["proven"]

def test_a_deck_given_as_a_folder_is_refused(tmp_path):
    with pytest.raises(dp.Refused) as refused:
        mp.make_folder(EXAMPLES / "decision-deck", tmp_path)
    assert "made from its content file" in " ".join(refused.value.problems)


def test_a_build_refused_after_the_author_reports_on_work_without_a_package(tmp_path, monkeypatch):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    def refused(source, out):
        raise dp.Refused(["BRIEF.md is not readable."])
    monkeypatch.setattr(mp, "make_folder", refused)
    assert mp.main([str(agent_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--session", "sess-1", "--work", "image-work",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 1
    [(url, signer, body)] = delivery.calls
    assert url.endswith("/api/messages?work=image-work")
    assert signer is not None and "package" not in body
    assert "BRIEF.md is not readable" in body["says"]

# -- a briefing: its purpose, conclusion and an opening of words ------------------------

def refused_for(tmp_path, deck) -> str:
    with pytest.raises(dp.Refused) as refused:
        mp.deck_of(write(tmp_path, deck))
    return " ".join(refused.value.problems)


def test_a_well_formed_briefing_is_accepted(tmp_path):
    deck = mp.deck_of(write(tmp_path, small_deck()))
    assert deck["purpose"] == "Two decisions on the team lunch."
    assert [s.get("id") for s in deck["slides"]] == ["s1"], "JARVIS adds no lead-in slide"
    assert mp.deck_of(write(tmp_path, presentation()))["conclusion"]


@pytest.mark.parametrize("change", [
    lambda d: d.pop("conclusion"),
    lambda d: d.update(conclusion=d["conclusion"] + " Then we vote."),
    lambda d: d["slides"][0]["blocks"].insert(0, {"table": {"head": ["Day", "People"],
                                                            "rows": [["Friday", "9"]]}}),
    lambda d: d["slides"][0].pop("say"),
    lambda d: d["slides"][0]["blocks"].extend({"p": " ".join(["word"] * 50)} for _ in range(3)),
])
def test_the_first_slide_and_the_words_are_the_author_s(tmp_path, change):
    """No rule shapes the first slide (words first, a narration, the
    conclusion shown), asks for a one-sentence conclusion, or counts a
    slide's words."""
    deck = small_deck()
    change(deck)
    mp.deck_of(write(tmp_path, deck))


@pytest.mark.parametrize("field", ["why_you", "evidence"])
def test_a_decision_carries_no_lead_in_fields(tmp_path, field):
    """JARVIS draws no lead-in slide: a decision's why_you and evidence are
    no fields of it."""
    deck = small_deck()
    deck["decisions"][0][field] = "It is yours." if field == "why_you" else ["It is here."]
    assert f"{field!r} is not a field of a decision" in refused_for(tmp_path, deck)
    deck = small_deck()
    deck["slides"].append({"title": "Before you decide", "lead_in": "where",
                           "blocks": [{"p": "Mine."}]})
    assert "'lead_in' is not a field of a slide" in refused_for(tmp_path, deck)


def test_each_decision_is_told_and_asked_on_its_own_slide(tmp_path):
    deck = mp.deck_of(write(tmp_path, small_deck()))
    deck["decisions"][0]["options"][0]["opens"] = ["when"]
    told = dp.builder().story(deck)["slides"]
    assert [s.get("waits") for s in told] == [None, "where", "when", None]
    where, when = told[1], told[2]
    assert where["beats"][0]["actions"] == [{"click": "#go-2"}]
    assert where["target"] == "#d-where"
    assert when["beats"][0]["actions"] == [{"click": "#go-3"}]
    assert when["asks_when"] == {"question": "where", "answers": ["in"]}
    from jarvis import package_story
    story = package_story.parse(package_story.encode(dp.builder().story(deck)))
    record = {"where": {"answer": "in"}}
    assert story.open_questions(record) == [3], "resume picks the when slide"


def test_the_border_moves_through_every_slide_the_deck_draws(tmp_path):
    """The border navigates the deck's own slides: each question and the
    vote is its own page, numbered as the deck numbers them."""
    deck = mp.deck_of(write(tmp_path, small_deck()))
    pages = dp.builder().pages(deck)
    told = dp.builder().story(deck)["slides"]
    assert [p["opener"] for p in pages] == [f"#go-{n}" for n in range(1, len(pages) + 1)]
    assert [p["asks"] for p in pages] == [None, "where", "when", None], \
        "no slide asks the vote: it is asked at Done"
    assert pages[-1]["title"] == "Your answers"
    opened = {o for s in told for b in s["beats"] for kind, o in
              ((k, v) for a in b.get("actions", []) for k, v in a.items()) if kind == "click"}
    assert opened <= {p["opener"] for p in pages}


