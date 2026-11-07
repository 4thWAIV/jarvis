"""The one command that sends Tim a decision package
(jarvis/decision_package.py): what it refuses in the content, that it builds
with the shared deck's own builder, and that it delivers only after every
check passed. The check in a real, isolated JARVIS is
tests/test_voice_page_decision_package.py; here it is a stand-in."""
from __future__ import annotations

import copy
import json
import zipfile
from pathlib import Path

import pytest

from jarvis import decision_check, decision_package as dp

EXAMPLE = Path(__file__).parent.parent / "examples" / "packages" / "decision-deck"
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
       '<rect width="320" height="200" fill="#38bdf8"/></svg>\n')


def small_deck() -> dict:
    return {
        "name": "lunch-decisions", "version": "1.0.0",
        "purpose": "Two decisions on the team lunch.", "title": "Team lunch",
        "slides": [{"id": "s1", "title": "Why we are asking",
                    "blocks": [{"p": "Lunch is on Friday."},
                               {"picture": "pictures/plate.svg", "alt": "A plate"}]}],
        "decisions": [
            {"id": "where", "short": "Where", "question": "Where do we eat?",
             "options": [{"id": "in", "name": "In the office", "says": "Food brought in.",
                          "picture": "pictures/plate.svg"},
                         {"id": "out", "name": "A restaurant", "says": "A walk away.",
                          "picture": "pictures/plate.svg"}],
             "recommended": "in", "reason": "Nobody has to book a table."},
            {"id": "when", "short": "When", "question": "What time?",
             "options": [{"id": "noon", "name": "Noon", "says": "Before the stand-up.",
                          "picture": "pictures/plate.svg"},
                         {"id": "one", "name": "One o'clock", "says": "After it.",
                          "picture": "pictures/plate.svg"}],
             "recommended": "noon", "reason": "The afternoon stays free."},
        ],
    }


def write(folder: Path, deck: dict) -> Path:
    (folder / "pictures").mkdir(parents=True, exist_ok=True)
    (folder / "pictures" / "plate.svg").write_text(SVG)
    content = folder / "content.json"
    content.write_text(json.dumps(deck))
    return content


def problems(tmp_path, deck) -> list[str]:
    return dp.problems_of(deck, write(tmp_path, deck).parent)


def test_the_example_deck_and_a_small_deck_are_right(tmp_path):
    assert dp.validate(EXAMPLE / "deck-data.js")["name"] == "decision-deck-example"
    assert problems(tmp_path, small_deck()) == []


def test_every_problem_is_listed_at_once(tmp_path):
    deck = small_deck()
    del deck["title"]
    deck["decisions"][0]["recommended"] = "elsewhere"
    deck["decisions"][1]["options"] = deck["decisions"][1]["options"][:1]
    deck["decisions"][1]["reason"] = ""
    found = problems(tmp_path, deck)
    assert "The deck has no title." in found
    assert any("recommendation 'elsewhere' is not one of its options" in p for p in found)
    assert any("when: it needs at least two options" in p for p in found)
    assert any("when: it has no reason" in p for p in found)


@pytest.mark.parametrize("change, said", [
    (lambda d: d["decisions"][0].update(id="Where Now"), "its id must be a slug"),
    (lambda d: d["decisions"][1].update(id="where"), "two decisions have this id"),
    (lambda d: d["decisions"][0]["options"][0].update(id="later"), "not later, none"),
    (lambda d: d["decisions"][0]["options"][1].update(id="in"), "two options have this id"),
    (lambda d: d["decisions"][0]["options"][0].update(says=""), "it has no says"),
    (lambda d: d["decisions"][0].update(question=""), "it has no question"),
    (lambda d: d["decisions"][0].update(reason="One.\nTwo."), "its reason must be one line"),
    (lambda d: d["decisions"][0].update(question="Why " * 40), "the question is 160"),
    (lambda d: d["decisions"][0]["options"][0].update(opens=["nowhere"]), "opens 'nowhere'"),
    (lambda d: d["slides"].append({"decision": "nowhere"}), "places decision 'nowhere'"),
    (lambda d: d["slides"][0]["blocks"].append({"p": "word " * 70}), "a line is 350"),
    (lambda d: d["slides"][0]["blocks"].extend({"p": "word " * 50} for _ in range(3)),
     "Split it into two slides"),
    (lambda d: d.update(language="fr"), "The language is 'fr'"),
])
def test_each_rule_refuses(tmp_path, change, said):
    deck = small_deck()
    change(deck)
    found = problems(tmp_path, deck)
    assert any(said in p for p in found), found


