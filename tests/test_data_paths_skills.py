"""sync_skills: the tracked repo folder is the source, the data folder the output."""
import data_paths


def _src(tmp_path):
    src = tmp_path / "src"
    (src / "alpha" / "sub").mkdir(parents=True)
    (src / "alpha" / "SKILL.md").write_text("new")
    (src / "alpha" / "sub" / "x.txt").write_text("x")
    return src


def test_copies_into_brain_skills_dir(tmp_path):
    assert data_paths.sync_skills(_src(tmp_path)) == ["alpha"]
    out = data_paths.brain_skills_dir() / "alpha"
    assert (out / "SKILL.md").read_text() == "new"
    assert (out / "sub" / "x.txt").read_text() == "x"
    assert not out.is_symlink()


def test_overwrites_but_never_deletes_other_files(tmp_path):
    src = _src(tmp_path)
    out = data_paths.brain_skills_dir()
    (out / "alpha").mkdir(parents=True)
    (out / "alpha" / "SKILL.md").write_text("old")
    (out / "alpha" / "mine.txt").write_text("keep")
    (out / "other").mkdir()
    (out / "other" / "SKILL.md").write_text("keep")
    data_paths.sync_skills(src)
    assert (out / "alpha" / "SKILL.md").read_text() == "new"
    assert (out / "alpha" / "mine.txt").read_text() == "keep"
    assert (out / "other" / "SKILL.md").read_text() == "keep"


def test_missing_source_is_a_noop(tmp_path):
    assert data_paths.sync_skills(tmp_path / "nope") == []
    assert not data_paths.brain_skills_dir().exists()


def test_respects_jarvis_data_dir(tmp_path, monkeypatch):
    other = tmp_path / "elsewhere"
    monkeypatch.setenv("JARVIS_DATA_DIR", str(other))
    data_paths.sync_skills(_src(tmp_path))
    assert (other / "jarvis" / ".claude" / "skills" / "alpha" / "SKILL.md").exists()


def test_repo_ships_after_action_report_and_syncs_it():
    names = data_paths.sync_skills()
    assert "after-action-report" in names and "jarvis-setup" in names
    assert (data_paths.brain_skills_dir() / "after-action-report" / "SKILL.md").is_file()
