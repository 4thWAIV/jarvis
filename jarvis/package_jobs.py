"""Packages as jobs: an agent starts one, asks how it stands, and answers
its author's questions.

The interface the agent's MCP tools (agent_mcp.py's `package_start`,
`package_status` and `package_answer`) and their command call. Each runs in
the sending agent's own process, signed as its session
(JARVIS_AGENT_SESSION); a job's state is a record. The job itself (its
detached process, its author session, its question rounds) is built
behind these three functions.
"""
from __future__ import annotations

from typing import Optional


def package_start(handoff_path: str, work: Optional[str] = None,
                  general: bool = False) -> dict:
    """Start a package job from the handoff at `handoff_path`, for the Work
    item `work` (or for none, `general`). Returns at once: `{"job": <id>,
    ...}`."""
    raise NotImplementedError("package jobs are not built yet")


def package_status(job: str) -> dict:
    """Where job `job` stands: its state, its author's questions when it
    waits on the sender, and what it delivered or why it was refused."""
    raise NotImplementedError("package jobs are not built yet")


def package_answer(job: str, answers: dict) -> dict:
    """Answer job `job`'s waiting questions (`answers`: question id ->
    answer); the author goes on with them. Returns the job's state."""
    raise NotImplementedError("package jobs are not built yet")
