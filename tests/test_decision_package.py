"""The one command that sends Tim a decision package
(jarvis/decision_package.py): what it refuses in the content, that it builds
with the shared deck's own builder, and that it delivers only a deck
JARVIS laid out and proved (presentation_proof), signed with the session's
own key. Decks are written as presentation.yaml (tests/deck_fixtures); the
shared deck's data form is what these content rules read."""
from __future__ import annotations

import base64
import copy
import json
import zipfile
from pathlib import Path

import pytest

from jarvis import decision_package as dp, decision_stamp
from tests import picture_fixtures

pytestmark = pytest.mark.usefixtures("authored_package_source")

EXAMPLE = Path(__file__).parent.parent / "examples" / "packages" / "decision-deck"
PNG = picture_fixtures.png()
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200">'
       '<rect width="320" height="200" fill="#38bdf8"/></svg>\n')


def small_deck() -> dict:
    return {
        "name": "lunch-decisions", "version": "1.0.0",
        "purpose": "Two decisions on the team lunch.", "title": "Team lunch",
        "conclusion": "Eat in the office at noon, so nobody books a table.",
        "slides": [{"id": "s1", "title": "Two decisions on the team lunch",
                    "blocks": [{"lead": "Eat in the office at noon, so nobody books a table."},
                               {"picture": "pictures/plate.svg", "alt": "A plate"}],
                    "say": "Two decisions on the team lunch. I suggest the office at noon."}],
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
    (lambda d: d["decisions"][0]["options"][0].update(opens=["nowhere"]), "opens 'nowhere'"),
    (lambda d: d["slides"].append({"decision": "nowhere"}), "places decision 'nowhere'"),
    (lambda d: d.update(language="fr"), "The language is 'fr'"),
])
def test_each_rule_refuses(tmp_path, change, said):
    deck = small_deck()
    change(deck)
    found = problems(tmp_path, deck)
    assert any(said in p for p in found), found


@pytest.mark.parametrize("change", [
    lambda d: d["decisions"][0].update(reason="One.\nTwo."),
    lambda d: d["decisions"][0].update(question="Why " * 40),
    lambda d: d["slides"][0]["blocks"].append({"p": "word " * 70}),
    lambda d: d["slides"][0]["blocks"].extend({"p": "word " * 50} for _ in range(3)),
])
def test_no_length_of_the_author_s_words_is_refused(tmp_path, change):
    """How long a question, reason, line or slide is, and how many lines a
    reason has, is the author's: no character or word count refuses it."""
    deck = small_deck()
    change(deck)
    assert problems(tmp_path, deck) == []


