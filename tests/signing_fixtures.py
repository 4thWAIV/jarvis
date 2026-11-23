"""A package signed as make_package signs one: with the sending agent's own
key (package_signature.py), what every test of an onboarded agent's package
admits it with."""
from __future__ import annotations

import shutil
from pathlib import Path

from jarvis import package_signature


def sign_as_agent(raw: Path, target: Path, agent, session: str, statements=(),
                  work_key: dict | None = None) -> None:
    """Sign the zip at `raw` into `target` with the agent's own key, as
    make_package does."""
    if raw != target:
        shutil.copyfile(raw, target)
    package_signature.sign(target, target, agent.private, agent.public, session,
                           tuple(statements), work_key)
