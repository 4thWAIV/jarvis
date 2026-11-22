"""The release check: every deck source JARVIS ships or tests with is built
and proven as an author's would be (its slots within their capacity,
deck_capacity), and proven again from its built bytes as Tim's JARVIS proves
a deck on arrival; every example package still reads as a package. A change
to the layout spec, the compiler, the components, the charts or the
capacity model that leaves any of them behind fails here, in CI, before a
release.

The corpus: examples/presentations/*/presentation.yaml, the three decks
`make_package --example` prints, the hard fixture deck (tests/deck_fixtures,
also drawn in the CI page test), the street example with the components and
with data, and the test decks' own (deck_fixtures.PRESENTATION); and every
folder under examples/packages."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from jarvis import layout_spec, make_package, package_story, presentation_proof, work_packages
from jarvis.presentation import compile_deck
from tests import deck_fixtures

EXAMPLES = Path(__file__).parents[1] / "examples"
STREET = EXAMPLES / "presentations" / "street"


def _copy(folder: Path, into: Path) -> Path:
    shutil.copytree(folder, into)
    return into / "presentation.yaml"


def _write(into: Path, data: dict) -> Path:
    into.mkdir(parents=True, exist_ok=True)
    (into / "presentation.yaml").write_text(yaml.safe_dump(json.loads(json.dumps(data)),
                                                           sort_keys=False, allow_unicode=True))
    return into / "presentation.yaml"


def _street_with(into: Path, change) -> Path:
    source = _copy(STREET, into)
    data = yaml.safe_load(source.read_text())
    change(data)
    source.write_text(yaml.safe_dump(json.loads(json.dumps(data)), sort_keys=False, allow_unicode=True))
    return source


CORPUS = {
    **{f"example-{p.parent.name}": (lambda into, p=p: _copy(p.parent, into))
       for p in sorted((EXAMPLES / "presentations").glob("*/presentation.yaml"))},
    "make-package-example-decisions": lambda into: _write(into, make_package.EXAMPLE),
    "make-package-example-presentation": lambda into: _write(into, make_package.PRESENTATION_EXAMPLE),
    "make-package-example-visuals": lambda into: _write(into, make_package.VISUALS_EXAMPLE),
    "hard-deck": lambda into: (into.mkdir(), deck_fixtures.hard_deck(into))[1],
    "street-with-components": lambda into: _street_with(
        into, lambda d: d["slides"].insert(2, json.loads(json.dumps(deck_fixtures.COMPONENTS_SLIDE)))),
    "street-with-data": lambda into: _street_with(into, deck_fixtures.with_data),
    "fixture-presentation": lambda into: (into.mkdir(), deck_fixtures.write_yaml(
        into, deck_fixtures.PRESENTATION))[1],
}


def test_the_corpus_names_every_shipped_example():
    shipped = {f"example-{p.parent.name}" for p in (EXAMPLES / "presentations").glob("*/presentation.yaml")}
    assert shipped and shipped <= set(CORPUS)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_every_deck_in_the_corpus_builds_proves_and_proves_again_on_arrival(name, tmp_path):
    source = CORPUS[name](tmp_path / "source")
    made = compile_deck(source, tmp_path / "compiled")
    folder = made.package_source
    proof = json.loads((folder / presentation_proof.PROOF_FILE).read_text())
    assert proof["proven"], name
    assert proof["templates"] == presentation_proof.template_hashes()
    assert all(o["capacity"]["fits"] for s in proof["slides"] for o in s.get("objects") or []
               if "capacity" in o), name
    assert presentation_proof.reprove(folder) == [], name
    package_story.parse((folder / "story.json").read_bytes())


@pytest.mark.parametrize("folder", sorted(p.parent for p in (EXAMPLES / "packages").glob("*/package.json")),
                         ids=lambda p: p.name)
def test_every_example_package_still_reads_as_a_package(folder):
    manifest = work_packages.Manifest.parse((folder / work_packages.MANIFEST).read_bytes())
    assert manifest.name
    if (folder / "story.json").is_file():
        package_story.parse((folder / "story.json").read_bytes())


def test_the_generated_files_are_the_spec_s():
    assert layout_spec.stale() == []
