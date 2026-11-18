"""The kit's acceptance check 10, JARVIS's half: no architecture or design
document in this repository, and none JARVIS ships, declares either of the
two literal targets the walk-through tests point at, IGrantStore's members
(`[data-declaration="IGrantStore"] [data-part="members"]`) and
IWindowsHelloNativeOps's BeginVerification
(`[data-declaration="IWindowsHelloNativeOps"] [data-member="BeginVerification"]`).

Those targets are drawn only on hand-made test pages
(tests/test_voice_page_walkthrough_story.py); a document declaring them
would let a story about it pass by pointing at stand-ins. The kit's own
repository proves the other half (its rule 2 ban, scanned across its
TypeScript); it cannot see documents, which are YAML, so this test does.

What is scanned: every file git tracks in this checkout (so every file
JARVIS ships: the jarvis package, its skills and authoring contract,
examples and docs), outside .github (CI configuration, not documents) and
the built page (jarvis/web), and this file, whose planted documents prove
the scan catches a declaration. A language document is found where this
repository keeps one:

* a .yaml or .yml file, whole: the checked review documents
  (docs/planning/*/review/source.yaml) and the kit fixtures
  (tests/fixtures/architecture/);
* a fenced block in a Markdown file (design documents, docs, skills) that
  has a `declare` key;
* a JSON file whose object has `declare`;
* in Python, a string or a dict literal with a `declare` key: the documents
  the tests and the kit's adoption smoke fixture (jarvis/language_kit.py)
  write inline.

With the pinned kit asset supplied (JARVIS_LANGUAGE_KIT_ASSET, the
language-kit workflow), the kit's own language files (arch.yaml,
metalang.yaml, propose.yaml), which JARVIS installs, are scanned too, and
every document is also read through the kit's renderer: its page-data
`doc` is the kit's own parse, so a quoting or escape the text match would
miss is still caught.

IGrantStore's members are declared by declaring IGrantStore at all (every
declaration's card has its members part), so no declaration may have that
id; IWindowsHelloNativeOps may be declared, but never with a member named
BeginVerification (or BeginVerification#n)."""
import ast
import functools
import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests import isolated_jarvis
from jarvis import language_documents, language_kit as lk

ROOT = Path(__file__).resolve().parent.parent
GRANT, HELLO, BEGIN = "IGrantStore", "IWindowsHelloNativeOps", "BeginVerification"
# This file plants documents declaring the targets to prove the scan sees them.
SKIPPED = (".github/", "jarvis/web/", "tests/test_architecture_targets_undeclared.py")
DECLARE = re.compile(r"""(?m)(^\s*-?\s*declare\s*:|["']declare["']\s*:)""")
FENCE = re.compile(r"(?ms)^[ \t]*(```|~~~)[^\n]*\n(.*?)^[ \t]*\1")


def _key(name: str, value: str) -> re.Pattern:
    return re.compile(rf"""(?:\b{name}\b|["']{name}["'])\s*:\s*["']?{re.escape(value)}(?:#\d+)?["']?(?![\w])""")


ID_GRANT, ID_HELLO, NAME_BEGIN = _key("id", GRANT), _key("id", HELLO), _key("name", BEGIN)


def tracked() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                            check=True).stdout.decode("utf-8").split("\0")
    return [p for p in listed if p and not p.startswith(SKIPPED) and (ROOT / p).is_file()]


