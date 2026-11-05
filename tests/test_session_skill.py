"""The talk-to-jarvis skill ships with the repo and teaches the user's own
sessions the needs board and session messages. Nothing here touches a real
~/.claude or ~/.codex."""

import pytest

from jarvis import preflight
from jarvis import session_skill


@pytest.fixture
def homes(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path / "data"))
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setattr(session_skill.session_watch, "config_roots",
                        lambda: [claude, tmp_path / "absent"])
    monkeypatch.setattr(session_skill.codex_sessions, "codex_home", lambda: codex)
    return tmp_path, claude, codex


def test_the_skill_is_rendered_with_this_install_s_folders(homes):
    tmp_path, _claude, _codex = homes

    text = session_skill.render()

    assert "{{" not in text
    assert str(tmp_path / "data" / "needs") + "/<project>.md" in text
    assert str(tmp_path / "data" / "messages") + "/<project>/<short-name>.md" in text
    assert text.startswith("---\nname: talk-to-jarvis\n")


def test_the_skill_ships_nothing_personal():
    text = session_skill.TEMPLATE.read_text()
    assert "Tim" not in text and "/Users/" not in text


def test_the_skill_is_not_one_of_the_brain_s_own():
    """skills/ is copied into the brain's home; the brain must not be taught
    to send messages to itself."""
    from jarvis import data_paths
    assert session_skill.TEMPLATE.parent.parent != data_paths._SKILLS_SOURCE


def test_install_writes_every_existing_home_and_status_follows(homes):
    _tmp, claude, codex = homes
    wanted = [claude / "skills" / "talk-to-jarvis" / "SKILL.md",
              codex / "skills" / "talk-to-jarvis" / "SKILL.md"]

    assert session_skill.status() == {p: session_skill.MISSING for p in wanted}
    assert session_skill.install() == wanted
    assert session_skill.status() == {p: session_skill.INSTALLED for p in wanted}

    wanted[0].write_text("old")
    assert session_skill.status()[wanted[0]] == session_skill.OUT_OF_DATE


def test_a_tool_he_does_not_use_gets_nothing(homes, monkeypatch, tmp_path):
    monkeypatch.setattr(session_skill.codex_sessions, "codex_home",
                        lambda: tmp_path / "no-codex")
    _tmp, claude, _codex = homes

    assert session_skill.targets() == [claude / "skills" / "talk-to-jarvis" / "SKILL.md"]


def test_the_command_installs_and_anything_else_is_usage(homes, capsys):
    assert session_skill.main(["session_skill.py"]) == 2
    assert session_skill.main(["session_skill.py", "install"]) == 0
    assert capsys.readouterr().out.count("installed ") == 2


def test_preflight_reports_and_never_installs(homes):
    _tmp, claude, _codex = homes

    check = preflight._check_session_skill_sync()

    assert check.status == preflight.STATUS_WARN
    assert "missing" in check.message and check.remedy == "Run: python -m jarvis.session_skill install"
    assert preflight._phrase_for(check) == "your sessions don't have the talk-to-jarvis skill"
    assert not (claude / "skills").exists()

    session_skill.install()
    assert preflight._check_session_skill_sync().status == preflight.STATUS_OK
