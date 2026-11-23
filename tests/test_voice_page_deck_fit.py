"""Every part of a decision slide fits its frame at every frame the layout
proof covers (Tim 2026-11-22): the question, the recommendation, the
context, each option with its Choose bar, Decide later, None of these, his
words field and the line saying what is recorded, nothing clipped and
nothing to scroll. Measured in CI on this fixture deck (tests/deck_measure.py:
the built deck drawn in JARVIS's frame in headless Chromium at every frame
from 662 x 489 to 1600 x 900); no package is measured on its way.

The decision here has the words of the first real package's decision that
ran past the bottom at 662 x 489 (as long, in the same places), so the
shared deck's layout (examples/packages/decision-deck, deck.css and
deck.js) is held to it.

Every part is judged against its own box as well (deck.js boxesOf, the
measure the same): an option card whose words and Choose bar are taller
than the card spills out of it though nothing scrolls or clips.

What passes the write-time capacity of a decision's slide and the answers
slide (jarvis/deck_capacity.py, worked out from deck.css at 662 x 489, every
word at its floor) is drawn whole: decks grown to the last size the
capacity takes are measured at every frame. Decks beyond it are built here
with that check set aside, to hold the shared deck's own fit.

A browser page test: run with `pytest -m page` (see pytest.ini)."""
from __future__ import annotations

import copy
import hashlib

import pytest
import yaml

from jarvis import deck_capacity, make_package as mp, picture_proof
from jarvis.presentation_source import DeckCompileError, read_source
from tests import deck_measure as deck_fit
from tests.deck_fixtures import PICTURE

pytestmark = pytest.mark.page

QUESTION = ("Put the overnight repairs ahead of the faster index searches, or send the plan "
            "back for a revision?")
CONTEXT = ("Return the plan with one live check for the monitor\n"
           "Trace an explicit build answer on the live system to the task it released and the "
           "assigned agent's first run. If no such case is there yet, follow the next one.\n"
           "If the agent stays idle: Show where the work stopped. Put the repair that starts "
           "the agent without another prompt first among the workflow fixes.\n"
           "If the agent starts: Leave the working steps alone. Show which failures remain.\n"
           "Bring back: The repairs still needed, in order, with the reason any should come "
           "before faster index searches.")
OPTIONS = [
    {"id": "reject", "name": "Reject — keep the index rework next",
     "says": "Have the monitor trace an authorized task and return a revised build order. "
             "Authorize no new repair."},
    {"id": "approve", "name": "Approve — build the repairs first",
     "says": "Give the plan the next slot, starting with cheaper automated tests. Defer the "
             "search improvement and keep the separate permission decisions."}]
REASON = ("Keep faster index searches next and have the monitor trace an authorized build "
          "before moving the repair plan ahead of the index rework.")


def _source(folder, deck: dict):
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / "presentation.yaml"
    source.write_text(yaml.safe_dump(deck, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return source


def _built(folder, deck: dict) -> object:
    made = mp.make_presentation(_source(folder, deck), folder / "out")
    return folder / "out" / "compiled" / made.deck["name"]


def _deck(folder, decision: dict) -> object:
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["decisions"] = [{**deck["decisions"][0], **decision}]
    return _built(folder, deck)


@pytest.fixture
def beyond_capacity(monkeypatch):
    """A decision deck built past what its slides hold, to hold the shared
    deck's own fit: the write-time check of decision and answers slides set
    aside."""
    monkeypatch.setattr(deck_capacity, "decision_problems", lambda *a, **k: [])
    monkeypatch.setattr(deck_capacity, "answers_problems", lambda *a, **k: [])


def test_the_long_decision_fits_every_frame(tmp_path):
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT, "options": OPTIONS,
                              "recommended": "reject", "reason": REASON})
    fit = deck_fit.measure(folder)
    assert len(fit["frames"]) >= 40 and [662, 489] in fit["frames"] and \
        [1600, 900] in fit["frames"]
    assert [s["id"] for s in fit["slides"]] == ["opening", "what-changed", "where", "answers"]
    assert fit["fits"], "\n".join(deck_fit.problems(fit))


