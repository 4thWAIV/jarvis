"""claude_env.command_prefix: the argv a configured `claude` starts with, for
the brain (claude_provider) and the run pipeline (run_executor)."""
import sys

import pytest

from jarvis import claude_env


def test_macos_keeps_shell_rules_so_the_setting_may_carry_arguments(monkeypatch):
    monkeypatch.setattr(claude_env.sys, "platform", "darwin")
    assert claude_env.command_prefix("/usr/local/bin/claude") == ["/usr/local/bin/claude"]
    assert claude_env.command_prefix("node '/opt/my tools/cli.js' --x") == [
        "node", "/opt/my tools/cli.js", "--x"]


def test_on_windows_an_existing_file_is_one_element_spaces_and_backslashes_kept(
        monkeypatch, tmp_path):
    """shlex's POSIX rules made C:\\Users\\...\\claude.EXE into C:Users...claude.EXE."""
    exe = tmp_path / "Claude Code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    monkeypatch.setattr(claude_env.sys, "platform", "win32")
    monkeypatch.setattr(claude_env, "_windows_split",
                        lambda text: pytest.fail("a whole path is never split"))
    assert claude_env.command_prefix(str(exe)) == [str(exe)]
    assert claude_env.command_prefix(f'"{exe}"') == [str(exe)]


def test_on_windows_anything_else_is_split_by_windows_rules(monkeypatch):
    monkeypatch.setattr(claude_env.sys, "platform", "win32")
    monkeypatch.setattr(claude_env, "_windows_split", lambda text: ["C:\\py\\python.exe", "x.py"])
    assert claude_env.command_prefix('"C:\\py\\python.exe" x.py') == [
        "C:\\py\\python.exe", "x.py"]


@pytest.mark.parametrize("launcher", ["C:\\npm\\claude.cmd", "C:\\npm\\CLAUDE.BAT"])
def test_on_windows_a_batch_launcher_is_refused(monkeypatch, launcher):
    """cmd.exe would parse the brain's arguments, its whole system prompt
    among them, a second time."""
    monkeypatch.setattr(claude_env.sys, "platform", "win32")
    monkeypatch.setattr(claude_env, "_windows_split", lambda text: [text])
    with pytest.raises(PermissionError):
        claude_env.command_prefix(launcher)


def test_on_windows_nothing_configured_is_not_found(monkeypatch):
    monkeypatch.setattr(claude_env.sys, "platform", "win32")
    with pytest.raises(FileNotFoundError):
        claude_env.command_prefix("   ")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own command-line rules")
def test_natively_windows_splits_a_quoted_path_with_spaces_and_arguments():
    assert claude_env._windows_split(
        '"C:\\Program Files\\Claude\\claude.exe" --flag "two words" C:\\a\\b') == [
        "C:\\Program Files\\Claude\\claude.exe", "--flag", "two words", "C:\\a\\b"]


def test_the_brain_and_the_run_pipeline_both_start_from_it(monkeypatch, tmp_path):
    from jarvis import claude_provider, run_executor
    from jarvis.brain import BrainConfig
    monkeypatch.setattr(claude_env, "command_prefix", lambda configured: ["PREFIX", configured])
    session = claude_provider.ClaudeSession(BrainConfig(home=tmp_path, claude_path="c"),
                                            lambda *a: None, None)
    assert session.command("i")[:2] == ["PREFIX", "c"]
    executor = run_executor.RunExecutor.__new__(run_executor.RunExecutor)
    executor._claude_path = "c"
    assert executor._command("run-1", None, "opus")[:2] == ["PREFIX", "c"]