def _python_documents(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and DECLARE.search(node.value):
            found.append(node.value)
        elif isinstance(node, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "declare"
                                                for k in node.keys):
            found.append(ast.unparse(node))
    return found


def documents_in(name: str, text: str) -> list[str]:
    """The language documents one file holds, as text."""
    if name.endswith((".yaml", ".yml")):
        return [text]
    if name.endswith(".md"):
        return [m.group(2) for m in FENCE.finditer(text) if DECLARE.search(m.group(2))]
    if name.endswith(".json"):
        try:
            body = json.loads(text)
        except ValueError:
            return []
        return [text] if isinstance(body, dict) and "declare" in body else []
    if name.endswith(".py"):
        return _python_documents(text)
    return []


def textual_problems(text: str) -> list[str]:
    problems = []
    if ID_GRANT.search(text):
        problems.append(f"declares {GRANT}")
    if ID_HELLO.search(text) and NAME_BEGIN.search(text):
        problems.append(f"declares {HELLO} with {BEGIN}")
    return problems


def parsed_problems(doc: dict) -> list[str]:
    """The same rule over the kit's own parse of a document."""
    problems = []
    for declaration in doc.get("declare") or []:
        for body in (declaration.values() if isinstance(declaration, dict) else ()):
            if not isinstance(body, dict):
                continue
            members = (body.get("value") or {}).get("members") if isinstance(body.get("value"), dict) else None
            names = [str(m.get("name", "")) for m in members if isinstance(m, dict)] \
                if isinstance(members, list) else []
            if body.get("id") == GRANT:
                problems.append(f"declares {GRANT}")
            if body.get("id") == HELLO and any(re.fullmatch(rf"{BEGIN}(#\d+)?", n) for n in names):
                problems.append(f"declares {HELLO} with {BEGIN}")
    return problems


@functools.lru_cache(maxsize=1)
def _scan() -> tuple[tuple[str, str], ...]:
    found = []
    for name in tracked():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        found.extend((name, document) for document in documents_in(name, text))
    return tuple(found)


def scanned() -> list[tuple[str, str]]:
    return list(_scan())


@pytest.fixture(scope="module")
def kit(tmp_path_factory):
    asset = os.getenv("JARVIS_LANGUAGE_KIT_ASSET")
    if not asset:
        yield None
        return
    data = tmp_path_factory.mktemp("kit-data")
    with isolated_jarvis._data_dir(data):
        installed = lk.stage(Path(asset), lk.Release(lk.PINNED_VERSION, lk.PINNED_COMMIT,
                                                     lk.PINNED_DIGEST, ("gate", "release")))
    yield data, installed


def test_the_scan_reads_the_documents_this_repository_keeps():
    names = {name for name, _ in scanned()}
    for kept in ("tests/fixtures/architecture/quoted-members.yaml",
                 "docs/planning/language-kit-integration/review/source.yaml",
                 "jarvis/language_kit.py", "tests/test_architecture_end_to_end.py"):
        assert kept in names, f"{kept} holds a language document the scan did not find"


@pytest.mark.parametrize("planted", [
    ("doc.yaml", f"declare:\n- constant:\n    id: {GRANT}\n"),
    ("doc.yaml", f"declare:\n- constant:\n    id: '{HELLO}'\n    value:\n      members:\n"
                 f"      - name: {BEGIN}#2\n"),
    ("design.md", f"Prose.\n\n```yaml\ndeclare:\n- constant:\n    id: \"{GRANT}\"\n```\n"),
    ("t.py", f"SOURCE = {{'declare': [{{'constant': {{'id': '{HELLO}', 'value': "
             f"{{'members': [{{'name': '{BEGIN}'}}]}}}}}}]}}\n"),
])
def test_a_document_declaring_either_target_is_caught(planted):
    name, text = planted
    [document] = documents_in(name, text)
    assert textual_problems(document)


def test_a_target_named_outside_a_declaration_is_not_a_declaration():
    # A story or a hand-made test page points at the targets; that is not
    # declaring them.
    text = (f'```json\n{{"targets": ["[data-declaration=\\"{GRANT}\\"] [data-part=\\"members\\"]"]}}\n```\n'
            f'<tr data-member="{BEGIN}">')
    assert documents_in("guide.md", text) == []
    assert not textual_problems(f"declare:\n- constant:\n    id: {HELLO}\n    value:\n"
                                f"      members:\n      - name: BeginOther\n")


def test_no_document_declares_either_target(kit):
    documents = scanned()
    if kit is not None:
        _, installed = kit
        documents += [(f"pinned kit {lk.PINNED_VERSION}/{name}",
                       (installed.directory / name).read_text(encoding="utf-8"))
                      for name in ("arch.yaml", "metalang.yaml", "propose.yaml")]
    found = [f"{name}: {problem}" for name, document in documents
             for problem in textual_problems(document)]
    if kit is not None:
        data, installed = kit
        with isolated_jarvis._data_dir(data):
            for name, document in documents:
                rendered = installed.run("render", document.encode("utf-8")[:lk.MAX_SOURCE_BYTES])
                if rendered.view is None:
                    continue              # not a document the kit reads; the text match stands
                try:
                    doc = language_documents.page_data(rendered.view)["doc"]
                except (language_documents.DocumentError, ValueError):
                    continue
                found += [f"{name} (kit's parse): {p}" for p in parsed_problems(doc)]
    assert found == [], "\n".join(found)