def test_three_options_with_long_names_fit_every_frame(tmp_path, beyond_capacity):
    options = [{"id": f"o{n}", "name": f"Option {n}: " + "a considerably longer name " * 2,
                "says": "What this option changes, said in two plain sentences. " * 2}
               for n in range(1, 4)]
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT.split("\n")[1],
                              "options": options, "recommended": "o1", "reason": REASON})
    fit = deck_fit.measure(folder)
    assert fit["fits"], "\n".join(deck_fit.problems(fit))


def test_what_cannot_fit_is_measured_and_said(tmp_path, beyond_capacity):
    folder = _deck(tmp_path, {"question": QUESTION, "context": CONTEXT * 6, "options": OPTIONS,
                              "recommended": "reject", "reason": REASON})
    fit = deck_fit.measure(folder, [(662, 489), (1600, 900)])
    assert not fit["fits"]
    where = deck_fit.of_slide(fit, "where")
    assert not where["fits"] and where["cut"][0]["fit"] == "cut", "every step of the fit tried"
    parts = {p["part"] for c in where["cut"] for p in c["parts"]}
    assert {"Decide later", "None of these"} <= parts
    [problem] = deck_fit.problems(fit)
    assert problem.startswith("Slide 3 (where) does not fit even after JARVIS's own fitting")
    assert "Split it into two slides" in problem
    # One slide alone: the same measure.
    alone = deck_fit.measure(folder, [(662, 489), (1600, 900)], only=["where"])
    assert [s["id"] for s in alone["slides"]] == ["where"]
    assert alone["slides"][0]["cut"] == where["cut"] and alone["slides"][0]["slide"] == 3


# ── what the write-time capacity takes is drawn whole ─────────────────────

WORDS = ("each repair is checked on the live system before the next one starts and what it "
         "changed is written to the item it belongs to").split()


def _words(n: int) -> str:
    return " ".join(WORDS[i % len(WORDS)] for i in range(max(1, n))).capitalize() + "."


def _with(decisions: list[dict], assets: list[dict] = ()) -> dict:
    deck = copy.deepcopy(mp.EXAMPLE)
    deck["assets"] = list(assets)
    deck["decisions"] = decisions
    deck["slides"] = deck["slides"][:2] + [{"id": f"ask-{d['id']}", "type": "decision",
                                            "decision": d["id"]} for d in decisions]
    return deck


def _pictures(folder) -> list[dict]:
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for ident, fill in (("plate-a", "#22c55e"), ("plate-b", "#a855f7"), ("plate-c", "#38bdf8")):
        raw = PICTURE.format(w=320, h=200, fill=fill).encode()
        (folder / f"{ident}.svg").write_bytes(raw)
        p = picture_proof.prove_bytes(raw)
        out.append({"id": ident, "path": f"{ident}.svg", "sha256": hashlib.sha256(raw).hexdigest(),
                    "mime": p.mime, "width": p.width, "height": p.height,
                    "source": {"reference": "lunch-plan"}})
    return out


def _passes(folder, deck: dict) -> bool:
    try:
        read_source(_source(folder, deck))
        return True
    except DeckCompileError as e:
        assert all(d.code == "capacity" for d in e.diagnostics), str(e)
        return False


def _edge(folder, grown) -> tuple[dict, int]:
    """The deck `grown(n)` at the largest n the capacity takes (and n + 1
    refused), found by doubling and halving."""
    low, high = 0, 1
    while _passes(folder, grown(high)):
        low, high = high, high * 2
        assert high < 4096, "the capacity never refused it"
    while high - low > 1:
        mid = (low + high) // 2
        if _passes(folder, grown(mid)):
            low = mid
        else:
            high = mid
    assert low > 0, "the capacity refused even the least of it"
    return grown(low), low


def _options(n_options: int, says: str, name: str = "Choice", pictured: bool = False,
             prefix: str = "o"):
    return [{"id": f"{prefix}{k}", "name": f"{name} {k}", "says": says,
             **({"picture": ["plate-a", "plate-b", "plate-c"][k % 3]} if pictured else {})}
            for k in range(1, n_options + 1)]


