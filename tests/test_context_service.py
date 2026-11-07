"""The context pack (phase 2): from records and notes alone, from the
engine when it answers in time, in a run behind JARVIS_RUN_CONTEXT, and for
the brain through work_context."""
from __future__ import annotations

import hashlib
import json

import pytest

from jarvis import context_service as cs
from jarvis import jarvis_memory, knowledge_links as kl
from tests.test_spawn_run_tool import _Executor, ready, wired  # noqa: F401


def seed_world():
    jarvis_memory.write_project_note("chitauri", "The step checker runs against the tool registry.")
    jarvis_memory.write_project_note("chitauri", "Parris Island Engine work starts with the step checker.")
    kl.create_topic("Denice", status="planned", ambiguous=["Denise"], pinned=True,
                    body="# Denice\n\n> \"Denice, pronounced Denise, is the successor direction.\" (README.md)\n")
    kl.create_topic("Parris Island Engine", status="planned", body="# Parris Island Engine\n\n> \"Denice on the new language.\"\n")
    kl.create_topic("Cake", status="active")
    kl.link("parris-island-engine", "stage_of", "denice")


def test_tier_zero_from_notes_and_topics_alone():
    seed_world()
    p = cs.pack(purpose="add a refusal list to the step checker", project="chitauri")
    assert p.tier == 0 and not p.partial
    assert p.text.startswith(cs.HEADER)
    assert "The step checker runs against the tool registry." in p.text
    # Named in a note, and its stage's topic two hops away; Cake is not touched.
    assert "Parris Island Engine (planned, not implemented)" in p.text
    assert "Denice (planned, not implemented)" in p.text
    assert "Cake" not in p.text
    assert "projects/chitauri.md" in p.sources and "topics/denice.md" in p.sources
    assert len(p.text) <= cs.RUN_BUDGET


def test_the_budget_is_kept():
    seed_world()
    for i in range(80):
        jarvis_memory.write_project_note("chitauri", f"Note {i} about the step checker " + "x" * 80)
    p = cs.pack(purpose="the step checker", project="chitauri", budget_chars=1000)
    assert len(p.text) <= 1000 and p.sections["project"]


def test_tier_one_keeps_to_his_words_and_caps_each_document():
    seed_world()
    hits = ([{"label": "doc-a", "source": "jref:a", "trust": "own", "text": f"own passage {i}"} for i in range(5)]
            + [{"label": "doc-b", "source": "jref:b", "trust": "untrusted", "text": "SOMEONE ELSE said this"}])
    p = cs.pack(purpose="step checker", project="chitauri", search=lambda t, own, d: hits)
    assert p.tier == 1 and "SOMEONE ELSE" not in p.text
    assert sum(1 for line in p.text.splitlines() if "(doc-a)" in line) == cs.PER_DOCUMENT
    walled = cs.pack(purpose="step checker", project="chitauri", trust="all", search=lambda t, own, d: hits)
    assert "SOMEONE ELSE" in walled.text


def test_a_late_engine_gives_tier_zero_marked_partial():
    seed_world()
    jarvis_memory.write_memory("Step checker", "The step checker refuses unknown tools.")
    p = cs.pack(purpose="step checker", project="chitauri", search=lambda t, own, d: None)
    assert p.partial and p.tier == 0 and "partial" in p.text.splitlines()[0]
    assert "refuses unknown tools" in p.text


@pytest.mark.asyncio
async def test_a_run_gets_no_context_while_the_switch_is_off(ready, monkeypatch):
    server, store, ex = ready
    monkeypatch.delenv("JARVIS_RUN_CONTEXT", raising=False)
    seed_world()
    await server.tool_spawn_run({"project": "chitauri", "prompt": "build it"})
    assert ex.spawned[0]["prompt"] == server.UNATTENDED_PREAMBLE + "build it"


@pytest.mark.asyncio
async def test_a_run_gets_its_projects_context_first_and_his_words_last(ready, monkeypatch):
    server, store, ex = ready
    monkeypatch.setenv("JARVIS_RUN_CONTEXT", "1")
    seed_world()
    await server.tool_spawn_run({"project": "chitauri", "prompt": "add a refusal list to the step checker"})
    prompt = ex.spawned[0]["prompt"]
    assert prompt.startswith(server.UNATTENDED_PREAMBLE + server.RUN_CONTEXT_START + cs.HEADER)
    assert prompt.endswith(server.RUN_CONTEXT_END + "add a refusal list to the step checker")
    assert "Parris Island Engine (planned, not implemented)" in prompt
    assert server.user_prompt_of(prompt) == "add a refusal list to the step checker"
    run_id = ex.spawned[0]["run_id"]
    from jarvis import data_paths
    text = (data_paths.run_context_dir() / f"{run_id}.md").read_text()
    assert text in prompt
    assert store.get_run(run_id)["context_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    await server.tool_spawn_run({"project": "chitauri", "prompt": "again", "context": False})
    assert ex.spawned[1]["prompt"] == server.UNATTENDED_PREAMBLE + "again"
