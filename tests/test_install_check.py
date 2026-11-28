"""The shared answers install.sh and install.ps1 ask: stamps, what is
already done, the model sizes and the preflight lines. The subprocess seam
and the modules that own each fact are faked."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jarvis import install_check, preflight, session_skill


@pytest.fixture
def root(tmp_path, monkeypatch):
    (tmp_path / "frontend" / "node_modules").mkdir(parents=True)
    (tmp_path / ".venv").mkdir()
    (tmp_path / "requirements.txt").write_text("-e .[test]\n")
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "frontend" / "package-lock.json").write_text("{}")
    monkeypatch.setattr(install_check, "ROOT", tmp_path)
    monkeypatch.setattr(install_check, "STAMPS", {
        "pip": (tmp_path / ".venv" / ".jarvis-install.json", ("requirements.txt", "pyproject.toml")),
        "npm": (tmp_path / "frontend" / "node_modules" / ".jarvis-install.json",
                ("frontend/package-lock.json",)),
    })
    return tmp_path


def test_a_stamp_is_not_ok_until_set(root):
    assert not install_check.stamp_ok("pip")
    assert install_check.stamp_set("pip")
    assert install_check.stamp_ok("pip")


def test_a_changed_input_forgets_the_stamp(root):
    install_check.stamp_set("npm")
    (root / "frontend" / "package-lock.json").write_text('{"changed": true}')
    assert not install_check.stamp_ok("npm")


def test_pip_stamp_follows_requirements_and_pyproject(root):
    install_check.stamp_set("pip")
    (root / "requirements.txt").write_text("-e .\n")
    assert not install_check.stamp_ok("pip")


def test_deleting_what_the_step_made_forgets_it(root):
    install_check.stamp_set("npm")
    (root / "frontend" / "node_modules" / ".jarvis-install.json").unlink()
    assert not install_check.stamp_ok("npm")


def test_a_stamp_is_not_written_where_nothing_was_made(root):
    (root / "frontend" / "node_modules").rmdir()
    assert not install_check.stamp_set("npm")


def test_a_corrupt_stamp_is_not_ok(root):
    (root / ".venv" / ".jarvis-install.json").write_text("not json")
    assert not install_check.stamp_ok("pip")


def test_certs_need_both_files(root):
    assert not install_check.certs_made()
    (root / "cert.pem").write_text("c")
    assert not install_check.certs_made()
    (root / "key.pem").write_text("k")
    assert install_check.certs_made()


def fake_dry_run(monkeypatch, places, returncode=0):
    out = "".join(f"Chrome\n  Install location:    {p}\n  Download url: x\n\n" for p in places)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, returncode, out, ""))


def test_playwright_needs_every_folder_finished(tmp_path, monkeypatch):
    a, b = tmp_path / "chromium-1", tmp_path / "ffmpeg-1"
    a.mkdir(); b.mkdir()
    fake_dry_run(monkeypatch, [a, b])
    assert not install_check.playwright_installed()
    (a / "INSTALLATION_COMPLETE").write_text("")
    assert not install_check.playwright_installed()
    (b / "INSTALLATION_COMPLETE").write_text("")
    assert install_check.playwright_installed()


def test_playwright_missing_or_failing_is_not_installed(tmp_path, monkeypatch):
    fake_dry_run(monkeypatch, [], returncode=0)
    assert not install_check.playwright_installed()
    fake_dry_run(monkeypatch, [tmp_path], returncode=1)
    assert not install_check.playwright_installed()


@pytest.mark.parametrize("found,expected", [
    ({}, install_check.NO_HOME),
    ({Path("a"): session_skill.INSTALLED, Path("b"): session_skill.INSTALLED}, 0),
    ({Path("a"): session_skill.INSTALLED, Path("b"): session_skill.MISSING}, 1),
    ({Path("a"): session_skill.OUT_OF_DATE}, 1),
])
def test_skill_state(monkeypatch, found, expected):
    monkeypatch.setattr(session_skill, "status", lambda: found)
    assert install_check.skill_state() == expected
    assert install_check.main(["done", "skill"]) == expected


def test_done_asks_the_module_that_owns_the_fact(monkeypatch):
    monkeypatch.setitem(install_check.DONE, "speech", lambda: True)
    monkeypatch.setitem(install_check.DONE, "embedding", lambda: False)
    assert install_check.main(["done", "speech"]) == 0
    assert install_check.main(["done", "embedding"]) == 1


def test_models_say_their_sizes(capsys):
    assert install_check.main(["models"]) == 0
    out = capsys.readouterr().out
    assert "English speech model: about" in out and "MiB" in out
    assert "multilingual-e5-small" in out


@pytest.mark.parametrize("args", [[], ["nope"], ["stamp-ok"], ["stamp-ok", "x"], ["done", "x"]])
def test_bad_usage_is_64(args, capsys):
    assert install_check.main(args) == 64


def test_preflight_lines_are_one_short_line(monkeypatch, capsys):
    checks = [preflight.Check("a", "ok", "fine"),
              preflight.Check("b", "warn", "line one\n  line two", remedy="do\nthis"),
              preflight.Check("c", "fail", "x" * 500)]
    monkeypatch.setattr(install_check, "preflight_checks", lambda: checks)
    assert install_check.main(["preflight"]) == 0   # preflight informs, never fails
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "OK   a: fine"
    assert lines[1] == "WARN b: line one line two (do this)"
    assert lines[2].startswith("FAIL c: ") and len(lines[2]) == install_check.LINE_MAX


def test_preflight_runs_claude_providers_checks(monkeypatch):
    seen = {}

    async def run_checks(*, provider_checks, **_):
        seen["provider"] = provider_checks
        return []
    monkeypatch.setattr(preflight, "run_checks", run_checks)
    assert install_check.preflight_checks() == []
    assert preflight._check_claude_login in seen["provider"][0]
    assert preflight._check_anthropic_key_leftover_sync in seen["provider"][1]


@pytest.mark.parametrize("script,preflight,skill", [
    ("install.sh", "check_py preflight", "check_py done skill"),
    ("install.ps1", '"preflight")', '"done", "skill")'),
])
def test_the_skill_step_follows_preflight(script, preflight, skill):
    """A first `claude` run, which preflight's login check is, makes the
    Claude home the skill is installed into; the skill step before it
    would find no home on a first run and install on the second."""
    text = (Path(__file__).resolve().parent.parent / script).read_text(encoding="utf-8")
    assert 0 < text.index(preflight) < text.index(skill)
