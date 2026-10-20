"""server.py reads its `.env` at import from JARVIS_ENV_FILE when that is set,
so a test never boots on the developer's real settings."""

import importlib


def test_the_boot_loader_reads_the_file_jarvis_env_file_names(monkeypatch, tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("JARVIS_TEST_BOOT_MARKER=from-the-test-file\n")
    monkeypatch.setenv("JARVIS_ENV_FILE", str(dotenv))
    monkeypatch.delenv("JARVIS_TEST_BOOT_MARKER", raising=False)

    import server
    importlib.reload(server)

    import os
    assert os.environ["JARVIS_TEST_BOOT_MARKER"] == "from-the-test-file"
    assert server._env_file_path() == dotenv


def test_the_suite_starts_pointed_away_from_the_real_dotenv():
    """Set before any test module imports server, so even the first import
    at collection never reads the repository's own .env."""
    from pathlib import Path

    from tests.conftest import NO_DOTENV_UNDER_TEST

    real = Path(__file__).resolve().parent.parent / ".env"
    assert NO_DOTENV_UNDER_TEST != real
    assert not NO_DOTENV_UNDER_TEST.exists()
