"""This machine's own test settings: `tests/local-config.toml`.

Git ignores the file, so whatever it skips is skipped on this computer only.
With no file, nothing is skipped and the suite runs as everyone else runs it.
`tests/local-config.example.toml` shows the format.
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

LOCAL_CONFIG = Path(__file__).resolve().parent.parent / "local-config.toml"


@dataclass
class LocalSkips:
    tests: list[str] = field(default_factory=list)    # node ids, or a file
    markers: list[str] = field(default_factory=list)
    reason: str = "skipped on this machine by tests/local-config.toml"

    def __bool__(self) -> bool:
        return bool(self.tests or self.markers)

    def skips(self, nodeid: str, markers: set[str]) -> bool:
        """A test is skipped when the file names it, a file or class that
        holds it, or one of its markers."""
        named = any(nodeid == t or nodeid.startswith(t + "::")
                    for t in self.tests)
        return named or bool(markers & set(self.markers))


def read_local_skips(path: Path = LOCAL_CONFIG) -> LocalSkips:
    """What the file says to skip. A missing file skips nothing; a file that
    cannot be read as the example's format stops the run rather than
    quietly running or skipping the wrong tests."""
    if not path.is_file():
        return LocalSkips()
    try:
        skip = tomllib.loads(path.read_text(encoding="utf-8")).get("skip", {})
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path.name} is not valid TOML: {e}") from e
    tests, markers = skip.get("tests", []), skip.get("markers", [])
    for name, value in (("tests", tests), ("markers", markers)):
        if not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
            raise ValueError(f"{path.name}: [skip] {name} must be a list of strings")
    reason = skip.get("reason")
    out = LocalSkips(tests=tests, markers=markers)
    if isinstance(reason, str) and reason.strip():
        out.reason = f"{reason.strip()} (tests/local-config.toml)"
    return out
