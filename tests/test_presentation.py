"""Source integrity and identity across the production YAML compiler boundary."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from jarvis.presentation_source import DeckCompileError, read_source

EXAMPLE = Path(__file__).parents[1] / "examples/presentations/street"


@pytest.fixture
def source(tmp_path):
    folder = tmp_path / "source"
    shutil.copytree(EXAMPLE, folder)
    return folder / "presentation.yaml"


def change(source, mutate):
    data = yaml.safe_load(source.read_text())
    mutate(data)
    source.write_text(yaml.safe_dump(data, sort_keys=False))


def test_stable_source_identity_and_provenance(source):
    first = read_source(source)
    change(source, lambda d: d["slides"][1]["objects"].reverse())
    second = read_source(source)
    assert first.digest != second.digest
    assert set(first.nodes) == set(second.nodes)
    assert second.document.references[0].kind == "authored-example"


@pytest.mark.parametrize("mutate,code,words", [
    (lambda d: d.update(version="presentation/2"), "capability", "presentation/1"),
    (lambda d: d["slides"][1]["objects"][3]["bars"][0].update(value="85"), "schema", "number"),
    (lambda d: d.update(theme={"font": "system-sans"}), "layout", "never carries `theme`"),
    (lambda d: d["slides"][1]["objects"][0].update(region="street-heading"), "layout", "give it a `slot`"),
    (lambda d: d["slides"][1].update(layout="street-layout"), "layout", "named layouts"),
    (lambda d: (d["slides"][1]["objects"][0].update(slot="fourth"),
                d["slides"][1]["objects"][1].update(slot="words")), "layout", "holds text"),
    (lambda d: d["slides"][1]["objects"][1].update(slot="fourth"), "layout", "columns filled in order"),
    (lambda d: d["slides"][1]["objects"][0].update(typo=True), "schema", "Extra"),
    (lambda d: d["slides"][0]["narration"][0].update(focus=["roof"]), "reference", "not visible"),
    (lambda d: d["slides"][0]["narration"][0].update(focus=["#roof"]), "schema", "pattern"),
    (lambda d: d["slides"][1]["objects"][0].update(id="opening-words"), "reference", "Duplicate ID"),
    (lambda d: d["slides"][1]["readingOrder"].append("water-route"), "accessibility", "exactly once"),
    (lambda d: d["assets"][0].update(sha256="0" * 64), "asset", "SHA-256"),
    (lambda d: d["assets"][0].update(path="../escape.svg"), "asset", "traverses"),
    (lambda d: d["assets"][0].update(path="index.html"), "asset", "collides"),
    (lambda d: d["slides"][1]["objects"][1]["members"][0].update(d="M,,1 2"), "schema", "separator"),
    (lambda d: d["slides"][1]["objects"][1]["members"][0].update(d="M 1 2 L 3"), "schema", "argument count"),
    (lambda d: d["slides"][1]["objects"][1]["members"][0].update(d="M 1 2 A 3 4 0 2 1 8 9"), "schema", "flags"),
    (lambda d: d["slides"].pop(), "reference", "Every decision"),
    (lambda d: d["assets"][0].update(width=241), "asset", "not the declared 241"),
    (lambda d: d["assets"][0].update(mime="image/png"), "asset", "not the declared image/png"),
])
def test_invalid_source_is_refused_before_rendering(source, mutate, code, words):
    change(source, mutate)
    with pytest.raises(DeckCompileError) as error:
        read_source(source)
    diagnostic = error.value.diagnostics[0]
    assert diagnostic.code == code
    assert words in str(error.value)
    assert diagnostic.line > 0 and diagnostic.column > 0


def test_duplicate_key_reports_both_locations(source):
    source.write_text(source.read_text().replace("title: A cooler city block", "title: First title\ntitle: Second title"))
    with pytest.raises(DeckCompileError, match="Duplicate mapping key.*first at.*again at"):
        read_source(source)


@pytest.mark.parametrize("tail", ["\n---\nid: another\n", "\ntest: &saved {}\n", "\ntest: !!str hello\n"])
def test_yaml_extensions_are_not_silently_normalized(source, tail):
    source.write_text(source.read_text() + tail)
    with pytest.raises(DeckCompileError):
        read_source(source)


def test_static_svg_refuses_active_content(source):
    import hashlib
    image = source.parent / "planted.svg"
    image.write_text(image.read_text().replace("<rect", '<script>alert(1)</script><rect', 1))
    change(source, lambda d: d["assets"][0].update(sha256=hashlib.sha256(image.read_bytes()).hexdigest()))
    with pytest.raises(DeckCompileError, match="not a static drawing element"):
        read_source(source)


def test_compiler_reuses_decisions_and_preserves_target_source(source, tmp_path):
    from jarvis.presentation import compile_deck
    from jarvis import package_story
    made = compile_deck(source, tmp_path / "compiled")
    folder = made.package_source
    assert (folder / "source.yaml").read_bytes() == source.read_bytes()
    assert (folder / "scripts/decide.py").read_bytes() == (EXAMPLE.parents[1] / "packages/decision-deck/scripts/decide.py").read_bytes()
    targets = json.loads(made.target_index.read_text())
    roof = next(t for t in targets if t["member"] == "roof")
    assert roof["source_sha256"] == made.source_sha256
    assert roof["reference"] == "sample-evidence"
    story = package_story.parse((folder / "story.json").read_bytes())
    # The standard vote is asked at the package view's Done, never as a slide.
    assert [slide.waits for slide in story.slides if slide.waits] == ["choose-street-trial"]
    assert json.loads((folder / "package.json").read_text())["vote"] == "approve-package"
    assert any("#" + roof["dom_id"] in beat.targets for slide in story.slides for beat in slide.beats)
    manifest = json.loads((folder / "package.json").read_text())
    assert manifest["understanding"]["source.yaml"] == manifest["brief"]


def test_maximum_story_uses_the_existing_story_limit(source, tmp_path):
    from copy import deepcopy
    from jarvis import make_package, package_story
    from jarvis.presentation import _deck, _targets
    data = yaml.safe_load(source.read_text())
    opening = data["slides"][0]
    data["decisions"] = []
    data["slides"] = [opening]
    # With no decisions the story is the slides alone: the standard vote is
    # asked at the package view's Done, never as a slide.
    for n in range(package_story.MAX_SLIDES - 1):
        slide = deepcopy(opening)
        suffix = f"-repeat-{n}"
        slide["id"] += suffix
        mapping = {}
        for obj in slide["objects"]:
            mapping[obj["id"]] = obj["id"] + suffix
            obj["id"] += suffix
            for paragraph in obj["paragraphs"]:
                paragraph["id"] += suffix
                for run in paragraph["runs"]:
                    mapping[run["id"]] = run["id"] + suffix
                    run["id"] += suffix
        slide["readingOrder"] = [mapping[id] for id in slide["readingOrder"]]
        for cue in slide["narration"]:
            cue["id"] += suffix
            cue["focus"] = [mapping[id] for id in cue["focus"]]
        data["slides"].append(slide)
    source.write_text(yaml.safe_dump(data, sort_keys=False))
    parsed = read_source(source)
    deck = make_package.prepare_deck(_deck(parsed, _targets(parsed)), source.parent)
    story = make_package.dp.builder().story(deck)
    assert len(package_story.parse(package_story.encode(story)).slides) == package_story.MAX_SLIDES
    extra = deepcopy(data["slides"][-1])
    renames = {}
    def rename(value):
        if isinstance(value, dict):
            if "id" in value:
                old = value["id"]
                value["id"] = old + "-extra"
                renames[old] = value["id"]
            for child in value.values():
                rename(child)
        elif isinstance(value, list):
            for child in value:
                rename(child)
    rename(extra)
    extra["readingOrder"] = [renames[id] for id in extra["readingOrder"]]
    for cue in extra["narration"]:
        cue["focus"] = [renames[id] for id in cue["focus"]]
    data["slides"].append(extra)
    source.write_text(yaml.safe_dump(data, sort_keys=False))
    from jarvis.presentation import compile_deck
    with pytest.raises(DeckCompileError, match="60") as failure:
        compile_deck(source, tmp_path / "over-limit")
    assert failure.value.diagnostics[0].code == "production"
    assert not (tmp_path / "over-limit" / data["id"]).exists()
    story["slides"].append(deepcopy(story["slides"][0]))
    with pytest.raises(package_story.StoryError, match="60"):
        package_story.parse(package_story.encode(story))


def test_visible_source_ids_and_frozen_file_evidence(source, tmp_path):
    from jarvis.presentation import compile_deck
    evidence=source.parent/'evidence.txt'
    evidence.write_text('A frozen observation for this presentation.\n')
    change(source, lambda d:d['references'][0].update(kind='file',locator='evidence.txt'))
    made=compile_deck(source,tmp_path/'compiled')
    targets=json.loads(made.target_index.read_text())
    authored=yaml.safe_load(source.read_text())
    expected={slide['id'] for slide in authored['slides']}
    for decision in authored['decisions']:
        expected.add(decision['id']);expected.update(option['id'] for option in decision['options'])
    assert expected <= {target['member'] for target in targets}
    assert all(t['reference']==authored['id'] for t in targets if t['member'] in expected)
    frozen=made.package_source/'reference-evidence/sample-evidence.bin'
    assert frozen.read_bytes()==evidence.read_bytes()
    evidence.write_text('Changed after creation.')
    assert frozen.read_text()=='A frozen observation for this presentation.\n'


def test_author_proof_includes_frozen_file_dependency(source):
    from jarvis.author_proof import source_digest, source_files
    evidence=source.parent/'observation.txt'
    evidence.write_text('First observation.')
    change(source,lambda d:d['references'][0].update(kind='file',locator='observation.txt'))
    before=source_digest(source)
    assert 'planted.svg' in source_files(source)
    assert 'reference-evidence/sample-evidence.bin' in source_files(source)
    evidence.write_text('Changed observation.')
    assert source_digest(source)!=before


@pytest.fixture
def presentation_page(source, tmp_path):
    """Compile before opening the browser; compilation has its own layout check."""
    from contextlib import ExitStack
    from playwright.sync_api import sync_playwright
    from jarvis.presentation import compile_deck

    with ExitStack() as resources:
        def load(mutate=lambda data: None):
            change(source, mutate)
            compiled = compile_deck(source, tmp_path / 'browser-compiled')
            playwright = resources.enter_context(sync_playwright())
            browser = playwright.chromium.launch()
            resources.callback(browser.close)
            page = browser.new_page(viewport={'width': 388, 'height': 602})
            helper = EXAMPLE.parents[2] / 'jarvis/package_story.js'
            page.route('**/api/packages/story.js', lambda route: route.fulfill(
                body=helper.read_text(), content_type='application/javascript'))
            page.goto((compiled.package_source / 'index.html').as_uri())
            page.wait_for_function("window.JarvisPresentation && document.querySelector('.slide.current')")
            page.evaluate('async()=>{await document.fonts.ready;}')
            return page, compiled
        yield load


def test_details_follows_reading_order_when_objects_are_reversed(presentation_page):
    def reverse_objects(data):
        for slide in data['slides']:
            if slide['type'] == 'content':
                slide['objects'].reverse()
    page, compiled = presentation_page(reverse_objects)
    data = yaml.safe_load((compiled.package_source / 'source.yaml').read_text())
    targets = json.loads(compiled.target_index.read_text())
    slide = data['slides'][1]
    order = [target['member'] for target in targets if target['slide'] == slide['id']
             and target['member'] in {obj['id'] for obj in slide['objects']}]
    assert order == slide['readingOrder']
    assert slide['readingOrder'] != [obj['id'] for obj in slide['objects']]
    opening = data['slides'][0]
    expected = [opening['id'], opening['id'] + '-title']
    for obj in opening['objects']:
        expected.append(obj['id'])
        for paragraph in obj['paragraphs']:
            expected.append(paragraph['id'])
            expected.extend(run['id'] for run in paragraph['runs'])
    page.evaluate('window.JarvisPresentation.inspect()')
    labels = page.locator('.presentation-inspector button').all_text_contents()[1:]
    names = {target['member']: target['accessible_name'] for target in targets}
    assert labels == [names[ident] for ident in expected]


@pytest.mark.parametrize('role', ['paragraph', 'heading', 'bullet', 'numbered'])
def test_wrapped_text_targets_share_range_geometry_with_narration(presentation_page, role):
    def text_role(data):
        data['slides'][0]['objects'][0]['paragraphs'][1]['role'] = role
    page, compiled = presentation_page(text_role)
    data = yaml.safe_load((compiled.package_source / 'source.yaml').read_text())
    obj = data['slides'][0]['objects'][0]
    paragraph = obj['paragraphs'][1]
    members = [obj['id'], paragraph['id'], paragraph['runs'][0]['id']]
    for width in (240, 388):
        page.set_viewport_size({'width': width, 'height': 602})
        for member in members:
            # The oracle uses native text-node ranges, independently of the API.
            measured = page.evaluate('''id => {
              const target=window.PRESENTATION.targets.find(t=>t.member===id);
              const el=document.getElementById(target.dom_id), walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
              let node;const expected=[];
              while(node=walker.nextNode()) {
                if(!node.textContent.trim())continue;
                const r=document.createRange();r.selectNodeContents(node);
                expected.push(...[...r.getClientRects()].filter(b=>b.width&&b.height).map(b=>b.toJSON()));
              }
              return {expected,actual:window.JarvisPresentation.rectangles(id),selector:'#'+target.dom_id};
            }''', member)
            assert measured['actual'] == measured['expected']
            if width == 240:
                assert len(measured['expected']) > 1, 'the test must exercise wrapped text'
            page.evaluate("m=>window.postMessage(m,'*')", {
                'jarvis': 'package', 'type': 'story.cue', 'beat': f'{member}-{width}',
                'targets': [measured['selector']],
            })
            page.wait_for_function('''expected => {
              const rings=[...document.querySelectorAll('.jarvis-story-ring')].filter(e=>e.style.display!=='none');
              return rings.length===expected.length && rings.every((e,i)=>{
                const b=e.getBoundingClientRect(), r=expected[i];
                return Math.abs(b.left-(r.left-6))<.1 && Math.abs(b.top-(r.top-6))<.1
                  && Math.abs(b.width-(r.width+12))<.1 && Math.abs(b.height-(r.height+12))<.1;
              });
            }''', arg=measured['expected'])
    page.evaluate("window.postMessage({jarvis:'package',type:'story.clear'},'*')")
    page.locator('.jarvis-story-ring').first.wait_for(state='detached')


def test_details_suspends_background_slide_shortcuts(presentation_page):
    page, _ = presentation_page()
    page.locator('#go-2').evaluate('e=>e.click()')
    current = page.locator('.slide.current').get_attribute('id')
    for cycle in range(2):
        page.evaluate('window.JarvisPresentation.inspect()')
        overlay = page.locator('.presentation-inspector')
        overlay.locator('button').nth(1).click()
        description = overlay.locator('[role=status]').inner_text()
        # Both public calls and the host message meet at the renderer boundary.
        page.evaluate('window.JarvisPresentation.inspect()')
        page.evaluate("window.postMessage({jarvis:'package',type:'presentation.inspect'},'*')")
        page.wait_for_timeout(30)
        assert overlay.count() == 1, cycle
        assert overlay.locator('[role=status]').inner_text() == description
        assert page.locator('#stage').evaluate('e=>e.inert')
        for key in ('PageDown', 'PageUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'):
            page.keyboard.press(key)
            assert page.locator('.slide.current').get_attribute('id') == current, key
            assert overlay.is_visible()
        page.keyboard.press('Escape')
        overlay.wait_for(state='detached')
        assert not page.locator('#stage').evaluate('e=>e.inert')
    page.keyboard.press('PageDown')
    assert page.locator('.slide.current').get_attribute('id') != current


def _with_table_and_follow_up(data):
    """The street deck with a table slide, a follow-up question, a decision's
    context, compare picture and short name, and a text-only follow-up."""
    data["slides"].insert(2, {
        "id": "costs", "type": "content", "title": "What each route costs", "short": "Costs",
        "layout": "table", "objects": [{
            "id": "cost-table", "type": "table", "slot": "table",
            "source": {"reference": "sample-evidence"}, "alt": "Cost of each route",
            "head": ["Route", "Cost"], "rowHeads": True,
            "rows": [{"id": "paved-row", "cells": ["Paved", "Low"]},
                     {"id": "planted-row", "cells": ["Planted", "Medium"]}]}],
        "readingOrder": ["cost-table"],
        "narration": [{"id": "cost-cue", "say": "Planting costs a little more.",
                       "focus": ["planted-row"]}],
        "notes": ""})
    decision = data["decisions"][0]
    decision.update(short="Trial", context="Two streets are ready for it.",
                    compare="paved-art", say="I recommend the planted trial.")
    follow = {"id": "trial-length", "question": "How long should the trial run?",
              "options": [{"id": "one-month", "name": "One month", "says": "Shorter closure."},
                          {"id": "three-months", "name": "Three months", "says": "More data."}],
              "recommended": "three-months", "reason": "A season of rain is needed.",
              "text_only": "These are lengths of time, which no picture shows better."}
    decision["options"][0]["opens"] = ["trial-length"]
    data["decisions"].append(follow)
    data["slides"].append({"id": "length-choice", "type": "decision", "decision": "trial-length"})


def test_presentation_yaml_says_everything_a_deck_says(source, tmp_path):
    from jarvis import package_story
    from jarvis.presentation import compile_deck
    change(source, _with_table_and_follow_up)
    made = compile_deck(source, tmp_path / "compiled")
    folder = made.package_source
    deck = json.loads((folder / "deck-data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
    table = next(b["table"] for s in deck["slides"] for b in s.get("blocks", []) if "table" in b)
    assert table["head"] == ["Route", "Cost"] and table["rows"][1] == ["Planted", "Medium"]
    assert next(s for s in deck["slides"] if s.get("title") == "What each route costs")["short"] == "Costs"
    first, follow = deck["decisions"][0], deck["decisions"][1]
    assert first["short"] == "Trial" and first["context"] and first["compare"] == "paved.svg"
    assert first["options"][0]["opens"] == ["trial-length"]
    assert follow["text_only"] and "picture" not in follow["options"][0]
    story = package_story.parse((folder / "story.json").read_bytes())
    child = next(slide for slide in story.slides if slide.waits == "trial-length")
    assert child.asks_when is not None
    rows = {t["member"]: t for t in json.loads(made.target_index.read_text())}
    assert rows["planted-row"]["kind"] == "row"


def test_a_story_too_long_to_hear_is_refused(source, tmp_path):
    from jarvis import decision_package as dp
    from jarvis.presentation import compile_deck
    long = " ".join(["word"] * 150)
    def lengthen(data):
        data["slides"][0]["narration"] = [{"id": f"cue-{n}", "say": long, "focus": ["recommendation-words"]}
                                          for n in range(int(dp.SPOKEN_MAX_SEC * dp.SPOKEN_WORDS_PER_SEC / 150) + 2)]
    change(source, lengthen)
    with pytest.raises(DeckCompileError, match="runs about"):
        compile_deck(source, tmp_path / "compiled")


def test_words_are_not_measured_and_any_character_is_drawn(source, tmp_path):
    """No font ships with JARVIS: a slide's words are drawn in the system
    font stack, so neither their room nor their characters are proven."""
    from jarvis.presentation import compile_deck
    def crowd(data):
        data["slides"][1]["title"] = "Rain ☃ routes"
        paragraphs = data["slides"][1]["objects"][0]["paragraphs"]
        paragraphs[1:] = [{"id": f"more-{n}", "role": "paragraph", "runs": [
            {"id": f"more-{n}-words", "text": "Rain stays on the planted block longer.", "emphasis": "normal"}]}
            for n in range(4)]
    change(source, crowd)
    compile_deck(source, tmp_path / "compiled")


def test_the_proof_is_recorded_and_templates_must_be_jarvis_own(source, tmp_path):
    from jarvis import presentation_proof
    from jarvis.presentation import compile_deck
    folder = compile_deck(source, tmp_path / "compiled").package_source
    proof = json.loads((folder / presentation_proof.PROOF_FILE).read_text())
    assert proof["proven"] and proof["templates"] == presentation_proof.template_hashes()
    assert proof["version"] == "presentation-proof/2" and "tightest" not in proof
    assert [662, 489] in proof["frames"] and [1600, 900] in proof["frames"]
    assert presentation_proof.prove_templates(folder) == []
    (folder / "deck.css").write_text((folder / "deck.css").read_text() + "\n.slide{zoom:3}\n")
    assert [f.member for f in presentation_proof.prove_templates(folder)] == ["deck.css"]


def test_the_generated_css_matches_the_spec():
    from jarvis import layout_spec
    assert layout_spec.stale() == []


# -- what a deck no longer carries --------------------------------------------------------

@pytest.mark.parametrize("mutate, field", [
    (lambda d: d.update(situation={"subject": "A trial."}), "situation"),
    (lambda d: d["decisions"][0].update(why_you="You decide the budget."), "why_you"),
    (lambda d: d["decisions"][0].update(evidence=["The plan."]), "evidence"),
])
def test_a_situation_or_lead_in_field_is_refused(source, mutate, field):
    """JARVIS draws no opening from a situation and no lead-in from a
    decision's fields: each is no field of presentation/1."""
    change(source, mutate)
    with pytest.raises(DeckCompileError, match="Extra inputs are not permitted") as refused:
        read_source(source)
    assert field in str(refused.value)


def test_the_author_s_first_slide_is_the_first_drawn(source, tmp_path):
    from jarvis.presentation import compile_deck
    data = yaml.safe_load(source.read_text())
    made = compile_deck(source, tmp_path / "out")
    model = json.loads((made.package_source / "presentation-data.js").read_text()
                       .split("=", 1)[1].strip().rstrip(";"))
    assert model["document"]["slides"][0]["id"] == data["slides"][0]["id"]


def test_a_deck_carries_and_links_no_font(source, tmp_path):
    from jarvis.presentation import compile_deck
    made = compile_deck(source, tmp_path / "out")
    html = (made.package_source / "index.html").read_text()
    assert "font" not in html
    css = (made.package_source / "presentation.css").read_text()
    assert "@font-face" not in css and "-apple-system" in css
    assert not any(p.suffix in {".ttf", ".otf", ".woff", ".woff2"}
                   for p in made.package_source.rglob("*"))
