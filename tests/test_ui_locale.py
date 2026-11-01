"""The interface's language (frontend/src/locale.ts): typed English and
Latin American Spanish catalogs with the same keys and placeholders, one
locale store fed only by the server's language in force (no browser
storage, no second preference), a stale read never undoing a pushed change,
and nothing on the page found by its translated words.

The catalogs and the store run under node (tests/ui_locale_cases.mts); the
server's side is tests/test_language_setting.py; words still written in
English are the `label-unlocalized` rule (tests/test_ui_coverage_rules.py).
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis import language_setting

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"


def _code(name: str) -> str:
    text = (SRC / name).read_text()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def test_the_catalogs_and_the_store_hold_under_node():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to run the page's catalogs")
    if not (ROOT / "frontend" / "node_modules" / "react").is_dir():
        pytest.skip("the frontend's node_modules are needed (npm ci)")
    out = subprocess.run([node, "--experimental-strip-types", "--no-warnings",
                          "--import", str(ROOT / "tests" / "ui_ts_loader.mjs"),
                          str(ROOT / "tests" / "ui_locale_cases.mts")],
                         capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert out.returncode == 0, out.stderr[-4000:]
    assert len(json.loads(out.stdout)["passed"]) == 7, out.stdout


def test_the_language_is_kept_only_by_the_server():
    for name in ("locale.ts", "messages/en.ts", "messages/es.ts", "LanguageControl.tsx"):
        code = _code(name)
        for store in ("localStorage", "sessionStorage", "indexedDB", "document.cookie"):
            assert store not in code, (name, store)


def test_the_page_hears_the_frame_the_server_publishes():
    assert language_setting.FRAME == "language"
    socket = _code("sessionsSocket.ts")
    assert 'msg.type === "language"' in socket and "languageReported(msg.language)" in socket


def test_nothing_on_the_page_is_found_by_its_words():
    """A label follows the language; an identity never does. Elements are
    found by registered ids (`data-ui-tab`, `data-session-id`, ...)."""
    found = []
    for path in sorted(SRC.glob("*.ts*")):
        code = _code(path.name)
        for m in re.finditer(r"(querySelector(?:All)?|closest|matches)\(([^)]*)\)", code):
            if re.search(r"\[(aria-label|title|placeholder|alt)\b", m.group(2)):
                found.append((path.name, m.group(0)))
    assert found == []
    assert '[data-ui-tab="${tab}"]' in _code("transferEndpoints.ts")
    assert "data-ui-tab={t.id}" in _code("Tabs.tsx")


def test_the_shell_s_words_come_from_the_catalog():
    """The worked example: the tabs, the menu and the language control."""
    for name in ("RightPanel.tsx", "CentreArea.tsx", "MenuControl.tsx", "LanguageControl.tsx"):
        assert "useT()" in _code(name), name
    assert re.search(r"t\(`tabs\.right\.\$\{tab\.id\}`\)", _code("RightPanel.tsx"))
    assert re.search(r"t\(`tabs\.main\.\$\{v\.id\}`\)", _code("CentreArea.tsx"))