def test_pictures_must_be_images_beside_the_content(tmp_path):
    deck = small_deck()
    deck["slides"][0]["blocks"][1]["picture"] = "pictures/missing.png"
    assert any("is not at" in p for p in problems(tmp_path, deck))
    deck["slides"][0]["blocks"][1]["picture"] = "/etc/hosts"
    assert any("must be a path beside" in p for p in problems(tmp_path, deck))
    deck["slides"][0]["blocks"][1]["picture"] = "pictures/notes.png"
    (tmp_path / "pictures").mkdir(exist_ok=True)
    (tmp_path / "pictures" / "notes.png").write_text("not an image")
    assert any("is not a PNG, JPEG" in p for p in problems(tmp_path, deck))
    (tmp_path / "pictures" / "notes.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
    assert problems(tmp_path, deck) == []


def test_it_builds_with_the_shared_builder(tmp_path):
    content = write(tmp_path / "in", small_deck())
    archive = dp.build(content, tmp_path / "out", dp.validate(content))
    assert archive.name == "lunch-decisions-1.0.0.zip"
    names = zipfile.ZipFile(archive).namelist()
    for name in ("package.json", "index.html", "deck.js", "deck-data.js", "story.json",
                 "scripts/decide.py", "pictures/plate.svg"):
        assert name in names
    assert "new_deck.py" not in names


class _Delivery:
    def __init__(self):
        self.calls = []

    def __call__(self, url, token, body, timeout=60):
        self.calls.append((url, token, body))
        return {"package": {"id": "lunch-decisions-1.0.0-local1"}, "unpacked": True}


def test_refused_content_is_not_built_checked_or_delivered(tmp_path, monkeypatch, capsys):
    deck = small_deck()
    deck["decisions"][0]["recommended"] = "elsewhere"
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    monkeypatch.setattr(decision_check, "check", lambda *a: pytest.fail("checked"))
    assert dp.main([str(write(tmp_path / "in", deck)), "--out", str(tmp_path / "out")]) == 1
    assert "Not built" in capsys.readouterr().out
    assert delivery.calls == [] and not list((tmp_path / "out").glob("*.zip"))


def test_a_failed_check_delivers_nothing(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    failed = decision_check.Result(passed=["one"], failures=["809x589 slide 2: too tall"],
                                   screenshots=[tmp_path / "shot.png"])
    monkeypatch.setattr(decision_check, "check", lambda *a: failed)
    assert dp.main([str(write(tmp_path / "in", small_deck())),
                    "--out", str(tmp_path / "out")]) == 1
    said = capsys.readouterr().out
    assert "Not delivered" in said and "too tall" in said and "shot.png" in said
    assert delivery.calls == []


def test_a_passed_check_delivers_once_with_the_token(tmp_path, monkeypatch, capsys):
    from jarvis import data_paths
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    monkeypatch.setattr(decision_check, "check",
                        lambda *a: decision_check.Result(passed=["everything"]))
    assert dp.main([str(write(tmp_path / "in", small_deck())), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--session", "abc",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, token, body)] = delivery.calls
    assert url == "http://127.0.0.1:19999/api/packages/local"
    assert token == data_paths.ensure_tool_token()
    assert body == {"path": str(tmp_path / "out" / "lunch-decisions-1.0.0.zip")}
    said = capsys.readouterr().out
    assert token not in said
    assert "lunch-decisions-1.0.0-local1" in said and '"Open the Team lunch package."' in said
    record = json.loads((tmp_path / "out" / "delivery.json").read_text())
    assert record["project"] == "lunch" and record["session"] == "abc"


def test_a_check_result_needs_a_pass_and_no_failure():
    assert not decision_check.Result().ok
    assert decision_check.Result(passed=["a"]).ok
    assert not decision_check.Result(passed=["a"], failures=["b"]).ok


def test_the_deck_given_is_not_changed(tmp_path):
    deck = small_deck()
    before = copy.deepcopy(deck)
    problems(tmp_path, deck)
    assert deck == before


def test_every_option_is_pictured_unless_the_decision_says_why_not(tmp_path):
    deck = small_deck()
    del deck["decisions"][1]["options"][1]["picture"]
    [problem] = problems(tmp_path, deck)
    assert "Decision when: option one has no picture" in problem and "text_only" in problem
    deck["decisions"][1]["text_only"] = "too short"
    assert len(problems(tmp_path, deck)) == 1
    deck["decisions"][1]["text_only"] = "A time of day has no picture that shows it better."
    assert problems(tmp_path, deck) == []


def test_only_a_stamped_decision_package_unpacks_and_only_for_its_token(tmp_path):
    from jarvis import decision_stamp
    out = tmp_path / "out"
    deck = dp.validate(write(tmp_path / "in", small_deck()))
    archive = dp.build(tmp_path / "in" / "content.json", out, deck)
    assert decision_stamp.is_decision_package(archive)
    assert "has not passed its check" in decision_stamp.problem(archive, "token-a")
    stamped = tmp_path / "stamped.zip"
    decision_stamp.stamp(archive, stamped, "token-a")
    assert decision_stamp.problem(stamped, "token-a") is None
    assert "not checked for this JARVIS" in decision_stamp.problem(stamped, "token-b")
    decision_stamp.stamp(stamped, stamped, "token-b")       # stamped again in place
    assert decision_stamp.problem(stamped, "token-b") is None
    with zipfile.ZipFile(archive) as a, zipfile.ZipFile(stamped) as b:
        names = [n for n in b.namelist() if not n.endswith(decision_stamp.STAMP)]
        assert sorted(names) == sorted(a.namelist())
        assert all(a.read(n) == b.read(n) for n in names), "the files checked are the files sent"
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(stamped) as b, zipfile.ZipFile(changed, "w") as c:
        for info in b.infolist():
            data = b.read(info)
            c.writestr(info, data + b"// changed" if info.filename.endswith("deck.js") else data)
    assert "changed after its check" in decision_stamp.problem(changed, "token-b")
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("package.json", "{}")
    assert decision_stamp.problem(plain, "token-a") is None, "only decision packages"
