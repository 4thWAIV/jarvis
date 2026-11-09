"""The one command that makes a package and sends it to Tim
(jarvis/make_package.py): a presentation deck (no votes), a hand-made
package's folder with only its purpose given, the version worked out, and
the package sent to Tim's package inbox with who sent it. Content checks shared with decision packages are in
tests/test_decision_package.py; the isolated JARVIS here is a stand-in."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from jarvis import (data_paths, decision_check, decision_package as dp, make_package as mp,
                    package_inbox)
from tests.test_decision_package import _Delivery, small_deck, write

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"


pytestmark = pytest.mark.usefixtures("authored_package_source")


def presentation() -> dict:
    return json.loads(json.dumps(mp.PRESENTATION_EXAMPLE))


@pytest.fixture(autouse=True)
def session_key():
    """Session sess-1's own key, which every delivery is signed with."""
    from jarvis import agent_keys
    return agent_keys.make_pair(agent_keys.key_folder("sess-1"), "jarvis-agent sess-1")


def passing_check(monkeypatch, seen=None):
    def check(archive, deck, out, signer=None, session=""):
        if seen is not None:
            seen.append(deck)
        return decision_check.Result(passed=["everything"])
    monkeypatch.setattr(decision_check, "check", check)


# -- a presentation ---------------------------------------------------------------------

