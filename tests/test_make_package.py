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

from jarvis import data_paths, decision_check, decision_package as dp, make_package as mp
from tests.test_decision_package import _Delivery, small_deck, write

EXAMPLES = Path(__file__).parent.parent / "examples" / "packages"


def presentation() -> dict:
    return json.loads(json.dumps(mp.PRESENTATION_EXAMPLE))


def passing_check(monkeypatch, seen=None):
    def check(archive, deck, out):
        if seen is not None:
            seen.append(deck)
        return decision_check.Result(passed=["everything"])
    monkeypatch.setattr(decision_check, "check", check)


# -- a presentation ---------------------------------------------------------------------

def test_a_presentation_builds_without_votes(tmp_path, monkeypatch, capsys):
    delivery, seen = _Delivery(), []
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch, seen)
    assert mp.main([str(write(tmp_path / "in", presentation())), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--deliver-to", "http://127.0.0.1:19999"]) == 0
    archive = tmp_path / "out" / "team-lunch-moved-1.0.0.zip"
    names = zipfile.ZipFile(archive).namelist()
    assert "SKILL.md" not in names and "scripts/decide.py" not in names
    package = json.loads(zipfile.ZipFile(archive).read("package.json"))
    assert package == {"name": "team-lunch-moved", "version": "1.0.0",
                       "purpose": "Team lunch moved", "runs": "page", "entry": "index.html",
                       "story": "story.json", "returns": []}
    story = json.loads(zipfile.ZipFile(archive).read("story.json"))
    assert [s["title"] for s in story["slides"]] == ["What changed", "What you need to do"]
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
    assert "a presentation, 0 decision(s)" in capsys.readouterr().out


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
    (folder / "package.json").write_text(json.dumps({"purpose": "Show the numbers.",
                                                     **manifest}))
    (folder / "results").mkdir()
    (folder / "results" / "old.json").write_text("{}")
    return folder


# -- a package for a Work item carries his vote --------------------------------------

def asking(folder: Path) -> Path:
    """The page folder's story with an answers file and one waiting slide."""
    story = json.loads((folder / "story.json").read_text())
    story["answers"] = "results/decisions.json"
    story["slides"][-1]["waits"] = "go-ahead"
    (folder / "story.json").write_text(json.dumps(story))
    return folder


@pytest.mark.parametrize("check_only", [False, True])
def test_a_presentation_for_a_work_item_is_refused_until_it_asks(
        tmp_path, monkeypatch, capsys, check_only):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch)
    argv = [str(write(tmp_path / "in", presentation())), "--work", "work-1",
            "--out", str(tmp_path / "out"), "--deliver-to", "http://127.0.0.1:19999"]
    assert mp.main(argv + (["--check-only"] if check_only else [])) == 1
    out = capsys.readouterr().out
    assert "names work item work-1 but asks Tim nothing" in out and "Add the vote" in out
    assert "`decisions`" in out and "`waits`" in out, "it says where the vote goes"
    assert delivery.calls == [] and not (tmp_path / "out").exists(), "nothing built or sent"


def test_a_page_folder_for_a_work_item_is_refused_until_its_story_waits(
        tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    folder = page_folder(tmp_path / "in")
    argv = [str(folder), "--work", "work-1", "--out", str(tmp_path / "out"),
            "--deliver-to", "http://127.0.0.1:19999"]
    assert mp.main(argv) == 1
    assert "asks Tim nothing" in capsys.readouterr().out and delivery.calls == []
    story = json.loads((folder / "story.json").read_text())
    story["answers"] = "results/decisions.json"
    (folder / "story.json").write_text(json.dumps(story))
    assert mp.main(argv) == 1, "an answers file alone asks nothing: a slide must wait"
    assert delivery.calls == []
    asking(folder)
    assert mp.main(argv) == 0
    [(url, _token, _body)] = delivery.calls
    assert "work=work-1" in url


def test_a_package_that_asks_nothing_and_names_no_work_item_still_goes(
        tmp_path, monkeypatch):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch)
    assert mp.main([str(write(tmp_path / "in", presentation())), "--out", str(tmp_path / "a"),
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    assert mp.main([str(page_folder(tmp_path / "f")), "--out", str(tmp_path / "b"),
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    assert len(delivery.calls) == 2


def test_a_decision_deck_for_a_work_item_goes(tmp_path, monkeypatch):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    passing_check(monkeypatch)
    assert mp.main([str(write(tmp_path / "in", small_deck())), "--work", "work-1",
                    "--out", str(tmp_path / "out"), "--deliver-to", "http://127.0.0.1:19999"]) == 0
    assert len(delivery.calls) == 1


def test_a_folder_with_only_a_purpose_is_worked_out(tmp_path):
    manifest = mp.manifest_of(page_folder(tmp_path))
    assert manifest == {"purpose": "Show the numbers.", "runs": "page", "entry": "index.html",
                        "story": "story.json", "name": "globe-numbers",
                        "returns": ["results/**"], "version": "1.0.0"}


def test_a_folder_is_zipped_read_back_and_delivered(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    monkeypatch.setattr(decision_check, "check", lambda *a: pytest.fail("a folder is no deck"))
    assert mp.main([str(page_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--project", "globe", "--deliver-to", "http://127.0.0.1:19999"]) == 0
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


def test_a_decision_package_is_stamped_for_the_live_jarvis_a_presentation_is_not(
        tmp_path, monkeypatch):
    from jarvis import decision_stamp
    monkeypatch.setattr(dp, "post", _Delivery())
    passing_check(monkeypatch)
    token = data_paths.ensure_tool_token()
    for name, deck in (("d", small_deck()), ("p", presentation())):
        out = tmp_path / name / "out"
        assert mp.main([str(write(tmp_path / name, deck)), "--out", str(out),
                        "--deliver-to", "http://127.0.0.1:19999"]) == 0
        [archive] = out.glob("*.zip")
        names = zipfile.ZipFile(archive).namelist()
        assert (decision_stamp.STAMP in names) == (name == "d")
        assert decision_stamp.problem(archive, token) is None


def test_a_deck_given_as_a_folder_is_refused(tmp_path):
    with pytest.raises(dp.Refused) as refused:
        mp.make_folder(EXAMPLES / "decision-deck", tmp_path)
    assert "made from its content file" in " ".join(refused.value.problems)
