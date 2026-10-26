"""tests/local-config.toml: one machine's own skips, and none without it."""

import tomllib
from pathlib import Path

import pytest

from tests.fixtures.local_config import LOCAL_CONFIG, read_local_skips

EXAMPLE = Path(__file__).parent / "local-config.example.toml"
SIPS = "tests/test_screen_sight.py::test_the_real_sips_sees_a_real_black_png_as_blank"


def test_no_file_skips_nothing(tmp_path):
    skips = read_local_skips(tmp_path / "local-config.toml")

    assert not skips
    assert not skips.skips(SIPS, {"sips"})


def test_a_listed_test_is_skipped_and_its_neighbours_are_not(tmp_path):
    path = tmp_path / "local-config.toml"
    path.write_text(f'[skip]\ntests = ["{SIPS}"]\n')

    skips = read_local_skips(path)

    assert skips.skips(SIPS, set())
    assert not skips.skips(SIPS + "_and_more", set())
    assert not skips.skips("tests/test_screen_sight.py::test_other", set())


def test_a_listed_file_skips_every_test_in_it(tmp_path):
    path = tmp_path / "local-config.toml"
    path.write_text('[skip]\ntests = ["tests/test_screen_sight.py"]\n')

    assert read_local_skips(path).skips(SIPS, set())


def test_a_listed_marker_skips_every_test_carrying_it(tmp_path):
    path = tmp_path / "local-config.toml"
    path.write_text('[skip]\nmarkers = ["sips"]\nreason = "sips crashes here"\n')

    skips = read_local_skips(path)

    assert skips.skips("tests/test_x.py::test_y", {"sips", "asyncio"})
    assert not skips.skips("tests/test_x.py::test_y", {"asyncio"})
    assert skips.reason == "sips crashes here (tests/local-config.toml)"


@pytest.mark.parametrize("text", ["[skip\n", '[skip]\ntests = "one"\n',
                                  "[skip]\nmarkers = [1]\n"])
def test_a_broken_file_stops_the_run_instead_of_guessing(tmp_path, text):
    path = tmp_path / "local-config.toml"
    path.write_text(text)

    with pytest.raises(ValueError):
        read_local_skips(path)


def test_the_example_skips_nothing_and_is_the_format_read(tmp_path):
    assert "[skip]" in EXAMPLE.read_text()
    assert tomllib.loads(EXAMPLE.read_text())["skip"] == {"tests": [], "markers": []}
    assert not read_local_skips(EXAMPLE)


def test_the_example_s_commented_skip_names_real_tests():
    """Uncommenting the example's lines must skip the sips tests."""
    import re
    text = EXAMPLE.read_text()
    assert f'# "{SIPS}"' in text
    assert re.search(r'#\s*"sips"', text)
    ini = (Path(__file__).parent.parent / "pytest.ini").read_text()
    assert "\n    sips:" in ini


def test_the_local_file_is_where_git_ignores_it():
    assert LOCAL_CONFIG == Path(__file__).parent / "local-config.toml"
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text()
    assert "tests/local-config.toml" in gitignore.splitlines()