def test_pictures_must_be_images_beside_the_content(tmp_path):
    deck = small_deck()
    deck["slides"][0]["blocks"][1]["picture"] = "pictures/missing.png"
    assert any("is not at" in p for p in problems(tmp_path, deck))
    deck["slides"][0]["blocks"][1]["picture"] = "/etc/hosts"
    assert any("must be a path beside" in p for p in problems(tmp_path, deck))
    deck["slides"][0]["blocks"][1]["picture"] = "pictures/notes.png"
    (tmp_path / "pictures").mkdir(exist_ok=True)
    (tmp_path / "pictures" / "notes.png").write_text("not an image")
    assert any("is not a PNG, GIF, JPEG" in p for p in problems(tmp_path, deck))
    (tmp_path / "pictures" / "notes.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 20)
    assert any("fails its CRC" in p for p in problems(tmp_path, deck))
    (tmp_path / "pictures" / "notes.png").write_bytes(PNG)
    assert problems(tmp_path, deck) == []


@pytest.mark.parametrize("root", [
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200">',
    '<svg xmlns="http://www.w3.org/2000/svg" width="320px" height="200">',
])
def test_an_svg_s_size_is_read_from_it(tmp_path, root):
    """An SVG need not state its size in pixels: JARVIS reads its width and
    height, else its viewBox."""
    deck = small_deck()
    content = write(tmp_path, deck)
    (tmp_path / "pictures" / "plate.svg").write_text(root + '<rect width="10" height="10"/></svg>')
    assert dp.problems_of(deck, content.parent) == []


@pytest.mark.parametrize("root", [
    '<svg xmlns="http://www.w3.org/2000/svg" width="320">',
    '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="200">',
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0 200">',
])
def test_an_svg_with_no_size_at_all_is_refused(tmp_path, root):
    """One with neither a size nor a viewBox, or a size of zero, draws as
    nothing: a blank slide that passed every check."""
    deck = small_deck()
    content = write(tmp_path, deck)
    (tmp_path / "pictures" / "plate.svg").write_text(root + '<rect width="10" height="10"/></svg>')
    found = dp.problems_of(deck, content.parent)
    assert any("Picture pictures/plate.svg" in p and "draws as nothing" in p for p in found), found


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

    def __call__(self, url, token, body, timeout=60, signer=None):
        self.calls.append((url, signer, body))
        return {"message": {"id": 41}, "package": {"id": "lunch-decisions-1.0.0-local1"},
                "arrived": True, "inbox": {"id": "pkg-0123456789ab", "title": "Team lunch"}}


def test_refused_content_is_not_built(tmp_path):
    from jarvis import make_package as mp
    from tests.deck_fixtures import write_yaml
    deck = small_deck()
    deck["decisions"][0]["recommended"] = "elsewhere"
    with pytest.raises(dp.Refused) as refused:
        mp.make_presentation(write_yaml(tmp_path / "in", deck), tmp_path / "out")
    assert "does not belong to this decision" in " ".join(refused.value.problems)
    assert not list((tmp_path / "out").glob("*.zip"))

def test_a_deck_whose_picture_cannot_be_read_is_not_built(tmp_path):
    from jarvis import make_package as mp
    from tests.deck_fixtures import write_yaml
    banner = ('<svg xmlns="http://www.w3.org/2000/svg" width="4000" height="20">'
              '<rect width="4000" height="20" fill="#38bdf8"/></svg>\n')
    with pytest.raises(dp.Refused) as refused:
        mp.make_presentation(write_yaml(tmp_path / "in", small_deck(), svg=banner),
                             tmp_path / "out")
    assert "cannot be read" in " ".join(refused.value.problems)

def _key(session="abc"):
    """The session's own key, made as `agent_key init` makes it."""
    from jarvis import agent_keys
    return agent_keys.make_pair(agent_keys.key_folder(session), f"jarvis-agent {session}")


def _agent_folder(root: Path) -> Path:
    folder = root / "Count Words"
    folder.mkdir(parents=True)
    (folder / "BRIEF.md").write_text("Count the words.\n")
    (folder / "package.json").write_text(json.dumps({"purpose": "Count the words."}))
    return folder


def test_nothing_is_delivered_without_the_session_s_own_key(tmp_path, monkeypatch, capsys):
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    assert dp.main([str(_agent_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--session", "nokey"]) == 1
    assert "no key to sign it with" in capsys.readouterr().out and delivery.calls == []

def test_a_package_delivers_once_signed_with_the_session_s_key(tmp_path, monkeypatch):
    from jarvis import package_signature
    private, public = _key()
    delivery = _Delivery()
    monkeypatch.setattr(dp, "post", delivery)
    assert dp.main([str(_agent_folder(tmp_path / "in")), "--out", str(tmp_path / "out"),
                    "--project", "lunch", "--session", "abc",
                    "--deliver-to", "http://127.0.0.1:19999"]) == 0
    [(url, signer, body)] = delivery.calls
    assert url.startswith("http://127.0.0.1:19999/api/messages?project=lunch"), \
        "a message carrying it"
    assert signer == (private, public), "the request is signed with the session's own key"
    archive = tmp_path / "out" / "count-words-1.0.0.zip"
    assert base64.b64decode(body["package"]) == archive.read_bytes()
    assert body["says"] == "Agent package: count words"
    signed = package_signature.read(archive)
    assert signed.session == "abc" and signed.checks(public)


def test_a_built_deck_carries_its_source_and_layout_proof(tmp_path):
    from jarvis import make_package as mp
    from tests.deck_fixtures import write_yaml
    made = mp.make_presentation(write_yaml(tmp_path / "in", small_deck()), tmp_path / "out")
    assert decision_stamp.carries_proof(made.archive), "its source and layout proof go with it"

def test_the_deck_given_is_not_changed(tmp_path):
    deck = small_deck()
    before = copy.deepcopy(deck)
    problems(tmp_path, deck)
    assert deck == before


# -- the short form: the agent supplies the substance, the system the rest --------------

def brief() -> dict:
    return {
        "title": "Team lunch",
        "purpose": "Settle where and when the team eats on Friday.",
        "conclusion": "Eat in the office at noon.",
        "decisions": [
            {"question": "Where do we eat on Friday?",
             "options": [{"name": "In the office", "says": "Food is brought in.",
                          "recommended": True, "opens": ["What time?"],
                          "picture": "pictures/plate.svg"},
                         {"name": "A restaurant", "says": "A short walk away.",
                          "picture": "pictures/plate.svg"}],
             "reason": "Nobody has to book a table."},
            {"short": "When", "question": "What time?",
             "text_only": "A time of day has no picture that shows it.",
             "options": [{"name": "Noon", "says": "Before the stand-up."},
                         {"name": "None", "says": "Whenever."}],
             "recommended": "Noon", "reason": "The afternoon stays free."},
        ],
        "slides": [{"title": "Where and when the team eats on Friday",
                    "blocks": [{"lead": "Eat in the office at noon."}],
                    "say": "This settles the team lunch on Friday: the office, at noon."},
                   {"decision": "When"}],
    }


def test_the_short_form_is_filled_in_and_valid(tmp_path):
    deck = dp.validate(write(tmp_path, brief()))
    assert (deck["name"], deck["version"]) == ("team-lunch", "1.0.0")
    where, when = deck["decisions"]
    assert where["id"] == "where-do-we-eat-on-friday"[:len(where["id"])]
    assert where["recommended"] == "in-the-office"
    assert where["options"][0]["opens"] == ["when"] and "recommended" not in where["options"][0]
    assert [o["id"] for o in when["options"]] == ["noon", "none-option"]
    assert when["recommended"] == "noon"
    assert [s.get("decision") or "opening" for s in deck["slides"]] == ["opening", "when"]


def test_a_full_form_deck_passes_through_expansion_unchanged():
    deck = small_deck()
    expanded = dp.expand(deck)
    assert expanded == deck
    example = dp.read(EXAMPLE / "deck-data.js")
    assert dp.expand(example) == example


def test_a_misspelt_field_is_named_not_ignored(tmp_path):
    deck = brief()
    deck["decisions"][0]["recomended"] = "x"
    deck["decisions"][1]["options"][0]["sais"] = "x"
    said = " ".join(problems_of_expanded(tmp_path, deck))
    assert "'recomended' is not a field of a decision; did you mean 'recommended'?" in said
    assert "'sais'" in said and "did you mean 'says'" in said


def problems_of_expanded(tmp_path, deck):
    content = write(tmp_path, deck)
    return dp.problems_of(dp.expand(dp.read(content)), content.parent)


def test_a_short_form_without_a_recommendation_is_refused(tmp_path):
    deck = brief()
    del deck["decisions"][0]["options"][0]["recommended"]
    assert any("recommendation" in p for p in problems_of_expanded(tmp_path, deck))


def test_check_only_validates_and_stops(tmp_path, monkeypatch, capsys):
    from tests.deck_fixtures import write_yaml
    monkeypatch.setattr(dp, "post", lambda *a, **k: pytest.fail("delivered"))
    assert dp.main([str(write_yaml(tmp_path, small_deck())), "--check-only"]) == 0
    assert "2 decision(s), package lunch-decisions-1.0.0" in capsys.readouterr().out
    assert not list(tmp_path.glob("*.zip"))


def test_every_option_is_pictured_unless_the_decision_says_why_not(tmp_path):
    deck = small_deck()
    del deck["decisions"][1]["options"][1]["picture"]
    [problem] = problems(tmp_path, deck)
    assert "Decision when: option one has no picture" in problem and "text_only" in problem
    deck["decisions"][1]["text_only"] = "  "
    assert len(problems(tmp_path, deck)) == 1, "it says why"
    deck["decisions"][1]["text_only"] = "No picture."
    assert problems(tmp_path, deck) == [], "however few its words"


def test_a_signature_covers_every_file_and_names_what_it_vouches_for(tmp_path):
    from jarvis import agent_keys, decision_stamp, package_signature
    out = tmp_path / "out"
    deck = dp.validate(write(tmp_path / "in", small_deck()))
    archive = dp.build(tmp_path / "in" / "content.json", out, deck)
    assert decision_stamp.is_decision_package(archive)
    assert package_signature.read(archive) is None, "unsigned until it is checked"
    private, public = _key()
    signed_zip = tmp_path / "signed.zip"
    package_signature.sign(archive, signed_zip, private, public, "abc", (package_signature.DECISION_CHECKED,))
    signed = package_signature.read(signed_zip)
    assert signed.fingerprint == agent_keys.fingerprint(public) and signed.checks(public)
    other = agent_keys.make_pair(tmp_path / "other", "x")[1]
    assert not signed.checks(other), "only the key that signed it"
    with zipfile.ZipFile(archive) as a, zipfile.ZipFile(signed_zip) as b:
        names = [n for n in b.namelist() if not n.endswith(package_signature.FILE)]
        assert sorted(names) == sorted(a.namelist())
        assert all(a.read(n) == b.read(n) for n in names), "the files checked are the files sent"
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(signed_zip) as b, zipfile.ZipFile(changed, "w") as c:
        for info in b.infolist():
            data = b.read(info)
            c.writestr(info, data + b"// changed" if info.filename.endswith("deck.js") else data)
    assert not package_signature.read(changed).checks(public), "changed after it was signed"
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("package.json", "{}")
    assert not decision_stamp.is_decision_package(plain), "only decision packages"