def _ask(ident="where-to-eat", **fields) -> dict:
    base = {"id": ident, "question": "Which way do we take?", "recommended": "o1",
            "reason": "It is the cheaper of them.",
            "text_only": "The options are ways of working, not things to picture."}
    base.update(fields)
    if any(o.get("picture") for o in base.get("options") or []) or base.get("picture"):
        base.pop("text_only", None)
    return base


SHAPES = {
    "a long context": lambda n: _with([_ask(context=_words(n),
                                            options=_options(2, _words(12)))]),
    "four options": lambda n: _with([_ask(options=_options(4, _words(n)))]),
    "three long names": lambda n: _with([_ask(options=[
        {**o, "name": _words(n)} for o in _options(3, _words(10))])]),
    "a long question and reason": lambda n: _with([_ask(
        question=_words(n) + "?", reason=_words(n), options=_options(2, _words(8)))]),
    "a child question": lambda n: _with([
        _ask(options=[{**o, "opens": ["then-what"]} if o["id"] == "o1" else o
                      for o in _options(2, _words(8))]),
        _ask("then-what", question="And then which?", context=_words(n), recommended="t1",
             options=_options(3, _words(6), prefix="t"))]),
    "many decisions": lambda n: _with([_ask(f"part-{k}", short=f"Part {k} of the plan",
                                            options=[{"id": f"p{k}-{j}", "name": f"Way {j}",
                                                      "says": "Said plainly."} for j in (1, 2)],
                                            recommended=f"p{k}-1")
                                       for k in range(1, n + 1)]),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_what_the_capacity_takes_is_drawn_whole_at_every_frame(tmp_path, shape):
    deck, n = _edge(tmp_path / "try", SHAPES[shape])
    folder = _built(tmp_path / "built", deck)
    asked = [s["id"] for s in deck["slides"] if s["type"] == "decision"]
    fit = deck_fit.measure(folder, only=asked + ["answers"])
    assert [s["id"] for s in fit["slides"]] == asked + ["answers"]
    assert fit["fits"], f"{shape} at {n}: " + "\n".join(deck_fit.problems(fit))


@pytest.mark.parametrize("grow", ["says", "context"])
@pytest.mark.parametrize("own", [False, True], ids=["options pictured", "and its own picture"])
def test_pictured_options_the_capacity_takes_are_drawn_whole(tmp_path, grow, own):
    """Pictured options, and pictured options beside the decision's own
    picture (the picture yields its room or moves above them, never the
    options or their Choose bars), at the largest the capacity takes, are
    drawn whole at every frame: no part's words spilling out of its box."""
    assets = _pictures(tmp_path / "try")
    _pictures(tmp_path / "built")
    picture = {"picture": "plate-c"} if own else {}
    deck, n = _edge(tmp_path / "try", lambda n: _with(
        [_ask(options=_options(3, _words(n if grow == "says" else 8), pictured=True),
              context=_words(n if grow == "context" else 12), **picture)], assets))
    folder = _built(tmp_path / "built", deck)
    fit = deck_fit.measure(folder, only=["ask-where-to-eat", "answers"])
    assert fit["fits"], f"pictured at {n}: " + "\n".join(deck_fit.problems(fit))


def test_a_part_spilling_out_of_its_box_does_not_fit(tmp_path, beyond_capacity):
    """An option card shorter than its words and its Choose bar is cut,
    though nothing on the slide scrolls or clips: the deck's own fit and
    the measure both judge every part's content against its box."""
    assets = _pictures(tmp_path)
    deck = _with([_ask(options=_options(4, _words(60), pictured=True), context=_words(80),
                       picture="plate-c")], assets)
    fit = deck_fit.measure(_built(tmp_path, deck), [(662, 489)], only=["ask-where-to-eat"])
    [slide] = fit["slides"]
    assert not slide["fits"] and slide["cut"][0]["fit"] == "cut"
    assert any(p["how"] == deck_fit.SPILLS for c in slide["cut"] for p in c["parts"])