def test_a_presentation_ends_with_the_standard_vote(tmp_path, monkeypatch, capsys):
    delivery, seen = _Delivery(), []
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch, seen)
    assert mp.main([str(write(tmp_path / "in", presentation())), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--session", "sess-1",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    archive = tmp_path / "out" / "team-lunch-moved-1.0.0.zip"
    z = zipfile.ZipFile(archive)
    assert "scripts/decide.py" in z.namelist(), "the vote is answered as any deck's is"
    package = json.loads(z.read("package.json"))
    understood = {f: "UNDERSTANDING.md" for f in (
        "SKILL.md", "deck-data.js", "deck.css", "deck.js", "index.html", "scripts/decide.py",
        "story.json")}
    assert package == {"name": "team-lunch-moved", "version": "1.0.0",
                       "purpose": "Team lunch moved", "runs": "page", "entry": "index.html",
                       "story": "story.json", "skill": "SKILL.md",
                       "returns": ["results/decisions.json", "results/decisions.md"],
                       "brief": "UNDERSTANDING.md", "understanding": understood}
    [vote] = seen[0]["decisions"]
    assert vote["id"] == package_inbox.UNDERSTOOD_ID
    assert [o["name"] for o in vote["options"]] == ["Got it", "I have questions"]
    story = json.loads(z.read("story.json"))
    titles = [s["title"] for s in story["slides"]]
    assert titles[:2] == ["What changed", "What you need to do"], "its own slides first"
    assert [s.get("waits") for s in story["slides"]].index("understood") == 2, \
        "the vote at the end, after every slide of its own"
    assert len(seen) == 1 and len(delivery.calls) == 1
    assert "Built a presentation" in capsys.readouterr().out


def test_a_deck_with_neither_slides_nor_decisions_is_refused(tmp_path, capsys):
    assert mp.main([str(write(tmp_path, {"title": "Nothing"})), "--check-only"]) == 1
    assert "neither decisions nor slides" in capsys.readouterr().out


def test_the_presentation_example_is_valid(tmp_path, capsys):
    assert mp.main(["--example", "presentation"]) == 0
    content = tmp_path / "content.json"
    content.write_text(capsys.readouterr().out)
    assert mp.main([str(content), "--check-only"]) == 0
    assert "a presentation, ending with the standard vote" in capsys.readouterr().out


# -- what is worked out -----------------------------------------------------------------

def test_it_goes_to_his_package_inbox_naming_its_sender(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch)
    assert mp.main([str(write(tmp_path / "in", small_deck())), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--session", "sess-1", "--workflow", "wf-1",
                    "--work", "work-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, _token, body)] = delivery.calls
    assert url.startswith("http://127.0.0.1:19999/api/packages/inbox?")
    assert "project=lunch" in url and "session=sess-1" in url and "workflow=wf-1" in url \
        and "work=work-1" in url
    assert isinstance(body, bytes) and body[:2] == b"PK", "the zip's own bytes"
    out = capsys.readouterr().out
    assert "Arrived in Tim's package inbox as pkg-0123456789ab" in out
    assert "Nothing is shown until he asks for it" in out
    assert not list(data_paths.session_messages_dir().glob("*/*.md")), \
        "no message: the inbox tells him"
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    assert record["inbox"] == "pkg-0123456789ab" and record["session"] == "sess-1"


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


def test_every_slide_over_the_word_limit_is_listed(tmp_path):
    deck = presentation()
    long = " ".join(["word"] * 70)
    deck["slides"] = [{"title": "One", "blocks": [{"p": long[:270]}, {"p": long[:270]}]},
                      {"title": "Two", "blocks": [{"p": long[:270]}, {"p": long[:270]}]}]
    found = dp.problems_of(dp.expand(deck), write(tmp_path, deck).parent)
    assert sum("words; at most 60" in p for p in found) == 2


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


# -- every package carries his vote -----------------------------------------------------

def built_story(out: Path) -> dict:
    [archive] = out.glob("*.zip")
    return json.loads(zipfile.ZipFile(archive).read("story.json"))


def test_a_presentation_for_a_work_item_goes_with_the_vote(tmp_path, monkeypatch):
    delivery, seen = _Delivery(), []
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch, seen)
    assert mp.main([str(write(tmp_path / "in", presentation())), "--work", "work-1",
                    "--out", str(tmp_path / "out"), "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, _token, _body)] = delivery.calls
    assert "work=work-1" in url and seen[0]["decisions"][-1]["id"] == "understood"


def test_a_page_with_no_story_gets_one_that_asks_the_vote(tmp_path, monkeypatch):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = tmp_path / "in" / "Plain Page"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>Hello</p>")
    (folder / "about.md").write_text("# Hello\nA page that says hello.")
    (folder / "package.json").write_text(json.dumps({"purpose": "Say hello.", "returns": [],
                                                     "understanding": {"index.html": "about.md"}}))
    out = tmp_path / "out"
    assert mp.main([str(folder), "--work", "work-1", "--out", str(out),
                    "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    story = built_story(out)
    assert story["answers"] == "results/decisions.json"
    assert [s.get("waits") for s in story["slides"]] == ["understood"]
    [archive] = out.glob("*.zip")
    manifest = json.loads(zipfile.ZipFile(archive).read("package.json"))
    assert manifest["story"] == "story.json" and "results/decisions.json" in manifest["returns"]
    assert not (folder / "story.json").exists(), "his folder is never changed"
    assert len(delivery.calls) == 1


def test_a_page_s_own_story_ends_with_the_vote_unless_it_already_asks(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "post", _Delivery())
    folder = page_folder(tmp_path / "in")
    given = json.loads((folder / "story.json").read_text())
    assert mp.main([str(folder), "--out", str(tmp_path / "a"),
                    "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    story = built_story(tmp_path / "a")
    assert [s["title"] for s in story["slides"]][:-1] == [s["title"] for s in given["slides"]]
    assert story["slides"][-1]["waits"] == "understood"
    # A story that already waits on its own question is left as it is.
    given["answers"] = "results/mine.json"
    given["slides"][-1]["waits"] = "go-ahead"
    (folder / "story.json").write_text(json.dumps(given))
    assert mp.main([str(folder), "--out", str(tmp_path / "b"),
                    "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    assert built_story(tmp_path / "b") == given


def in_force(language: str) -> None:
    data_paths.language_path().write_text(json.dumps({"language": language}))


@pytest.mark.parametrize("given, saved, words", [
    ("es", "en", ["Entendido", "Tengo preguntas"]),
    ("en", "es", ["Got it", "I have questions"]),
    (None, "es", ["Entendido", "Tengo preguntas"]),
    (None, "en", ["Got it", "I have questions"]),
])
def test_a_deck_s_vote_is_in_its_language_else_the_one_in_force(
        tmp_path, monkeypatch, given, saved, words):
    in_force(saved)
    content = presentation() | ({"language": given} if given else {})
    deck = mp.deck_of(write(tmp_path, content))
    assert [o["name"] for o in deck["decisions"][-1]["options"]] == words


def test_a_new_story_is_in_the_language_in_force_and_a_page_s_own_in_its_own(
        tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "post", _Delivery())
    in_force("es")
    folder = tmp_path / "in" / "Plain Page"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>Hola</p>")
    (folder / "about.md").write_text("# Hola\nUna página que saluda.")
    (folder / "package.json").write_text(json.dumps({"purpose": "Saludar.",
                                                     "understanding": {"index.html": "about.md"}}))
    assert mp.main([str(folder), "--out", str(tmp_path / "a"),
                    "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    story = built_story(tmp_path / "a")
    assert story["language"] == "es" and story["slides"][-1]["title"] == "¿Entendido?"
    # A page's own story names no language, so it is told in English: so is its vote.
    assert mp.main([str(page_folder(tmp_path / "f")), "--out", str(tmp_path / "b"),
                    "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    assert built_story(tmp_path / "b")["slides"][-1]["title"] == "Got it?"


@pytest.mark.parametrize("check_only", [False, True])
def test_a_conversation_package_for_a_work_item_is_refused(tmp_path, monkeypatch, capsys,
                                                          check_only):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = tmp_path / "in" / "Say Notes"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("Say the notes.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Say the notes."}))
    argv = [str(folder), "--out", str(tmp_path / "out"), "--session", "sess-1",
            "--deliver-to", "http://127.0.0.1:19999"]
    assert mp.main(argv + ["--work", "work-1"] + (["--check-only"] if check_only else [])) == 1
    out = capsys.readouterr().out
    assert "names work item work-1, but a conversation package cannot carry Tim's vote" in out
    assert "Send it without --work" in out and delivery.calls == []
    assert mp.main(argv) == 0, "with no Work item it goes"
    assert len(delivery.calls) == 1


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
    monkeypatch.setattr(decision_check, "check", lambda *a: pytest.fail("a folder is no deck"))
    assert mp.main([str(page_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--project", "globe", "--session", "sess-1",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    archive = tmp_path / "out" / "globe-numbers-1.0.0.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert "app.js" in names and "story.json" in names and "results/old.json" not in names
    assert len(delivery.calls) == 1
    out = capsys.readouterr().out
    assert "Built a page package" in out and "Arrived in Tim's package inbox" in out


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
    assert dp.main([str(write(tmp_path, presentation())), "--check-only"]) == 0
    assert "a presentation" in capsys.readouterr().out


def test_every_deck_is_signed_as_checked_a_presentation_too(
        tmp_path, monkeypatch, session_key):
    """A presentation carries the standard vote (PR 89), so it is checked
    and signed exactly as a decision package is: no "only as sent" path."""
    from jarvis import package_signature
    monkeypatch.setattr(dp, "post", _Delivery())
    passing_check(monkeypatch)
    for name, deck in (("d", small_deck()), ("p", presentation())):
        out = tmp_path / name / "out"
        assert mp.main([str(write(tmp_path / name, deck)), "--out", str(out),
                        "--session", "sess-1", "--deliver-to", "http://127.0.0.1:19999"]) == 0
        [archive] = out.glob("*.zip")
        signed = package_signature.read(archive)
        assert signed.session == "sess-1" and signed.checks(session_key[1])
        assert package_signature.DECISION_CHECKED in signed.statements


def test_a_deck_given_as_a_folder_is_refused(tmp_path):
    with pytest.raises(dp.Refused) as refused:
        mp.make_folder(EXAMPLES / "decision-deck", tmp_path)
    assert "made from its content file" in " ".join(refused.value.problems)
